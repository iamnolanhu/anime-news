"""Mock render of a whole storyboard: zero reused source moments across the episode."""
import json
import shutil
from pathlib import Path

import pytest

from shot_director import render_storyboard as R, storyboard as S
from shot_director.router_client import MockRouter

pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")


def test_cut_has_no_reused_moments(tmp_path):
    media = tmp_path / "media"
    sb = S.build("Mayor bans Mondays", minutes=0.5)
    entry = R.render(sb, MockRouter(media), media, quality="480p", parallel=4)
    assert entry["status"] == "done" and not entry["errors"]
    report = json.loads(Path(str(media / entry["file"]) + ".cuts.json").read_text())
    reuse = report["reuse"]
    assert reuse["overlapping_windows"] == 0
    assert reuse["still_repeats"] == 0
    # every storyboard shot was generated once and shows up in the cut
    srcs = {Path(s["src"]).name for s in report["timeline"] if s["src"]}
    shots = json.loads((media / entry["shots_file"]).read_text())["shots"]
    for r in shots:
        name = r["clip"] or r["frame"]
        assert name in srcs, f"{r['id']} ({r['kind']}) not in the cut"
    # a clip's own start frame never appears as a still next to its clip
    start_frames = {r["frame"] for r in shots if r["clip"]}
    assert not start_frames & {Path(s["src"]).name for s in report["timeline"] if s["kind"] == "still"}
    assert any(s["caption"] for s in report["timeline"])           # captions are on
    assert report["shots"] >= len(shots)                            # every shot at least once (a clip: once per angle)
    ledger = json.loads((media / "episodes.json").read_text())
    assert ledger[-1]["headline"] == "Mayor bans Mondays" and ledger[-1]["cuts_per_minute"]


def test_edit_plan_keeps_storyboard_scenes_whole():
    """One edit scene per storyboard scene, its shots in storyboard order (talk, action, stills and inserts mixed),
    every rendered shot once; the countdown only when the storyboard names one."""
    sb = S.build("Mayor bans Mondays", minutes=1)
    shots = []
    for sc in sb["scenes"]:
        for s in sc["shots"]:
            video = s["kind"] in ("talk", "action")
            shots.append({"id": s["id"], "scene": sc["id"], "kind": s["kind"], "cast": s["cast"],
                          "line": s.get("line", ""), "frame": f"f-{s['id']}.png",
                          "clip": f"c-{s['id']}.mp4" if video else None, "error": None})
    plan = R.edit_plan(sb, shots, Path("/m"))
    assert [x["storyboard_scene"] for x in plan["scenes"]] == [sc["id"] for sc in sb["scenes"]]
    for sc, x in zip(sb["scenes"], plan["scenes"]):
        assert [i["kind"] for i in x["shots"]] == [{"insert": "still"}.get(s["kind"], s["kind"]) for s in sc["shots"]]
    paths = [i.get("clip") or i.get("path") for x in plan["scenes"] for i in x["shots"]]
    assert len(paths) == len(set(paths)) == len(shots)
    assert plan["countdown"] and plan["countdown"]["label"] == sb["countdown"]
    assert R.edit_plan(dict(sb, countdown=""), shots, Path("/m"))["countdown"] is None


def test_retry_respects_estimate_only_and_zero_cap(tmp_path, monkeypatch):
    """--retry must not spend: estimate_only returns the estimate, and a 0 cap refuses a live retry."""
    import json
    from shot_director import render_storyboard as rs

    sb = rs.storyboard.normalize(rs.storyboard.template("Mayor bans Mondays", minutes=0.5))
    sc = sb["scenes"][0]
    talk = next(s for s in sc["shots"] if s["kind"] == "talk")
    shots = [{"id": s["id"], "scene": sc["id"], "kind": s["kind"], "line": s.get("line"), "cast": s.get("cast") or [],
              "clip": None if s is talk else "x.mp4", "frame": None if s is talk else "x.png"} for s in sc["shots"]]
    doc = {"storyboard": sb, "shots": shots, "episode_id": "episode-test", "quality": "480p"}
    f = tmp_path / "episode-test.shots.json"
    f.write_text(json.dumps(doc))

    class Live:
        mode = "live"
        seedance_resolution = "480p"

    out = rs.retry(Live(), tmp_path, f, estimate_only=True)
    assert out["failed_shots"] == 1 and out["estimate"]["usd"] > 0
    try:
        rs.retry(Live(), tmp_path, f, max_cost=0.0)
    except rs.CostCapExceeded:
        pass
    else:
        raise AssertionError("a zero cap must refuse a live retry")
