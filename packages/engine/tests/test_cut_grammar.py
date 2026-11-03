"""The cut's grammar, checked on the plan (cuts.json) with the art-director review's own metrics (cut_metrics.py).

Synthetic media made with ffmpeg: action clips with a real internal hard cut at 2.5 s (two angles), talk takes
whose sound pauses between clauses, stills, a narration line longer than its pictures. Nothing is generated.
Rules are the review's R1-R18 as amended by the PsyopAnime cut log (docs/EDIT.md).
"""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from cut_metrics import CLOCK, metrics
from shot_director import edit, render_storyboard as R, storyboard as S

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


def test_headline_lower_third_is_shown_once_never_under_a_voice(media, tmp_path):
    """A weekly segment's headline: on the scene's first pictures for 2.5 s; after an opening talk take, on the
    first pictures after it, with the scene's narration moved past it; never overlapping a spoken subtitle."""
    plan = {"fps": FPS, "size": [320, 180], "scenes": [
        {"beat": "setup", "headline": "Mayor bans Mondays citywide", "narration": "The mayor signed it at noon. Nobody objected.",
         "narration_audio": str(media / "narr.wav"),
         "shots": [{"kind": "talk", "clip": str(media / "talk1.mp4"), "line": LINE1},
                   {"kind": "still", "path": str(media / "still1.png")}, {"kind": "action", "clip": str(media / "a.mp4")}]},
        {"beat": "turn", "headline": "Ducks unionize", "shots": [{"kind": "still", "path": str(media / "still2.png")},
                                                                  {"kind": "action", "clip": str(media / "b.mp4")}]},
        {"beat": "sign_off", "shots": [{"kind": "action", "clip": str(media / "c.mp4")}]}]}
    segs = edit.plan_segments(plan, tmp_path, media)
    report = json.loads(json.dumps(edit.cut_report(plan, segs, edit.plan_segments.voice, [])))
    heads = report["headlines"]
    assert [(h["scene"], h["text"]) for h in heads] == [(0, "Mayor bans Mondays citywide"), (1, "Ducks unionize")]
    first = {s["scene"]: s for s in reversed(_shots(report))}
    talk = next(s for s in _shots(report) if s["extra"].get("talk"))
    assert abs(heads[0]["at"] - (talk["at"] + talk["frames"] / FPS)) < 1e-3   # right after the opening take
    assert abs(heads[1]["at"] - first[1]["at"]) < 1e-3                         # on the scene's first shot
    for h in heads:
        assert h["dur"] == 2.5
        for v in report["voice"]:
            assert v["at"] >= h["at"] + h["dur"] - 1e-3 or v["at"] + v["played"] <= h["at"] + 1e-3, (h, v)
    assert all(abs(v["played"] - v["file_dur"]) < 0.05 for v in report["voice"])
    assert metrics(report)["cut_in_first08"] == 0
    shown = [x for s in _shots(report) for x in s["extra"].get("headline", [])]
    assert {x[0] for x in shown} == {h["text"] for h in heads}
    assert not any(s["extra"].get("headline") for s in _shots(report) if s["scene"] == 2)


def test_weekly_segment_scenes_carry_their_headline():
    sb = S.normalize({"headline": "Week", "segments": [{"topic": "tech", "headline": "Robots learn to nap"}],
                      "scenes": [{"act": "cold_open", "shots": [{"kind": "still", "prompt": "a clock"}]},
                                 {"act": "setup", "topic": "tech", "caption": "Robots learn to nap",
                                  "shots": [{"kind": "still", "prompt": "a robot asleep"}]}]})
    shots = [{"id": s["id"], "scene": sc["id"], "kind": s["kind"], "cast": [], "line": "", "frame": "f.png",
              "clip": None, "error": None} for sc in sb["scenes"] for s in sc["shots"]]
    plan = R.edit_plan(sb, shots, Path("/m"))
    assert [x.get("headline") for x in plan["scenes"]] == [None, "Robots learn to nap"]


