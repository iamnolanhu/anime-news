"""Anime-paced cut: turn generated clips + stills into a captioned, scored edit with ffmpeg only.

`assemble(plan, out)` -> out. The grammar (docs/EDIT.md; the art-director review of 2026-11-03 and the PsyopAnime
cut log): every edit scene is one storyboard scene with its shots in storyboard order; a clip shows each of its
angles (the spans between its own internal hard cuts) once, in source order, so a scene never returns to a clip,
never runs backwards and never hides a cut inside a window. Action runs open on an establishing shot, cut short
(0.8 s) and end on a hold; talk takes hold one framing and lend the rest of their line to the next shots (L/J cuts
on a clause boundary); narration plays in full and the picture grows to fit it. Hard cuts only: no flashes, black
frames, speed ramps, punch-ins or title card. Subtitles follow the voice; a countdown when the plan asks for one;
the series wordmark top-left; a 2.5 s end card. Audio: voices, a ducked music bed, SFX hits on storyboard scene
changes, loudnorm. No generation happens here; audio.py makes the sound files.
"""
from __future__ import annotations

import argparse
import itertools
import json
import logging
import math
import os
import re
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
WORDMARK = os.environ.get("SHOT_DIRECTOR_WORDMARK")   # unset: the plan's series title (else "ANIME NEWS"); "" = none
# Brand mark drawn before the wordmark (~28 px at 720p; an-mark-small.png has strokes weighted for small sizes). Optional: a missing file or an empty
# SHOT_DIRECTOR_WORDMARK_MARK skips the mark and the cut still renders.
WORDMARK_MARK = os.environ.get("SHOT_DIRECTOR_WORDMARK_MARK",
                               str(Path(__file__).parent / "static" / "brand" / "an-mark-small.png"))
END_FRAMES_S = 2.5         # the end card (PsyopAnime: the logo card holds the last 2.5 s; no opening title card)
ACTION_OPEN = 1.5          # the scene's opening action shot establishes (PsyopAnime hooks open on 1.9-2.1 s shots)
ACTION_SHORT = 0.8         # a shot inside an action run (PsyopAnime action shots 0.6-1.0 s, median 0.64 s)
ACTION_HOLD = 2.0          # the run's last shot holds and resets the beat (1.5-2.5 s)
BURST_MIN = 3              # this many consecutive short shots of distinct compositions, from 2+ clips, are a burst
STILL_HOLD = 1.9           # a still holds (PsyopAnime stills average 1.9 s), with one Ken Burns move
STILL_MAX = 6.0            # ... and up to this long when a line needs the time
EXTEND_SOFT = 2.5          # a line that needs more picture first grows every shot of its run up to this
TALK_HOLD_MAX = 4.0        # a talk take longer than this lends part of its line to the next (or previous) shots
TALK_HOLD_MIN = 2.0        # ... but holds at least this long on the speaker
TALK_HOLD_TARGET = 2.5     # ... aiming at this (PsyopAnime talk shots hold 2-3 s)
SHARP_GATE = 0.4           # skip clip windows whose sharpness < 40 % of the angle's median
SHARP_FLOOR = 250.0        # and never below this absolute Laplacian variance (smeared frames measured 70-780, sharp 1800+)
SCENE_CUT = 0.25           # ffmpeg scene score of a clip's internal hard cut (a Seedance "Hard cut to:" second angle)
ANGLE_MIN_S = 0.5          # a piece of an angle shorter than 12 frames is never shown
MOTION_FRAMES = 12         # motion energy of a window = mean frame difference over its first 12 frames
COUNTDOWN_RED = "0xFF2A2A"
CLOCK = re.compile(r"\d+:\d\d")
BED_VOLUME = 0.8
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


