"""Sound for the cut: narration (ElevenLabs TTS), SFX hits and a looped music bed, via Comfy Router.

Every function fails soft: on any error it logs one line and returns None, so `edit.assemble` still
renders a video (with a silent track). Results are cached by output path: if `out` already exists and
is non-empty it is returned without a new Router call (calls spend shared credits).

Router contract used here (from GET /v2/models/elevenlabs/<model>/openapi.json):
  POST {BASE}/v2/models/elevenlabs/<model>   native ElevenLabs body, 200 = raw audio bytes (mp3)
  TTS  (eleven_v3 / v4 / v4_turbo = text-to-dialogue):  {"inputs": [{"text", "voice_id"}]}
  SFX  (eleven_sfx_v2):  {"text", "duration_seconds" (0.5-30, required), "loop", "prompt_influence"}
If Router ever answers with JSON instead of audio (a queued request), we poll the queue routes with the
shared LiveRouter HTTP helpers from router_client and download the first audio URL in the result.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

from .router_client import BASE_URL, CREDITS, LiveRouter, RouterError, load_api_key

log = logging.getLogger("shot_director.audio")

TTS_MODEL = os.environ.get("SHOT_DIRECTOR_TTS_MODEL", "elevenlabs/eleven_v3")
SFX_MODEL = "elevenlabs/eleven_sfx_v2"
# Voice from the Router openapi example (ElevenLabs premade "Rachel"); override per call or via env.
DEFAULT_VOICE = os.environ.get("SHOT_DIRECTOR_VOICE", "21m00Tcm4TlvDq8ikWAM")
MUSIC_PROMPT = ("upbeat synthwave anime opening loop, driving drums, bright arpeggiated synth lead, "
                "punchy bass, energetic, seamless loop, no vocals")
MUSIC_LOOP_SECONDS = 22.0          # one SFX call, looped with ffmpeg to the needed length
_warned: set[str] = set()


def _warn_once(kind: str, msg: str) -> None:
    if kind not in _warned:
        _warned.add(kind)
        log.warning("audio %s skipped: %s", kind, msg)


def _cached(out: Path) -> bool:
    return out.exists() and out.stat().st_size > 1000


def _post_audio(model: str, body: dict, out: Path) -> Path:
    key = load_api_key()
    if not key:
        raise RouterError("no COMFY_API_KEY (or mock mode)")
    out.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(
        f"{BASE_URL}/v2/models/{model}", data=json.dumps(body).encode(), method="POST",
        headers={"X-API-Key": key, "Content-Type": "application/json", "Accept": "*/*",
                 "Idempotency-Key": str(uuid.uuid4())})
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            CREDITS.record(r.headers)
            ctype = r.headers.get("Content-Type", "")
            data = r.read()
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")[:300]
        raise RouterError(f"Router HTTP {e.code}: {detail}") from e
    if "json" not in ctype:
        out.write_bytes(data)
        return out
    doc = json.loads(data or b"{}")
    rid = doc.get("request_id")
    if not rid:
        url = LiveRouter._find_url(doc, (".mp3", ".wav", ".ogg"))
        if not url:
            raise RouterError(f"no audio in Router reply: {str(doc)[:200]}")
        with urllib.request.urlopen(url, timeout=120) as r:
            out.write_bytes(r.read())
        return out
    # Queued: poll status, then fetch the result as raw bytes (it may be audio, not JSON).
    lr = LiveRouter(key, out.parent)
    base = f"{BASE_URL}/v2/models/{model}/requests"
    deadline = time.time() + 300
    while time.time() < deadline:
        _, hdrs, status = lr._req_full("GET", f"{base}/{rid}/status", job=rid)   # one charge per job
        if status.get("error_type"):
            raise RouterError(f"Router job failed: {lr._error_text(status)}")
        if str(status.get("status", "")).upper() == "COMPLETED":
            rq = urllib.request.Request(f"{base}/{rid}", headers={"X-API-Key": key, "Accept": "*/*"})
            with urllib.request.urlopen(rq, timeout=120) as r:
                CREDITS.record(r.headers, rid)
                if r.status == 200:
                    ctype, data = r.headers.get("Content-Type", ""), r.read()
                    if "json" not in ctype:
                        out.write_bytes(data)
                        return out
                    url = LiveRouter._find_url(json.loads(data), (".mp3", ".wav", ".ogg"))
                    if not url:
                        raise RouterError("completed job has no audio URL")
                    with urllib.request.urlopen(url, timeout=120) as r2:
                        out.write_bytes(r2.read())
                    return out
        time.sleep(lr._wait(hdrs))
    raise RouterError("Router audio job timed out")


def _ok(out: Path) -> bool:
    """The file decodes as audio with a positive duration."""
    if not shutil.which("ffprobe"):
        return out.exists() and out.stat().st_size > 0
    p = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
                        str(out)], capture_output=True, text=True)
    try:
        return float(p.stdout.strip()) > 0
    except ValueError:
        return False


def narrate(text: str, out_mp3: Path, voice: str | None = None) -> Path | None:
    """Energetic anime-reporter read of `text` (eleven_v3 audio tags carry the delivery, no SSML)."""
    out_mp3 = Path(out_mp3)
    if _cached(out_mp3):
        return out_mp3
    line = text.strip()
    if TTS_MODEL.endswith("eleven_v3") and not line.startswith("["):
        line = f"[excited] {line}"
    body = {"inputs": [{"text": line, "voice_id": voice or DEFAULT_VOICE}]}
    try:
        _post_audio(TTS_MODEL, body, out_mp3)
        if not _ok(out_mp3):
            raise RouterError("TTS reply did not decode as audio")
        return out_mp3
    except Exception as e:  # fail soft: the cut still renders
        out_mp3.unlink(missing_ok=True)
        _warn_once("narration", str(e)[:200])
        return None


def sfx(prompt: str, out: Path, seconds: float = 1.5, loop: bool = False) -> Path | None:
    """One sound effect (whoosh, impact, ...) of `seconds` (0.5-30)."""
    out = Path(out)
    if _cached(out):
        return out
    body = {"text": prompt, "duration_seconds": max(0.5, min(30.0, float(seconds))),
            "loop": loop, "prompt_influence": 0.6}
    try:
        _post_audio(SFX_MODEL, body, out)
        if not _ok(out):
            raise RouterError("SFX reply did not decode as audio")
        return out
    except Exception as e:
        out.unlink(missing_ok=True)
        _warn_once("sfx", str(e)[:200])
        return None


def music_bed(seconds: float, out: Path) -> Path | None:
    """Music source for tonight: ONE eleven_sfx_v2 call for a <=22 s loopable synthwave anime opening loop,
    looped with ffmpeg to `seconds`. A proper music model is a follow-up."""
    out = Path(out)
    if _cached(out) and _duration(out) >= seconds - 0.05:
        return out
    loop_src = out.with_name(out.stem + ".loop.mp3")
    if sfx(MUSIC_PROMPT, loop_src, MUSIC_LOOP_SECONDS, loop=True) is None:
        _warn_once("music", "loop generation failed")
        return None
    try:
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-stream_loop", "-1", "-i", str(loop_src),
                        "-t", f"{seconds:.3f}", "-c:a", "libmp3lame", "-b:a", "192k", str(out)], check=True)
        return out
    except Exception as e:
        _warn_once("music", f"ffmpeg loop failed: {e}")
        return None


def _duration(path: Path) -> float:
    p = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
                        str(path)], capture_output=True, text=True)
    try:
        return float(p.stdout.strip())
    except ValueError:
        return 0.0
