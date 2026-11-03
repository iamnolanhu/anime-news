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
import math
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
END_CARD_TEXT = os.environ.get("SHOT_DIRECTOR_END_CARD", "ANIME NEWS · open source · #OpenSourceHiggsfield")
WORDMARK = os.environ.get("SHOT_DIRECTOR_WORDMARK", "ANIME NEWS")
# Brand mark drawn before the wordmark (~28 px at 720p; an-mark-small.png has strokes weighted for small sizes). Optional: a missing file or an empty
# SHOT_DIRECTOR_WORDMARK_MARK skips the mark and the cut still renders.
WORDMARK_MARK = os.environ.get("SHOT_DIRECTOR_WORDMARK_MARK",
                               str(Path(__file__).parent / "static" / "brand" / "an-mark-small.png"))
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
SHARP_FLOOR = 250.0        # and never below this absolute Laplacian variance (smeared frames measured 70-780, sharp 1800+)
TALK_WIN = (1.6, 3.0)      # a talk take is shown nearly whole, in 2-3 abutting windows of this length
TALK_PUNCH = 1.3           # punch-in crop on alternate talk windows
COUNTDOWN_RED = "0xFF2A2A"
BED_VOLUME = 0.8
ACTION_WORDS = ("action", "chase", "fight", "escalat", "montage", "burst", "battle", "attack", "clash", "run")
FONT_CANDIDATES = [
    os.environ.get("SHOT_DIRECTOR_FONT", ""),
    "/System/Library/Fonts/Supplemental/Arial Black.ttf",
    "/System/Library/Fonts/Supplemental/Impact.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
]
MONO_FONT_CANDIDATES = [
    os.environ.get("SHOT_DIRECTOR_MONO_FONT", ""),
    "/System/Library/Fonts/Menlo.ttc",
    "/System/Library/Fonts/Supplemental/Courier New Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf",
    "/usr/share/fonts/dejavu/DejaVuSansMono-Bold.ttf",
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


def _font(candidates: list[str] = FONT_CANDIDATES) -> str:
    for f in candidates:
        if f and Path(f).exists():
            return f
    return ""


_AUDIO_CACHE: dict[str, bool] = {}


def _has_audio(path: str) -> bool:
    """True when the file has an audio stream (mock FLUX clips have none)."""
    if path not in _AUDIO_CACHE:
        p = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=index",
                            "-of", "csv=p=0", path], capture_output=True, text=True)
        _AUDIO_CACHE[path] = bool(p.stdout.strip())
    return _AUDIO_CACHE[path]


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


def _wrap_caption(text: str, width: int = 30) -> str:
    text = " ".join(text.split())
    if len(text) > 60:
        text = text[:59].rstrip() + "…"
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


def _talk_cuts(total: int, line: str, fps: int) -> list[int]:
    """Frame counts of the 2-3 abutting windows of a talk take (`total` frames). Cuts fall on a clause
    boundary of the line (mapped by word count) when one lies within 0.6 s of the even split and keeps every
    window inside TALK_WIN; otherwise on the even split."""
    length = total / fps
    if length < 2 * TALK_WIN[0]:
        return [total]
    k = min(3, max(2, math.ceil(length / TALK_WIN[1])))
    words = [len(c.split()) for c in split_clauses(line)] if line else []
    marks = [length * sum(words[:i]) / max(1, sum(words)) for i in range(1, len(words))]
    bounds = [0]
    for j in range(1, k):
        ideal, prev = length * j / k, bounds[-1] / fps
        lo = max(prev + TALK_WIN[0], length - TALK_WIN[1] * (k - j))
        hi = min(prev + TALK_WIN[1], length - TALK_WIN[0] * (k - j))
        ok = [m for m in marks if lo <= m <= hi and abs(m - ideal) <= 0.6]
        bounds.append(round((min(ok, key=lambda m: abs(m - ideal)) if ok else ideal) * fps))
    bounds.append(total)
    return [b - a for a, b in zip(bounds, bounds[1:])]