class Source:
    """One clip, decoded once at 160x90 grayscale. Per frame: sharpness (Laplacian variance, computed on demand;
    motion smear -> low value) and motion (mean absolute difference to the previous frame). Plus the clip's
    internal hard cuts (Seedance action clips carry a "Hard cut to:" second angle) and the angles between them."""
    W, H = 160, 90

    def __init__(self, path: str) -> None:
        self.path = path
        self.dur = _duration(path)
        p = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=r_frame_rate",
                            "-of", "csv=p=0", path], capture_output=True, text=True)
        num, _, den = (p.stdout.strip() or "24/1").partition("/")
        self.fps = float(num) / float(den or 1) if float(num or 0) > 0 else 24.0
        raw = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-vf", f"scale={self.W}:{self.H},format=gray",
                              "-f", "rawvideo", "-"], capture_output=True).stdout
        n = self.W * self.H
        self.frames = [raw[i:i + n] for i in range(0, len(raw) - n + 1, n)]
        self._sharp: dict[int, float] = {}
        self._gates: dict[tuple[float, float], float] = {}
        self.motion = [0.0] + [sum(abs(a[k] - b[k]) for k in range(0, n, 4)) / (n / 4)
                               for a, b in zip(self.frames, self.frames[1:])]
        self.cuts = detect_cuts(path, self.dur)
        lo, hi = 0.15, max(0.2, self.dur - 0.15)
        ts = sorted(self.sharp(lo + (hi - lo) * i / 9) for i in range(10))
        self.median = ts[len(ts) // 2]
        self.gate = max(SHARP_GATE * self.median, SHARP_FLOOR)

    def index(self, t: float) -> int:
        return min(len(self.frames) - 1, max(0, int(t * self.fps + 0.5)))

    def sharp(self, t: float) -> float:
        if not self.frames:
            return 0.0
        i = self.index(t)
        if i not in self._sharp:
            px, w = self.frames[i], self.W
            vals = [px[k - 1] + px[k + 1] + px[k - w] + px[k + w] - 4 * px[k]
                    for y in range(1, self.H - 1) for k in range(y * w + 1, y * w + w - 1)]
            m = sum(vals) / len(vals)
            self._sharp[i] = sum((a - m) ** 2 for a in vals) / len(vals)
        return self._sharp[i]

    def seg_sharp(self, start: float, src_dur: float) -> float:
        """Worst of the first, middle and last frame of a segment (a smeared head or tail fails the gate)."""
        return min(self.sharp(start + min(0.08, src_dur / 4)), self.sharp(start + src_dur / 2),
                   self.sharp(start + max(0.0, src_dur - 0.08)))

    def motion_at(self, t: float, n: int = MOTION_FRAMES) -> float:
        i = self.index(t)
        part = self.motion[i + 1:i + 1 + n]
        return sum(part) / len(part) if part else 0.0

    def gate_at(self, t: float) -> float:
        """The sharpness gate of the angle holding `t`: each angle is its own shot (a soft close-up next to a sharp
        wide), so the bar is SHARP_GATE x that angle's median (10 samples), never below SHARP_FLOOR."""
        a, b = next(((a, b) for a, b in self.angles() if a - 1e-6 <= t <= b + 1e-6), (0.0, self.dur))
        if (a, b) not in self._gates:
            ts = sorted(self.sharp(a + (b - a) * (0.05 + 0.9 * i / 9)) for i in range(10))
            self._gates[(a, b)] = max(SHARP_GATE * ts[len(ts) // 2], SHARP_FLOOR)
        return self._gates[(a, b)]

    def angles(self) -> list[tuple[float, float]]:
        """The continuous spans between internal cuts, each at least ANGLE_MIN_S long. A cut zone (one cut or
        a cluster of changes) is excluded with one frame of margin on each side."""
        out, t, f = [], 0.0, 1 / self.fps
        for a, b in self.cuts + [(self.dur - 0.05 + f, self.dur)]:
            if a - f - t >= ANGLE_MIN_S:
                out.append((t, a - f))
            t = b + f
        return out


_SOURCES: dict[str, Source] = {}


def source(path: str) -> Source:
    if path not in _SOURCES:
        _SOURCES[path] = Source(path)
    return _SOURCES[path]


def detect_cuts(path: str, dur: float) -> list[tuple[float, float]]:
    """Internal hard cuts of a clip: frames whose ffmpeg scene score exceeds SCENE_CUT, ignoring the first and
    last 0.2 s (encoder artefacts); changes closer than 0.3 s are one cut zone (first, last)."""
    p = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", path, "-vf", f"select='gt(scene,{SCENE_CUT})',showinfo",
                        "-an", "-f", "null", "-"], capture_output=True, text=True)
    zones: list[list[float]] = []
    for t in sorted(float(m) for m in re.findall(r"pts_time:([0-9.]+)", p.stderr)):
        if t < 0.2 or t > dur - 0.2:
            continue
        if zones and t - zones[-1][1] < 0.3:
            zones[-1][1] = t
        else:
            zones.append([t, t])
    return [(a, b) for a, b in zones]


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
def pick_window(src: Source, a: float, b: float, length: float, fps: int) -> tuple[float, float] | None:
    """The shown window (start, length) of one angle [a, b], never crossing its ends (R4). Start: the earliest one
    whose window passes the angle's sharpness gate, then within the next 0.5 s the one with the most motion in its
    first frames (cut on motion). A smeared tail shortens the window, never below ANGLE_MIN_S, rather than moving
    it. None when no window of the angle passes the gate."""
    step, gate = 1 / fps, src.gate_at(a)

    def fit(t: float) -> float:
        n = int(min(length, b - t) * fps + 1e-6)
        while n / fps >= ANGLE_MIN_S - 1e-6:
            if src.seg_sharp(t, n / fps) >= gate:
                return n / fps
            n -= 1
        return 0.0

    t, first = a, None
    while t <= b - ANGLE_MIN_S + 1e-6:
        if (n := fit(t)):
            first = (t, n)
            break
        t += step
    if first is None:
        return None
    best, best_m, t = first, src.motion_at(first[0]), first[0] + step
    while t <= min(first[0] + 0.5, b - ANGLE_MIN_S) + 1e-6:
        if (n := fit(t)) >= first[1] - 1e-6 and src.motion_at(t) > best_m:
            best, best_m = (t, n), src.motion_at(t)
        t += step
    return best


def max_len(src: Source, start: float, b: float, fps: int) -> float:
    """The longest window from a fixed `start` to at most `b` that passes the angle's gate (0 if none)."""
    gate, n = src.gate_at(start), int((b - start) * fps + 1e-6)
    while n / fps >= ANGLE_MIN_S - 1e-6:
        if src.seg_sharp(start, n / fps) >= gate:
            return n / fps
        n -= 1
    return 0.0


_GAPS: dict[str, list[float]] = {}


def speech_gaps(clip: str) -> list[float]:
    """Midpoints (source seconds) of the pauses in a take's own sound (ffmpeg silencedetect, -32 dB for 0.12 s):
    where a cut inside a spoken line may land (R12). Empty for a take without sound."""
    if clip not in _GAPS:
        gaps: list[float] = []
        if _has_audio(clip):
            p = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", clip, "-vn", "-af",
                                "silencedetect=noise=-32dB:d=0.12", "-f", "null", "-"], capture_output=True, text=True)
            starts = [float(x) for x in re.findall(r"silence_start: (-?[0-9.]+)", p.stderr)]
            ends = [float(x) for x in re.findall(r"silence_end: ([0-9.]+)", p.stderr)]
            gaps = [(a + b) / 2 for a, b in zip(starts, ends)]
        _GAPS[clip] = gaps
    return _GAPS[clip]


