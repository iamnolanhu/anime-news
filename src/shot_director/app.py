"""FastAPI app: one process serves the API, the static UI and the generated media."""

import argparse
import os
import shutil
import uuid
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import presets, stitch
from .router_client import (CONTINUATION_ENGINE, ENGINES, IMAGE_MODEL, VIDEO_MODEL, RouterError,
                            make_router)

ROOT = Path(__file__).resolve().parents[2]
MEDIA = Path(os.environ.get("SHOT_DIRECTOR_MEDIA", ROOT / "media"))
MEDIA.mkdir(parents=True, exist_ok=True)
STATIC = Path(__file__).parent / "static"
MAX_SHOTS = 3

app = FastAPI(title="Shot Director")
app.mount("/media", StaticFiles(directory=MEDIA), name="media")
router = make_router(MEDIA)
queue: list[dict] = []  # in-memory shot queue, max 3


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


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


@app.get("/api/presets")
def get_presets() -> dict:
    return {**presets.as_json(), "mode": router.mode, "video_model": VIDEO_MODEL,
            "image_model": IMAGE_MODEL, "max_shots": MAX_SHOTS, "engines": ENGINES}


@app.post("/api/frame")
def upload_frame(file: UploadFile = File(...)) -> dict:
    ext = Path(file.filename or "frame.png").suffix.lower() or ".png"
    if ext not in {".png", ".jpg", ".jpeg", ".webp"}:
        raise HTTPException(400, "start frame must be png, jpg or webp")
    name = f"frame-{uuid.uuid4().hex[:8]}{ext}"
    with (MEDIA / name).open("wb") as f:
        shutil.copyfileobj(file.file, f)
    return {"frame": name, "url": f"/media/{name}"}


@app.post("/api/frame/generate")
def generate_frame(req: FrameRequest) -> dict:
    name = f"frame-{uuid.uuid4().hex[:8]}.png"
    try:
        router.generate_frame(req.prompt, req.body, MEDIA / name)
    except (RouterError, RuntimeError) as e:
        raise HTTPException(502, str(e)) from e
    return {"frame": name, "url": f"/media/{name}"}


@app.post("/api/shot")
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
            "continued": previous_id is not None, "continued_from": previous_id}
    queue.append(shot)
    return shot


@app.get("/api/queue")
def get_queue() -> dict:
    return {"shots": queue, "max_shots": MAX_SHOTS}


@app.delete("/api/queue")
def clear_queue() -> dict:
    queue.clear()
    return {"shots": queue}


@app.delete("/api/queue/{shot_id}")
def remove_shot(shot_id: str) -> dict:
    queue[:] = [s for s in queue if s["id"] != shot_id]
    return {"shots": queue}


@app.post("/api/stitch")
def stitch_queue() -> dict:
    if not queue:
        raise HTTPException(400, "queue is empty: generate a shot first")
    name = f"sequence-{uuid.uuid4().hex[:8]}.mp4"
    try:
        stitch.stitch([_media_path(s["id"]) for s in queue], MEDIA / name)
    except RuntimeError as e:
        raise HTTPException(500, str(e)) from e
    return {"url": f"/media/{name}", "file": name, "shots": len(queue)}


def main() -> None:
    import uvicorn

    ap = argparse.ArgumentParser(description="Shot Director server")
    ap.add_argument("--host", default=os.environ.get("HOST", "127.0.0.1"))
    ap.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8000")))
    args = ap.parse_args()
    print(f"Shot Director ({router.mode} mode) on http://{args.host}:{args.port}")
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
