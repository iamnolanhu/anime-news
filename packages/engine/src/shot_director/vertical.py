"""9:16 export of a finished 16:9 episode for X / TikTok / Reels: the episode sits full width in the middle
of a blurred, darkened copy of itself, with the series name and the episode title above it."""

import argparse
import subprocess
import tempfile
from pathlib import Path

from .edit import GOLD, _font, _q

W, H = 1080, 1920


def vertical(src: Path, out: Path, title: str, series: str = "ANIME NEWS") -> Path:
    font = _font()
    fontopt = f"fontfile={_q(font)}:" if font else ""
    with tempfile.TemporaryDirectory() as td:
        t1, t2 = Path(td) / "series.txt", Path(td) / "title.txt"
        t1.write_text(series)
        t2.write_text(title)
        graph = (f"[0:v]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},boxblur=24:2,"
                 f"eq=brightness=-0.18[bg];"
                 f"[0:v]scale={W}:-2[fg];[bg][fg]overlay=0:(H-h)/2,"
                 f"drawtext={fontopt}textfile={_q(t1)}:expansion=none:fontsize=54:fontcolor={GOLD}:x=(w-text_w)/2:y=330,"
                 f"drawtext={fontopt}textfile={_q(t2)}:expansion=none:fontsize=78:fontcolor=white:"
                 f"box=1:boxcolor=black@0.55:boxborderw=18:x=(w-text_w)/2:y=420[v]")
        p = subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(src), "-filter_complex", graph, "-map", "[v]",
                            "-map", "0:a?", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt",
                            "yuv420p", "-c:a", "copy", "-movflags", "+faststart", str(out)],
                           capture_output=True, text=True)
        if p.returncode != 0:
            raise RuntimeError(f"ffmpeg failed: {p.stderr[-600:]}")
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="9:16 export of a finished episode (blurred fill, title on top).")
    ap.add_argument("src", type=Path)
    ap.add_argument("--title", required=True)
    ap.add_argument("--out", type=Path)
    a = ap.parse_args()
    print(vertical(a.src, a.out or a.src.with_name(a.src.stem + ".9x16.mp4"), a.title))


if __name__ == "__main__":
    main()
