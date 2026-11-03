import json
import os
from pathlib import Path

import pytest

from shot_director import storyboard as S
from shot_director import style


@pytest.mark.parametrize("minutes,scenes", [(0.5, 2), (1, 5), (2, 9), (3, 14), (5, 23)])
def test_minutes_to_scene_count(minutes, scenes):
    sb = S.build("Mayor bans Mondays", minutes=minutes)
    assert len(sb["scenes"]) == scenes == S.scene_count(minutes)
    assert sb["minutes"] == minutes


def test_template_shape_matches_the_contract():
    sb = S.build("Mayor bans Mondays", text="The council voted 7-2.", minutes=1)
    for k in ("id", "headline", "source_text_excerpt", "series", "logline", "minutes", "cast", "scenes"):
        assert k in sb
    assert sb["series"] == "THE WEEKLY BRIEFING"
    assert sb["source_text_excerpt"].startswith("The council")
    cast_ids = {c["id"] for c in sb["cast"]}
    assert {"anchor", "operative", "analyst"} <= cast_ids
    for c in sb["cast"]:
        assert {"id", "name", "role", "look", "voice"} <= set(c) <= {"id", "name", "role", "look", "voice", "trait", "poses"}
    ids = set()
    for sc in sb["scenes"]:
        assert sc["act"] in S.ACTS
        assert {"id", "act", "setting", "caption", "narration", "shots"} <= set(sc)
        assert 3 <= len(sc["shots"]) <= 6
        for s in sc["shots"]:
            assert s["kind"] in S.KINDS
            assert s["prompt"] and s["move"] in S.MOVES and isinstance(s["seconds"], int)
            assert s["id"] not in ids
            ids.add(s["id"])
            if s["kind"] == "talk":
                assert s["line"] and len(s["line"]) <= S.LINE_MAX and s["cast"][0] in cast_ids
    kinds = {s["kind"] for sc in sb["scenes"] for s in sc["shots"]}
    assert kinds == set(S.KINDS)
    assert sb["scenes"][0]["act"] == "cold_open" and sb["scenes"][-1]["act"] == "sign_off"
    assert sb["scenes"][-1]["shots"][-1]["line"].endswith("Back next week.")


@pytest.mark.parametrize("minutes", [0.5, 1, 1.5, 2, 3, 5])
def test_template_runs_at_psyopanime_pace(minutes):
    """18-22 shots per minute of target length, the kind mix within one shot of 5 talk / 9 still or insert / 6 action
    a minute, talk lines a two-line subtitle, every action a clip with a second angle, the sign-off last."""
    sb = S.build("Mayor bans Mondays", minutes=minutes)
    st = S.stats(sb)
    assert 18 <= st["shots"] / minutes <= 22
    assert abs(st["talk"] - 5 * minutes) <= 1
    assert abs(st["still"] + st["insert"] - 9 * minutes) <= 1
    assert abs(st["action"] - 6 * minutes) <= 1
    assert 4 <= len(sb["scenes"]) / minutes <= 5 or len(sb["scenes"]) == 2
    shots = [s for sc in sb["scenes"] for s in sc["shots"]]
    assert all(len(s["line"]) <= 60 for s in shots if s["kind"] == "talk")
    assert all("Hard cut to:" in s["prompt"] for s in shots if s["kind"] == "action")
    assert not any(s["kind"] == "talk" for s in sb["scenes"][0]["shots"]) or sb["scenes"][0]["act"] != "cold_open"
    assert shots[-1]["kind"] == "talk" and shots[-1]["line"].endswith("Back next week.")
    assert all(isinstance(s["seconds"], int) and s["seconds"] >= 4 for s in shots)   # the clip length (the estimate)
    assert [[s["kind"] for s in sc["shots"]] for sc in sb["scenes"]] == [k for _, k in S.shot_plan(minutes)]