def clause_marks(line: str, a0: float, a1: float, gaps: list[float]) -> list[float]:
    """Source times of the clause boundaries of a spoken line over [a0, a1]: placed by word count, then snapped to
    the nearest real pause of the take within 0.5 s when there is one."""
    words = [len(c.split()) for c in split_clauses(line)] if line else []
    out = []
    for i in range(1, len(words)):
        m = a0 + (a1 - a0) * sum(words[:i]) / max(1, sum(words))
        near = [g for g in gaps if abs(g - m) <= 0.5 and a0 < g < a1]
        out.append(min(near, key=lambda g: abs(g - m)) if near else m)
    return sorted(set(out))


def _scene_items(scene: dict, resolve) -> list[dict]:
    """The scene's shots in storyboard order, {"kind": "talk" | "action" | "still", "path", "line"}. A plan written
    for this cut lists them under "shots" (each with "clip" or "path"); an older plan's scene is one talk take
    ("talk": true) or its "clips" followed by its "stills" (it records no order between the two)."""
    if "shots" in scene:
        raw = [dict(s, path=s.get("clip") or s.get("path")) for s in scene["shots"]]
    elif scene.get("talk"):
        raw = [{"kind": "talk", "path": (scene.get("clips") or [None])[0],
                "line": scene.get("line") or scene.get("caption") or ""}]
    else:
        raw = [{"kind": "action", "path": c} for c in scene.get("clips", [])]
        for st in scene.get("stills", []):
            path, of = (st.get("path"), st.get("start_frame_of")) if isinstance(st, dict) else (st, None)
            raw.append({"kind": "still", "path": path, "fallback_for": resolve(of) if of else None})
    out = []
    for it in raw:
        p = resolve(it["path"]) if it.get("path") else None
        if p:
            out.append({**it, "kind": "still" if it.get("kind") == "insert" else it.get("kind"), "path": p})
    return out


def _extend(run: list[dict], target: float, fps: int) -> float:
    """Grow a run's shots until it lasts `target` s, spreading the time: first every shot up to EXTEND_SOFT (last
    shot first), then clip windows up to their angle's end (moving pictures carry a long line better), then stills
    up to STILL_MAX. A clip window only runs on while it passes its angle's gate. Returns the run's new length."""
    total = sum(sh["len"] for sh in run)
    for kinds, cap in ((("clip", "still"), EXTEND_SOFT), (("clip",), None), (("still",), STILL_MAX)):
        for sh in reversed(run):
            if total >= target - 1e-6:
                return total
            if sh["kind"] not in kinds:
                continue
            want = sh["len"] + target - total
            if cap is not None:
                want = min(want, max(sh["len"], cap))
            if sh["kind"] == "still":
                new = max(sh["len"], math.ceil(want * fps - 1e-6) / fps)
            else:
                new = sh["len"]
                n = min(math.ceil(want * fps - 1e-6), int((sh["b"] - sh["start"]) * fps + 1e-6))
                while n / fps > sh["len"] + 1e-6:
                    if sh["src"].seg_sharp(sh["start"], n / fps) >= sh["src"].gate_at(sh["start"]):
                        new = n / fps
                        break
                    n -= 1
            total += new - sh["len"]
            sh["len"] = new
    return total


