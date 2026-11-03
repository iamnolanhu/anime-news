"""Comfy Router client (live) plus an offline mock with the same interface.

Router queue flow (verified 2026-11-02 against each model's openapi.json and a live FLUX run):
  POST {BASE}/v2/models/{model}/requests   model's NATIVE body, headers X-API-Key + Idempotency-Key
       -> 201 {request_id, status, queue_position, status_url, response_url, cancel_url}
  GET  .../requests/{id}/status            -> {status: IN_QUEUE|IN_PROGRESS|COMPLETED, ...} + Retry-After
       a failed run is COMPLETED carrying `error_type` (there is no FAILED status)
  GET  .../requests/{id}                   -> 200 native output when done, 202 status body while running
No credits -> HTTP 402 insufficient_credits. Output URLs expire (BFL in ~10 min), so download at once.
"""

import base64
import json
import mimetypes
import os
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

from . import stitch

BASE_URL = os.environ.get("COMFY_ROUTER_BASE", "https://api.comfy.org")
VIDEO_MODEL = os.environ.get("SHOT_DIRECTOR_VIDEO_MODEL", "bfl/flux-3-video")
# Per-shot engine picker (shown in the UI). Continuation (v2v) only exists on FLUX 3 Video.
ENGINES = [
    {"id": "bfl/flux-3-video", "label": "FLUX 3 Video", "time": "~2 min",
     "best_for": "Default. Takes the start frame; continues from the previous shot."},
    {"id": "ltx/ltx-2-5-fast", "label": "LTX 2.5 Fast", "time": "~30 s",
     "best_for": "Fastest (≈30 s). Text-only, ignores the frame. Good for previews/establishing shots."},
    {"id": "minimax/minimax-h3", "label": "MiniMax H3", "time": "~5 min",
     "best_for": "Slow (≈5 min). Takes the frame, returns sound. Good for a hero shot with audio."},
]
CONTINUATION_ENGINE = "bfl/flux-3-video"
IMAGE_MODEL = os.environ.get("SHOT_DIRECTOR_IMAGE_MODEL", "bfl/flux-3-image")
SECRETS_FILE = Path.home() / ".claude/.secrets/services/comfy/default.env"
POLL_SECONDS = 3
MAX_POLL_SECONDS = 10
TIMEOUT_SECONDS = 660  # Router's sync cutoff is 10 min

# Tunable request defaults per model family.
FRAME_ASPECT_RATIO = "16:9"
FRAME_RESOLUTION = "1k"            # flux-3-image: 768sq | 1k | 1.5k | 2k | 4k
LTX_DURATION = 4                   # ltx-2-5: 2,3,4,5,6,8,10,12,14,16,18,20
LTX_RESOLUTION = "1280x720"
LTX_FPS = 24                       # 24 | 25 | 48 | 50
FLUX_VIDEO_DURATION = 5            # flux-3-video: 5-20 s or "auto"
FLUX_VIDEO_RESOLUTION = "hd"       # hd | fhd | qhd | uhd
FLUX_VIDEO_ASPECT_RATIO = "16:9"
MINIMAX_DURATION = 4               # minimax-h3: 4-15 s
MINIMAX_RESOLUTION = "768P"        # 768P | 2K
MINIMAX_RATIO = "16:9"             # required (not adaptive) when there is no first frame


def load_api_key() -> str | None:
    """COMFY_API_KEY from the environment, else from the local secrets file. Never logged."""
    if os.environ.get("SHOT_DIRECTOR_MOCK") == "1":
        return None
    key = os.environ.get("COMFY_API_KEY", "").strip()
    if key:
        return key
    if SECRETS_FILE.exists():
        for line in SECRETS_FILE.read_text().splitlines():
            line = line.strip().removeprefix("export ").strip()
            if line.startswith("COMFY_API_KEY="):
                return line.split("=", 1)[1].strip().strip("'\"") or None
    return None


class RouterError(RuntimeError):
    pass


def _b64(path: Path) -> str:
    return base64.b64encode(path.read_bytes()).decode()


def _data_uri(path: Path, default_mime: str) -> str:
    mime = mimetypes.guess_type(path.name)[0] or default_mime
    return f"data:{mime};base64,{_b64(path)}"


