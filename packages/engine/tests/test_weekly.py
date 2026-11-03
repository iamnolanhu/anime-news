"""THE WEEKLY BRIEFING, offline: the Wikipedia parser on saved Current events pages, topic tags, the somber filter,
the five-headline pick, the weekly storyboard shape, the manifest, and `weekly --plan-only` printing five headlines."""

import json
from pathlib import Path
from urllib.error import HTTPError

import pytest

from shot_director import cli, storyboard as S, weekly as W

FIXTURES = Path(__file__).parent / "fixtures"


def fixture(day: int) -> str:
    return (FIXTURES / f"current-events-2026-10-{day}.wiki").read_text()


def fake_fetch(url: str) -> bytes:
    """Wikipedia day pages for Oct 28-30 2026 from the fixtures; every other URL is a 404 (Google News included)."""
    for day in (28, 29, 30):
        if f"2026_October_{day}&" in url:
            return fixture(day).encode()
    raise HTTPError(url, 404, "not in the fixtures", {}, None)


def item(text, category="Business and economy", context=(), sources=1):
    return {"date": "2026-10-28", "category": category, "context": list(context), "text": text, "links": [],
            "sources": [{"name": f"S{i}", "url": f"https://example.com/{i}"} for i in range(sources)]}


# -- weeks --------------------------------------------------------------------------------------------

def test_iso_weeks_and_ranges():
    days = W.week_days("2026-W44")
    assert days[0].isoformat() == "2026-10-26" and days[-1].isoformat() == "2026-11-01"
    assert W.week_range("2026-W42..2026-W44") == ["2026-W42", "2026-W43", "2026-W44"]
    assert W.week_range("2026-W44..2026-W42") == ["2026-W44", "2026-W43", "2026-W42"]
    assert W.week_range("2026-W01..2026-W44")[-1] == "2026-W44" and len(W.week_range("2026-W01..2026-W44")) == 44
    assert W.day_title(days[6]) == "Portal:Current_events/2026_November_1"
    with pytest.raises(ValueError):
        W.parse_week("2026-44")


# -- the parser -----------------------------------------------------------------------------------------

def test_parser_reads_items_links_sources_and_context():
    items = W.parse_day(fixture(28), "2026-10-28")
    assert len(items) >= 15
    jio = next(i for i in items if i["text"].startswith("Jio Platforms raises"))
    assert jio["category"] == "Business and economy"
    assert "Jio Platforms" in jio["links"] and "initial public offering" in jio["links"]
    assert jio["sources"][0]["url"].startswith("https://www.france24.com/")
    assert jio["sources"][0]["name"] == "AFP via France 24"
    assert "[[" not in jio["text"] and "http" not in jio["text"]
    nested = next(i for i in items if i["text"].startswith("The Bank of Canada"))
    assert nested["context"] and "Iran war" in nested["context"][0]
    italic = next(i for i in items if "sudantribune" in i["sources"][0]["url"])
    assert italic["sources"][0]["name"] == "Sudan Tribune"   # '' italics stripped from the source name


def test_fetch_week_caches_and_records_missing_days(tmp_path):
    doc = W.fetch_week("2026-W44", tmp_path, fetch=fake_fetch)
    assert [d["date"] for d in doc["days"]][0] == "2026-10-26" and len(doc["days"]) == 7
    assert sum(1 for d in doc["days"] if d["items"]) == 3
    assert sum(1 for d in doc["days"] if d["error"]) == 4
    assert (tmp_path / "weekly" / "sources" / "2026-W44.json").exists()


# -- tagging and the somber filter ----------------------------------------------------------------------

def test_topic_tags():
    assert "markets" in W.tag(item("Jio Platforms raises $3.2 billion in India's largest-ever initial public offering"))
    assert "crypto" in W.tag(item("Cryptocurrency exchange Binance confirms that assets were stolen"))
    assert "ai" in W.tag(item("The government invests in artificial intelligence company Cohere"))
    assert "tech" in W.tag(item("SpaceX launches a new rocket", category="Science and technology"))
    assert "politics" in W.tag(item("Bulgarians vote in the presidential election", category="Politics and elections"))
    assert "ai" not in W.tag(item("The minister said nothing about the paint", category="Arts and culture"))
    assert "tech" not in W.tag(item("The court approves the appointment", category="Law and crime"))   # "app" is a word