def _run_max(run: list[dict], fps: int) -> float:
    return sum(STILL_MAX if sh["kind"] == "still" else max(sh["len"], max_len(sh["src"], sh["start"], sh["b"], fps))
               for sh in run)


def _first_at_least(run: list[dict], sec: float, fps: int) -> bool:
    """R12: a line that starts with this run must not meet a cut in its first 0.8 s, so the run's first shot lasts
    at least `sec`."""
    first = run[0]
    if first["len"] >= sec - 1e-6:
        return True
    _extend([first], sec, fps)
    return first["len"] >= sec - 1e-6


def plan_segments(plan: dict, workdir: Path, base: Path) -> list[Seg]:
    """The cut, in storyboard order (R1-R3): every edit scene is one storyboard scene, its shots in the order given;
    a clip shows each of its angles once, in source order (R4); talk takes hold and lend their voice to the shots
    next to them (L/J cuts); narration plays in full and the picture grows to fit it (R13). Hard cuts only.
    Also sets `plan_segments.gated` (dropped angles) and `plan_segments.voice` (every line as placed)."""
    fps = int(plan.get("fps", 24))

    def frames(sec: float) -> int:
        return max(1, round(sec * fps))

    def resolve(p: str) -> str | None:
        pp = Path(p)
        pp = pp if pp.is_absolute() else base / pp
        return str(pp) if pp.exists() else None

    gated: list[dict] = []
    voices: list[dict] = []
    used: set[str] = set()   # every clip and still is on screen in one place only
    segs: list[Seg] = []
    n_still = 0
    for si, scene in enumerate(plan.get("scenes", [])):
        shots: list[dict] = []
        for it in _scene_items(scene, resolve):
            p = it["path"]
            if p in used:
                log.warning("scene %d: %s is already in the cut, skipped", si, Path(p).name)
                continue
            if it["kind"] == "talk":
                dur = _duration(p)
                if dur > 0.5:
                    used.add(p)
                    shots.append({"kind": "talk", "clip": p, "a0": 0.15, "a1": 0.15 + int((dur - 0.25) * fps) / fps,
                                  "line": it.get("line") or "", "p0": 0.15, "p1": 0.15 + int((dur - 0.25) * fps) / fps})
            elif it["kind"] == "action":
                if _duration(p) <= 0.5:
                    continue
                used.add(p)
                src = source(p)
                for k, (a, b) in enumerate(src.angles()):
                    if pick_window(src, a, b, ACTION_HOLD, fps):
                        shots.append({"kind": "clip", "src": src, "angle": k, "a": a, "b": b})
                    else:
                        gated.append({"src": p, "angle": k, "gate": round(src.gate_at(a), 1)})
            else:
                shots.append({"kind": "still", "path": p, "len": STILL_HOLD, "fallback_for": it.get("fallback_for")})
        shown = {sh["src"].path for sh in shots if sh["kind"] == "clip"}
        shots = [sh for sh in shots if sh["kind"] != "still" or not sh.get("fallback_for") or sh["fallback_for"] not in shown]
        if not shots:
            log.warning("scene %d: nothing usable (every window failed the sharpness gate), skipped", si)
            continue
        for sh in shots:
            if sh["kind"] == "still":
                used.add(sh["path"])
        # runs: the stretches of action and stills between talk takes
        seq: list[dict | list[dict]] = []
        for sh in shots:
            if sh["kind"] == "talk":
                seq.append(sh)
            elif seq and isinstance(seq[-1], list):
                seq[-1].append(sh)
            else:
                seq.append([sh])
        runs = [x for x in seq if isinstance(x, list)]
        # action pacing: the scene's opening shot establishes, a clip followed by another clip is short, a clip that
        # ends its run or meets a still holds; three or more short shots of distinct compositions from 2+ clips are a burst
        for run in runs:
            for j, sh in enumerate(run):
                if sh["kind"] != "clip":
                    continue
                nxt = run[j + 1] if j + 1 < len(run) else None
                more = nxt is not None and nxt["kind"] == "clip"
                length = (ACTION_OPEN if sh is shots[0] else ACTION_SHORT) if more else ACTION_HOLD
                sh["start"], sh["len"] = pick_window(sh["src"], sh["a"], sh["b"], length, fps)
                sh["short"] = more and sh is not shots[0]
            j = 0
            while j < len(run):
                k = j
                while k < len(run) and run[k].get("short"):
                    k += 1
                if k - j >= BURST_MIN and len({sh["src"].path for sh in run[j:k]}) >= 2:
                    for sh in run[j:k]:
                        sh["burst"] = True
                j = k + 1
        hosted: set[int] = set()   # runs already carrying a voice
        # narration rides the scene's first run and plays in full: the run grows to fit it (R13)
        narr = resolve(scene["narration_audio"]) if scene.get("narration_audio") else None
        if narr and runs:
            run, nd = runs[0], _duration(narr)
            got = _extend(run, 0.1 + nd + 0.15, fps)
            _first_at_least(run, 0.95, fps)
            if got < 0.1 + nd + 0.15 - 1e-6:
                log.warning("scene %d: narration %.2fs does not fit its pictures (%.2fs)", si, nd, got)
            voices.append({"kind": "narration", "path": narr, "start": 0.0, "dur": min(nd, got - 0.25),
                           "file_dur": nd, "text": scene.get("narration") or "", "anchor": run[0], "offset": 0.1})
            hosted.add(id(run))
        # talk takes over TALK_HOLD_MAX hold 2-3 s on the speaker; the rest of the line plays over the next run
        # (L-cut) or its start over the previous run (J-cut), cut on a clause boundary that falls in a pause (R12)
        for i, sh in enumerate(seq):
            if not isinstance(sh, dict) or sh["a1"] - sh["a0"] <= TALK_HOLD_MAX:
                continue
            a0, a1 = sh["a0"], sh["a1"]
            marks = clause_marks(sh["line"], a0, a1, speech_gaps(sh["clip"]))
            nxt = seq[i + 1] if i + 1 < len(seq) and isinstance(seq[i + 1], list) else None
            prv = seq[i - 1] if i and isinstance(seq[i - 1], list) else None
            if nxt is not None and id(nxt) not in hosted:
                room = _run_max(nxt, fps)
                ok = [t for t in marks if t - a0 >= TALK_HOLD_MIN and 0.8 <= a1 - t <= room - 0.1]
                if ok:
                    t = min(ok, key=lambda x: abs(x - a0 - TALK_HOLD_TARGET))
                    sh["p1"] = a0 + round((t - a0) * fps) / fps
                    _extend(nxt, a1 - sh["p1"] + 0.1, fps)
                    hosted.add(id(nxt))
                    continue
            if prv is not None and id(prv) not in hosted:
                room, now = _run_max(prv, fps), sum(x["len"] for x in prv)
                best = None
                for t in marks:
                    j_len = round((t - a0) * fps) / fps
                    if j_len < 0.85 or a1 - t < TALK_HOLD_MIN or j_len > room:
                        continue
                    trial = [dict(x) for x in prv]
                    total = _extend(trial, j_len, fps)
                    if total < j_len - 1e-6:
                        continue
                    # the voice starts j_len before the run ends; no cut may fall in its first 0.8 s
                    at, ends = total - j_len, list(itertools.accumulate(x["len"] for x in trial))[:-1]
                    if any(at + 1e-6 < e < at + 0.8 for e in ends):
                        continue
                    if best is None or abs(a1 - t - TALK_HOLD_TARGET) < abs(a1 - best[0] - TALK_HOLD_TARGET):
                        best = (t, j_len, trial)
                if best:
                    t, j_len, trial = best
                    for x, y in zip(prv, trial):
                        x["len"] = y["len"]
                    sh["p0"] = a0 + j_len
                    hosted.add(id(prv))
        # the segments
        body: list[Seg] = []
        for x in seq:
            for sh in ([x] if isinstance(x, dict) else x):
                if sh["kind"] == "talk":
                    seg = Seg("clip", round((sh["p1"] - sh["p0"]) * fps), src=sh["clip"], start=sh["p0"], scene=si)
                    seg.extra.update(src_dur=seg.frames / fps, talk=True)
                    voices.append({"kind": "dialogue", "path": sh["clip"], "start": sh["a0"], "dur": sh["a1"] - sh["a0"],
                                   "file_dur": sh["a1"] - sh["a0"], "text": sh["line"], "anchor": seg,
                                   "offset": sh["a0"] - sh["p0"]})
                elif sh["kind"] == "clip":
                    seg = Seg("clip", frames(sh["len"]), src=sh["src"].path, start=sh["start"], scene=si)
                    seg.extra.update(src_dur=seg.frames / fps, angle=sh["angle"])
                    if sh.get("burst"):
                        seg.extra["burst"] = True
                else:
                    n_still += 1
                    seg = Seg("still", frames(sh["len"]), src=sh["path"], scene=si,
                              motion=("push", "shake", "pull")[(n_still - 1 + si) % 3])
                sh["seg"] = seg
                body.append(seg)
        for v in voices:   # anchors that are shot records become their segments
            if isinstance(v["anchor"], dict):
                v["anchor"] = v["anchor"]["seg"]
        segs += body
    segs.append(Seg("end", frames(END_FRAMES_S)))
    t = 0.0
    for s in segs:
        s.at = round(t, 4)
        t += s.frames / fps
    starts = {}
    for s in segs:
        starts.setdefault(s.scene, s.at)
    for item in plan.get("narration") or []:   # free-standing lines: at `at`, else at their scene, else at 0.15 s
        p = resolve(item.get("path") or "")
        sc = item.get("scene", -1)
        if not p or (sc is not None and sc >= 0 and sc not in starts):
            continue   # the scene was skipped by the gate: its line is dropped, never played over another scene
        at = float(item["at"]) if item.get("at") is not None else (starts[sc] + 0.1 if sc is not None and sc >= 0 else 0.15)
        nd, nxt = _duration(p), min([st for st in starts.values() if st > at + 0.2] + [t])
        voices.append({"kind": "narration", "path": p, "start": 0.0, "dur": min(nd, max(0.3, nxt - at - 0.1)),
                       "file_dur": nd, "text": item.get("text") or "", "at": round(at, 4)})
    for v in voices:
        if "anchor" in v:
            v["at"] = round(v.pop("anchor").at + v.pop("offset"), 4)
    voices.sort(key=lambda v: v["at"])
    _captions(segs, voices, fps)
    _countdown(plan, segs, fps)
    plan_segments.gated = gated
    plan_segments.voice = voices
    return segs