def test_a_line_longer_than_its_pictures_is_never_cut_off(media, tmp_path):
    """R13 when the run cannot grow enough: the run's last still holds as long as it takes; with no still, the last
    shot freezes on its last frame until the line ends plus 0.2 s. The voice is never trimmed."""
    _ff("-f", "lavfi", "-i", "sine=f=300:d=9:sample_rate=48000", str(tmp_path / "long9.wav"))
    _ff("-f", "lavfi", "-i", "sine=f=300:d=7:sample_rate=48000", str(tmp_path / "long7.wav"))
    plan = {"fps": FPS, "size": [320, 180], "scenes": [
        {"beat": "setup", "narration": "One long line.", "narration_audio": str(tmp_path / "long9.wav"),
         "shots": [{"kind": "still", "path": str(media / "still1.png")}]},
        {"beat": "turn", "narration": "Another long line.", "narration_audio": str(tmp_path / "long7.wav"),
         "shots": [{"kind": "action", "clip": str(media / "k.mp4")}]}]}
    segs = edit.plan_segments(plan, tmp_path, media)
    report = json.loads(json.dumps(edit.cut_report(plan, segs, edit.plan_segments.voice, [])))
    assert [abs(v["played"] - v["file_dur"]) < 0.05 for v in report["voice"]] == [True, True]
    for v in report["voice"]:   # each line ends, plus 0.2 s, inside its own scene's pictures
        sc = next(x["scene"] for x in _shots(report) if x["at"] <= v["at"] < x["at"] + x["frames"] / FPS)
        scene_end = max(x["at"] + x["frames"] / FPS for x in _shots(report) if x["scene"] == sc)
        assert scene_end >= v["at"] + v["file_dur"] + 0.2 - 1e-3, v
    still = next(s for s in _shots(report) if s["scene"] == 0)
    assert still["frames"] / FPS > edit.STILL_MAX                         # the still held past its usual cap
    last = [s for s in _shots(report) if s["scene"] == 1][-1]
    assert last["extra"]["freeze"] > 0 and last["extra"]["src_dur"] < last["frames"] / FPS   # the last frame holds
    assert report["reuse"]["overlapping_windows"] == 0 and metrics(report)["straddle"] == 0
    out = edit.assemble({**plan, "scenes": plan["scenes"][1:]}, tmp_path / "freeze.mp4", audio=True, base=media)
    rep = json.loads(Path(str(out) + ".cuts.json").read_text())
    got = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(out)],
                               capture_output=True, text=True).stdout)
    assert abs(got - rep["duration"]) < 0.1 and rep["voice"][0]["played"] == rep["voice"][0]["file_dur"]


def test_no_pause_in_a_take_means_no_cut_while_it_speaks(media, tmp_path):
    """A talk picture is cut only on a real pause found in the take's sound, at least 2 s in; a take that speaks
    without a pause (or has no sound) holds whole, with no L- or J-cut."""
    _ff("-f", "lavfi", "-i", f"testsrc2=s=320x180:r={FPS}:d=6,hue=h=30",
        "-f", "lavfi", "-i", "aevalsrc='0.4*sin(2*PI*220*t)*between(t,0.3,5.7)':s=48000:d=6",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(tmp_path / "nopause.mp4"))
    _ff("-f", "lavfi", "-i", f"testsrc2=s=320x180:r={FPS}:d=6,hue=h=60", "-c:v", "libx264", "-pix_fmt", "yuv420p",
        str(tmp_path / "silent.mp4"))
    assert edit.clause_marks(LINE1, 0.15, 5.9, edit.speech_gaps(str(tmp_path / "nopause.mp4"))) == []
    plan = {"fps": FPS, "size": [320, 180], "scenes": [
        {"beat": "thesis", "shots": [{"kind": "talk", "clip": str(tmp_path / "nopause.mp4"), "line": LINE1},
                                     {"kind": "action", "clip": str(media / "a.mp4")}]},
        {"beat": "vignette", "shots": [{"kind": "action", "clip": str(media / "b.mp4")},
                                       {"kind": "talk", "clip": str(tmp_path / "silent.mp4"), "line": LINE3}]}]}
    segs = edit.plan_segments(plan, tmp_path, media)
    talk = [s for s in segs if s.extra.get("talk")]
    assert len(talk) == 2
    for s in talk:   # whole: 0.15 s to the end minus 0.1 s
        assert abs(s.start - 0.15) < 1e-6 and abs(s.frames / FPS - 5.75) < 1 / FPS
    report = json.loads(json.dumps(edit.cut_report(plan, segs, edit.plan_segments.voice, [])))
    assert metrics(report)["cut_in_line"] == 0