class LiveRouter:
    mode = "live"

    def __init__(self, api_key: str, media_dir: Path):
        self._key = api_key
        self.media = media_dir
        self._warned_ltx_frame = False

    # -- HTTP -------------------------------------------------------------
    def _req_full(self, method: str, url: str, body: dict | None = None) -> tuple[int, dict, dict]:
        """Returns (http status, response headers, json body)."""
        headers = {"X-API-Key": self._key, "Accept": "application/json"}
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
            headers["Idempotency-Key"] = str(uuid.uuid4())
        req = urllib.request.Request(url, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.status, dict(r.headers), json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")[:400]
            if e.code == 402:
                raise RouterError("Router says insufficient_credits (402): top up or get hackathon credits") from e
            raise RouterError(f"Router HTTP {e.code}: {detail}") from e

    def _req(self, method: str, url: str, body: dict | None = None) -> dict:
        return self._req_full(method, url, body)[2]

    @staticmethod
    def _wait(headers: dict) -> float:
        try:
            ra = float(headers.get("Retry-After") or headers.get("retry-after") or POLL_SECONDS)
        except ValueError:
            ra = POLL_SECONDS
        return min(MAX_POLL_SECONDS, max(POLL_SECONDS, ra))

    @staticmethod
    def _error_text(doc: dict) -> str:
        msg = doc.get("message") or doc.get("detail") or doc.get("error")
        if isinstance(msg, dict):
            msg = msg.get("message") or json.dumps(msg)
        return f"{doc.get('error_type')}: {msg or json.dumps(doc)[:400]}"

    def _run_model(self, model: str, body: dict) -> dict:
        base = f"{BASE_URL}/v2/models/{model}/requests"
        rid = self._req("POST", base, body).get("request_id")
        if not rid:
            raise RouterError("Router returned no request_id")
        deadline = time.time() + TIMEOUT_SECONDS
        while time.time() < deadline:
            _, hdrs, status = self._req_full("GET", f"{base}/{rid}/status")
            if status.get("error_type"):
                raise RouterError(f"Router job failed: {self._error_text(status)}")
            if str(status.get("status", "")).upper() == "COMPLETED":
                code, hdrs, result = self._req_full("GET", f"{base}/{rid}")
                if code == 200:
                    if isinstance(result, dict) and result.get("error_type"):
                        raise RouterError(f"Router job failed: {self._error_text(result)}")
                    return result
                # 202: still finishing, keep polling
            time.sleep(self._wait(hdrs))
        raise RouterError("Router job timed out")

    @staticmethod
    def _find_url(result: object, exts: tuple[str, ...]) -> str | None:
        """Fallback only: search a result for the first media URL when the known path is missing."""
        stack = [result]
        fallback = None
        while stack:
            cur = stack.pop()
            if isinstance(cur, dict):
                stack.extend(cur.values())
            elif isinstance(cur, list):
                stack.extend(cur)
            elif isinstance(cur, str) and cur.startswith("http"):
                if cur.split("?")[0].lower().endswith(exts):
                    return cur
                fallback = fallback or cur
        return fallback

    @staticmethod
    def _dig(doc: object, *keys: str) -> object:
        for k in keys:
            if not isinstance(doc, dict):
                return None
            doc = doc.get(k)
        return doc

    def _download(self, url: str, out: Path) -> Path:
        with urllib.request.urlopen(url, timeout=120) as r:
            out.write_bytes(r.read())
        return out

    # -- public interface ---------------------------------------------------
    def generate_frame(self, prompt: str, body: str, out: Path) -> Path:
        req = {"prompt": prompt, "aspect_ratio": FRAME_ASPECT_RATIO,
               "resolution": FRAME_RESOLUTION, "grounding": False}
        result = self._run_model(IMAGE_MODEL, req)
        # flux-3-image output: {"result": {"sample": "<signed url>"}}
        url = self._dig(result, "result", "sample") or self._find_url(
            result, (".png", ".jpg", ".jpeg", ".webp"))
        if not url:
            raise RouterError("no image URL in Router result")
        return self._download(url, out)

    def generate_clip(self, prompt: str, body: str, move: str, out: Path,
                      frame: Path | None = None, model: str | None = None,
                      previous_clip: Path | None = None) -> Path:
        model = model or VIDEO_MODEL
        if model.startswith("ltx/"):
            if frame is not None and not self._warned_ltx_frame:
                print(f"[shot-director] {model} is text-to-video only on Router: start frame ignored",
                      file=sys.stderr)
                self._warned_ltx_frame = True
            req = {"prompt": prompt, "duration": LTX_DURATION, "resolution": LTX_RESOLUTION,
                   "fps": LTX_FPS, "generate_audio": False}
            result = self._run_model(model, req)
            # LTX output: {"status": pending|processing|completed|failed, "result": {"video_url"}, "error"}
            if str(result.get("status", "")).lower() == "failed":
                raise RouterError(f"LTX job failed: {self._dig(result, 'error', 'message') or result.get('error')}")
            url = self._dig(result, "result", "video_url")
        elif model == "bfl/flux-3-video":
            req = {"prompt": prompt, "duration": FLUX_VIDEO_DURATION,
                   "resolution": FLUX_VIDEO_RESOLUTION, "aspect_ratio": FLUX_VIDEO_ASPECT_RATIO,
                   "generate_audio": False}
            # Raw base64 (no data: prefix) is accepted for keyframes / start_video.
            if previous_clip is not None:
                req.update(mode="v2v", start_video=_b64(previous_clip))
            elif frame is not None:
                req.update(mode="i2v", keyframes=[_b64(frame)])
            else:
                req["mode"] = "t2v"
            result = self._run_model(model, req)
            # flux-3-video output: {"result": {"sample": "<signed url>"}}
            url = self._dig(result, "result", "sample")
        elif model == "minimax/minimax-h3":
            content: list[dict] = [{"type": "text", "text": prompt}]
            req = {"resolution": MINIMAX_RESOLUTION, "duration": MINIMAX_DURATION}
            if frame is not None:
                # image_url.url takes a public URL, mm_file://id, or a data URI.
                content.append({"type": "image_url", "role": "first_frame",
                                "image_url": {"url": _data_uri(frame, "image/png")}})
            else:
                req["ratio"] = MINIMAX_RATIO
            req["content"] = content
            result = self._run_model(model, req)
            # minimax-h3 output: {"task": {"status", "content": {"url"}, "error": {"code","message"}}}
            task = result.get("task") or {}
            if str(task.get("status", "")).lower() in {"failed", "cancelled", "expired"}:
                raise RouterError(f"MiniMax job {task.get('status')}: {self._dig(task, 'error', 'message') or task.get('error')}")
            url = self._dig(task, "content", "url")
        else:
            raise RouterError(f"unsupported video model: {model} "
                              "(use ltx/ltx-2-5-fast, ltx/ltx-2-5-pro, bfl/flux-3-video or minimax/minimax-h3)")
        url = url or self._find_url(result, (".mp4", ".mov", ".webm"))
        if not url:
            raise RouterError("no video URL in Router result")
        return self._download(url, out)


class MockRouter:
    """Offline stand-in: renders placeholder frames/clips locally with ffmpeg."""

    mode = "mock"

    def __init__(self, media_dir: Path):
        self.media = media_dir

    def generate_frame(self, prompt: str, body: str, out: Path) -> Path:
        from .presets import CAMERA_BODIES
        color = CAMERA_BODIES.get(body, CAMERA_BODIES["modern"])["color"]
        return stitch.mock_frame(f"MOCK FLUX 3 FRAME - {prompt[:50]}", color, out)

    def generate_clip(self, prompt: str, body: str, move: str, out: Path,
                      frame: Path | None = None, model: str | None = None,
                      previous_clip: Path | None = None) -> Path:
        from .presets import CAMERA_BODIES, CAMERA_MOVES
        color = CAMERA_BODIES.get(body, CAMERA_BODIES["modern"])["color"]
        title = f"MOCK {CAMERA_MOVES[move]['label'].upper()}"
        sub = f"{CAMERA_BODIES[body]['label']} - {model or VIDEO_MODEL}"
        return stitch.mock_clip(title, sub, color, out, frame=frame)


def make_router(media_dir: Path):
    key = load_api_key()
    return LiveRouter(key, media_dir) if key else MockRouter(media_dir)