def test_somber_filter_is_hard():
    assert W.somber(item("A collision between two trains", category="Disasters and accidents"))
    assert W.somber(item("Saudi Arabia launches an airstrike at a market", category="Armed conflicts and attacks"))
    assert W.somber(item("Shares fall as three people are killed in a factory", category="Business and economy"))
    assert W.somber(item("Oil prices rise", context=["2026 Iran war fuel crisis"]))   # the parent bullet counts
    assert W.somber(item("A cyclone makes landfall"))
    assert W.somber(item("An AI gateway is compromised by attackers", category="Science and technology"))
    assert not W.somber(item("The Bank of Canada raises its policy interest rate by 25 basis points"))
    assert not W.somber(item("New studies show the chip warns users"))   # "dies" / "war" only as whole words


def test_pick_one_per_topic_no_repeats_no_somber():
    doc = {"week": "2026-W44", "days": [{"items": [
        item("Crypto exchange Binance confirms US$6 billion of bitcoin was stolen", sources=3),
        item("Bitcoin price falls after a stock market selloff", sources=1),
        item("OpenAI unveils a robot that files tax returns", category="Science and technology", sources=2),
        item("Parliament votes to ban Mondays", category="Politics and elections", sources=2),
        item("SpaceX launches the largest rocket ever built", category="Science and technology", sources=2),
        item("The central bank raises interest rates as stocks surge", sources=2),
        item("A drone strike kills ten in a market", category="Armed conflicts and attacks", sources=9),
    ]}]}
    picks = W.pick(W.candidates(doc))
    assert [p["topic"] for p in picks] == list(W.TOPICS)
    texts = [p["text"] for p in picks]
    assert len(set(texts)) == 5
    assert not any("drone" in t for t in texts)
    assert next(p for p in picks if p["topic"] == "crypto")["text"].startswith("Crypto exchange Binance")
    assert all(len(p["headline"]) <= S.CAPTION_MAX for p in picks)


def test_short_headline_fits_a_caption():
    long = ("Canada's federal government invests up to C$1.6 billion (US$1.1 billion) in Toronto-based artificial "
            "intelligence company Cohere, taking an equity stake as part of a C$7 billion funding round.")
    h = W.short_headline(long)
    assert h == "Canada's government invests $1.6bn in AI company Cohere"
    assert W.short_headline("Mayor bans Mondays.") == "Mayor bans Mondays"
    assert len(W.short_headline("word " * 50)) <= S.CAPTION_MAX


# -- the storyboard ---------------------------------------------------------------------------------

SEGS = [{"topic": t, "headline": f"The {t} headline of the week", "source_text": f"{t} source text"}
        for t in W.TOPICS]


def test_weekly_storyboard_shape():
    sb = S.build("", segments=SEGS, minutes=1, mock=True, week="2026-W44")
    acts = [sc["act"] for sc in sb["scenes"]]
    assert acts[0] == "cold_open" and acts[-1] == "sign_off"
    segs = [sc for sc in sb["scenes"] if sc.get("topic") in W.TOPICS]
    assert [sc["topic"] for sc in segs] == list(W.TOPICS)
    for sc, seg in zip(segs, SEGS):
        assert sc["caption"] == seg["headline"]            # verbatim on screen
        assert sc["shots"][0]["kind"] == "talk"
        assert 2 <= len(sc["shots"]) <= 3
    markets = [sc for sc in sb["scenes"] if sc.get("topic") == "markets_10s"]
    assert len(markets) == 1 and markets[0]["caption"] == S.MARKETS_CAPTION
    assert not any(s["kind"] == "talk" for s in markets[0]["shots"])
    assert sb["scenes"][-1]["shots"][-1]["line"].endswith("Back next week.")
    assert 18 <= S.stats(sb)["shots"] <= 22
    assert sb["series"] == "THE WEEKLY BRIEFING" and sb["countdown"] == "NEXT BRIEFING IN"
    assert [s["headline"] for s in sb["segments"]] == [s["headline"] for s in SEGS] and sb["week"] == "2026-W44"
    assert S.normalize(sb) == sb                            # an edited weekly storyboard survives the validator
    longer = S.build("", segments=SEGS, minutes=3, mock=True)
    assert 36 <= S.stats(longer)["shots"] <= 44