def test_a_headline_never_runs_onto_a_talk_take(media, tmp_path):
    """The headline ends 0.1 s before the next line (at most 2.5 s); under 1.2 s it moves to the scene's next quiet
    stretch; with none it is dropped."""
    for name, d in (("w2", 2.1), ("w1a", 1.1), ("w1b", 1.1)):
        _ff("-f", "lavfi", "-i", f"testsrc2=s=320x180:r={FPS}:d={d}", "-pix_fmt", "yuv420p", str(tmp_path / f"{name}.mp4"))
    for name in ("ta", "tb", "tc"):   # 3.5 s takes: under TALK_HOLD_MAX, so they hold whole
        _ff("-f", "lavfi", "-i", f"testsrc2=s=320x180:r={FPS}:d=3.5,hue=h=120",
            "-f", "lavfi", "-i", "aevalsrc='0.4*sin(2*PI*220*t)':s=48000:d=3.5",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(tmp_path / f"{name}.mp4"))
    talk = lambda n: {"kind": "talk", "clip": str(tmp_path / f"{n}.mp4"), "line": "A short line here."}   # noqa: E731
    act = lambda n: {"kind": "action", "clip": str(tmp_path / f"{n}.mp4")}                             # noqa: E731
    plan = {"fps": FPS, "size": [320, 180], "scenes": [
        {"beat": "setup", "headline": "Shortened", "shots": [act("w2"), talk("ta")]},
        {"beat": "turn", "headline": "Moved", "shots": [act("w1a"), talk("tb"), {"kind": "still", "path": str(media / "still2.png")}]},
        {"beat": "climax", "headline": "Dropped", "shots": [act("w1b"), talk("tc")]}]}
    segs = edit.plan_segments(plan, tmp_path, media)
    report = json.loads(json.dumps(edit.cut_report(plan, segs, edit.plan_segments.voice, [])))
    heads = {h["text"]: h for h in report["headlines"]}
    assert set(heads) == {"Shortened", "Moved"}
    voice = {next(s["scene"] for s in _shots(report) if s["extra"].get("talk") and abs(s["at"] - v["at"]) < 0.5): v
             for v in report["voice"]}
    short = heads["Shortened"]
    assert 1.2 <= short["dur"] < 2.5 and abs(short["at"] + short["dur"] - (voice[0]["at"] - 0.1)) < 1e-3
    moved = heads["Moved"]
    assert moved["at"] >= voice[1]["at"] + voice[1]["played"] + 0.1 - 1e-3 and moved["dur"] >= 1.2
    for h in heads.values():
        for v in report["voice"]:
            assert v["at"] >= h["at"] + h["dur"] + 0.1 - 1e-3 or v["at"] + v["played"] <= h["at"] - 0.1 + 1e-3, (h, v)
    assert not any(c[0] == "Dropped" for s in _shots(report) for c in s["extra"].get("headline", []))


def test_writers_never_draw_clocks_and_the_overlay_skips_shots_that_do(media, tmp_path):
    """One clock on screen: both writer prompts forbid clocks, timers, countdowns and numbers in frames (the overlay
    carries the countdown), the template storyboards draw none, and a shot whose prompt still draws one gets no
    overlay."""
    from shot_director import writer
    rule = "Never draw clocks, timers, countdowns or on-screen numbers in frames: the overlay carries the countdown."
    assert writer.NO_CLOCKS == rule
    for prompt in (writer.SYSTEM_PROMPT_V2, S.SYSTEM_PROMPT):
        assert rule in prompt
        for stale in ("visible ticking clock", "countdown clock appears", "document or countdown", "a ticker, the countdown"):
            assert stale not in prompt
    segs = [{"topic": t, "headline": f"{t} story", "source_text": ""} for t in ("ai", "tech")]
    for sb in (S.template("Mayor bans Mondays", minutes=1), S.weekly_template(segs, minutes=1)):
        assert not [s["prompt"] for sc in sb["scenes"] for s in sc["shots"] if edit.CLOCK_WORDS.search(s["prompt"])]
    act = lambda n, prompt: {"kind": "action", "clip": str(media / f"{n}.mp4"), "prompt": prompt}   # noqa: E731
    plan = {"fps": FPS, "size": [320, 180], "countdown": {"label": "LAUNCH IN", "scene_beat": "escalation"},
            "scenes": [{"beat": "escalation", "shots": [act("a", "Wide shot of traders sprinting with buckets"),
                                                        act("b", "Close-up of a giant red countdown clock hitting zero")]}]}
    plan_segs = edit.plan_segments(plan, tmp_path, media)
    over = {(Path(s.src).stem, bool(s.extra.get("countdown"))) for s in plan_segs if s.kind == "clip"}
    assert over == {("a", True), ("b", False)}


