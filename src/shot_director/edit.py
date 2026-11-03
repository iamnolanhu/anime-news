"""Anime-paced cut: turn generated clips + stills into a fast, captioned, scored edit with ffmpeg only.

`assemble(plan, out)` -> out. Rules (see docs/EDIT.md, tuned to docs/psyopanime-formula.md): every source
moment is on screen at most once (clip windows of one file never overlap, each still is used once, no
freeze frames). Every clip is chopped into up to 3 disjoint windows of 1.45-2.5 s (0.33 s bursts in action
beats, from still-unused source), stills become 0.9-1.3 s Ken Burns / shake cutaways, cold open = title over
the strongest frame, each scene opens on its strongest window, 2-frame white flash on beats, 1-frame black
on hard beats, every other scene opens on one of its own windows in a CRT monitor, one speed ramp per
scene, yellow-on-dark subtitles
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
CLIP_WIN_MIN = 1.45       # a clip holds as many disjoint windows of >= this as fit (5 s -> 3)
MAX_WINDOWS = 3
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
    """Worst of the first, middle and last frame of a segment (a smeared head or tail fails the gate)."""
    return min(sharpness(clip, start + min(0.08, src_dur / 4)), sharpness(clip, start + src_dur / 2),
               sharpness(clip, start + max(0.0, src_dur - 0.08)))


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
class SourceLedger:
    """Source seconds already on screen, per clip file. Every window of a file must be disjoint from the
    others, so no moment of a clip is shown twice."""

    def __init__(self) -> None:
        self.used: dict[str, list[tuple[float, float]]] = {}

    def take(self, src: str, a: float, b: float) -> None:
        self.used.setdefault(src, []).append((a, b))

    def release(self, src: str, a: float, b: float) -> None:
        spans = self.used.get(src, [])
        if (a, b) in spans:
            spans.remove((a, b))

    def free(self, src: str, dur: float) -> list[tuple[float, float]]:
        """Unused source intervals of `src` inside [0, dur - 0.05]."""
        out, t = [], 0.0
        for a, b in sorted(self.used.get(src, [])):
            if a > t + 1e-6:
                out.append((t, min(a, dur - 0.05)))
            t = max(t, b)
        if dur - 0.05 > t + 1e-6:
            out.append((t, dur - 0.05))
        return [(a, b) for a, b in out if b - a > 1e-6]

    def room_after(self, src: str, t: float, dur: float) -> float:
        """Free source seconds from `t` to the next used window (or the clip's end)."""
        nxt = [a for a, _ in self.used.get(src, []) if a >= t - 1e-6]
        return max(0.0, min([dur - 0.05] + nxt) - t)


def _windows(free: list[tuple[float, float]], fps: int, rng: random.Random, t_best: float,
             hold: float = 0.0) -> list[tuple[float, float]]:
    """Disjoint (start, length) windows inside the free source intervals. Each interval holds as many
    slots of at least CLIP_WIN_MIN as fit (a 5 s clip: 3 slots of about 1.65 s), at most MAX_WINDOWS
    spread over the clip; each window is CLIP_SEG long or its slot, whichever is shorter, in whole
    frames. The window whose slot holds the strongest frame snaps to it, inside its slot only.
    `hold` keeps a strip at the end of the longest interval free (burst shots in action beats)."""
    free = list(free)
    if hold and free:
        i = max(range(len(free)), key=lambda j: free[j][1] - free[j][0])
        if free[i][1] - free[i][0] > hold + CLIP_SEG_MIN:
            free[i] = (free[i][0], free[i][1] - hold)
    slots: list[tuple[float, float]] = []
    for a, b in free:
        n = int((b - a) // CLIP_WIN_MIN) or (1 if b - a >= CLIP_SEG_MIN else 0)
        slots += [(a + i * (b - a) / n, (b - a) / n) for i in range(n)]
    if not slots and free:   # a very short clip: one window over its longest free run
        a, b = max(free, key=lambda x: x[1] - x[0])
        if b - a >= 0.5:
            slots = [(a, b - a)]
    if len(slots) > MAX_WINDOWS:
        slots = [slots[round(j * (len(slots) - 1) / (MAX_WINDOWS - 1))] for j in range(MAX_WINDOWS)]
    out = []
    for j, (a, w) in enumerate(slots):
        n = max(1, int(min(rng.uniform(*CLIP_SEG), w) * fps + 1e-6))
        length = min(n / fps, w)
        frac = j / (len(slots) - 1) if len(slots) > 1 else 0.0
        out.append([a + (w - length) * frac, length, a, w])
    if out:
        near = min(out, key=lambda x: 0.0 if x[2] <= t_best <= x[2] + x[3] else abs(x[0] + x[1] / 2 - t_best))
        near[0] = min(max(near[2], t_best - 0.1), near[2] + near[3] - near[1])
    return [(st, length) for st, length, _, _ in out]


def _still_items(scene: dict, abs_path, resolve) -> list[tuple[str, str | None]]:
    """(resolved still path, clip it is the start frame of, or None). A still is a path or
    {"path": ..., "start_frame_of": "<clip path>"}."""
    out = []
    for item in scene.get("stills", []):
        path, of = (item.get("path"), item.get("start_frame_of")) if isinstance(item, dict) else (item, None)
        r = resolve(path) if path else None
        if r:
            out.append((r, abs_path(of) if of else None))
    return out


def plan_segments(plan: dict, workdir: Path, base: Path) -> list[Seg]:
    fps = int(plan.get("fps", 24))
    rng = random.Random(plan.get("seed", 7))

    def frames(sec: float) -> int:
        return max(1, round(sec * fps))

    def abs_path(p: str) -> str:
        pp = Path(p)
        return str(pp if pp.is_absolute() else base / pp)

    def resolve(p: str) -> str | None:
        return abs_path(p) if Path(abs_path(p)).exists() else None

    gated: list[dict] = []
    ledger = SourceLedger()
    used_stills: set[str] = set()
    title = Seg("title", frames(TITLE_FRAMES_S))
    segs: list[Seg] = [title]
    # profile every clip first: the cold open takes the episode's strongest moment before any scene does
    profiles: list[dict[str, tuple[float, float, float, float]]] = []   # clip -> (dur, t_best, score, median)
    for scene in plan.get("scenes", []):
        info: dict[str, tuple[float, float, float, float]] = {}
        for clip in (resolve(p) for p in scene.get("clips", [])):
            if clip and clip not in info:
                dur = _duration(clip)
                if dur > 0.5:
                    info[clip] = (dur, *clip_profile(clip, dur))
        profiles.append(info)
    best = max(((v[2], c, v[1], v[0]) for info in profiles for c, v in info.items()), default=None)
    span = title.frames / fps
    if best and best[3] - 0.05 >= span:   # cold open: title over the strongest frame in the whole episode
        _, clip, t, dur = best
        title.src, title.start = clip, min(max(0.0, t - 0.2), dur - 0.05 - span)
        title.extra["src_dur"] = span
        ledger.take(clip, title.start, title.start + span)
    for si, scene in enumerate(plan.get("scenes", [])):
        cap = scene.get("caption") or scene.get("beat") or ""
        action = bool(scene.get("action")) or any(w in str(scene.get("beat", "")).lower() for w in ACTION_WORDS)
        info = profiles[si]
        stills = _still_items(scene, abs_path, resolve)
        segs += [Seg("flash", 2, scene=si)]   # 2-frame white impact flash on every scene change
        clip_segs: list[tuple[float, Seg]] = []  # (sharpness, seg) that pass the gate
        candidates: list[tuple[float, Seg]] = []
        hold = BURST_FRAMES / fps + 0.05 if action else 0.0
        for clip, (dur, t_best, _score, med) in info.items():
            for start, length in _windows(ledger.free(clip, dur), fps, rng, t_best, hold):
                s = Seg("clip", round(length * fps), src=clip, start=start, caption=cap, scene=si)
                s.extra["src_dur"] = length
                sh = seg_sharpness(clip, start, length)
                s.extra["sharp"], s.extra["sharp_median"] = round(sh, 1), round(med, 1)
                s.extra["passes_gate"] = sh >= SHARP_GATE * med
                candidates.append((sh, s))
        # sharpness gate applies to every window, the opener included; drop smeared windows
        for sh, s in candidates:
            if s.extra["passes_gate"]:
                clip_segs.append((sh, s))
            else:
                gated.append({"src": s.src, "start": round(s.start, 2), "sharp": round(sh, 1),
                              "median": s.extra["sharp_median"]})
        if candidates and not clip_segs and stills:
            # nothing passes: the scene opens on its stills (always sharp); the smeared clip stays gated
            log.warning("scene %d: no clip window passes the sharpness gate; using stills only", si)
        elif candidates and not clip_segs:   # no stills either: a scene must show something, say so
            sh, s = max(candidates, key=lambda x: x[0])
            gated[:] = [g for g in gated if not (g["src"] == s.src and g["start"] == round(s.start, 2))]
            clip_segs.append((sh, s))
            s.extra["gate_exception"] = True
            log.warning("scene %d: no clip window passes the sharpness gate and no stills; opening on "
                        "the sharpest anyway (%s @ %.2fs, sharpness %.0f)", si, Path(s.src).name, s.start, sh)
        for _, s in clip_segs:   # these windows are now on screen: nothing else may show them
            ledger.take(s.src, s.start, s.start + s.extra["src_dur"])
        # a start frame is its clip's first moment: with clip footage on screen it would show it twice
        usable = list(dict.fromkeys(p for p, of in stills if p not in used_stills and not (clip_segs and of in info)))
        if not clip_segs and not usable:
            continue
        # the sharpest gate-passing window opens the scene; the rest round-robin across the clips
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
            speed = 1.4 if si % 2 == 0 else 0.7
            dur = info[ramp.src][0]
            # keep the gated window's start (moving it back could pull in smeared frames); a speed-up
            # may only run on into unused source, else it shortens the shot instead
            ledger.release(ramp.src, ramp.start, ramp.start + ramp.extra["src_dur"])
            src_dur = min(ramp.frames / fps / speed, ledger.room_after(ramp.src, ramp.start, dur))
            n = int(src_dur * speed * fps + 1e-6)
            if n / fps >= 0.6:
                ramp.speed, ramp.frames = speed, n
                ramp.extra["src_dur"] = n / fps / speed
                ramp.extra["ramp"] = True
            ledger.take(ramp.src, ramp.start, ramp.start + ramp.extra["src_dur"])
        # CRT-monitor transition into every other scene: one of the scene's own windows gets the CRT
        # treatment (never the opener, the ramp or the closing shot), so it adds no repeated footage
        if si % 2 == 1:
            pick = [s for s in ordered[1:-1] if not s.extra.get("ramp")]
            if pick:
                c = pick[0]
                ledger.release(c.src, c.start, c.start + c.extra["src_dur"])
                c.kind, c.motion = "crt", ""
                c.frames = min(c.frames, frames(rng.uniform(*CRT_SEG)))
                c.extra["src_dur"] = c.frames / fps
                ledger.take(c.src, c.start, c.start + c.extra["src_dur"])
        motions = ["push", "shake", "pull"]
        body: list[Seg] = []
        n_still = 0

        def still_seg() -> Seg:
            nonlocal n_still
            n_still += 1
            src = usable.pop(0)
            used_stills.add(src)   # every still is on screen at most once in the whole cut
            return Seg("still", frames(rng.uniform(*STILL_SEG)), src=src,
                       motion=motions[(n_still - 1 + si) % 3], caption=cap, scene=si)

        for idx, cs in enumerate(ordered):
            body.append(cs)
            # alternate clip / still / clip: a cutaway after clip segments 0, 2, 4 ...
            if idx % 2 == 0 and idx < len(ordered) - 1 and usable:
                body.append(still_seg())
        # the leftover stills go before the last clip segment (or after the only one); a scene
        # without usable clip windows becomes a montage of its stills
        while usable:
            pos = len(body) - 1 if len(ordered) > 1 else len(body)
            body.insert(pos, still_seg())
        # action beats: a burst of 0.33 s shots from still-unused parts of the clips, before the last shot
        if action and ordered:
            burst = []
            bl = BURST_FRAMES / fps
            srcs = list(dict.fromkeys(s.src for s in ordered))
            for b in range(BURST_COUNT):
                src = srcs[b % len(srcs)]
                dur, _, _, med = info[src]
                spans = [(a, e) for a, e in ledger.free(src, dur) if e - a >= bl]
                st = None
                for _ in range(5):   # sharpness gate for burst shots as well; skip the shot if nothing passes
                    if not spans:
                        break
                    a, e = rng.choice(spans)
                    cand = rng.uniform(a, e - bl)
                    if seg_sharpness(src, cand, bl) >= SHARP_GATE * med:
                        st = cand
                        break
                if st is None:
                    continue
                ledger.take(src, st, st + bl)
                burst.append(Seg("clip", BURST_FRAMES, src=src, start=st, caption=cap, scene=si,
                                 extra={"src_dur": bl, "burst": True}))
            body = body[:-1] + burst + body[-1:] if len(body) > 1 else body + burst
        crt = next((s for s in body if s.kind == "crt"), None)
        if crt:   # the CRT shot is the transition: it plays first
            body.remove(crt)
            body.insert(0, crt)
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
    segs += [Seg("flash", 2), Seg("end", frames(END_FRAMES_S))]
    t = 0.0
    plan_segments.gated = gated
    for s in segs:
        s.at = round(t, 4)
        t += s.frames / fps
    return segs


def reuse_report(timeline: list[dict], fps: int) -> dict:
    """Recount reuse from the final timeline (not the planner's ledger): clip footage windows of the
    same file overlapping by more than one frame, and stills shown more than once."""
    spans: dict[str, list[tuple[float, float]]] = {}
    stills: list[str] = []
    for s in timeline:
        if s["kind"] in ("clip", "crt", "title") and s["src"]:
            shown = s["extra"]["src_dur"] if s["extra"].get("ramp") else s["frames"] / fps / s["speed"]
            spans.setdefault(s["src"], []).append((s["start"], s["start"] + shown))
        elif s["kind"] == "still":
            stills.append(s["src"])
    overlaps = sum(1 for iv in spans.values() for i in range(len(iv)) for j in range(i + 1, len(iv))
                   if min(iv[i][1], iv[j][1]) - max(iv[i][0], iv[j][0]) > 1 / fps)
    return {"overlapping_windows": overlaps, "still_repeats": len(stills) - len(set(stills)),
            "unique_sources": len(spans) + len(set(stills)),
            "source_seconds_used": round(sum(b - a for iv in spans.values() for a, b in iv), 2)}


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
    report["reuse"] = reuse_report(report["timeline"], fps)
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
