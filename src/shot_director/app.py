"""FastAPI app: one process serves the API, the static UI and the generated media."""

import argparse
import hashlib
import hmac
import os
import shutil
import threading
import time
import uuid
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

from fastapi import Depends, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import episode, news, presets, stitch
from .router_client import (CONTINUATION_ENGINE, ENGINES, IMAGE_MODEL, VIDEO_MODEL, RouterError,
                            clamp_duration, make_router)

ROOT = Path(__file__).resolve().parents[2]
MEDIA = Path(os.environ.get("SHOT_DIRECTOR_MEDIA", ROOT / "media"))
MEDIA.mkdir(parents=True, exist_ok=True)
STATIC = Path(__file__).parent / "static"


def _max_shots() -> int:
    try:
        return max(1, int(os.environ.get("SHOT_DIRECTOR_MAX_SHOTS", "12")))
    except ValueError:
        return 12


MAX_SHOTS = _max_shots()  # the Studio storyboard holds up to 12 shots across its scenes

app = FastAPI(title="Shot Director")
app.mount("/media", StaticFiles(directory=MEDIA), name="media")
app.mount("/static", StaticFiles(directory=STATIC), name="static")
router = make_router(MEDIA)
queue: list[dict] = []  # in-memory shot queue, max MAX_SHOTS


# -- public deploy: access token + rate limit on mutating routes ---------------------------------
# SHOT_DIRECTOR_ACCESS_TOKEN unset (local dev): every route stays open, nothing is limited.
# Set: POST/DELETE routes need header X-Access-Token (or ?token=), and spending routes are capped per
# token per rolling hour so a leaked token cannot drain the shared Router pool. GET routes stay public.

class AccessDenied(Exception):
    def __init__(self, status: int, message: str):
        self.status, self.message = status, message


@app.exception_handler(AccessDenied)
def _access_denied(_: Request, exc: AccessDenied) -> JSONResponse:
    return JSONResponse({"error": exc.message, "detail": exc.message}, status_code=exc.status)


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


TOKEN = _token_gate(None)              # mutating but free (queue edits, uploads, ffmpeg stitch)
SPEND = _token_gate("generate")        # spends Router credits: frames and shots
EPISODE = _token_gate("episode")       # spends the most: whole episodes


class FrameRequest(BaseModel):
    prompt: str
    body: str = "modern"


class ShotRequest(BaseModel):
    body: str = "35mm"
    move: str = "dolly_in"
    scene: str = ""
    frame: str | None = None  # file name under /media returned by /api/frame*
    engine: str | None = None  # one of ENGINES ids; default VIDEO_MODEL
    model: str | None = None   # legacy alias for engine


def _media_path(name: str) -> Path:
    p = (MEDIA / Path(name).name).resolve()
    if not p.exists() or p.parent != MEDIA.resolve():
        raise HTTPException(404, f"no such media file: {name}")
    return p


@app.api_route("/", methods=["GET", "HEAD"])  # HEAD: link previews and uptime checks
def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


@app.api_route("/episodes", methods=["GET", "HEAD"])
def episodes_page() -> FileResponse:
    return FileResponse(STATIC / "episodes.html")


@app.get("/api/presets")
def get_presets() -> dict:
    return {**presets.as_json(), "mode": router.mode, "video_model": VIDEO_MODEL,
            "image_model": IMAGE_MODEL, "max_shots": MAX_SHOTS, "engines": ENGINES,
            "access_required": bool(os.environ.get("SHOT_DIRECTOR_ACCESS_TOKEN"))}


@app.post("/api/frame", dependencies=[TOKEN])
def upload_frame(file: UploadFile = File(...)) -> dict:
    ext = Path(file.filename or "frame.png").suffix.lower() or ".png"
    if ext not in {".png", ".jpg", ".jpeg", ".webp"}:
        raise HTTPException(400, "start frame must be png, jpg or webp")
    name = f"frame-{uuid.uuid4().hex[:8]}{ext}"
    with (MEDIA / name).open("wb") as f:
        shutil.copyfileobj(file.file, f)
    return {"frame": name, "url": f"/media/{name}"}


@app.post("/api/frame/generate", dependencies=[SPEND])
def generate_frame(req: FrameRequest) -> dict:
    name = f"frame-{uuid.uuid4().hex[:8]}.png"
    try:
        router.generate_frame(req.prompt, req.body, MEDIA / name)
    except (RouterError, RuntimeError) as e:
        raise HTTPException(502, str(e)) from e
    return {"frame": name, "url": f"/media/{name}"}


@app.post("/api/shot", dependencies=[SPEND])
def generate_shot(req: ShotRequest) -> dict:
    if len(queue) >= MAX_SHOTS:
        raise HTTPException(409, f"queue is full ({MAX_SHOTS} shots): stitch or clear it first")
    try:
        prompt = presets.compile_prompt(req.body, req.move, req.scene)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    engine = req.engine or req.model or VIDEO_MODEL
    if engine not in {e["id"] for e in ENGINES} | {VIDEO_MODEL, "ltx/ltx-2-5-pro"}:
        raise HTTPException(400, f"unknown engine: {engine}")
    frame = _media_path(req.frame) if req.frame else None
    name = f"shot-{uuid.uuid4().hex[:8]}.mp4"
    extra = {}
    previous_id = None
    if router.mode == "live" and engine == CONTINUATION_ENGINE and queue:
        # FLUX 3 Video v2v: shot N+1 continues from the end of shot N (per shot, FLUX only).
        # The predecessor is captured here, so the record stays truthful even if the queue is cleared mid-run.
        previous_id = queue[-1]["id"]
        extra["previous_clip"] = _media_path(previous_id)
    try:
        router.generate_clip(prompt, req.body, req.move, MEDIA / name, frame=frame, model=engine,
                             **extra)
    except (RouterError, RuntimeError) as e:
        raise HTTPException(502, str(e)) from e
    shot = {"id": name, "url": f"/media/{name}", "body": req.body, "move": req.move,
            "prompt": prompt, "mode": router.mode, "engine": engine,
            "continued": previous_id is not None, "continued_from": previous_id,
            "frame": req.frame,  # start frame file name, so the storyboard can show shared frames
            "duration": 2.0 if router.mode == "mock" else float(clamp_duration(engine, None))}
    queue.append(shot)
    return shot


