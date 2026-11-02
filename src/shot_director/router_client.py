"""Comfy Router client (live) plus an offline mock with the same interface.

Router flow (from docs.comfy.org/development/comfy-router/quickstart):
  POST {BASE}/v2/models/{model}/requests       header X-API-Key -> {"request_id": ...}
  GET  {BASE}/v2/models/{model}/requests/{id}/status
  GET  {BASE}/v2/models/{model}/requests/{id}  -> result with the output URL(s)
No credits -> HTTP 402 insufficient_credits.

Only the Seedance request body is documented. Every field marked TODO below is a
best guess for LTX / FLUX: check each model page (or ask a Comfy dev) and fix.
"""

import base64
import json
import mimetypes
import os
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

from . import stitch

BASE_URL = os.environ.get("COMFY_ROUTER_BASE", "https://api.comfy.org")
VIDEO_MODEL = os.environ.get("SHOT_DIRECTOR_VIDEO_MODEL", "ltx/ltx-2-5-fast")  # final: ltx/ltx-2-5-pro
IMAGE_MODEL = os.environ.get("SHOT_DIRECTOR_IMAGE_MODEL", "bfl/flux-3-image")
SECRETS_FILE = Path.home() / ".claude/.secrets/services/comfy/default.env"
POLL_SECONDS = 3
TIMEOUT_SECONDS = 600


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


class LiveRouter:
    mode = "live"

    def __init__(self, api_key: str, media_dir: Path):
        self._key = api_key
        self.media = media_dir

    # -- HTTP -------------------------------------------------------------
    def _req(self, method: str, url: str, body: dict | None = None) -> dict:
        headers = {"X-API-Key": self._key, "Accept": "application/json"}
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
            headers["Idempotency-Key"] = str(uuid.uuid4())
        req = urllib.request.Request(url, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")[:400]
            if e.code == 402:
                raise RouterError("Router says insufficient_credits (402): top up or get hackathon credits") from e
            raise RouterError(f"Router HTTP {e.code}: {detail}") from e

    def _run_model(self, model: str, content: list[dict], extra: dict | None = None) -> dict:
        base = f"{BASE_URL}/v2/models/{model}/requests"
        body = {"content": content, **(extra or {})}
        rid = self._req("POST", base, body).get("request_id")
        if not rid:
            raise RouterError("Router returned no request_id")
        deadline = time.time() + TIMEOUT_SECONDS
        while time.time() < deadline:
            status = self._req("GET", f"{base}/{rid}/status")
            # TODO(confirm on site): exact status field name and terminal values.
            state = str(status.get("status") or status.get("state") or "").lower()
            if state in {"completed", "succeeded", "success", "done"}:
                return self._req("GET", f"{base}/{rid}")
            if state in {"failed", "error", "cancelled", "canceled"}:
                raise RouterError(f"Router job {state}: {json.dumps(status)[:400]}")
            time.sleep(POLL_SECONDS)
        raise RouterError("Router job timed out")

    @staticmethod
    def _find_url(result: object, exts: tuple[str, ...]) -> str | None:
        """TODO(confirm on site): result schema unknown, so search it for the first media URL."""
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

    def _download(self, url: str, out: Path) -> Path:
        with urllib.request.urlopen(url, timeout=120) as r:
            out.write_bytes(r.read())
        return out

    @staticmethod
    def _image_part(path: Path) -> dict:
        mime = mimetypes.guess_type(path.name)[0] or "image/png"
        b64 = base64.b64encode(path.read_bytes()).decode()
        # TODO(confirm on site): image input shape. Guess = OpenAI-style image_url with a data URI.
        # Also confirm LTX 2.5 accepts a first-frame image at all (MiniMax H3 / Seedance do per docs).
        return {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}}

    # -- public interface ---------------------------------------------------
    def generate_frame(self, prompt: str, body: str, out: Path) -> Path:
        # TODO(confirm on site): FLUX 3 params (aspect ratio / size field names).
        result = self._run_model(IMAGE_MODEL, [{"type": "text", "text": prompt}],
                                 {"aspect_ratio": "16:9"})
        url = self._find_url(result, (".png", ".jpg", ".jpeg", ".webp"))
        if not url:
            raise RouterError("no image URL in Router result")
        return self._download(url, out)

    def generate_clip(self, prompt: str, body: str, move: str, out: Path,
                      frame: Path | None = None, model: str | None = None) -> Path:
        content = [{"type": "text", "text": prompt}]
        if frame is not None:
            content.append(self._image_part(frame))
        # TODO(confirm on site): LTX duration / resolution field names.
        result = self._run_model(model or VIDEO_MODEL, content, {"duration": 5})
        url = self._find_url(result, (".mp4", ".mov", ".webm"))
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
                      frame: Path | None = None, model: str | None = None) -> Path:
        from .presets import CAMERA_BODIES, CAMERA_MOVES
        color = CAMERA_BODIES.get(body, CAMERA_BODIES["modern"])["color"]
        title = f"MOCK {CAMERA_MOVES[move]['label'].upper()}"
        sub = f"{CAMERA_BODIES[body]['label']} - {model or VIDEO_MODEL}"
        return stitch.mock_clip(title, sub, color, out, frame=frame)


def make_router(media_dir: Path):
    key = load_api_key()
    return LiveRouter(key, media_dir) if key else MockRouter(media_dir)