def _captions(segs: list[Seg], voices: list[dict], fps: int) -> None:
    """Subtitles follow the voice, not the cuts (PsyopAnime): each line's clauses are on screen for their
    word-count share of the line as it plays, across whatever cuts it spans; no subtitles over wordless pictures."""
    cues = []
    for v in voices:
        clauses = split_clauses(v["text"]) if v["text"].strip() else []
        words = [max(1, len(c.split())) for c in clauses]
        acc = 0
        for c, w in zip(clauses, words):
            cues.append((c, v["at"] + v["dur"] * acc / sum(words), v["at"] + v["dur"] * (acc + w) / sum(words)))
            acc += w
    for s in segs:
        if s.kind not in ("clip", "still"):
            continue
        d = s.frames / fps
        s.extra["cues"] = [(c, round(max(0.0, a - s.at), 3), round(min(d, b - s.at), 3))
                           for c, a, b in cues if b > s.at + 1e-3 and a < s.at + d - 1e-3]
        s.caption = s.extra["cues"][0][0] if s.extra["cues"] else ""
        if not s.extra["cues"]:
            del s.extra["cues"]


def _countdown(plan: dict, segs: list[Seg], fps: int) -> None:
    """The countdown overlay, only when the plan asks for one: over the last run of non-talk shots of the first scene
    with that beat, running down to 00:00 exactly at the run's end. No subtitle states a time under it (R18)."""
    cd = plan.get("countdown")
    if not cd:
        return
    for si, scene in enumerate(plan.get("scenes", [])):
        if scene.get("beat") != cd.get("scene_beat"):
            continue
        runs: list[list[Seg]] = [[]]
        for s in (x for x in segs if x.scene == si):
            if s.extra.get("talk"):
                runs.append([])
            else:
                runs[-1].append(s)
        run = next((r for r in reversed(runs) if r), None)
        if not run:
            continue
        total = sum(s.frames for s in run) / fps
        for s in run:
            s.extra["countdown"] = {"label": cd.get("label", ""), "total": round(total, 3),
                                    "offset": round(s.at - run[0].at, 4)}
            if s.extra.get("cues"):
                s.extra["cues"] = [c for c in s.extra["cues"] if not CLOCK.search(c[0])]
                s.caption = s.extra["cues"][0][0] if s.extra["cues"] else ""
        return