def test_writer_gets_the_shot_plan_and_a_thin_reply_is_sent_back(monkeypatch):
    from shot_director import writer
    full = S.template("Mayor bans Mondays", minutes=1)
    thin = {**full, "scenes": [{**sc, "shots": sc["shots"][:1]} for sc in full["scenes"]]}   # 5 shots, not 20
    systems, replies = [], [json.dumps(thin), json.dumps(thin), json.dumps(full)]
    monkeypatch.setattr(writer, "_chat", lambda system, prompt, **kw: systems.append(system) or replies.pop(0))
    with pytest.raises(S.StoryboardError, match="expected about 20 shots"):
        S.write("Mayor bans Mondays", minutes=1)
    assert "20 shots in total (18-22 a minute): about 5 talk, 9 still or insert, 6 action" in systems[0]
    assert "1. cold_open: 4 shots (action, still, insert, action)" in systems[0] and "Hard cut to:" in systems[0]
    assert S.stats(S.write("Mayor bans Mondays", minutes=1))["shots"] == 20


def test_template_is_deterministic():
    assert S.build("Mayor bans Mondays", minutes=2) == S.build("Mayor bans Mondays", minutes=2)


def test_normalize_keeps_edits_and_has_no_cap():
    sb = S.build("Mayor bans Mondays", minutes=1)
    sb["scenes"][1]["shots"] = (sb["scenes"][1]["shots"] * 7)[:25]   # 25 shots in one scene, duplicated ids
    out = S.normalize(sb)
    assert len(out["scenes"][1]["shots"]) == 25
    assert len({s["id"] for sc in out["scenes"] for s in sc["shots"]}) == sum(len(sc["shots"]) for sc in out["scenes"])


def test_normalize_rejects_bad_input():
    with pytest.raises(S.StoryboardError):
        S.normalize({"headline": "", "scenes": []})
    sb = S.build("Mayor bans Mondays", minutes=1)
    sb["scenes"][0]["shots"][0]["kind"] = "hologram"
    with pytest.raises(S.StoryboardError):
        S.normalize(sb)
    sb = S.build("Mayor bans Mondays", minutes=1)
    talk = next(s for sc in sb["scenes"] for s in sc["shots"] if s["kind"] == "talk")
    talk["line"] = ""
    with pytest.raises(S.StoryboardError):
        S.normalize(sb)


def test_long_lines_are_clipped_to_60_chars():
    sb = S.build("Mayor bans Mondays", minutes=1)
    talk = next(s for sc in sb["scenes"] for s in sc["shots"] if s["kind"] == "talk")
    talk["line"] = "word " * 40
    out = S.normalize(sb)
    t2 = next(s for sc in out["scenes"] for s in sc["shots"] if s["id"] == talk["id"])
    assert len(t2["line"]) <= 60


def test_empty_headline_is_an_error():
    with pytest.raises(S.StoryboardError):
        S.build("   ")


def test_url_fetch_fails_soft():
    assert S.fetch_url("https://example.invalid/story") == ""   # urlopen is patched to raise
    sb = S.build("Mayor bans Mondays", url="https://example.invalid/story", minutes=0.5)
    assert sb["headline"] == "Mayor bans Mondays"


def test_strip_html():
    assert S.strip_html("<html><script>x()</script><p>Hello &amp; <b>bye</b></p></html>") == "Hello & bye"


# -- the cast file (<media>/cast.json) ------------------------------------------------------------

CAST_DOC = {"cast": [
    {"id": "anchor", "name": "SHION", "role": "the Anchor", "look": "adult woman, mid-20s, long lavender hair, thin glasses",
     "trait": "perfectly composed", "voice": "21m00Tcm4TlvDq8ikWAM",
     "poses": {"talk": {"id": "upper80v2_P019", "phrase": "tucks a strand of hair behind her ear", "ref_image": "/nope.png"},
               "still": "rests her chin on both palms"}},
    {"id": "operative", "name": "HINA", "role": "the Field Reporter", "look": "adult woman, mid-20s, copper bob, hoodie"},
    {"id": "analyst", "name": "DR. RENA", "role": "the Analyst", "look": "adult woman, mid-20s, long navy hair, lab coat"},
]}


def _write_cast(doc):
    media = Path(os.environ["ANIME_NEWS_MEDIA"])
    media.mkdir(parents=True, exist_ok=True)
    (media / "cast.json").write_text(json.dumps(doc))
    return media


