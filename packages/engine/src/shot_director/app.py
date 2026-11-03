"""FastAPI app: the local Anime News engine. One process serves the API, the static UI and the generated media.

Contract (shared with apps/web): GET /api/health, GET|PUT /api/config, POST /api/storyboard, POST /api/render
(202 + background job), GET|DELETE /api/jobs/{id}, GET /api/episodes, GET /api/weekly, GET /media/{file}
(including /media/weekly/<week>/<file>). CORS: an origin
allowlist (ANIME_NEWS_ALLOWED_ORIGINS) and Access-Control-Allow-Private-Network on every preflight, so the
hosted web app can drive the engine on the user's own machine. The Comfy API key is stored in
~/.anime-news/config.json and never returned, logged or echoed.
"""

import argparse
from contextlib import asynccontextmanager
import hashlib
import hmac
import os
import re
import sys
import threading
import time
import uuid
from collections import deque
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, version as pkg_version
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import config, episode, hosted, news, presets
from .router_client import ENGINES, IMAGE_MODEL, VIDEO_MODEL, RouterError, make_router

ROOT = Path(__file__).resolve().parents[2]
STATIC = Path(__file__).parent / "static"
# Exact origins only: a *.vercel.app wildcard would let any Vercel-hosted page set a user's key and start paid renders.
# Patterns with * still work when set explicitly in ANIME_NEWS_ALLOWED_ORIGINS (e.g. for preview deploys).
DEFAULT_ORIGINS = "https://anime-news-nine.vercel.app,http://localhost:3000,http://127.0.0.1:3000"
MAX_MINUTES = 5.0
# Hosted mode: a render may carry this many seconds of shots per allowed minute (the writer makes about 4 scenes of
# 3-6 five-second shots a minute, and the cut trims them), so an edited storyboard cannot grow without bound.
HOSTED_SHOT_SECONDS_PER_MINUTE = 2.5


def _version() -> str:
    try:
        return pkg_version("anime-news")
    except PackageNotFoundError:
        return "0.0.0"


# -- CORS with a wildcard-capable allowlist + Chrome Private Network Access -------------------------

def _origin_patterns() -> list[re.Pattern]:
    raw = os.environ.get("ANIME_NEWS_ALLOWED_ORIGINS", DEFAULT_ORIGINS)
    pats = []
    for o in (x.strip().rstrip("/") for x in raw.split(",")):
        if o:
            pats.append(re.compile("^" + re.escape(o).replace(r"\*", r"[a-z0-9-]+(?:\.[a-z0-9-]+)*") + "$", re.I))
    return pats


def origin_allowed(origin: str | None) -> bool:
    return bool(origin) and any(p.match(origin) for p in _origin_patterns())


CORS_METHODS = "GET,POST,PUT,DELETE,OPTIONS"
CORS_HEADERS = "content-type, x-access-token"


def _install_cors(app: FastAPI) -> None:
    @app.middleware("http")
    async def cors(request: Request, call_next):
        origin = request.headers.get("origin")
        allowed = origin_allowed(origin)
        if request.method == "OPTIONS" and request.headers.get("access-control-request-method"):
            headers = {"Access-Control-Allow-Private-Network": "true", "Vary": "Origin"}
            if allowed:
                headers.update({"Access-Control-Allow-Origin": origin, "Access-Control-Allow-Methods": CORS_METHODS,
                                "Access-Control-Allow-Headers": CORS_HEADERS, "Access-Control-Max-Age": "600"})
            return Response(status_code=204 if allowed else 403, headers=headers)
        response = await call_next(request)
        if allowed:
            response.headers["Access-Control-Allow-Origin"] = origin
            response.headers["Vary"] = "Origin"
        return response


# -- public deploy: access token + rate limit on mutating routes ---------------------------------
# SHOT_DIRECTOR_ACCESS_TOKEN unset (the local engine): every route stays open, nothing is limited.
# Set: mutating routes need header X-Access-Token (or ?token=), and spending routes are capped per token per
# rolling hour so a leaked token cannot drain the shared Router pool. GET routes stay public.

class AccessDenied(Exception):
    def __init__(self, status: int, message: str):
        self.status, self.message = status, message


