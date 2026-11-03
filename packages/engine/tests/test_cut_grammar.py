"""The cut's grammar, checked on the plan (cuts.json) with the art-director review's own metrics (cut_metrics.py).

Synthetic media made with ffmpeg: action clips with a real internal hard cut at 2.5 s (two angles), talk takes
whose sound pauses between clauses, stills, a narration line longer than its pictures. Nothing is generated.
Rules are the review's R1-R18 as amended by the PsyopAnime cut log (docs/EDIT.md).
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from cut_metrics import CLOCK, metrics
from shot_director import edit

pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")
FPS = 24


def _ff(*args: str) -> None:
    subprocess.run(["ffmpeg", "-v", "error", "-y", *args], check=True)


def _two_angles(out: Path, hue_a: int, hue_b: int) -> None:
    """5 s: testsrc2 then testsrc, a hard cut at 2.5 s (a Seedance clip's hidden second angle)."""
    _ff("-f", "lavfi", "-i", f"testsrc2=s=320x180:r={FPS}:d=2.5,hue=h={hue_a}",
        "-f", "lavfi", "-i", f"testsrc=s=320x180:r={FPS}:d=2.5,hue=h={hue_b}",
        "-filter_complex", "[0:v][1:v]concat=n=2:v=1,format=yuv420p", str(out))


def _talk(out: Path, hue: int) -> None:
    """6 s take that speaks 0.3-2.6 s and 3.0-5.7 s (a pause between its two clauses at 2.8 s)."""
    _ff("-f", "lavfi", "-i", f"testsrc2=s=320x180:r={FPS}:d=6,hue=h={hue}",
        "-f", "lavfi", "-i", "aevalsrc='0.4*sin(2*PI*220*t)*(between(t,0.3,2.6)+between(t,3.0,5.7))':s=48000:d=6",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(out))


@pytest.fixture(scope="module")
def media(tmp_path_factory) -> Path:
    m = tmp_path_factory.mktemp("cutmedia")
    for i, name in enumerate(["a", "b", "c", "d", "x", "f", "g", "h", "k"]):
        _two_angles(m / f"{name}.mp4", 40 * i, 40 * i + 180)
    _talk(m / "talk1.mp4", 200)
    _talk(m / "talk3.mp4", 300)
    for i in (1, 2):
        _ff("-f", "lavfi", "-i", f"testsrc=s=640x360:r=1:d=1,hue=h={90 * i}", "-frames:v", "1", str(m / f"still{i}.png"))
    _ff("-f", "lavfi", "-i", "sine=f=330:d=6:sample_rate=48000", str(m / "narr.wav"))
    return m


LINE1 = "First clause runs right here. Second clause follows it now."
LINE3 = "Third line comes first here. Then the closing words land."


def _plan(m: Path) -> dict:
    act = lambda n: {"kind": "action", "clip": str(m / f"{n}.mp4")}   # noqa: E731
    return {"title": "THE WEEKLY BRIEFING", "fps": FPS, "size": [320, 180],
            "countdown": {"label": "LAUNCH IN", "scene_beat": "escalation"},
            "scenes": [
                {"beat": "cold_open", "shots": [act("a"), act("b")]},
                {"beat": "thesis", "shots": [{"kind": "talk", "clip": str(m / "talk1.mp4"), "line": LINE1}, act("c")]},
                {"beat": "escalation", "narration": "Launch in 00:05, says the clock. Nobody moved at all.",
                 "narration_audio": str(m / "narr.wav"),
                 "shots": [{"kind": "still", "path": str(m / "still1.png")}, act("d"),
                           {"kind": "still", "path": str(m / "still2.png")}]},
                {"beat": "vignette", "shots": [act("x"), {"kind": "talk", "clip": str(m / "talk3.mp4"), "line": LINE3}]},
                {"beat": "climax", "shots": [act("f"), act("g"), act("h")]},
                {"beat": "sign_off", "shots": [act("k")]},
            ]}


@pytest.fixture(scope="module")
def planned(media, tmp_path_factory):
    plan = _plan(media)
    segs = edit.plan_segments(plan, tmp_path_factory.mktemp("work"), media)
    report = json.loads(json.dumps(edit.cut_report(plan, segs, edit.plan_segments.voice, [])))
    return plan, report, metrics(report)


def _shots(report: dict) -> list[dict]:
    return [s for s in report["timeline"] if s["kind"] in ("clip", "still")]


def test_r1_a_scene_leaves_a_clip_once(planned):
    _, report, m = planned
    assert m["returns_src"] == 0 and m["returns_angle"] == 0 and m["aba"] == 0
    for sc in {s["scene"] for s in _shots(report)}:
        runs = [s["src"] for s in _shots(report) if s["scene"] == sc]
        runs = [x for i, x in enumerate(runs) if i == 0 or runs[i - 1] != x]
        assert len(runs) == len(set(runs)), f"scene {sc} returns to a clip: {runs}"


def test_r2_time_only_moves_forward_inside_a_clip(planned):
    _, report, m = planned
    assert m["timeback"] == 0
    last: dict[str, float] = {}
    for s in _shots(report):
        if s["kind"] != "clip":
            continue
        assert s["start"] >= last.get(s["src"], -1) - 1 / FPS, f"{s['src']} rewinds at {s['at']}"
        last[s["src"]] = s["start"] + s["extra"]["src_dur"]


def test_r3_shots_follow_storyboard_order(planned):
    plan, report, _ = planned
    for si, scene in enumerate(plan["scenes"]):
        index = {(it.get("clip") or it.get("path")): i for i, it in enumerate(scene["shots"])}
        seq = [(index[s["src"]], s["extra"].get("angle", 0)) for s in _shots(report) if s["scene"] == si]
        assert seq == sorted(seq), f"scene {si} reorders its shots: {seq}"
        assert {i for i, _ in seq} == set(range(len(scene["shots"]))), f"scene {si} drops a shot"


def test_r4_a_window_never_hides_a_cut(planned):
    _, report, m = planned
    assert m["straddle"] == 0
    for s in _shots(report):
        if s["kind"] != "clip" or s["extra"].get("talk"):
            continue
        a, b = s["start"], s["start"] + s["extra"]["src_dur"]
        assert b - a >= 0.5 - 1e-6
        for c in report["src_cuts"][s["src"]]:
            assert not (a + 1 / FPS < c < b - 1 / FPS), f"{Path(s['src']).name} window {a:.2f}-{b:.2f} hides {c}"
    assert all(len(v) == 2 for v in report["src_cuts"].values())   # each synthetic clip's one cut was found


def test_r5_no_same_shot_jump_cuts(planned):
    assert planned[2]["jumps"] == 0


def test_r7_bursts_are_distinct_compositions_from_two_clips(planned):
    _, report, m = planned
    climax = [s for s in _shots(report) if s["scene"] == 4]
    burst = [s for s in climax if s["extra"].get("burst")]
    assert m["bursts"] == 1 and len(burst) >= 3
    comps = [(s["src"], s["extra"]["angle"]) for s in burst]
    assert len(comps) == len(set(comps)) and len({s["src"] for s in burst}) >= 2
    assert all(0.6 <= s["frames"] / FPS <= 1.0 for s in burst)
    assert not climax[-1]["extra"].get("burst") and climax[-1]["frames"] / FPS >= 1.5   # the hold resets the beat
    assert not any(s["extra"].get("burst") for s in _shots(report) if s["scene"] == 5)  # one clip: never a burst


def test_r11_talk_takes_hold_one_framing(planned):
    _, report, m = planned
    assert m["talk_reframes"] == 0
    talk = [s for s in _shots(report) if s["extra"].get("talk")]
    assert len(talk) == 2 and all(s["motion"] == "" for s in talk)
    # take 1 cuts away on its clause pause (L-cut), take 3 comes in on it (J-cut); each holds 2-3.5 s on the speaker
    assert all(2.0 <= s["frames"] / FPS <= 3.5 for s in talk)
    lcut, jcut = talk
    assert abs(lcut["start"] + lcut["frames"] / FPS - 2.8) < 0.1   # the picture leaves at the real pause
    assert abs(jcut["start"] - 2.8) < 0.1


def test_r12_no_cut_in_the_first_08s_of_a_line(planned):
    assert planned[2]["cut_in_first08"] == 0


def test_r13_every_line_plays_in_full(planned):
    _, report, m = planned
    assert m["narration_cut_off"] == []
    narr = next(v for v in report["voice"] if v["kind"] == "narration")
    assert abs(narr["played"] - narr["file_dur"]) < 0.05
    run = [s for s in _shots(report) if s["scene"] == 2]
    assert sum(s["frames"] for s in run) / FPS >= narr["file_dur"] + 0.25 - 1e-6   # the picture grew to fit
    for v in report["voice"]:   # dialogue: the whole take's line, also where its picture cut away
        assert abs(v["played"] - v["file_dur"]) < 0.05


def test_r15_hard_cuts_only(planned):
    _, report, m = planned
    assert m["flashes"] == 0 and m["blacks"] == 0 and m["ramps"] == 0
    assert {s["kind"] for s in report["timeline"]} <= {"clip", "still", "end"}


def test_r16_the_payoff_is_shown_once(planned):
    _, report, m = planned
    assert not any(s["kind"] == "title" for s in report["timeline"])   # no opening title card
    assert m["xscene"] == {} and not m["title_spoils_later_scene"]
    assert report["timeline"][0]["scene"] == 0                       # it opens cold on the first scene


def test_r18_no_time_in_subtitles_under_the_countdown(planned):
    _, report, _ = planned
    under = [s for s in _shots(report) if s["extra"].get("countdown")]
    assert under and all(s["scene"] == 2 for s in under)
    assert not any(CLOCK.search(c[0]) for s in under for c in s["extra"].get("cues", []))
    assert abs(sum(s["frames"] for s in under) / FPS - under[0]["extra"]["countdown"]["total"]) < 1e-3


def test_subtitles_follow_the_voice_not_the_pictures(planned):
    _, report, _ = planned
    assert not any(s["extra"].get("cues") for s in _shots(report) if s["scene"] in (0, 4, 5))   # wordless action
    after_lcut = [s for s in _shots(report) if s["scene"] == 1 and not s["extra"].get("talk")]
    assert "Second clause follows it now." in [c[0] for c in after_lcut[0]["extra"]["cues"]]


def test_old_plan_format_still_cuts(media, tmp_path):
    plan = {"fps": FPS, "size": [320, 180], "scenes": [
        {"beat": "chase", "caption": "Old style.", "clips": [str(media / "a.mp4")], "stills": [str(media / "still1.png")]},
        {"beat": "thesis", "talk": True, "line": LINE1, "clips": [str(media / "talk1.mp4")]}]}
    segs = edit.plan_segments(plan, tmp_path, media)
    m = metrics(json.loads(json.dumps(edit.cut_report(plan, segs, edit.plan_segments.voice, []))))
    assert m["returns_src"] == m["timeback"] == m["straddle"] == 0
    assert [s.kind for s in segs] == ["clip", "clip", "still", "clip", "end"]


def test_assemble_delivers_full_lines_under_the_true_peak_ceiling(media, tmp_path):
    plan = _plan(media)
    plan["scenes"] = plan["scenes"][1:3]
    out = edit.assemble(plan, tmp_path / "cut.mp4", audio=True, base=media)
    report = json.loads(Path(str(out) + ".cuts.json").read_text())
    assert report["loudness"]["TP"] is not None and report["loudness"]["TP"] <= -1.0
    assert all(abs(v["played"] - v["file_dur"]) < 0.05 for v in report["voice"])
    assert report["reuse"]["overlapping_windows"] == 0