NARRATION_PATHS = ["run-still", "run-clip", "after-headline", "talk-only-scene", "free-by-scene", "free-at-time",
                   "free-default"]


def _long_line_plan(media: Path, tmp: Path, path: str) -> dict:
    """One narrated scene whose line outlasts every picture in it (7 s against at most ~5 s), then a next scene."""
    line = tmp / "line7.wav"
    if not line.exists():
        _ff("-f", "lavfi", "-i", "sine=f=300:d=7:sample_rate=48000", str(line))
    act = lambda n: {"kind": "action", "clip": str(media / f"{n}.mp4")}   # noqa: E731
    shots = {"run-still": [{"kind": "still", "path": str(media / "still1.png")}], "run-clip": [act("k")],
             "after-headline": [{"kind": "still", "path": str(media / "still2.png")}, act("a")],
             "talk-only-scene": [{"kind": "talk", "clip": str(media / "talk1.mp4"), "line": LINE1}]}.get(path, [act("b")])
    first = {"beat": "setup", "shots": shots}
    plan = {"fps": FPS, "size": [320, 180], "scenes": [first, {"beat": "turn", "shots": [act("x")]}]}
    if path.startswith("free"):
        item = {"text": "A free-standing line.", "path": str(line),
                **({"scene": 0} if path == "free-by-scene" else {"at": 0.5} if path == "free-at-time" else {})}
        plan["narration"] = [item]
    else:
        first.update(narration="A long scene line.", narration_audio=str(line))
        if path == "after-headline":
            first["headline"] = "A headline"
    return plan


@pytest.mark.parametrize("path", NARRATION_PATHS)
def test_a_line_outlasting_its_scene_plays_whole_on_every_path(media, tmp_path, path):
    """R13 on every way a narration line reaches the cut: played == file length, it never overlaps another line, no
    cut lands in its first 0.8 s, and the next scene starts only after it ends (+0.2 s)."""
    plan = _long_line_plan(media, tmp_path, path)
    segs = edit.plan_segments(plan, tmp_path, media)
    report = json.loads(json.dumps(edit.cut_report(plan, segs, edit.plan_segments.voice, [])))
    narr = [v for v in report["voice"] if v["kind"] == "narration"]
    assert len(narr) == 1 and abs(narr[0]["played"] - narr[0]["file_dur"]) < 1e-3 and narr[0]["file_dur"] > 6.9
    v = narr[0]
    next_scene = min(s["at"] for s in report["timeline"] if s["scene"] == 1)
    assert next_scene >= v["at"] + v["played"] + 0.2 - 1e-3, (path, v, next_scene)
    for w in report["voice"]:
        if w is not v:
            assert w["at"] >= v["at"] + v["played"] - 1e-3 or w["at"] + w["played"] <= v["at"] + 1e-3, (path, v, w)
    assert metrics(report)["cut_in_first08"] == 0


@pytest.mark.parametrize("path", ["talk-only-scene", "free-at-time"])
def test_the_rendered_line_is_not_trimmed(media, tmp_path, path):
    """The audio graph plays a narration line whole (no atrim or fade): its tone is heard from start to end."""
    plan = _long_line_plan(media, tmp_path, path)
    out = edit.assemble(plan, tmp_path / "cut.mp4", audio=True, base=media)
    v = next(v for v in json.loads(Path(str(out) + ".cuts.json").read_text())["voice"] if v["kind"] == "narration")
    p = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(out), "-vn", "-af",
                        "silencedetect=noise=-45dB:d=0.05", "-f", "null", "-"], capture_output=True, text=True)
    quiet = [float(x) for x in re.findall(r"silence_start: ([0-9.]+)", p.stderr)]
    assert not [q for q in quiet if v["at"] + 0.1 < q < v["at"] + v["file_dur"] - 0.1], (v, quiet)