def reuse_report(timeline: list[dict], fps: int) -> dict:
    """Recount reuse from the final timeline (not the planner's ledger): clip footage windows of the
    same file overlapping by more than one frame, and stills shown more than once."""
    spans: dict[str, list[tuple[float, float]]] = {}
    stills: list[str] = []
    for s in timeline:
        if s["kind"] == "clip" and s["src"]:
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


def _wordmark(font: str, workdir: Path, H: int, text: str) -> str:
    if not text:
        return ""
    tf = workdir / "wordmark.txt"
    if not tf.exists():
        tf.write_text(text)
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


def wordmark_text(plan: dict) -> str:
    """The corner wordmark: SHOT_DIRECTOR_WORDMARK when set (empty = none), else the plan's series title."""
    return WORDMARK if WORDMARK is not None else (plan.get("title") or "ANIME NEWS")


def render_segment(s: Seg, idx: int, plan: dict, workdir: Path, font: str) -> Path:
    W, H = plan.get("size", [1280, 720])
    fps = int(plan.get("fps", 24))
    out = workdir / f"seg-{idx:03d}.mp4"
    fit = f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},setsar=1"
    enc = ["-frames:v", str(s.frames), "-r", str(fps), "-an", "-c:v", "libx264", "-preset", "veryfast",
           "-crf", "19", "-pix_fmt", "yuv420p", "-video_track_timescale", str(fps * 512), str(out)]
    mark = _wordmark(font, workdir, H, wordmark_text(plan))
    cap = _caption_filter(s, font, workdir, idx, H) + mark + _countdown_filter(s, workdir, idx, H)
    if s.kind == "clip":
        src_dur = s.extra.get("src_dur", s.frames / fps / s.speed)
        vf = f"{fit},setpts={s.speed}*(PTS-STARTPTS),fps={fps}"
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
    elif s.kind == "end":
        lines = [(END_CARD_TEXT, H // 22, "white", "h/2-text_h/2")]
        bar = f"drawbox=x=(iw-{W // 3})/2:y=ih/2+{H // 22}:w={W // 3}:h={max(4, H // 120)}:color={GOLD}:t=fill"
        cmd = ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"color=c=black:s={W}x{H}:r={fps}",
               "-vf", bar + "," + _card_filter(lines, font, workdir, idx) + mark] + enc
    else:
        raise ValueError(f"unknown segment kind {s.kind}")
    _run(cmd)
    return out


# ---------------------------------------------------------------- audio
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


def _audio_graph(plan: dict, segs: list[Seg], voices: list[dict], total: float, base: Path,
                 work: Path) -> tuple[list[str], str, list[str]]:
    """Renders the buses to WAV stems in `work` and returns (ffmpeg input args, filter_complex, notes) for the
    final mix; input 0 is the video. Buses: voice (every line where plan_segments placed it: narration in full,
    each talk take's own sound for its whole line, also where its picture has cut away; the music ducks under
    it), music (the bed, continuous to the end), SFX (hits on storyboard scene changes and the end card, and the
    action clips' own sound at `clip_audio_gain`). Final loudnorm I=-14 TP=-1."""
    def resolve(p) -> str | None:
        if not p:
            return None
        pp = Path(p)
        pp = pp if pp.is_absolute() else base / pp
        return str(pp) if pp.exists() and pp.stat().st_size > 0 else None

    fps = int(plan.get("fps", 24))
    stereo = "aresample=48000,aformat=channel_layouts=stereo"
    notes: list[str] = []
    scene_start: dict[int, float] = {}
    for s in segs:
        scene_start.setdefault(s.scene, s.at)
    changes = sorted(scene_start.values())[1:]   # every storyboard scene change and the end card; never 0:00

    voice = _Bus()
    n_narr = 0
    for v in voices:
        if v["kind"] == "narration":
            line = _line_wav(v["path"], work / f"line-{len(voice.outs)}.wav")
            fade = f",afade=t=out:st={max(0.0, v['dur'] - 0.25):.3f}:d=0.25" if v["dur"] < v["file_dur"] - 0.05 else ""
            chain = f"{stereo},atrim=0:{v['dur']:.3f}{fade}"
            n_narr += 1
        elif _has_audio(v["path"]):
            line = _line_wav(v["path"], work / f"line-{len(voice.outs)}.wav", v["start"], v["dur"])
            chain = stereo
        else:
            continue
        ms = int(v["at"] * 1000)
        voice.add(["-i", str(line)], f"{chain},adelay={ms}|{ms}")
    voice_wav = voice.render(work / "bus-voice.wav", total)
    if n_narr:
        notes.append(f"narration x{n_narr}")
    if len(voice.outs) > n_narr:
        notes.append(f"dialogue x{len(voice.outs) - n_narr}")

    music_bus = _Bus()
    music = resolve(plan.get("music"))
    music_wav = None
    if music:
        music_bus.add(["-stream_loop", "-1", "-i", music],
                      # timestamps from the sample count: a looped mp3's own timestamps run ahead of its
                      # samples, so trimming by them ended the bed ~0.6 s early
                      f"{stereo},asetpts=N/SR/TB,atrim=0:{total:.3f},volume={BED_VOLUME},"
                      f"afade=t=in:d=0.4,afade=t=out:st={max(0.0, total - 1.2):.3f}:d=1.2")
        if voice_wav:   # duck the bed under dialogue and narration (never under SFX)
            k = music_bus.args.count("-i")
            # the key is padded past the end: sidechaincompress stops ~0.6 s before its key does
            post = (f"[bus]apad,atrim=0:{total:.3f}[m];[{k}:a]{stereo},apad=whole_dur={total + 2:.3f}[key];"
                    f"[m][key]sidechaincompress=threshold=0.03:ratio=10:attack=20:release=400:makeup=1,"
                    f"apad,atrim=0:{total:.3f}[out]")
            music_wav = music_bus.render(work / "bus-music.wav", total, post, ["-i", str(voice_wav)])
        else:
            music_wav = music_bus.render(work / "bus-music.wav", total)
        notes.append("music bed" + (" (ducked)" if voice_wav else ""))

    fx = _Bus()
    for item in plan.get("sfx") or []:
        p = resolve(item.get("path") or item.get("name"))
        if not p:
            continue
        times = [float(item["at"])] if item.get("at") is not None else changes
        gain = float(item.get("gain", 0.8))
        for t in times:
            ms = int(max(0.0, t - float(item.get("lead", 0.0))) * 1000)
            fx.add(["-i", p], f"{stereo},volume={gain},adelay={ms}|{ms}")
    hits = len(fx.outs)
    if hits:
        notes.append(f"sfx hits x{hits}")
    # action shots: each shown window's own sound (same source span as the picture) on the SFX bus
    for si, sc in enumerate(plan.get("scenes", [])):
        gain = float(sc.get("clip_audio_gain") or 0)
        if gain <= 0:
            continue
        for s in segs:
            if s.scene != si or s.kind != "clip" or s.extra.get("talk") or not _has_audio(s.src):
                continue
            span, out_s, ms = s.extra.get("src_dur", s.frames / fps / s.speed), s.frames / fps, int(s.at * 1000)
            fx.add(["-i", s.src], f"atrim=start={s.start:.4f}:duration={span:.4f},asetpts=PTS-STARTPTS,{stereo},"
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
def cut_report(plan: dict, segs: list[Seg], voices: list[dict], notes: list[str]) -> dict:
    """What `<out>.cuts.json` holds: the counts, the full timeline, every voice line as placed (`played` vs the file's
    `file_dur`: a line is never cut off, R13), each clip's internal cuts (`src_cuts`, the zones its windows avoid),
    the reuse recount and the countdown."""
    fps = int(plan.get("fps", 24))
    total = sum(s.frames for s in segs) / fps
    content = [s for s in segs if s.kind in ("clip", "still")]
    report = {"duration": round(total, 3), "fps": fps, "segments": len(segs), "shots": len(content),
              "cuts_per_minute": round(len(content) / total * 60, 1) if total else 0,
              "avg_shot_s": round(sum(s.frames for s in content) / max(1, len(content)) / fps, 3),
              "gated_segments": getattr(plan_segments, "gated", []),
              "bursts": sum(1 for s in segs if s.extra.get("burst")),
              "longest_shot_s": round(max((s.frames for s in content), default=0) / fps, 3),
              "audio": notes, "timeline": [asdict(s) for s in segs],
              "voice": [{"kind": v["kind"], "path": v["path"], "at": v["at"], "played": round(v["dur"], 3),
                         "file_dur": round(v["file_dur"], 3), "text": v["text"]} for v in voices],
              "src_cuts": {c: [t for zone in source(c).cuts for t in zone]
                           for c in dict.fromkeys(s.src for s in segs if s.kind == "clip" and not s.extra.get("talk"))}}
    report["reuse"] = reuse_report(report["timeline"], fps)
    cd = next((s for s in segs if s.extra.get("countdown")), None)
    report["countdown"] = ({"scene": cd.scene, "label": cd.extra["countdown"]["label"],
                            "seconds": cd.extra["countdown"]["total"]} if cd else None)
    return report


def assemble(plan: dict, out: Path, audio: bool = True, base: Path | None = None) -> Path:
    """Render `plan` to `out` (H.264 + AAC, faststart). Writes `<out>.cuts.json` (cut_report)."""
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    base = Path(base or Path.cwd())
    fps = int(plan.get("fps", 24))
    font = _font()
    with tempfile.TemporaryDirectory(prefix="sd-cut-") as td:
        work = Path(td)
        segs = plan_segments(plan, work, base)
        voices = plan_segments.voice
        with ThreadPoolExecutor(max_workers=4) as ex:
            files = list(ex.map(lambda a: render_segment(a[1], a[0], plan, work, font), enumerate(segs)))
        lst = work / "list.txt"
        lst.write_text("".join(f"file '{f}'\n" for f in files))
        video = work / "video.mp4"
        _run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy",
              str(video)])
        total = sum(s.frames for s in segs) / fps
        if audio:
            inputs, graph, notes = _audio_graph(plan, segs, voices, total, base, work)
        else:
            inputs, graph, notes = [], f"anullsrc=r=48000:cl=stereo,atrim=0:{total:.3f}[aout]", ["silence (--no-audio)"]
        _run(["ffmpeg", "-y", "-v", "error", "-i", str(video)] + inputs +
             ["-filter_complex", graph, "-map", "0:v", "-map", "[aout]", "-c:v", "copy", "-c:a", "aac",
              "-b:a", "192k", "-ar", "48000", "-t", f"{total:.3f}", "-movflags", "+faststart", str(out)])
    report = cut_report(plan, segs, voices, notes)
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