@app.get("/api/queue")
def get_queue() -> dict:
    return {"shots": queue, "max_shots": MAX_SHOTS}


@app.delete("/api/queue", dependencies=[TOKEN])
def clear_queue() -> dict:
    queue.clear()
    return {"shots": queue}


@app.delete("/api/queue/{shot_id}", dependencies=[TOKEN])
def remove_shot(shot_id: str) -> dict:
    queue[:] = [s for s in queue if s["id"] != shot_id]
    return {"shots": queue}


class StitchRequest(BaseModel):
    order: list[str] | None = None  # shot ids in storyboard order; default = queue order


@app.post("/api/stitch", dependencies=[TOKEN])
def stitch_queue(req: StitchRequest | None = None) -> dict:
    if not queue:
        raise HTTPException(400, "queue is empty: generate a shot first")
    shots = queue
    if req and req.order:
        by_id = {s["id"]: s for s in queue}
        unknown = [i for i in req.order if i not in by_id]
        if unknown:
            raise HTTPException(400, f"not in the queue: {', '.join(unknown)}")
        shots = [by_id[i] for i in dict.fromkeys(req.order)]
    name = f"sequence-{uuid.uuid4().hex[:8]}.mp4"
    try:
        stitch.stitch([_media_path(s["id"]) for s in shots], MEDIA / name)
    except RuntimeError as e:
        raise HTTPException(500, str(e)) from e
    return {"url": f"/media/{name}", "file": name, "shots": len(shots)}


class EpisodeRequest(BaseModel):
    headline: str | None = None  # default: the top trending story not yet rendered
    source: str = ""
    engine: str | None = None    # default VIDEO_MODEL (FLUX 3 Video, continuation inside each scene)
    body: str = "anime"
    render: bool = True
    minutes: float = episode.DEFAULT_MINUTES
    clip_seconds: int = episode.DEFAULT_CLIP_SECONDS
    parallel: int = episode.DEFAULT_PARALLEL
    cut: bool = True             # anime cut with captions, narration, SFX, music (adds about 60-90 s)
    writer: bool = True          # Router script writer; false = template script
    style_sheet: bool = True     # anchor-desk start frames use the series style sheet
    news_strategy: str | None = None  # auto-pick: "viral" (last30days engagement) or "headline"
    continuity: bool = False     # one start frame per scene + chained clips (default: one frame per shot)


@app.get("/api/news")
def get_news(strategy: str | None = None):
    """Public. strategy=viral serves ONLY the 30-minute viral cache and never starts the engine (a cold
    fetch is minutes of multi-process search); cold -> 202 with no items. POST /api/news/refresh warms it."""
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
    """Token-gated and rate-limited like other spending routes; single flight: 409 while a fetch runs."""
    from . import viral
    if not viral.available():
        raise HTTPException(503, "last30days engine not installed")
    if not viral.start_refresh():
        raise HTTPException(409, "a viral refresh is already running")
    return JSONResponse({"refreshing": True}, status_code=202)


def run_episode(req: EpisodeRequest) -> dict:
    """Plan (and render) one episode. Uses its own shot list, never the 3-slot UI queue."""
    try:
        return episode.produce(router, MEDIA, headline=req.headline, source=req.source,
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


@app.post("/api/episode", dependencies=[EPISODE])
def make_episode(req: EpisodeRequest) -> dict:
    return run_episode(req)


@app.get("/api/episodes")
def list_episodes() -> dict:
    return {"episodes": episode.read_ledger(MEDIA), "auto": os.environ.get("SHOT_DIRECTOR_AUTO") == "1"}


@app.on_event("startup")
def start_auto_channel() -> None:
    """SHOT_DIRECTOR_AUTO=1 (or --auto): render a new episode every SHOT_DIRECTOR_AUTO_EVERY (default 60m)."""
    if os.environ.get("SHOT_DIRECTOR_AUTO") != "1":
        return
    import threading
    every = episode.parse_every(os.environ.get("SHOT_DIRECTOR_AUTO_EVERY", "60m"))
    minutes = float(os.environ.get("SHOT_DIRECTOR_AUTO_MINUTES", episode.DEFAULT_MINUTES))
    threading.Thread(target=episode.run_loop, args=(router, MEDIA, every), daemon=True,
                     kwargs={"minutes": minutes, "log": lambda line: print(f"[auto] {line}", flush=True)},
                     name="shot-director-auto").start()


def main() -> None:
    import uvicorn

    ap = argparse.ArgumentParser(description="Shot Director server")
    ap.add_argument("--host", default=os.environ.get("HOST", "127.0.0.1"))
    ap.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8000")))
    ap.add_argument("--auto", action="store_true",
                    help="self-running channel: a new news episode every SHOT_DIRECTOR_AUTO_EVERY (60m)")
    args = ap.parse_args()
    if args.auto:
        os.environ["SHOT_DIRECTOR_AUTO"] = "1"
    print(f"Shot Director ({router.mode} mode) on http://{args.host}:{args.port}")
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
