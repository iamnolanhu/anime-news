"""Stutter metrics of a cut, read from its `<out>.cuts.json` alone.

A port of the art-director review's analyze.py (2026-11-03): the same shot kinds, compositions and flags
(RETURN-src, RETURN-angle, A-B-A, TIME-BACK, JUMP-CUT, IN-WINDOW-CUT, cuts inside spoken lines), so a re-cut
is scored the way the review scored the cut it replaces. Differences, all forced by the new plan format:
- a clip's internal hard cuts come from cuts.json `src_cuts` (the cut detects them itself, same ffmpeg scene
  score 0.25); pass `src_cuts=` to score a cut that predates them;
- spoken spans come from cuts.json `voice` (dialogue and narration as actually placed), not from plan scenes;
- a still counts as a source for RETURN-src (the review counted clip returns only).
"""
from __future__ import annotations

import re
import statistics
from pathlib import Path

CLOCK = re.compile(r"\d+:\d\d")


def _internal(cuts: list[float]) -> list[float]:
    """The review's rule: ignore first-frame artefacts (< 0.2 s) and keep the first time of a cluster (< 0.3 s)."""
    out: list[float] = []
    for t in sorted(cuts):
        if t < 0.2 or (out and t - out[-1] < 0.3):
            continue
        out.append(t)
    return out


def shot_log(report: dict, src_cuts: dict | None = None) -> list[dict]:
    fps = report.get("fps", 24)
    src_cuts = src_cuts if src_cuts is not None else report.get("src_cuts", {})
    names: dict[str, str] = {}
    letters = iter("ABCDEFGHIJKLMNOPQRSTUVWXYZ")

    def name(src: str) -> str:
        if src not in names:
            names[src] = next(letters) if src.endswith(".mp4") else "S" + str(
                sum(1 for k in names if not k.endswith(".mp4")) + 1)
        return names[src]

    shots = []
    for s in report["timeline"]:
        if s["kind"] in ("flash", "black"):
            continue
        e = s.get("extra", {})
        kind = s["kind"]
        if e.get("talk"):
            kind = "talk-punch" if s["motion"] == "talk_punch" else "talk"
        elif e.get("burst"):
            kind = "burst"
        elif e.get("ramp"):
            kind = "ramp-slow" if s["speed"] > 1 else "ramp-fast"
        elif kind == "clip" and s["motion"] == "punch":
            kind = "opener"
        elif kind == "clip":
            kind = "action"
        dur = s["frames"] / fps
        rec = {"at": s["at"], "len": dur, "kind": kind, "scene": s["scene"], "src": s["src"], "speed": s["speed"],
               "cap": s.get("caption", ""), "cues": e.get("cues", [])}
        if s["src"].endswith(".mp4"):
            sd = e.get("src_dur", dur * s["speed"])
            a, b = s["start"], s["start"] + sd
            cs = _internal(src_cuts.get(s["src"], []))
            ang = sum(1 for c in cs if c <= a + 1e-3)
            straddle = [c for c in cs if a + 0.04 < c < b - 0.04]
            rec.update(sa=a, sb=b, angle=ang, straddle=straddle, comp=f"{name(s['src'])}{ang + 1}")
            if straddle:
                rec["comp"] += f"+{name(s['src'])}{ang + 2}"
        else:
            rec["comp"] = name(s["src"]) if s["src"] else "card"
        shots.append(rec)
    return shots


def spoken_spans(report: dict) -> list[tuple[float, float, str]]:
    return [(v["at"], v["at"] + v["played"], v["kind"]) for v in report.get("voice", [])]


