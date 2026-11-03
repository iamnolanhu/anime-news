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
    assert report["shots"] >= 10
    ledger = json.loads((media / "episodes.json").read_text())
    assert ledger[-1]["headline"] == "Mayor bans Mondays" and ledger[-1]["cuts_per_minute"]


def test_edit_plan_groups_talk_and_cutins():
    sb = S.build("Mayor bans Mondays", minutes=1)
    shots = []
    for sc in sb["scenes"]:
        for s in sc["shots"]:
            video = s["kind"] in ("talk", "action")
            shots.append({"id": s["id"], "scene": sc["id"], "kind": s["kind"], "cast": s["cast"],
                          "line": s.get("line", ""), "frame": f"f-{s['id']}.png",
                          "clip": f"c-{s['id']}.mp4" if video else None, "error": None})
    plan = R.edit_plan(sb, shots, Path("/m"))
    talk = [x for x in plan["scenes"] if x.get("talk")]
    assert len(talk) == sum(1 for s in shots if s["kind"] == "talk")
    clips = [c for x in plan["scenes"] for c in x["clips"]]
    stills = [c for x in plan["scenes"] for c in x["stills"]]
    assert len(clips) == len(set(clips)) == sum(1 for s in shots if s["clip"])
    assert len(stills) == len(set(stills)) == sum(1 for s in shots if s["kind"] in ("still", "insert"))
    assert plan["title_after_scene"] is not None   # the title lands after the cold open
