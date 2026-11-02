"""ffmpeg helpers: stitch clips into one mp4, and render mock media offline."""

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

W, H, FPS = 1280, 720, 24

_FONT_CANDIDATES = [
    os.environ.get("SHOT_DIRECTOR_FONT", ""),
    "/System/Library/Fonts/Supplemental/Arial.ttf",  # macOS
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",  # Debian/Ubuntu
]


def _ffmpeg() -> str:
    exe = shutil.which("ffmpeg")
    if not exe:
        raise RuntimeError("ffmpeg not found on PATH (macOS: brew install ffmpeg)")
    return exe


def _run(args: list[str]) -> None:
    proc = subprocess.run([_ffmpeg(), "-hide_banner", "-loglevel", "error", "-y", *args],
                          capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {proc.stderr.strip()[-800:]}")


def _drawtext(textfile: Path, size: int, y: str) -> str:
    font = next((f for f in _FONT_CANDIDATES if f and Path(f).exists()), None)
    font_opt = f"fontfile={font}:" if font else ""
    return (f"drawtext={font_opt}textfile={textfile}:fontcolor=white:fontsize={size}:"
            f"box=1:boxcolor=black@0.55:boxborderw=14:x=(w-text_w)/2:y={y}")


def _normalize() -> str:
    """Scale + pad any input to one frame size so concat never chokes on mixed clips."""
    return (f"scale={W}:{H}:force_original_aspect_ratio=decrease,"
            f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={FPS},format=yuv420p")


def stitch(clips: list[Path], out: Path) -> Path:
    """Concatenate clips (video only) into one H.264 mp4. Re-encodes, so mixed sources are fine."""
    if not clips:
        raise ValueError("no clips to stitch")
    args: list[str] = []
    for c in clips:
        args += ["-i", str(c)]
    chains = "".join(f"[{i}:v]{_normalize()}[v{i}];" for i in range(len(clips)))
    joined = "".join(f"[v{i}]" for i in range(len(clips)))
    graph = f"{chains}{joined}concat=n={len(clips)}:v=1:a=0[out]"
    _run([*args, "-filter_complex", graph, "-map", "[out]", "-c:v", "libx264",
          "-preset", "veryfast", "-crf", "20", "-movflags", "+faststart", str(out)])
    return out


def mock_frame(label: str, color: str, out: Path) -> Path:
    """Solid-colour PNG with a caption: stands in for a FLUX start frame offline."""
    with tempfile.TemporaryDirectory() as td:
        tf = Path(td) / "t.txt"
        tf.write_text(label[:80])
        _run(["-f", "lavfi", "-i", f"color=c={color}:s={W}x{H}",
              "-vf", _drawtext(tf, 36, "h*0.82"), "-frames:v", "1", str(out)])
    return out


def mock_clip(title: str, subtitle: str, color: str, out: Path, frame: Path | None = None,
              seconds: float = 2.0) -> Path:
    """2 s placeholder clip: the start frame (or a solid colour) with the shot's preset drawn on it."""
    with tempfile.TemporaryDirectory() as td:
        t1, t2 = Path(td) / "a.txt", Path(td) / "b.txt"
        t1.write_text(title[:60])
        t2.write_text(subtitle[:90])
        if frame is not None:
            src = ["-loop", "1", "-t", str(seconds), "-i", str(frame)]
        else:
            src = ["-f", "lavfi", "-t", str(seconds), "-i", f"color=c={color}:s={W}x{H}:r={FPS}"]
        vf = f"{_normalize()},{_drawtext(t1, 56, 'h*0.38')},{_drawtext(t2, 28, 'h*0.58')}"
        _run([*src, "-vf", vf, "-c:v", "libx264", "-preset", "veryfast", "-t", str(seconds),
              "-movflags", "+faststart", str(out)])
    return out