# -- the manifest and plan-only ---------------------------------------------------------------------

def test_manifest_lists_weeks_newest_first(tmp_path):
    for week, status in (("2026-W42", "done"), ("2026-W44", "planned"), ("2026-W43", "partial")):
        d = tmp_path / "weekly" / week
        d.mkdir(parents=True)
        (d / "meta.json").write_text(json.dumps({
            "week": week, "status": status, "start": "x", "end": "y", "label": "L", "title": "T",
            "headlines": [{"topic": "ai", "headline": "H", "text": "long"}],
            "files": {"mp4": "episode.mp4", "poster": "poster.jpg"} if status != "planned" else {}}))
    doc = W.update_manifest(tmp_path)
    assert [w["week"] for w in doc["weeks"]] == ["2026-W44", "2026-W43", "2026-W42"]
    done = doc["weeks"][2]
    assert done["mp4_url"] == "/media/weekly/2026-W42/episode.mp4" and done["poster_url"].endswith("poster.jpg")
    assert done["headlines"] == [{"topic": "ai", "headline": "H"}] and done["published"] is False
    assert doc["weeks"][0]["mp4_url"] is None
    assert W.read_manifest(tmp_path) == doc
    assert W.publish(tmp_path, ["2026-W42", "2026-W44"]) == ["2026-W42"]
    assert W.read_manifest(tmp_path)["weeks"][2]["published"] is True