def test_cast_file_replaces_the_builtin_cast():
    _write_cast(CAST_DOC)
    cast = {c["id"]: c for c in S.default_cast()}
    assert cast["anchor"]["name"] == "SHION" and cast["analyst"]["name"] == "DR. RENA"
    assert cast["anchor"]["poses"]["talk"] == {"phrase": "tucks a strand of hair behind her ear",
                                               "id": "upper80v2_P019", "ref_image": "/nope.png"}
    assert cast["anchor"]["poses"]["still"] == {"phrase": "rests her chin on both palms"}
    assert cast["operative"]["voice"] == S.DEFAULT_VOICES["operative"]   # missing fields fall back per id
    sb = S.build("Mayor bans Mondays", minutes=2)              # 9 scenes: every template act, every speaker
    assert [c["name"] for c in sb["cast"]] == ["SHION", "HINA", "DR. RENA"]
    prompts = " ".join(s["prompt"] + s.get("line", "") for sc in sb["scenes"] for s in sc["shots"])
    assert "SHION" in prompts and "Hina here." in prompts
    block = S._cast_block(S.default_cast())
    assert "talk shot: she tucks a strand of hair behind her ear" in block and "perfectly composed" in block


def test_default_cast_is_the_shion_hina_rena_trio():
    cast = {c["id"]: c for c in S.default_cast()}      # no cast file in the test media dir
    assert list(cast) == ["anchor", "operative", "analyst"]
    assert [c["name"] for c in cast.values()] == ["SHION", "HINA", "DR. RENA"]
    assert [c["voice"] for c in cast.values()] == ["EXAVITQu4vr4xnSDxMaL", "FGY2WhTYpPnrIDTdsKH5", "pFZP5JQG7iQjIQuC4Bku"]
    assert all(c["trait"] and set(c["poses"]) == {"talk", "action", "still"} for c in cast.values())
    assert S.DEFAULT_VOICES["anchor"] == cast["anchor"]["voice"]
    assert "crimson" in cast["anchor"]["look"] and "fuzzy" in cast["operative"]["look"]
    sb = S.build("Mayor bans Mondays", minutes=1)
    assert [c["name"] for c in sb["cast"]] == ["SHION", "HINA", "DR. RENA"]


def test_broken_cast_file_falls_back_to_the_builtin_cast():
    _write_cast({"cast": CAST_DOC["cast"][:2]})            # no analyst
    assert [c["name"] for c in S.default_cast()] == ["SHION", "HINA", "DR. RENA"]
    _write_cast("not a cast")
    assert S.default_cast() == S.builtin_cast()


def test_style_sheet_prompt_follows_the_cast_file_anchor():
    media = Path(os.environ["ANIME_NEWS_MEDIA"])
    assert style.sheet_prompt(media) == style.SHEET_PROMPT and "crimson-red hair" in style.SHEET_PROMPT
    assert "middle-aged" not in style.SHEET_PROMPT and "crew cut" not in style.SHEET_PROMPT
    _write_cast(CAST_DOC)
    p = style.sheet_prompt(media)
    assert "SHION" in p and "long lavender hair, thin glasses" in p and "crew cut" not in p


def test_styled_frame_sends_the_pose_drawing_as_a_second_reference(tmp_path):
    (tmp_path / style.SHEET_NAME).write_bytes(b"sheet")
    pose = tmp_path / "pose.png"
    pose.write_bytes(b"pose")
    sent = []

    class FakeLive:
        mode = "live"

        def _run_model(self, model, body):
            sent.append(body)
            return {"result": {"sample": "https://x/img.png"}}

        def _dig(self, doc, *keys):
            for k in keys:
                doc = doc.get(k)
            return doc

        def _download(self, url, out):
            out.write_bytes(b"img")
            return out

    style.generate_styled_frame(FakeLive(), "a scene", tmp_path / "a.png", tmp_path, pose_ref=pose)
    style.generate_styled_frame(FakeLive(), "a scene", tmp_path / "b.png", tmp_path, pose_ref=tmp_path / "missing.png")
    assert len(sent[0]["images"]) == 2 and sent[0]["prompt"].startswith(style.POSE_REFERENCE_LEAD)
    assert len(sent[1]["images"]) == 1 and sent[1]["prompt"].startswith(style.REFERENCE_LEAD)
