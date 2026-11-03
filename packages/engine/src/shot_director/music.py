"""Music kit for episodes: a real driving bed, an 8 s riser into a hit, and a small SFX pack.

The kit lives in `<media_dir>/music/` (outside git; the bed is a Pixabay track, see CREDITS.txt).
`music_kit` only reads the folder. `fetch_kit` rebuilds it, fail-soft and skipping what exists:
bed download + loudnorm (-14 LUFS), SFX via `audio.sfx` (cached, <= 5 paid calls), intro via ffmpeg.
"""
from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import urllib.request
from pathlib import Path

log = logging.getLogger("shot_director.music")

BED_PAGE = "https://pixabay.com/music/synthwave-dark-synthwave-624644/"
BED_CDN = ("https://cdn.pixabay.com/download/audio/2026/10/22/audio_134c1e9286.mp3"
           "?filename=kulakovka-dark-synthwave-624644.mp3")
PROXY = os.environ.get("ANIME_NEWS_PROXY", "")   # optional socks5://host:port for the bed download
CREDITS = ("Bed: \"Dark Synthwave\" by Kulakovka\n"
           f"Source: {BED_PAGE}\nLicense: Pixabay Content License (no attribution required; do not redistribute "
           "the file as-is, so it is kept out of git and fetched by shot_director.music.fetch_kit)\n"
           "SFX: generated with elevenlabs/eleven_sfx_v2 through Comfy Router. Intro: built from the bed with ffmpeg.\n")
SFX = {  # name -> (prompt, seconds)
    "impact": ("deep anime impact boom with sub drop", 1.5),
    "whoosh": ("fast whoosh", 0.8),
    "crt": ("old CRT TV switch-on click with static", 1.0),
    "clock": ("loud ticking clock", 3.0),
    "glitch": ("digital glitch stutter", 1.0),
}
NAMES = ["bed", "intro", *SFX]


def _run(cmd: list[str]) -> bool:
    try:
        subprocess.run(cmd, check=True, capture_output=True)
        return True
    except (subprocess.CalledProcessError, OSError) as e:
        log.warning("music: %s failed: %s", cmd[0], str(getattr(e, "stderr", e))[-200:])
        return False


def music_kit(media_dir) -> dict:
    """Paths of the kit files that exist in `<media_dir>/music/`, None for the rest."""
    d = Path(media_dir) / "music"
    out = {}
    for n in NAMES:
        p = d / f"{n}.mp3"
        out[n] = str(p) if p.exists() and p.stat().st_size > 0 else None
    return out


def _download_bed(raw: Path) -> bool:
    """Pixabay's CDN link (optionally through ANIME_NEWS_PROXY). If the link rots, drop any mp3 at
    <media>/music/bed.mp3 or point ANIME_NEWS_MUSIC_BED at a file or URL."""
    custom = os.environ.get("ANIME_NEWS_MUSIC_BED", "")
    if custom and not custom.startswith("http"):
        src = Path(custom).expanduser()
        if src.exists() and src.stat().st_size > 100_000:
            raw.write_bytes(src.read_bytes())
            return True
    proxy = ["--socks5-hostname", PROXY.split("//")[-1]] if PROXY else []
    def get(url: str) -> bool:
        return _run(["curl", "-sfL", *proxy, "-A", "Mozilla/5.0 Chrome/130", "-e", "https://pixabay.com/",
                     "-o", str(raw), url]) and raw.exists() and raw.stat().st_size > 100_000
    if custom.startswith("http") and get(custom):
        return True
    if get(BED_CDN):
        return True
    log.warning("music: bed download failed; put an mp3 at <media>/music/bed.mp3 or set ANIME_NEWS_MUSIC_BED")
    return False


def _intro(bed: Path, impact: Path | None, out: Path) -> bool:
    """8 s riser built from the bed (low-passed, swelling) plus a noise sweep, landing on the impact at 7.5 s."""
    ins = ["-i", str(bed)]
    graph = ("[0:a]atrim=0:8,asetpts=PTS-STARTPTS,lowpass=f=700,volume='min(1,0.15+0.85*pow(t/7.5,2))':eval=frame[b];"
             "anoisesrc=d=8:c=pink:a=0.5,highpass=f=500,volume='pow(t/7.5,3)':eval=frame,afade=t=out:st=7.5:d=0.1[n];"
             "[b][n]amix=inputs=2:normalize=0[r]")
    last = "[r]"
    if impact:
        ins += ["-i", str(impact)]
        graph += ";[1:a]adelay=7500|7500,volume=1.4[h];[r][h]amix=inputs=2:normalize=0[m]"
        last = "[m]"
    graph += f";{last}atrim=0:8,alimiter=limit=0.89[o]"
    return _run(["ffmpeg", "-y", "-v", "error", *ins, "-filter_complex", graph, "-map", "[o]",
                 "-c:a", "libmp3lame", "-b:a", "192k", str(out)])


def _peak_gain(path: Path) -> float:
    """dB to add so the file peaks at -1 dBFS."""
    r = subprocess.run(["ffmpeg", "-i", str(path), "-af", "volumedetect", "-f", "null", "-"],
                       capture_output=True, text=True)
    m = re.search(r"max_volume: (-?[\d.]+) dB", r.stderr)
    return -1.6 - float(m.group(1)) if m else 0.0


def fetch_kit(media_dir) -> dict:
    """Rebuild `<media_dir>/music/`; every step is fail-soft and skipped if its file exists. Returns music_kit()."""
    from .audio import sfx  # local import: audio pulls in the Router client
    d = Path(media_dir) / "music"
    d.mkdir(parents=True, exist_ok=True)
    (d / "CREDITS.txt").write_text(CREDITS)
    bed = d / "bed.mp3"
    if not bed.exists():
        raw = d / "raw.mp3"
        if raw.exists() or _download_bed(raw):
            if _run(["ffmpeg", "-y", "-v", "error", "-i", str(raw), "-af", "loudnorm=I=-14:TP=-1.5:LRA=11",
                     "-c:a", "libmp3lame", "-b:a", "192k", str(bed)]):
                raw.unlink(missing_ok=True)
    for name, (prompt, secs) in SFX.items():
        p = d / f"{name}.mp3"
        if p.exists():
            continue
        raw = d / f"{name}.raw.mp3"
        if sfx(prompt, raw, secs) is not None:
            _run(["ffmpeg", "-y", "-v", "error", "-i", str(raw), "-af", f"volume={_peak_gain(raw):.2f}dB",
                  "-c:a", "libmp3lame", "-b:a", "192k", str(p)])
            raw.unlink(missing_ok=True)
    if not (d / "intro.mp3").exists() and bed.exists():
        imp = d / "impact.mp3"
        _intro(bed, imp if imp.exists() else None, d / "intro.mp3")
    return music_kit(media_dir)