def test_plan_only_prints_five_headlines(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(W, "_get", fake_fetch)
    cli.main(["weekly", "--week", "2026-W44", "--plan-only", "--out", str(tmp_path)])
    out = capsys.readouterr().out
    lines = [ln for ln in out.splitlines() if ln.startswith("  ") and ln.split()[0] in W.TOPICS]
    assert [ln.split()[0] for ln in lines] == list(W.TOPICS)
    assert "estimate $" in out
    wdir = tmp_path / "weekly" / "2026-W44"
    meta = json.loads((wdir / "meta.json").read_text())
    assert meta["status"] == "planned" and len(meta["headlines"]) == 5 and meta["cost_estimate"] > 0
    assert all(h["sources"] for h in meta["headlines"])
    sb = json.loads((wdir / "storyboard.json").read_text())
    assert [sc["caption"] for sc in sb["scenes"] if sc.get("topic") in W.TOPICS] == [h["headline"] for h in meta["headlines"]]
    assert W.read_manifest(tmp_path)["weeks"][0]["week"] == "2026-W44"
    assert not list(wdir.glob("*.mp4"))                     # nothing rendered


def test_api_weekly_serves_the_manifest_and_week_files(tmp_path):
    from fastapi.testclient import TestClient
    from shot_director.app import create_app
    client = TestClient(create_app(tmp_path))
    assert client.get("/api/weekly").json()["weeks"] == []
    d = tmp_path / "weekly" / "2026-W44"
    d.mkdir(parents=True)
    (d / "meta.json").write_text(json.dumps({"week": "2026-W44", "status": "done", "headlines": [],
                                             "files": {"poster": "poster.jpg"}}))
    (d / "poster.jpg").write_bytes(b"jpg")
    W.update_manifest(tmp_path)
    weeks = client.get("/api/weekly").json()["weeks"]
    assert weeks[0]["week"] == "2026-W44"
    r = client.get(weeks[0]["poster_url"])
    assert r.status_code == 200 and r.content == b"jpg"


# -- the batch budget (live router stubbed: nothing is rendered or billed) ------------------------------

class LiveStub:
    mode = "live"


def live_batch(tmp_path, monkeypatch):
    """Every week gets W44's picks and the template storyboard (no writer); returns the week's estimate total."""
    picks, _ = W.headlines("2026-W44", tmp_path / "src", use_google=False, fetch=fake_fetch)
    monkeypatch.setattr(W, "headlines", lambda *a, **k: (picks, {}))
    real = W.make_storyboard
    monkeypatch.setattr(W, "WRITES", [], raising=False)

    def make_storyboard(week, p, minutes=1.0, mock=None):
        if mock is False:
            W.WRITES.append(week)                        # where the paid writer call would happen
        return real(week, p, minutes, True)
    monkeypatch.setattr(W, "make_storyboard", make_storyboard)
    sb = real("2026-W44", picks, 1.0, True)
    return W.estimate_usd(sb, "480p", tmp_path)["total_usd"]


def test_failed_week_counts_toward_the_batch_budget(tmp_path, monkeypatch):
    from shot_director import render_storyboard as rs
    est = live_batch(tmp_path, monkeypatch)
    attempts = []

    def render(sb, router, media, **k):
        attempts.append(k.get("max_cost"))
        raise RuntimeError("Router job failed half-way")
    monkeypatch.setattr(rs, "render", render)
    res = W.run(["2026-W40", "2026-W41", "2026-W42"], tmp_path, router=LiveStub(), budget_usd=est * 2.5,
                allow_unfinished=True, out=open("/dev/null", "w"))
    assert [w["status"] for w in res["weeks"]] == ["failed", "failed", "budget_stop"]
    assert len(attempts) == 2 and res["spent_usd"] == round(est * 2, 2)
    assert attempts == [pytest.approx(est * 2.5), pytest.approx(est * 1.5)]   # the 2nd cap excludes the 1st week


def test_budget_is_checked_before_the_paid_writer_call(tmp_path, monkeypatch):
    from shot_director import render_storyboard as rs
    est = live_batch(tmp_path, monkeypatch)
    monkeypatch.setattr(rs, "render", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no render")))
    res = W.run(["2026-W40"], tmp_path, router=LiveStub(), budget_usd=est * 0.9, allow_unfinished=True,
                out=open("/dev/null", "w"))
    assert [w["status"] for w in res["weeks"]] == ["budget_stop"] and W.WRITES == [] and res["spent_usd"] == 0


def test_budget_that_fits_one_week_writes_one_storyboard(tmp_path, monkeypatch):
    from shot_director import render_storyboard as rs
    est = live_batch(tmp_path, monkeypatch)
    monkeypatch.setattr(rs, "render", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("render failed")))
    res = W.run(["2026-W40", "2026-W41"], tmp_path, router=LiveStub(), budget_usd=est * 1.5,
                allow_unfinished=True, out=open("/dev/null", "w"))
    assert [w["status"] for w in res["weeks"]] == ["failed", "budget_stop"] and W.WRITES == ["2026-W40"]


def test_a_failed_writer_counts_its_billed_calls(tmp_path, monkeypatch):
    from shot_director import render_storyboard as rs, writer
    est = live_batch(tmp_path, monkeypatch)

    def bad_writer(week, p, minutes=1.0, mock=None):
        writer._count_paid()
        writer._count_paid()                             # two billed replies, both rejected
        raise S.StoryboardError("weekly writer failed: bad JSON")
    monkeypatch.setattr(W, "make_storyboard", lambda week, p, minutes=1.0, mock=None:
                        bad_writer(week, p) if mock is False else S.weekly_template(W.segments_of(p), minutes))
    res = W.run(["2026-W40"], tmp_path, router=LiveStub(), budget_usd=est * 3, allow_unfinished=True,
                out=open("/dev/null", "w"))
    assert res["weeks"][0]["status"] == "failed" and res["spent_usd"] == round(2 * rs.PRICE["writer"], 2)
