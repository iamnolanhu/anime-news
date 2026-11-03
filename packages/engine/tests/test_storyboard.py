import pytest

from shot_director import storyboard as S


@pytest.mark.parametrize("minutes,scenes", [(0.5, 2), (1, 4), (2, 8), (3, 12), (5, 20)])
def test_minutes_to_scene_count(minutes, scenes):
    sb = S.build("Mayor bans Mondays", minutes=minutes)
    assert len(sb["scenes"]) == scenes == S.scene_count(minutes)
    assert sb["minutes"] == minutes


def test_template_shape_matches_the_contract():
    sb = S.build("Mayor bans Mondays", text="The council voted 7-2.", minutes=1)
    for k in ("id", "headline", "source_text_excerpt", "series", "logline", "minutes", "cast", "scenes"):
        assert k in sb
    assert sb["series"] == "THE HOURLY BRIEFING"
    assert sb["source_text_excerpt"].startswith("The council")
    cast_ids = {c["id"] for c in sb["cast"]}
    assert {"anchor", "operative", "analyst"} <= cast_ids
    for c in sb["cast"]:
        assert set(c) == {"id", "name", "role", "look", "voice"}
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
    assert sb["scenes"][-1]["shots"][-1]["line"].endswith("Back in one hour.")


def test_template_is_deterministic():
    assert S.build("Mayor bans Mondays", minutes=2) == S.build("Mayor bans Mondays", minutes=2)


def test_normalize_keeps_edits_and_has_no_cap():
    sb = S.build("Mayor bans Mondays", minutes=1)
    sb["scenes"][1]["shots"] = sb["scenes"][1]["shots"] * 5   # 25 shots in one scene, duplicated ids
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