def metrics(report: dict, src_cuts: dict | None = None) -> dict:
    fps = report.get("fps", 24)
    shots = shot_log(report, src_cuts)
    spoken = spoken_spans(report)

    def in_line(t: float):
        for a, b, k in spoken:
            if a + 1e-3 < t < b - 1e-3:
                return k, t - a
        return None

    seen_src: dict[int, set] = {}
    seen_comp: dict[int, set] = {}
    last_sa: dict[int, dict] = {}
    for i, r in enumerate(shots):
        sc, flags = r["scene"], []
        prev = shots[i - 1] if i else None
        if r["src"] and not r["src"].endswith(".mp4") and r["kind"] == "still":   # stills are sources too
            if r["src"] in seen_src.get(sc, set()) and prev and prev["src"] != r["src"]:
                flags.append("RETURN-src")
            seen_src.setdefault(sc, set()).add(r["src"])
        if r["src"].endswith(".mp4") and not r["kind"].startswith("talk"):
            if r["src"] in seen_src.get(sc, set()) and prev and prev["src"] != r["src"]:
                flags.append("RETURN-src")
            if r["comp"] in seen_comp.get(sc, set()) and prev and prev["comp"] != r["comp"]:
                flags.append("RETURN-angle")
            if r["src"] in last_sa.get(sc, {}) and r["sa"] < last_sa[sc][r["src"]] - 1e-3:
                flags.append("TIME-BACK")
            if r["straddle"]:
                flags.append("IN-WINDOW-CUT")
            if prev and prev.get("comp") == r["comp"] and prev["scene"] == sc and abs(r["sa"] - prev["sb"]) > 1.5 / fps:
                flags.append("JUMP-CUT")
            seen_src.setdefault(sc, set()).add(r["src"])
            seen_comp.setdefault(sc, set()).add(r["comp"])
            last_sa.setdefault(sc, {})[r["src"]] = max(r["sa"], last_sa.get(sc, {}).get(r["src"], -1))
        if i >= 2 and r["src"] and r["src"] == shots[i - 2]["src"] != shots[i - 1]["src"] \
                and r["scene"] == shots[i - 2]["scene"]:
            flags.append("A-B-A")
        r["flags"] = flags

    content = [r for r in shots if r["kind"] != "end"]
    lens = [r["len"] for r in content]
    cut_times = [r["at"] for r in shots[1:]]
    places: dict[str, list[int]] = {}
    for r in shots:
        if r["src"].endswith(".mp4"):
            p = places.setdefault(r["src"], [])
            if not p or p[-1] != r["scene"]:
                p.append(r["scene"])
    title = next((r for r in shots if r["kind"] == "title"), None)
    later = {r["src"] for r in shots if title and r["at"] > title["at"] and r["kind"] != "title"}
    action = [r["len"] for r in content if r["kind"] in ("action", "burst", "opener", "ramp-slow", "ramp-fast")]
    talk = [r["len"] for r in content if r["kind"].startswith("talk")]
    return {
        "duration": report["duration"], "shots": len(content), "cuts": len(cut_times),
        "cuts_per_min": round(len(cut_times) / report["duration"] * 60, 1),
        "avg": round(statistics.mean(lens), 2), "median": round(statistics.median(lens), 2),
        "avg_action": round(statistics.mean(action), 2) if action else None,
        "avg_talk": round(statistics.mean(talk), 2) if talk else None,
        "returns_src": sum("RETURN-src" in r["flags"] for r in shots),
        "returns_angle": sum("RETURN-angle" in r["flags"] for r in shots),
        "aba": sum("A-B-A" in r["flags"] for r in shots),
        "timeback": sum("TIME-BACK" in r["flags"] for r in shots),
        "jumps": sum("JUMP-CUT" in r["flags"] for r in shots),
        "straddle": sum(1 for r in shots if r.get("straddle")),
        "xscene": {Path(k).name: v for k, v in places.items() if len(v) > 1},
        "title_spoils_later_scene": bool(title and title["src"] and title["src"] in later),
        "bursts": sum(1 for i, r in enumerate(shots) if r["kind"] == "burst" and (i == 0 or shots[i - 1]["kind"] != "burst")),
        "burst_shots": sum(1 for r in shots if r["kind"] == "burst"),
        "stills": sum(1 for r in shots if r["kind"] == "still"),
        "stills_adj": sum(1 for i, r in enumerate(shots) if i and r["kind"] == "still" and shots[i - 1]["kind"] == "still"),
        "cut_in_line": sum(1 for t in cut_times if in_line(t)),
        "cut_in_first08": sum(1 for t in cut_times if in_line(t) and in_line(t)[1] < 0.8),
        "talk_reframes": sum(1 for i, r in enumerate(shots) if i and r["kind"].startswith("talk")
                             and shots[i - 1]["src"] == r["src"]),
        "flashes": sum(1 for s in report["timeline"] if s["kind"] == "flash"),
        "blacks": sum(1 for s in report["timeline"] if s["kind"] == "black"),
        "ramps": sum(1 for r in shots if r["kind"].startswith("ramp")),
        "narration_cut_off": [v for v in report.get("voice", []) if v["played"] < v["file_dur"] - 0.05],
        "shot_log": shots,
    }