def _limit(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


_RATE_LOCK = threading.Lock()
_RATE_HITS: dict[tuple[str, str], deque] = {}


def _rate_check(bucket: str, who: str, per_hour: int) -> None:
    if per_hour <= 0:
        return
    now = time.monotonic()
    with _RATE_LOCK:
        hits = _RATE_HITS.setdefault((bucket, who), deque())
        while hits and now - hits[0] > 3600:
            hits.popleft()
        if len(hits) >= per_hour:
            wait = int(3600 - (now - hits[0])) + 1
            raise AccessDenied(429, f"rate limit: {per_hour} {bucket} requests per hour, retry in {wait} s")
        hits.append(now)


def _token_gate(bucket: str | None):
    def check(request: Request) -> None:
        expected = os.environ.get("SHOT_DIRECTOR_ACCESS_TOKEN", "")
        if not expected:
            return
        given = request.headers.get("x-access-token") or request.query_params.get("token") or ""
        if not hmac.compare_digest(given.encode(), expected.encode()):
            raise AccessDenied(401, "access token required")
        if bucket:
            who = hashlib.sha256(given.encode()).hexdigest()[:16]
            if bucket == "episode":
                _rate_check("episode", who, _limit("SHOT_DIRECTOR_EPISODES_PER_HOUR", 6))
            else:
                _rate_check("generate", who, _limit("SHOT_DIRECTOR_CALLS_PER_HOUR", 60))
    return Depends(check)


TOKEN = _token_gate(None)              # mutating but free
SPEND = _token_gate("generate")        # spends Router credits: one writer call
EPISODE = _token_gate("episode")       # spends the most: whole episodes


# -- request bodies -------------------------------------------------------------------------------

class ConfigBody(BaseModel):
    comfy_api_key: str | None = None
    comfy_credential: str | None = None   # alias used by API-CONTRACT.md's wording


class StoryboardBody(BaseModel):
    headline: str = ""
    text: str | None = None
    url: str | None = None
    minutes: float | None = 1.0
    style: str | None = None


class RenderBody(BaseModel):
    storyboard: dict
    quality: str = "480p"
    vertical: bool = False


class EpisodeRequest(BaseModel):
    """LEGACY (/api/episode, still used by a private backend): the template/v1 pipeline, synchronous."""
    headline: str | None = None
    source: str = ""
    engine: str | None = None
    body: str = "anime"
    render: bool = True
    minutes: float = episode.DEFAULT_MINUTES
    clip_seconds: int = episode.DEFAULT_CLIP_SECONDS
    parallel: int = episode.DEFAULT_PARALLEL
    cut: bool = True
    writer: bool = True
    style_sheet: bool = True
    news_strategy: str | None = None
    continuity: bool = False


# -- render jobs ----------------------------------------------------------------------------------

class Jobs:
    """Render jobs on background threads; jobs live in memory. Up to `max_parallel` run at once and the rest wait
    in a queue (the local engine runs one and answers 409 instead of queueing; see post_render)."""

    def __init__(self, max_parallel: int = 1) -> None:
        self.lock = threading.Lock()
        self.jobs: dict[str, dict] = {}
        self.cancels: dict[str, threading.Event] = {}
        self.active: set[str] = set()
        self.pending: deque[tuple[str, object]] = deque()
        self.max_parallel = max(1, max_parallel)

    def public(self, job_id: str) -> dict | None:
        with self.lock:
            j = self.jobs.get(job_id)
            if not j:
                return None
            out = {k: v for k, v in j.items() if not k.startswith("_")}
            if out.get("status") == "queued":
                pos = next((i for i, (jid, _) in enumerate(self.pending) if jid == job_id), None)
                if pos is not None:
                    out["position"] = pos + 1
                    out["stage"] = f"queued, {pos + 1} ahead of you" if pos else "queued, next up"
        return out

    def update(self, job_id: str, **fields) -> None:
        with self.lock:
            self.jobs[job_id].update(fields)

    def submit(self, job_id: str, fn) -> None:
        """Start `fn` now when a slot is free, else queue it (call with the job already registered)."""
        with self.lock:
            if len(self.active) < self.max_parallel:
                self.active.add(job_id)
                start = True
            else:
                self.pending.append((job_id, fn))
                start = False
        if start:
            self._start(job_id, fn)

    def _start(self, job_id: str, fn) -> None:
        def run() -> None:
            try:
                fn()
            finally:
                self._finished(job_id)
        threading.Thread(target=run, daemon=True, name=f"render-{job_id}").start()

    def _finished(self, job_id: str) -> None:
        with self.lock:
            self.active.discard(job_id)
            nxt = self.pending.popleft() if self.pending and len(self.active) < self.max_parallel else None
            if nxt:
                self.active.add(nxt[0])
        if nxt:
            self._start(*nxt)

    def drop_queued(self, job_id: str) -> bool:
        """Remove a job that has not started yet. True when it was still queued."""
        with self.lock:
            for i, (jid, _) in enumerate(self.pending):
                if jid == job_id:
                    del self.pending[i]
                    return True
        return False


def _abs(base: str, name: str | None) -> str | None:
    return f"{base}media/{name}" if name else None


def _episode_view(entry: dict, base: str) -> dict:
    cut = entry.get("cut") or {}
    file = entry.get("file") if entry.get("status") == "done" else None
    return {**entry, "id": entry.get("id"), "headline": entry.get("headline") or entry.get("headline_raw"),
            "created": entry.get("created"), "status": entry.get("status"),
            "mp4_url": _abs(base, file), "vertical_url": _abs(base, entry.get("vertical_file")) if file else None,
            "duration": entry.get("duration") or cut.get("duration"),
            "shots": entry.get("cut_shots") or cut.get("shots") or entry.get("clips"),
            "cuts_per_minute": entry.get("cuts_per_minute") or cut.get("cuts_per_minute"),
            "quality": entry.get("quality"), "cost_estimate": entry.get("cost_estimate")}


# -- the app --------------------------------------------------------------------------------------

def start_auto_channel(media: Path) -> None:
    """SHOT_DIRECTOR_AUTO=1 (or --auto): render a new episode every SHOT_DIRECTOR_AUTO_EVERY (default 60m)."""
    if os.environ.get("SHOT_DIRECTOR_AUTO") != "1":
        return
    every = episode.parse_every(os.environ.get("SHOT_DIRECTOR_AUTO_EVERY", "60m"))
    minutes = float(os.environ.get("SHOT_DIRECTOR_AUTO_MINUTES", episode.DEFAULT_MINUTES))
    threading.Thread(target=episode.run_loop, args=(make_router(media), media, every), daemon=True,
                     kwargs={"minutes": minutes, "log": lambda line: print(f"[auto] {line}", flush=True)},
                     name="shot-director-auto").start()


def create_app(media: Path | None = None) -> FastAPI:
    media = Path(media or config.media_dir()).expanduser().resolve()
    media.mkdir(parents=True, exist_ok=True)
    is_hosted = hosted.enabled()
    usage = hosted.Usage(media)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        start_auto_channel(media)
        yield

    app = FastAPI(title="Anime News engine", version=_version(), lifespan=lifespan)
    app.state.media = media
    jobs = Jobs(hosted.limits()["max_parallel_jobs"] if is_hosted else 1)
    app.state.jobs = jobs

    @app.exception_handler(AccessDenied)
    def _access_denied(_: Request, exc: AccessDenied) -> JSONResponse:
        return JSONResponse({"error": exc.message, "detail": exc.message}, status_code=exc.status)

    @app.exception_handler(hosted.LimitReached)
    def _limit_reached(_: Request, exc: hosted.LimitReached) -> JSONResponse:
        return JSONResponse({"error": exc.message, "detail": exc.message}, status_code=429,
                            headers={"Retry-After": str(exc.retry_after)})

    _install_cors(app)
    if is_hosted:
        @app.middleware("http")
        async def access_gate(request: Request, call_next):
            if not hosted.access_ok(request.headers):
                return JSONResponse({"error": "access token required", "detail": "access token required"},
                                    status_code=401)
            return await call_next(request)

    def visitor(request: Request) -> str:
        return hosted.client_ip(request.headers, request.client.host if request.client else None)

    # Hosted: media URLs are site-relative (/media/x) so the site's proxy serves them from its own origin.
    def media_base(request: Request) -> str:
        return "/" if is_hosted else str(request.base_url)

    app.mount("/media", StaticFiles(directory=media), name="media")
    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    @app.api_route("/", methods=["GET", "HEAD"])  # HEAD: link previews and uptime checks
    def index() -> FileResponse:
        return FileResponse(STATIC / "index.html")

    @app.api_route("/episodes", methods=["GET", "HEAD"])
    def episodes_page() -> FileResponse:
        return FileResponse(STATIC / "episodes.html")

    # -- contract ---------------------------------------------------------------------------------
    @app.get("/api/health")
    def health(request: Request) -> dict:
        has = config.has_key()
        out = {"ok": True, "version": _version(), "mode": config.mode(), "has_credential": has, "has_api_key": has}
        if is_hosted:
            lim = hosted.limits()
            left = usage.summary(visitor(request))
            with jobs.lock:
                running, queued = len(jobs.active), len(jobs.pending)
            out.update({"hosted": True, "limits": {k: v for k, v in lim.items() if k != "writer_usd"},
                        "remaining_for_you": left["renders_left_for_you"],
                        "storyboards_left_for_you": left["storyboards_left_for_you"],
                        "budget_remaining_usd": left["budget_remaining_usd"],
                        "renders_running": running, "renders_queued": queued})
        return out

    @app.get("/api/config")
    def get_config() -> dict:
        has = config.has_key()
        out = {"has_credential": has, "has_api_key": has, "mode": config.mode(), "media_dir": str(media)}
        if is_hosted:
            out.pop("media_dir")
            out["hosted"] = True
        return out

    @app.put("/api/config", dependencies=[TOKEN])
    def put_config(body: ConfigBody) -> dict:
        if is_hosted:
            raise HTTPException(403, "the shared demo engine uses its own key; run your own engine to use yours")
        value = body.comfy_api_key if body.comfy_api_key is not None else body.comfy_credential
        if value is None:
            raise HTTPException(400, "send {\"comfy_api_key\": \"...\"} (an empty string clears it)")
        if len(value) > 4096 or any(c in value for c in "\r\n"):
            raise HTTPException(400, "that does not look like an API key")
        config.set_key(value)
        has = config.has_key()
        return {"has_credential": has, "has_api_key": has, "mode": config.mode()}

    @app.post("/api/storyboard", dependencies=[SPEND])
    def post_storyboard(body: StoryboardBody, request: Request) -> dict:
        from . import storyboard
        headline = " ".join((body.headline or "").split())
        if not headline:
            raise HTTPException(400, "headline is empty")
        minutes = 1.0 if body.minutes is None else body.minutes
        if not (0 < minutes <= MAX_MINUTES):
            raise HTTPException(400, f"minutes must be above 0 and at most {MAX_MINUTES:g}")
        charged = None
        if is_hosted:
            lim = hosted.limits()
            minutes = min(minutes, lim["max_minutes"])
            charged = (visitor(request), "storyboards", lim["writer_usd"])
            charged = (*charged, usage.charge(*charged))   # + the day it was charged, for a refund
        from . import writer
        paid0 = writer.paid_calls()   # per thread: this request's writer calls only

        def refund_if_unpaid() -> None:
            # a writer reply that came back (even one later rejected) was billed: the charge stands
            if charged and writer.paid_calls() == paid0:
                usage.refund(*charged)
        try:
            sb = storyboard.build(headline, text=body.text or "", url=body.url or "", minutes=minutes,
                                  style=body.style or "")
        except storyboard.StoryboardError as e:
            refund_if_unpaid()
            raise HTTPException(502, str(e)) from e
        except Exception as e:  # noqa: BLE001 - network, 402, timeouts: the writer failed
            refund_if_unpaid()
            raise HTTPException(502, f"writer failed: {type(e).__name__}: {str(e)[:300]}") from e
        return {"storyboard": sb, "stats": storyboard.stats(sb)}

    @app.post("/api/render", dependencies=[EPISODE])
    def post_render(body: RenderBody, request: Request) -> JSONResponse:
        from . import render_storyboard as rs, storyboard
        quality = hosted.limits()["quality"] if is_hosted else body.quality
        if quality not in rs.QUALITIES:
            raise HTTPException(400, f"quality must be one of {', '.join(rs.QUALITIES)}")
        try:
            sb = storyboard.normalize(body.storyboard)
        except storyboard.StoryboardError as e:
            raise HTTPException(400, str(e)) from e
        router = make_router(media, quality)
        est = rs.estimate(sb, quality, media)
        cap = float(os.environ.get("ANIME_NEWS_MAX_COST", "0") or 0)
        if router.mode == "live" and cap and est["usd"] > cap:
            raise HTTPException(400, f"estimated ${est['usd']:.2f} is over ANIME_NEWS_MAX_COST ${cap:.2f}")
        charged = None
        if is_hosted:
            max_s = hosted.limits()["max_minutes"] * 60 * HOSTED_SHOT_SECONDS_PER_MINUTE
            shot_s = sum(s["seconds"] for sc in sb["scenes"] for s in sc["shots"])
            if shot_s > max_s:
                raise HTTPException(400, f"this storyboard has {shot_s} s of shots; the shared demo engine renders up "
                                         f"to {max_s:g} s. Remove some shots, or run your own engine")
            charged = (visitor(request), "renders", est["usd"])
            charged = (*charged, usage.charge(*charged))   # + the day it was charged, for a refund
        base = media_base(request)
        total = sum(len(sc["shots"]) for sc in sb["scenes"])
        job_id = uuid.uuid4().hex[:12]
        cancel = threading.Event()
        with jobs.lock:
            if not is_hosted and jobs.active:
                raise HTTPException(409, f"a render is already running (job {next(iter(jobs.active))})")
            jobs.cancels[job_id] = cancel
            jobs.jobs[job_id] = {"job_id": job_id, "status": "queued", "stage": "queued", "done": 0, "total": total,
                                 "started": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                 "mode": router.mode, "quality": quality, "estimate_usd": est["usd"],
                                 "_charged": charged}

        def progress(stage: str, done: int, n: int) -> None:
            jobs.update(job_id, status="cutting" if stage == "cutting" else "rendering", stage=stage, done=done, total=n)

        def run() -> None:
            if cancel.is_set():
                jobs.update(job_id, status="failed", stage="failed", error="cancelled")
                return
            jobs.update(job_id, status="rendering", stage="starting")
            try:
                entry = rs.render(sb, router, media, quality=quality, vertical=body.vertical,
                                  progress=progress, cancel=cancel)
                jobs.update(job_id, status="done", stage="done", done=total, episode={
                    "id": entry["id"], "mp4_url": _abs(base, entry.get("file")),
                    "vertical_url": _abs(base, entry.get("vertical_file")), "duration": entry.get("duration"),
                    "shots": entry.get("cut_shots"), "cuts_per_minute": entry.get("cuts_per_minute"),
                    "reuse": entry.get("reuse"), "errors": entry.get("errors"),
                    "credits_used": entry.get("credits_used"), "cost_estimate": entry.get("cost_estimate")})
            except BaseException as e:  # noqa: BLE001 - the job records it
                jobs.update(job_id, status="failed", stage="failed",
                            error="cancelled" if isinstance(e, rs.Cancelled) else f"{type(e).__name__}: {str(e)[:300]}")

        jobs.submit(job_id, run)
        return JSONResponse({"job_id": job_id, "estimate_usd": est["usd"], "mode": router.mode, "quality": quality},
                            status_code=202)

    @app.get("/api/jobs/{job_id}")
    def get_job(job_id: str) -> dict:
        j = jobs.public(job_id)
        if not j:
            raise HTTPException(404, f"no such job: {job_id}")
        return j

    @app.delete("/api/jobs/{job_id}", dependencies=[TOKEN])
    def delete_job(job_id: str) -> dict:
        with jobs.lock:
            ev = jobs.cancels.get(job_id)
        if not ev:
            raise HTTPException(404, f"no such job: {job_id}")
        ev.set()  # best effort: shots already sent to the Router finish; nothing new starts
        if jobs.drop_queued(job_id):
            with jobs.lock:
                charged = jobs.jobs[job_id].get("_charged")
            jobs.update(job_id, status="failed", stage="failed", error="cancelled")
            if charged:
                usage.refund(*charged)
        return {"cancelled": True}

    @app.get("/api/weekly")
    def list_weekly() -> dict:
        """THE WEEKLY BRIEFING manifest (<media>/weekly/index.json), newest week first. Shape: docs/WEEKLY.md.
        File URLs are engine-relative (/media/weekly/<week>/<file>); the /media mount serves them."""
        from . import weekly
        return weekly.read_manifest(media)

    @app.get("/api/episodes")
    def list_episodes(request: Request) -> dict:
        base = media_base(request)
        items = [_episode_view(e, base) for e in episode.read_ledger(media)]
        items.sort(key=lambda e: str(e.get("finished") or e.get("created") or ""), reverse=True)
        return {"episodes": items, "auto": os.environ.get("SHOT_DIRECTOR_AUTO") == "1"}

    # -- kept from the hosted app ----------------------------------------------------------------
    @app.get("/api/presets")
    def get_presets() -> dict:
        return {**presets.as_json(), "mode": config.mode(), "video_model": VIDEO_MODEL,
                "image_model": IMAGE_MODEL, "engines": ENGINES,
                "access_required": bool(os.environ.get("SHOT_DIRECTOR_ACCESS_TOKEN"))}

    @app.get("/api/news")
    def get_news(strategy: str | None = None):
        """Public. strategy=viral serves ONLY the 30-minute viral cache and never starts the engine."""
        if (strategy or "").lower() == "viral":
            from . import viral
            hit = viral.cached(limit=10)
            if hit is None:
                return JSONResponse({"items": [], "strategy": "viral", "cached": False, "fetched_at": None,
                                     "refreshing": viral.refreshing()}, status_code=202)
            fetched = datetime.fromtimestamp(hit["fetched"], timezone.utc).isoformat(timespec="seconds")
            return {"items": hit["items"], "strategy": "viral", "cached": True, "fetched_at": fetched,
                    "refreshing": viral.refreshing()}
        return {"items": news.trending(10), "strategy": "headline"}

    @app.post("/api/news/refresh", dependencies=[SPEND])
    def refresh_news():
        if is_hosted:
            raise HTTPException(403, "not available on the shared demo engine")
        from . import viral
        if not viral.available():
            raise HTTPException(503, "last30days engine not installed")
        if not viral.start_refresh():
            raise HTTPException(409, "a viral refresh is already running")
        return JSONResponse({"refreshing": True}, status_code=202)

    # LEGACY: POST /api/episode is the old synchronous template/v1 pipeline. A private backend still calls it;
    # new clients use POST /api/storyboard + POST /api/render. Do not remove without checking that backend.
    @app.post("/api/episode", dependencies=[EPISODE])
    def make_episode(req: EpisodeRequest) -> dict:
        if is_hosted:   # the legacy pipeline has no per-visitor caps: the shared engine only takes /api/render
            raise HTTPException(403, "not available on the shared demo engine")
        try:
            return episode.produce(make_router(media), media, headline=req.headline, source=req.source,
                                   engine=req.engine, body=req.body, minutes=req.minutes,
                                   clip_seconds=req.clip_seconds, parallel=req.parallel,
                                   render=req.render, cut=req.cut, use_writer=req.writer,
                                   style_sheet=req.style_sheet, news_strategy=req.news_strategy,
                                   continuity=req.continuity)
        except LookupError as e:
            raise HTTPException(503, str(e)) from e
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        except (RouterError, RuntimeError) as e:
            raise HTTPException(502, str(e)) from e

    return app


def serve(host: str = "127.0.0.1", port: int = 8765, media: Path | None = None) -> None:
    import uvicorn
    app = create_app(media)
    print(f"Anime News engine ({config.mode()} mode) on http://{host}:{port}  media: {app.state.media}", flush=True)
    uvicorn.run(app, host=host, port=port)


def main() -> None:
    """LEGACY `shot-director` entry point (hosted deploys): same app, media in SHOT_DIRECTOR_MEDIA or ./media."""
    ap = argparse.ArgumentParser(description="Anime News engine (legacy shot-director entry point)")
    ap.add_argument("--host", default=os.environ.get("HOST", "127.0.0.1"))
    ap.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8000")))
    ap.add_argument("--auto", action="store_true",
                    help="self-running channel: a new news episode every SHOT_DIRECTOR_AUTO_EVERY (60m)")
    args = ap.parse_args()
    if args.auto:
        os.environ["SHOT_DIRECTOR_AUTO"] = "1"
    serve(args.host, args.port, Path(os.environ.get("SHOT_DIRECTOR_MEDIA", ROOT / "media")))


if __name__ == "__main__":
    main()


_legacy_app: FastAPI | None = None


def __getattr__(name: str):
    """`shot_director.app:app` (old uvicorn targets) still works: built on first access, legacy media dir."""
    global _legacy_app
    if name == "app":
        if _legacy_app is None:
            _legacy_app = create_app(Path(os.environ.get("SHOT_DIRECTOR_MEDIA", ROOT / "media")))
        return _legacy_app
    raise AttributeError(name)