def _talk_segments(clip: str, dur: float, line: str, fps: int, si: int) -> list[Seg]:
    """A talk take, nearly whole (0.15 s to the end - 0.1 s), as abutting windows: wide, punch-in, wide.
    Captions are the line's clauses timed by word count across the take, as cues per window."""
    a0 = 0.15
    total = int((dur - 0.1 - a0) * fps + 1e-6)
    segs, f0 = [], 0
    for j, n in enumerate(_talk_cuts(total, line, fps)):
        s = Seg("clip", n, src=clip, start=a0 + f0 / fps, scene=si, motion="talk_punch" if j % 2 else "")
        s.extra.update(src_dur=n / fps, talk=True)
        segs.append(s)
        f0 += n
    clauses = split_clauses(line) if line else []
    words = [max(1, len(c.split())) for c in clauses]
    span, acc, cues = f0 / fps, 0, []
    for c, w in zip(clauses, words):
        cues.append((c, span * acc / sum(words), span * (acc + w) / sum(words)))
        acc += w
    off = 0.0
    for s in segs:
        d = s.frames / fps
        s.extra["cues"] = [(c, round(max(0.0, a - off), 3), round(min(d, b - off), 3))
                           for c, a, b in cues if b > off + 1e-6 and a < off + d - 1e-6]
        s.caption = s.extra["cues"][0][0] if s.extra["cues"] else ""
        off += d
    return segs


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
        for clip in ([] if scene.get("talk") else (resolve(p) for p in scene.get("clips", []))):
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
        cap = scene["caption"] if "caption" in scene else (scene.get("beat") or "")  # "" = no subtitles
        action = bool(scene.get("action")) or any(w in str(scene.get("beat", "")).lower() for w in ACTION_WORDS)
        info = profiles[si]
        if scene.get("talk"):   # one continuous take with its own dialogue: no ramp, burst, CRT, black or stills
            clip = next((c for c in (resolve(p) for p in scene.get("clips", [])) if c), None)
            dur = _duration(clip) if clip else 0.0
            if dur <= 0.5:
                log.warning("scene %d: talk scene without a usable clip, skipped", si)
                continue
            talk = _talk_segments(clip, dur, scene.get("line") or cap, fps, si)
            for s in talk:
                ledger.take(s.src, s.start, s.start + s.extra["src_dur"])
            segs += [Seg("flash", 2, scene=si)] + talk
            continue
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
                s.extra["passes_gate"] = sh >= max(SHARP_GATE * med, SHARP_FLOOR)
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
        elif candidates and not clip_segs:
            # absolute gate (Nolan 2026-11-02): no window passes and no stills -> the scene is skipped, never a smeared opener
            log.warning("scene %d: no clip window passes the sharpness gate and no stills; scene skipped", si)
            continue
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
    after = plan.get("title_after_scene")
    if after is not None:   # cold open first, the title lands on the drop right after scene `after`
        idx = max((i for i, s in enumerate(segs) if 0 <= s.scene <= after), default=None)
        if idx is not None:
            segs.remove(title)
            segs.insert(idx, title)
    segs += [Seg("flash", 2), Seg("end", frames(END_FRAMES_S))]
    t = 0.0
    plan_segments.gated = gated
    for s in segs:
        s.at = round(t, 4)
        t += s.frames / fps
    cd = plan.get("countdown")
    for si, scene in enumerate(plan.get("scenes", []) if cd else []):
        body = [s for s in segs if s.scene == si and s.kind != "flash"]
        if scene.get("beat") != cd.get("scene_beat") or scene.get("talk") or not body:
            continue
        total = sum(s.frames for s in body) / fps
        for s in body:
            if s.kind in ("clip", "still", "crt"):
                s.extra["countdown"] = {"label": cd.get("label", ""), "total": round(total, 3),
                                        "offset": round(s.at - body[0].at, 4)}
        break
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
    size = max(26, H // 18)
    fontopt = f"fontfile={_q(font)}:" if font else ""
    style_ = (f":expansion=none:fontsize={size}:fontcolor={GOLD}:box=1:boxcolor=black@0.62:boxborderw={size // 3}"
              f":line_spacing={size // 5}:x=(w-text_w)/2:y=h-text_h-{H // 11}")
    if s.extra.get("cues"):   # talk take: each clause on screen for its share of the spoken line
        out = ""
        for k, (text, a, b) in enumerate(s.extra["cues"]):
            tf = workdir / f"cap-{idx}-{k}.txt"
            tf.write_text(_wrap_caption(text))
            alpha = f":alpha='min(1,(t-{a:.3f})*10)'" if a > 0.001 else ""
            out += f",drawtext={fontopt}textfile={_q(tf)}{style_}:enable='between(t,{a:.3f},{b:.3f})'{alpha}"
        return out
    if not s.caption:
        return ""
    tf = workdir / f"cap-{idx}.txt"
    tf.write_text(_wrap_caption(s.caption))
    alpha = ":alpha='min(1,t*10)'" if s.pop else ""
    return f",drawtext={fontopt}textfile={_q(tf)}{style_}{alpha}"


def _countdown_filter(s: Seg, workdir: Path, idx: int, H: int) -> str:
    """Big red digital countdown top-right: label above, 00:SS counting down to 00:00 over the scene;
    the final second flashes white."""
    cd = s.extra.get("countdown")
    if not cd:
        return ""
    mono = _font(MONO_FONT_CANDIDATES) or _font()
    fontopt = f"fontfile={_q(mono)}:" if mono else ""
    rem = f"({cd['total']:.3f}-{cd['offset']:.4f}-t)"
    digits, label = workdir / f"cd-{idx}.txt", workdir / f"cdl-{idx}.txt"
    digits.write_text("00:%{eif:max(0,floor(" + rem + ")):d:2}")
    label.write_text(cd["label"][:28])
    size, lsize, m = H // 9, max(16, H // 34), H // 24
    box = f":box=1:boxcolor=black@0.72:boxborderw={max(6, size // 6)}"
    red = f"gte({rem},1)+lt({rem},1)*gte(mod(t*6,1),0.5)"
    white = f"lt({rem},1)*lt(mod(t*6,1),0.5)"
    out = ""
    if cd["label"]:
        out += (f",drawtext={fontopt}textfile={_q(label)}:expansion=none:fontsize={lsize}:fontcolor={COUNTDOWN_RED}"
                f":box=1:boxcolor=black@0.72:boxborderw=6:x=w-text_w-{m}:y={m}")
    for color, enable in ((COUNTDOWN_RED, red), ("white", white)):
        out += (f",drawtext={fontopt}textfile={_q(digits)}:expansion=normal:fontsize={size}:fontcolor={color}"
                f"{box}:x=w-text_w-{m}:y={m + lsize + 18}:enable='{enable}'")
    return out


def _wordmark(font: str, workdir: Path, H: int) -> str:
    if not WORDMARK:
        return ""
    tf = workdir / "wordmark.txt"
    if not tf.exists():
        tf.write_text(WORDMARK)
    fontopt = f"fontfile={_q(font)}:" if font else ""
    size, pad = max(14, H // 40), H // 30
    x = pad
    mark = ""
    if WORDMARK_MARK and Path(WORDMARK_MARK).is_file():
        m = max(16, H * 28 // 720)
        x = pad + m + max(4, m // 4)
        # the PNG is one frame; overlay repeats it for the whole segment (eof_action defaults to repeat)
        mark = (f"[wmbase];movie={_q(WORDMARK_MARK)},scale={m}:{m}:flags=lanczos,format=rgba[wmmark];"
                f"[wmbase][wmmark]overlay=x={pad}:y={max(0, pad + size // 2 - m // 2)}:format=auto")
    return (f",drawtext={fontopt}textfile={_q(tf)}:expansion=none:fontsize={size}:fontcolor={GOLD}"
            f":shadowcolor=black@0.8:shadowx=2:shadowy=2:x={x}:y={pad}" + mark)


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
    cap = _caption_filter(s, font, workdir, idx, H) + _wordmark(font, workdir, H) + _countdown_filter(s, workdir, idx, H)
    if s.kind == "clip":
        src_dur = s.extra.get("src_dur", s.frames / fps / s.speed)
        vf = f"{fit},setpts={s.speed}*(PTS-STARTPTS),fps={fps}"
        if s.motion == "punch":   # quick push-in on the opener
            vf += f",zoompan=z='min(1+0.006*on,1.18)':d=1:x='iw/2-iw/zoom/2':y='ih/2-ih/zoom/2':s={W}x{H}:fps={fps}"
        elif s.motion == "talk_punch":   # tighter framing on the speaker: centred, upper third (the face)
            vf += (f",scale=trunc(iw*{TALK_PUNCH}/2)*2:trunc(ih*{TALK_PUNCH}/2)*2"
                   f",crop={W}:{H}:'(iw-ow)/2':'max(0,min(ih-oh,ih/3-oh/2))'")
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
            title = _wrap_caption(plan.get("title", "Anime News"), 28)
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
INTRO_HIT_FROM_END = 0.55   # music kit riser: its impact sits 0.55 s before the end


class _Bus:
    """One audio bus rendered by its own ffmpeg run: many delayed file inputs in one graph with the other
    buses ended the whole mix early (ffmpeg 7.1), so each bus is a WAV stem of exactly the cut's length."""

    def __init__(self) -> None:
        self.args: list[str] = []
        self.parts: list[str] = []
        self.outs: list[str] = []

    def add(self, args: list[str], chain: str) -> None:
        i = self.args.count("-i")
        self.args += args
        self.parts.append(f"[{i}:a]{chain}[a{i}]")
        self.outs.append(f"[a{i}]")

    def render(self, out: Path, total: float, post: str = "", extra: list[str] | None = None) -> Path | None:
        """Mix the bus to `out`; `post` is a filter chain from [bus] to [out] (default: pad/trim only)."""
        if not self.outs:
            return None
        mixed = "".join(self.outs) + (f"amix=inputs={len(self.outs)}:normalize=0:duration=longest" if len(self.outs) > 1
                                      else "anull")
        graph = ";".join(self.parts + [mixed + "[bus]", post or f"[bus]apad,atrim=0:{total:.3f}[out]"])
        _run(["ffmpeg", "-y", "-v", "error"] + self.args + (extra or []) + ["-filter_complex", graph, "-map", "[out]",
             "-c:a", "pcm_f32le", "-ar", "48000", str(out)])
        got = _duration(out)
        if got < total - 0.1:
            raise RuntimeError(f"audio bus {out.name} rendered {got:.2f}s of {total:.2f}s")
        return out


def _line_wav(src: str, out: Path, start: float = 0.0, dur: float | None = None) -> Path:
    """One voice line loudnorm'd (-16 LUFS) to its own WAV, optionally a source span padded to exactly `dur`.
    Done per line in its own run: loudnorm inside the multi-input bus graph truncated the bus at the third line."""
    span = f"atrim=start={start:.4f}:duration={dur:.4f},asetpts=PTS-STARTPTS," if dur else ""
    pad = f",apad,atrim=0:{dur:.4f}" if dur else ""
    _run(["ffmpeg", "-y", "-v", "error", "-i", src, "-af",
          f"{span}aresample=48000,aformat=channel_layouts=stereo,loudnorm=I=-16:TP=-2,aresample=48000{pad}",
          "-c:a", "pcm_f32le", str(out)])
    return out


def _audio_graph(plan: dict, segs: list[Seg], total: float, base: Path,
                 work: Path) -> tuple[list[str], str, list[str]]:
    """Renders the buses to WAV stems in `work` and returns (ffmpeg input args, filter_complex, notes) for the
    final mix; input 0 is the video. Buses: voice (narration + the talk takes' own dialogue; the music ducks
    under it), music (optional intro riser from 0, the bed from the title drop, continuous to the end), SFX
    (hits + action clips' own sound at `clip_audio_gain`). Final loudnorm I=-14 TP=-1."""
    def resolve(p) -> str | None:
        if not p:
            return None
        pp = Path(p)
        pp = pp if pp.is_absolute() else base / pp
        return str(pp) if pp.exists() and pp.stat().st_size > 0 else None

    fps = int(plan.get("fps", 24))
    stereo = "aresample=48000,aformat=channel_layouts=stereo"
    notes: list[str] = []
    scene_start = {}
    for s in segs:
        if s.scene >= 0 and s.kind not in ("flash", "black") and s.scene not in scene_start:
            scene_start[s.scene] = s.at
    changes = [s.at for s in segs if s.kind == "flash"]
    title = next((s for s in segs if s.kind == "title"), None)

    voice = _Bus()
    items = list(plan.get("narration") or [])
    for si, sc in enumerate(plan.get("scenes", [])):
        if sc.get("narration_audio"):
            items.append({"path": sc["narration_audio"], "scene": si})
    for item in items:
        p = resolve(item.get("path"))
        if not p:
            continue
        sc = item.get("scene", -1)
        if sc is not None and sc >= 0 and sc not in scene_start:
            continue  # the scene was skipped by the gate: its narration is dropped, never played over another scene
        at = float(item["at"]) if item.get("at") is not None else (
            0.15 if sc is None or sc < 0 else scene_start.get(sc, 0.0) + 0.1)
        ms = int(at * 1000)
        line = _line_wav(p, work / f"line-n{len(voice.outs)}.wav")
        voice.add(["-i", str(line)], f"{stereo},adelay={ms}|{ms}")
    n_narr = len(voice.outs)
    # dialogue: each talk take's own sound for exactly the source span on screen, one line per take
    talks: dict[int, list[Seg]] = {}
    for s in segs:
        if s.extra.get("talk"):
            talks.setdefault(s.scene, []).append(s)
    for ws in talks.values():
        if not _has_audio(ws[0].src):
            continue
        span, ms = sum(w.frames for w in ws) / fps, int(ws[0].at * 1000)
        line = _line_wav(ws[0].src, work / f"line-d{len(voice.outs)}.wav", ws[0].start, span)
        voice.add(["-i", str(line)], f"{stereo},adelay={ms}|{ms}")
    voice_wav = voice.render(work / "bus-voice.wav", total)
    if n_narr:
        notes.append(f"narration x{n_narr}")
    if len(voice.outs) > n_narr:
        notes.append(f"dialogue bus x{len(voice.outs) - n_narr}")

    music_bus = _Bus()
    music = resolve(plan.get("music"))
    intro = resolve(plan.get("music_intro"))
    music_wav = None
    if music:
        drop = title.at if title and title.at > 0 else 0.0
        bed_at = drop if intro and drop > 0 else 0.0
        if bed_at > 0:
            # the riser's hit (INTRO_HIT_FROM_END before its end) lands on the drop: a cold open shorter than
            # the riser skips the riser's head, a longer one starts the riser late; the bed takes over on the drop
            offset = bed_at - max(0.0, _duration(intro) - INTRO_HIT_FROM_END)
            head = f"atrim=start={-offset:.3f},asetpts=PTS-STARTPTS," if offset < 0 else ""
            delay = f",adelay={int(offset * 1000)}:all=1" if offset > 0 else ""
            music_bus.add(["-i", intro], f"{stereo},{head}volume={BED_VOLUME}{delay},atrim=0:{bed_at + 0.15:.3f},"
                                         f"afade=t=out:st={bed_at:.3f}:d=0.15")
        length, ms = total - bed_at, int(bed_at * 1000)
        music_bus.add(["-stream_loop", "-1", "-i", music],
                      # timestamps from the sample count: a looped mp3's own timestamps run ahead of its
                      # samples, so trimming by them ended the bed ~0.6 s early
                      f"{stereo},asetpts=N/SR/TB,atrim=0:{length:.3f},volume={BED_VOLUME},"
                      f"afade=t=in:d={0.15 if bed_at > 0 else 0.4},afade=t=out:st={max(0.0, length - 1.2):.3f}:d=1.2,"
                      f"adelay={ms}|{ms}")
        if voice_wav:   # duck the bed under dialogue and narration (never under SFX)
            k = music_bus.args.count("-i")
            # the key is padded past the end: sidechaincompress stops ~0.6 s before its key does
            post = (f"[bus]apad,atrim=0:{total:.3f}[m];[{k}:a]{stereo},apad=whole_dur={total + 2:.3f}[key];"
                    f"[m][key]sidechaincompress=threshold=0.03:ratio=10:attack=20:release=400:makeup=1,"
                    f"apad,atrim=0:{total:.3f}[out]")
            music_wav = music_bus.render(work / "bus-music.wav", total, post, ["-i", str(voice_wav)])
        else:
            music_wav = music_bus.render(work / "bus-music.wav", total)
        notes.append(("intro riser + " if bed_at > 0 else "") + "music bed" + (" (ducked)" if voice_wav else ""))

    fx = _Bus()
    title_hit = [title.at] if title and plan.get("title_after_scene") is not None and title.at > 0 else []
    for item in plan.get("sfx") or []:
        p = resolve(item.get("path") or item.get("name"))
        if not p:
            continue
        times = [float(item["at"])] if item.get("at") is not None else changes + title_hit
        gain = float(item.get("gain", 0.8))
        for t in times:
            ms = int(max(0.0, t - float(item.get("lead", 0.0))) * 1000)
            fx.add(["-i", p], f"{stereo},volume={gain},adelay={ms}|{ms}")
    hits = len(fx.outs)
    if hits:
        notes.append(f"sfx hits x{hits}")
    # action scenes: each shown window's own sound (same source span as the picture) on the SFX bus
    for si, sc in enumerate(plan.get("scenes", [])):
        gain = float(sc.get("clip_audio_gain") or 0)
        if gain <= 0 or sc.get("talk"):
            continue
        for s in segs:
            if s.scene != si or s.kind not in ("clip", "crt") or not s.src or not _has_audio(s.src):
                continue
            span, out_s, ms = s.extra.get("src_dur", s.frames / fps / s.speed), s.frames / fps, int(s.at * 1000)
            tempo = f",atempo={1 / s.speed:.4f}" if s.speed != 1.0 else ""
            fx.add(["-i", s.src], f"atrim=start={s.start:.4f}:duration={span:.4f},asetpts=PTS-STARTPTS,{stereo}{tempo},"
                                  f"apad,atrim=0:{out_s:.4f},volume={gain},adelay={ms}|{ms}")
    if len(fx.outs) > hits:
        notes.append(f"clip audio x{len(fx.outs) - hits}")
    fx_wav = fx.render(work / "bus-fx.wav", total)

    stems = [w for w in (voice_wav, music_wav, fx_wav) if w]
    if not stems:
        notes.append("silence")
        return [], f"anullsrc=r=48000:cl=stereo,atrim=0:{total:.3f}[aout]", notes
    inputs = [a for w in stems for a in ("-i", str(w))]
    graph = ("".join(f"[{i + 1}:a]" for i in range(len(stems)))
             + f"amix=inputs={len(stems)}:normalize=0:duration=longest,"
             f"loudnorm=I=-14:TP=-1.0:LRA=11,aresample=48000,apad,atrim=0:{total:.3f}[aout]")
    return inputs, graph, notes


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
            inputs, graph, notes = _audio_graph(plan, segs, total, base, work)
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
    title = next((s for s in segs if s.kind == "title"), None)
    report["title_at"] = title.at if title else None
    cd = next((s for s in segs if s.extra.get("countdown")), None)
    report["countdown"] = ({"scene": cd.scene, "label": cd.extra["countdown"]["label"],
                            "seconds": cd.extra["countdown"]["total"]} if cd else None)
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
