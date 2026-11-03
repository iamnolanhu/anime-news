"""Anime-paced cut: turn generated clips + stills into a fast, captioned, scored edit with ffmpeg only.

`assemble(plan, out)` -> out. Rules (see docs/EDIT.md, tuned to docs/psyopanime-formula.md): every clip
is chopped into 2-3 segments of 1.3-2.1 s from different parts of the clip (0.33 s bursts in action
beats), stills become 0.8-1.2 s Ken Burns / shake cutaways, cold open = title over the strongest frame,
each scene opens on its strongest frame, 2-frame white flash on beats, 1-frame black on hard beats,
an occasional CRT-monitor insert as a transition, one speed ramp per scene, yellow-on-dark subtitles
one clause per cut, a small gold wordmark top-left, end card. Audio: narration,
ducked music bed, SFX hits on scene changes, loudnorm; a silent track when nothing is given.
No generation happens here; audio.py makes the sound files.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import random
import shutil
import subprocess
import tempfile
import textwrap
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from pathlib import Path

log = logging.getLogger("shot_director.edit")

GOLD = "0xF5C518"
END_CARD_TEXT = "Shot Director · open source · #OpenSourceHiggsfield"
WORDMARK = os.environ.get("SHOT_DIRECTOR_WORDMARK", "SHOT DIRECTOR")
TITLE_FRAMES_S = 1.25
END_FRAMES_S = 1.6
CLIP_SEG = (1.6, 2.5)      # output seconds of a clip segment (formula: avg shot 1.3-1.8 s, never > 2.5)
CLIP_SEG_MIN = 1.2
STILL_SEG = (0.9, 1.3)     # output seconds of a still cutaway
BURST_FRAMES = 8           # 0.33 s shots in action-beat bursts
BURST_COUNT = 4
CRT_SEG = (0.5, 0.8)       # CRT monitor insert used as a transition
SHARP_GATE = 0.4           # skip clip segments whose sharpness < 40 % of the clip's median
ACTION_WORDS = ("action", "chase", "fight", "escalat", "montage", "burst", "battle", "attack", "clash", "run")
FONT_CANDIDATES = [
    os.environ.get("SHOT_DIRECTOR_FONT", ""),
    "/System/Library/Fonts/Supplemental/Arial Black.ttf",
    "/System/Library/Fonts/Supplemental/Impact.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
]


@dataclass
class Seg:
    kind: str                  # clip | still | crt | flash | black | title | end
    frames: int
    src: str = ""
    start: float = 0.0         # source seconds (clip)
    speed: float = 1.0         # setpts factor: 1.4 = slow-mo, 0.7 = sped up
    motion: str = ""           # still: push | pull | shake ; clip: punch (opening) | ""
    caption: str = ""
    scene: int = -1
    pop: bool = False          # caption pops in on this segment
    at: float = 0.0            # filled in after planning: timeline seconds
    extra: dict = field(default_factory=dict)


# ---------------------------------------------------------------- helpers
def _run(cmd: list[str]) -> None:
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError(f"ffmpeg failed ({p.returncode}): {p.stderr[-800:]}")


def _duration(path: str | Path) -> float:
    p = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
                        str(path)], capture_output=True, text=True)
    try:
        return float(p.stdout.strip())
    except ValueError:
        return 0.0


def _font() -> str:
    for f in FONT_CANDIDATES:
        if f and Path(f).exists():
            return f
    return ""


_SHARP_CACHE: dict[tuple[str, int], float] = {}


def sharpness(clip: str, t: float, w: int = 160, h: int = 90) -> float:
    """Laplacian variance of a small grayscale frame (pure Python; motion smear -> low value)."""
    key = (clip, int(t * 100))
    if key in _SHARP_CACHE:
        return _SHARP_CACHE[key]
    p = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{max(0.0, t):.3f}", "-i", clip, "-frames:v", "1",
                        "-vf", f"scale={w}:{h},format=gray", "-f", "rawvideo", "-"], capture_output=True)
    px = p.stdout
    if len(px) < w * h:
        return 0.0
    vals = []
    for y in range(1, h - 1):
        r = y * w
        for x in range(1, w - 1):
            i = r + x
            vals.append(px[i - 1] + px[i + 1] + px[i - w] + px[i + w] - 4 * px[i])
    m = sum(vals) / len(vals)
    v = sum((a - m) ** 2 for a in vals) / len(vals)
    _SHARP_CACHE[key] = v
    return v


def clip_profile(clip: str, dur: float, samples: int = 10) -> tuple[float, float, float]:
    """(strongest time, its sharpness, median sharpness) over `samples` evenly spaced frames."""
    lo, hi = 0.15, max(0.2, dur - 0.15)
    ts = [lo + (hi - lo) * i / max(1, samples - 1) for i in range(samples)]
    scored = sorted((sharpness(clip, t), t) for t in ts)
    med = scored[len(scored) // 2][0]
    best, t = scored[-1]
    return t, best, med


def seg_sharpness(clip: str, start: float, src_dur: float) -> float:
    """Worst of the middle and the last frame of a segment (a smeared tail fails the gate)."""
    return min(sharpness(clip, start + src_dur / 2), sharpness(clip, start + max(0.0, src_dur - 0.08)))


def _wrap_caption(text: str, width: int = 22) -> str:
    text = " ".join(text.split())
    if len(text) > 40:
        text = text[:39].rstrip() + "…"
    lines = textwrap.wrap(text, width=width) or [""]
    if len(lines) > 2:
        lines = [lines[0], " ".join(lines[1:])]
    return "\n".join(lines)


def split_clauses(text: str) -> list[str]:
    """Subtitle chunks: one clause each (split on , ; : . ! ? and dashes), each <= 8 words."""
    import re
    parts = [p.strip(" ,;:-–—") for p in re.split(r"(?<=[,;:.!?])\s+|\s+[–—-]\s+", " ".join(text.split()))]
    out: list[str] = []
    for p in parts:
        words = p.split()
        while len(words) > 8:
            out.append(" ".join(words[:6]))
            words = words[6:]
        if words:
            out.append(" ".join(words))
    return out or [""]


def _q(path: Path | str) -> str:
    """Quote a path for use inside a filtergraph option."""
    return "'" + str(path).replace("\\", "\\\\").replace("'", r"'\''") + "'"


# ---------------------------------------------------------------- planning
def plan_segments(plan: dict, workdir: Path, base: Path) -> list[Seg]:
    fps = int(plan.get("fps", 24))
    rng = random.Random(plan.get("seed", 7))

    def frames(sec: float) -> int:
        return max(1, round(sec * fps))

    def resolve(p: str) -> str | None:
        pp = Path(p)
        pp = pp if pp.is_absolute() else base / pp
        return str(pp) if pp.exists() else None

    gated: list[dict] = []
    title = Seg("title", frames(TITLE_FRAMES_S))
    segs: list[Seg] = [title]
    best_open: tuple[int, str, float] | None = None
    for si, scene in enumerate(plan.get("scenes", [])):
        cap = scene.get("caption") or scene.get("beat") or ""
        action = bool(scene.get("action")) or any(w in str(scene.get("beat", "")).lower() for w in ACTION_WORDS)
        clips = [c for c in (resolve(p) for p in scene.get("clips", [])) if c]
        stills = [s for s in (resolve(p) for p in scene.get("stills", [])) if s]
        segs += [Seg("flash", 2, scene=si)]   # 2-frame white impact flash on every scene change
        clip_segs: list[tuple[float, Seg]] = []  # (score, seg)
        freeze_src: list[tuple[str, float]] = []
        for clip in clips:
            dur = _duration(clip)
            if dur <= 0.5:
                continue
            t_best, score, med = clip_profile(clip, dur)
            freeze_src.append((clip, t_best))
            if best_open is None or score > best_open[0]:
                best_open = (score, clip, t_best)
            k = 3 if dur >= 4.2 else 2 if dur >= 2.8 else 1
            lens = [min(rng.uniform(*CLIP_SEG), dur - 0.05) for _ in range(k)]
            if k == 1:
                lens = [max(min(dur - 0.05, CLIP_SEG[1]), min(CLIP_SEG_MIN, dur))]
            # windows spread across the clip; the one nearest the strongest frame snaps to it
            starts = [((dur - lens[j]) * j / (k - 1)) if k > 1 else 0.0 for j in range(k)]
            near = min(range(k), key=lambda j: abs(starts[j] + lens[j] / 2 - t_best))
            starts[near] = min(max(0.0, t_best - 0.1), dur - lens[near])
            for j in range(k):
                s = Seg("clip", frames(lens[j]), src=clip, start=starts[j], caption=cap, scene=si)
                s.extra["src_dur"] = lens[j]
                sh = seg_sharpness(clip, starts[j], lens[j])
                s.extra["sharp"], s.extra["sharp_median"] = round(sh, 1), round(med, 1)
                if j != near and sh < SHARP_GATE * med:   # sharpness gate: drop smeared windows
                    gated.append({"src": clip, "start": round(starts[j], 2), "sharp": round(sh, 1),
                                  "median": round(med, 1)})
                    continue
                clip_segs.append((score if j == near else 0, s))
        if not clip_segs and not stills:
            continue
        # strongest frame opens the scene; the rest round-robin across the clips (different parts)
        clip_segs.sort(key=lambda x: -x[0])
        opener = [clip_segs[0][1]] if clip_segs else []
        rest = [s for _, s in clip_segs[1:]]
        by_src: dict[str, list[Seg]] = {}
        for s in rest:
            by_src.setdefault(s.src, []).append(s)
        order: list[Seg] = []
        while any(by_src.values()):
            for src in list(by_src):
                if by_src[src]:
                    order.append(by_src[src].pop(-1 if len(order) % 2 else 0))
        ordered = opener + order
        # never let a smeared tail end a scene: the sharpest-ending non-opener segment goes last
        if len(ordered) > 2:
            tail = max(ordered[1:], key=lambda x: sharpness(x.src, x.start + x.extra["src_dur"] - 0.08)
                       / max(1.0, x.extra.get("sharp_median", 1.0)))
            ordered.remove(tail)
            ordered.append(tail)
        if opener:
            opener[0].motion = "punch"
        # one speed ramp per scene, on the 2nd clip segment (or the only one)
        if ordered:
            ramp = ordered[1] if len(ordered) > 1 else ordered[0]
            ramp.speed = 1.4 if si % 2 == 0 else 0.7
            out_s = ramp.frames / fps
            ramp.extra["src_dur"] = out_s / ramp.speed
            dur = _duration(ramp.src)
            if ramp.start + ramp.extra["src_dur"] > dur - 0.05:
                ramp.start = max(0.0, dur - 0.05 - ramp.extra["src_dur"])
                if ramp.start == 0.0 and ramp.extra["src_dur"] > dur - 0.05:   # clip too short: shorten output
                    ramp.extra["src_dur"] = dur - 0.05
                    ramp.frames = frames(ramp.extra["src_dur"] * ramp.speed)
            ramp.extra["ramp"] = True
        # still pool: given stills first, then freeze frames from the clips' strongest moments
        pool = list(stills)
        for clip, t in freeze_src:
            png = workdir / f"freeze-{si}-{len(pool)}.png"
            _run(["ffmpeg", "-y", "-v", "error", "-ss", f"{t:.3f}", "-i", clip, "-frames:v", "1", str(png)])
            pool.append(str(png))
        motions = ["push", "shake", "pull"]
        body: list[Seg] = []
        n_still = 0
        for idx, cs in enumerate(ordered):
            body.append(cs)
            # alternate clip / still / clip: a cutaway after clip segments 0, 2, 4 ...
            if idx % 2 == 0 and idx < len(ordered) - 1 and pool:
                st = Seg("still", frames(rng.uniform(*STILL_SEG)), src=pool[n_still % len(pool)],
                         motion=motions[(n_still + si) % 3], caption=cap, scene=si)
                body.append(st)
                n_still += 1
        if not ordered:   # stills-only scene
            for i, p in enumerate(pool[:3]):
                body.append(Seg("still", frames(rng.uniform(*STILL_SEG)), src=p, motion=motions[i % 3],
                                caption=cap, scene=si))
        # action beats: a burst of 0.33 s shots from scattered parts of the clips, before the last shot
        if action and ordered:
            burst = []
            for b in range(BURST_COUNT):
                src = ordered[b % len(ordered)].src
                d = _duration(src)
                floor = SHARP_GATE * ordered[b % len(ordered)].extra.get("sharp_median", 0)
                st = None
                for _ in range(5):   # sharpness gate for burst shots as well; skip the shot if nothing passes
                    cand = rng.uniform(0.0, max(0.0, d - BURST_FRAMES / fps - 0.1))
                    if seg_sharpness(src, cand, BURST_FRAMES / fps) >= floor:
                        st = cand
                        break
                if st is None:
                    continue
                burst.append(Seg("clip", BURST_FRAMES, src=src, start=st, caption=cap, scene=si,
                                 extra={"src_dur": BURST_FRAMES / fps, "burst": True}))
            body = body[:-1] + burst + body[-1:] if len(body) > 1 else body + burst
        # occasional CRT-monitor insert as the transition into every other scene
        if si % 2 == 1 and ordered:
            src = ordered[-1].src
            n_crt = frames(rng.uniform(*CRT_SEG))
            d = _duration(src)
            body.insert(0, Seg("crt", n_crt, src=src, start=max(0.0, d / 2 - n_crt / fps / 2), caption=cap,
                               scene=si, extra={"src_dur": n_crt / fps}))
        # 1-frame black on hard beats: before the speed ramp and before the scene's last shot
        final: list[Seg] = []
        for i, s in enumerate(body):
            if (s.extra.get("ramp") or (i == len(body) - 1 and len(body) > 2)) and i > 0 \
                    and final and final[-1].kind != "black":
                final.append(Seg("black", 1, scene=si))
            final.append(s)
        # subtitles: one clause at a time, split across the scene's cuts in order
        clauses = split_clauses(cap) if cap else [""]
        content = [s for s in final if s.kind in ("clip", "still", "crt")]
        prev = None
        for j, s in enumerate(content):
            s.caption = clauses[min(len(clauses) - 1, j * len(clauses) // max(1, len(content)))]
            s.pop = s.caption != prev
            prev = s.caption
        segs += final
    if best_open:   # cold open: title over the strongest frame in the whole episode
        title.src, title.start = best_open[1], max(0.0, best_open[2] - 0.2)
        title.extra["src_dur"] = title.frames / fps
        for sg in segs:   # a scene opener on the same moment continues after the title instead of repeating it
            if sg.kind == "clip" and sg.src == title.src and abs(sg.start - title.start) < 1.5:
                sg.start = min(_duration(sg.src) - sg.extra["src_dur"] - 0.05, title.start + title.frames / fps)
    segs += [Seg("flash", 2), Seg("end", frames(END_FRAMES_S))]
    t = 0.0
    plan_segments.gated = gated
    for s in segs:
        s.at = round(t, 4)
        t += s.frames / fps
    return segs


# ---------------------------------------------------------------- rendering
def _caption_filter(s: Seg, font: str, workdir: Path, idx: int, H: int) -> str:
    if not s.caption:
        return ""
    tf = workdir / f"cap-{idx}.txt"
    tf.write_text(_wrap_caption(s.caption))
    size = max(26, H // 18)
    alpha = ":alpha='min(1,t*10)'" if s.pop else ""
    fontopt = f"fontfile={_q(font)}:" if font else ""
    return (f",drawtext={fontopt}textfile={_q(tf)}:expansion=none:fontsize={size}:fontcolor={GOLD}"
            f":box=1:boxcolor=black@0.62:boxborderw={size // 3}:line_spacing={size // 5}"
            f":x=(w-text_w)/2:y=h-text_h-{H // 11}{alpha}")


def _wordmark(font: str, workdir: Path, H: int) -> str:
    if not WORDMARK:
        return ""
    tf = workdir / "wordmark.txt"
    if not tf.exists():
        tf.write_text(WORDMARK)
    fontopt = f"fontfile={_q(font)}:" if font else ""
    return (f",drawtext={fontopt}textfile={_q(tf)}:expansion=none:fontsize={max(14, H // 40)}:fontcolor={GOLD}"
            f":shadowcolor=black@0.8:shadowx=2:shadowy=2:x={H // 30}:y={H // 30}")


def _card_filter(lines: list[tuple[str, int, str, str]], font: str, workdir: Path, idx: int) -> str:
    out = []
    for j, (text, size, color, y) in enumerate(lines):
        tf = workdir / f"card-{idx}-{j}.txt"
        tf.write_text(text)
        fontopt = f"fontfile={_q(font)}:" if font else ""
        out.append(f"drawtext={fontopt}textfile={_q(tf)}:expansion=none:fontsize={size}:fontcolor={color}"
                   f":box=1:boxcolor=black@0.55:boxborderw={max(6, size // 4)}"
                   f":x=(w-text_w)/2:y={y}:alpha='min(1,t*8)'")
    return ",".join(out)


def render_segment(s: Seg, idx: int, plan: dict, workdir: Path, font: str) -> Path:
    W, H = plan.get("size", [1280, 720])
    fps = int(plan.get("fps", 24))
    out = workdir / f"seg-{idx:03d}.mp4"
    fit = f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},setsar=1"
    enc = ["-frames:v", str(s.frames), "-r", str(fps), "-an", "-c:v", "libx264", "-preset", "veryfast",
           "-crf", "19", "-pix_fmt", "yuv420p", "-video_track_timescale", str(fps * 512), str(out)]
    cap = _caption_filter(s, font, workdir, idx, H) + _wordmark(font, workdir, H)
    if s.kind == "clip":
        src_dur = s.extra.get("src_dur", s.frames / fps / s.speed)
        vf = f"{fit},setpts={s.speed}*(PTS-STARTPTS),fps={fps}"
        if s.motion == "punch":   # quick push-in on the opener
            vf += f",zoompan=z='min(1+0.006*on,1.18)':d=1:x='iw/2-iw/zoom/2':y='ih/2-ih/zoom/2':s={W}x{H}:fps={fps}"
        cmd = ["ffmpeg", "-y", "-v", "error", "-ss", f"{s.start:.3f}", "-t", f"{src_dur + 0.25:.3f}",
               "-i", s.src, "-vf", vf + cap + ",tpad=stop_mode=clone:stop_duration=1"] + enc
    elif s.kind == "still":
        big = f"scale={W * 2}:{H * 2}:force_original_aspect_ratio=increase,crop={W * 2}:{H * 2},setsar=1"
        if s.motion == "shake":
            amp = max(8, W // 60)
            vf = (f"scale={W + 2 * amp}:{H + 2 * amp}:force_original_aspect_ratio=increase,"
                  f"crop={W + 2 * amp}:{H + 2 * amp},setsar=1,"
                  f"crop={W}:{H}:x='{amp}+{amp}*sin(n*2.1)*max(0.25,1-n/{s.frames})'"
                  f":y='{amp}+{amp}*cos(n*1.7)*max(0.25,1-n/{s.frames})',fps={fps}")
            cmd = ["ffmpeg", "-y", "-v", "error", "-loop", "1", "-framerate", str(fps), "-i", s.src,
                   "-vf", vf + cap] + enc
        else:
            z = (f"1+0.22*on/{s.frames}" if s.motion == "push" else f"1.22-0.22*on/{s.frames}")
            vf = (f"{big},zoompan=z='{z}':d={s.frames}:x='iw/2-iw/zoom/2':y='ih/2-ih/zoom/2'"
                  f":s={W}x{H}:fps={fps}")
            cmd = ["ffmpeg", "-y", "-v", "error", "-i", s.src, "-vf", vf + cap] + enc
    elif s.kind == "crt":
        sw, sh = int(W * 0.62) // 2 * 2, int(H * 0.62) // 2 * 2
        vf = (f"{fit},setpts=PTS-STARTPTS,fps={fps},scale={sw}:{sh},lenscorrection=k1=0.18:k2=0.06,"
              f"eq=saturation=0.7:contrast=1.15,colorbalance=bs=0.25:bm=0.12,"
              f"drawgrid=w=iw:h=3:t=1:c=black@0.5,noise=alls=14:allf=t,"
              f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:color=0x05070a,"
              f"drawbox=x=(iw-{sw})/2-12:y=(ih-{sh})/2-12:w={sw + 24}:h={sh + 24}:color=0x26303a:t=10")
        cmd = ["ffmpeg", "-y", "-v", "error", "-ss", f"{s.start:.3f}", "-t", f"{s.frames / fps + 0.25:.3f}",
               "-i", s.src, "-vf", vf + cap + ",tpad=stop_mode=clone:stop_duration=1"] + enc
    elif s.kind in ("flash", "black"):
        color = "white" if s.kind == "flash" else "black"
        cmd = ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"color=c={color}:s={W}x{H}:r={fps}"] + enc
    elif s.kind in ("title", "end"):
        if s.kind == "title":
            title = _wrap_caption(plan.get("title", "Shot Director"), 28)
            logline = "\n".join(textwrap.wrap(plan.get("logline", ""), 60)[:2])
            lines = [(title, H // 10, "white", f"h/2-text_h-{H // 30}"),
                     (logline, H // 28, GOLD, f"h/2+{H // 18}")]
        else:
            lines = [(END_CARD_TEXT, H // 22, "white", "h/2-text_h/2")]
        bar = f"drawbox=x=(iw-{W // 4})/2:y=ih/2+{H // 60}:w={W // 4}:h={max(4, H // 120)}:color={GOLD}:t=fill"
        if s.kind == "end":
            bar = f"drawbox=x=(iw-{W // 3})/2:y=ih/2+{H // 22}:w={W // 3}:h={max(4, H // 120)}:color={GOLD}:t=fill"
        vf = bar + "," + _card_filter(lines, font, workdir, idx) + _wordmark(font, workdir, H)
        if s.kind == "title" and s.src:   # cold open: crash zoom on the strongest frame, darkened under the title
            pre = (f"{fit},setpts=PTS-STARTPTS,fps={fps},zoompan=z='min(1+0.03*on,1.4)':d=1"
                   f":x='iw/2-iw/zoom/2':y='ih/2-ih/zoom/2':s={W}x{H}:fps={fps},eq=brightness=-0.25:saturation=1.15,")
            cmd = ["ffmpeg", "-y", "-v", "error", "-ss", f"{s.start:.3f}", "-t", f"{s.frames / fps + 0.25:.3f}",
                   "-i", s.src, "-vf", pre + vf + ",tpad=stop_mode=clone:stop_duration=1"] + enc
        else:
            cmd = ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"color=c=black:s={W}x{H}:r={fps}",
                   "-vf", vf] + enc
    else:
        raise ValueError(f"unknown segment kind {s.kind}")
    _run(cmd)
    return out


# ---------------------------------------------------------------- audio
def _audio_graph(plan: dict, segs: list[Seg], total: float, base: Path) -> tuple[list[str], str, list[str]]:
    """Returns (ffmpeg input args, filter_complex, notes). Input 0 is the video."""
    def resolve(p) -> str | None:
        if not p:
            return None
        pp = Path(p)
        pp = pp if pp.is_absolute() else base / pp
        return str(pp) if pp.exists() and pp.stat().st_size > 0 else None

    inputs: list[str] = []
    parts: list[str] = [f"anullsrc=r=48000:cl=stereo,atrim=0:{total:.3f}[base]"]
    mix = ["[base]"]
    notes: list[str] = []
    n = 1
    scene_start = {}
    for s in segs:
        if s.scene >= 0 and s.kind not in ("flash", "black") and s.scene not in scene_start:
            scene_start[s.scene] = s.at
    changes = [s.at for s in segs if s.kind == "flash"]

    voices = []
    items = list(plan.get("narration") or [])
    for si, sc in enumerate(plan.get("scenes", [])):
        if sc.get("narration_audio"):
            items.append({"path": sc["narration_audio"], "scene": si})
    for item in items:
        p = resolve(item.get("path"))
        if not p:
            continue
        sc = item.get("scene", -1)
        at = float(item["at"]) if item.get("at") is not None else (
            0.15 if sc is None or sc < 0 else scene_start.get(sc, 0.0) + 0.1)
        inputs += ["-i", p]
        ms = int(at * 1000)
        parts.append(f"[{n}:a]aresample=48000,aformat=channel_layouts=stereo,loudnorm=I=-16:TP=-2,"
                     f"adelay={ms}|{ms}[v{n}]")
        voices.append(f"[v{n}]")
        n += 1
    if voices:
        parts.append("".join(voices) + f"amix=inputs={len(voices)}:normalize=0:duration=longest,"
                     f"apad,atrim=0:{total:.3f},asplit=2[voice][vkey]")
        mix.append("[voice]")
        notes.append(f"narration x{len(voices)}")

    music = resolve(plan.get("music"))
    if music:
        inputs += ["-stream_loop", "-1", "-i", music]
        fade_out = max(0.0, total - 1.2)
        parts.append(f"[{n}:a]aresample=48000,aformat=channel_layouts=stereo,atrim=0:{total:.3f},"
                     f"volume=0.55,afade=t=in:d=0.4,afade=t=out:st={fade_out:.3f}:d=1.2[m]")
        if voices:   # duck the bed under narration
            parts.append("[m][vkey]sidechaincompress=threshold=0.03:ratio=10:attack=20:release=400:makeup=1[md]")
            mix.append("[md]")
        else:
            mix.append("[m]")
        notes.append("music bed" + (" (ducked)" if voices else ""))
        n += 1
    elif voices:
        parts.append("[vkey]anullsink")

    hits = 0
    for item in plan.get("sfx") or []:
        p = resolve(item.get("path") or item.get("name"))
        if not p:
            continue
        times = [float(item["at"])] if item.get("at") is not None else changes
        gain = float(item.get("gain", 0.8))
        for t in times:
            inputs += ["-i", p]
            ms = int(max(0.0, t - float(item.get("lead", 0.0))) * 1000)
            parts.append(f"[{n}:a]aresample=48000,aformat=channel_layouts=stereo,volume={gain},"
                         f"adelay={ms}|{ms}[s{n}]")
            mix.append(f"[s{n}]")
            n += 1
            hits += 1
    if hits:
        notes.append(f"sfx hits x{hits}")
    if len(mix) == 1:
        parts.append(f"[base]anull[aout]")
        notes.append("silence")
    else:
        parts.append("".join(mix) + f"amix=inputs={len(mix)}:normalize=0:duration=first,"
                     f"loudnorm=I=-16:TP=-1.5:LRA=11,aresample=48000,atrim=0:{total:.3f}[aout]")
    return inputs, ";".join(parts), notes


# ---------------------------------------------------------------- public
def assemble(plan: dict, out: Path, audio: bool = True, base: Path | None = None) -> Path:
    """Render `plan` to `out` (H.264 + AAC, faststart). Writes `<out>.cuts.json` with the segment list."""
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    base = Path(base or Path.cwd())
    fps = int(plan.get("fps", 24))
    font = _font()
    with tempfile.TemporaryDirectory(prefix="sd-cut-") as td:
        work = Path(td)
        segs = plan_segments(plan, work, base)
        with ThreadPoolExecutor(max_workers=4) as ex:
            files = list(ex.map(lambda a: render_segment(a[1], a[0], plan, work, font), enumerate(segs)))
        lst = work / "list.txt"
        lst.write_text("".join(f"file '{f}'\n" for f in files))
        video = work / "video.mp4"
        _run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy",
              str(video)])
        total = sum(s.frames for s in segs) / fps
        if audio:
            inputs, graph, notes = _audio_graph(plan, segs, total, base)
        else:
            inputs, graph, notes = [], f"anullsrc=r=48000:cl=stereo,atrim=0:{total:.3f}[aout]", ["silence (--no-audio)"]
        _run(["ffmpeg", "-y", "-v", "error", "-i", str(video)] + inputs +
             ["-filter_complex", graph, "-map", "0:v", "-map", "[aout]", "-c:v", "copy", "-c:a", "aac",
              "-b:a", "192k", "-ar", "48000", "-t", f"{total:.3f}", "-movflags", "+faststart", str(out)])
    content = [s for s in segs if s.kind in ("clip", "still", "crt", "title")]
    report = {"duration": round(total, 3), "segments": len(segs), "shots": len(content),
              "cuts_per_minute": round(len(content) / total * 60, 1) if total else 0,
              "avg_shot_s": round(sum(s.frames for s in content) / max(1, len(content)) / fps, 3),
              "gated_segments": getattr(plan_segments, "gated", []),
              "bursts": sum(1 for s in segs if s.extra.get("burst")),
              "crt_inserts": sum(1 for s in segs if s.kind == "crt"),
              "longest_shot_s": round(max((s.frames for s in content), default=0) / fps, 3),
              "audio": notes, "timeline": [asdict(s) for s in segs]}
    Path(str(out) + ".cuts.json").write_text(json.dumps(report, indent=1))
    log.info("cut: %s shots, %.1fs, %s cuts/min, audio=%s", report["shots"], total,
             report["cuts_per_minute"], notes)
    return out


def contact_sheet(video: Path, out: Path, n: int = 12, cols: int = 4) -> Path:
    """`n` evenly spaced frames tiled into one PNG, for eyeballing the pacing."""
    p = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-count_packets", "-show_entries",
                        "stream=nb_read_packets", "-of", "csv=p=0", str(video)], capture_output=True, text=True)
    total = int(p.stdout.strip() or 0)
    rows = -(-n // cols)
    picks = sorted({int((i + 0.5) * total / n) for i in range(n)})
    sel = "+".join(f"eq(n\\,{k})" for k in picks)
    _run(["ffmpeg", "-y", "-v", "error", "-i", str(video), "-vf",
          f"select='{sel}',scale=400:-2,tile={cols}x{rows}:padding=4:color=black", "-fps_mode", "vfr",
          "-frames:v", "1", str(out)])
    return Path(out)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="shot-director-cut",
                                 description="Anime-paced cut of generated clips + stills (ffmpeg only).")
    ap.add_argument("--plan", required=True, type=Path, help="plan.json (paths relative to its folder)")
    ap.add_argument("--out", required=True, type=Path, help="output .mp4")
    ap.add_argument("--no-audio", action="store_true", help="ignore narration/music/sfx; silent track")
    ap.add_argument("--sheet", type=Path, help="also write a 12-frame contact sheet PNG")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    if not shutil.which("ffmpeg"):
        print("ffmpeg not found on PATH")
        return 2
    plan = json.loads(a.plan.read_text())
    out = assemble(plan, a.out, audio=not a.no_audio, base=a.plan.resolve().parent)
    rep = json.loads(Path(str(out) + ".cuts.json").read_text())
    print(f"{out}  {rep['duration']}s  shots={rep['shots']}  cuts/min={rep['cuts_per_minute']}  "
          f"audio={', '.join(rep['audio'])}")
    if a.sheet:
        print(contact_sheet(out, a.sheet))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
