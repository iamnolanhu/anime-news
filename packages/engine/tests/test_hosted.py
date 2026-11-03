"""Hosted mode (ANIME_NEWS_HOSTED=1): access gate, per-visitor caps, daily budget, no key writes, render queue."""

import json
import threading
import time

import pytest
from fastapi.testclient import TestClient

from shot_director import app as app_module, hosted, render_storyboard as rs

ACCESS = "test-access-0123456789"
AUTH = {"x-access-token": ACCESS, "x-forwarded-for": "203.0.113.7, 10.0.0.1"}


@pytest.fixture(autouse=True)
def hosted_env(monkeypatch):
    monkeypatch.setenv("ANIME_NEWS_HOSTED", "1")
    monkeypatch.setenv("ANIME_NEWS_ACCESS_TOKEN", ACCESS)
    for name in ("ANIME_NEWS_DAILY_BUDGET_USD", "ANIME_NEWS_IP_RENDERS_PER_DAY", "ANIME_NEWS_IP_STORYBOARDS_PER_DAY",
                 "ANIME_NEWS_MAX_MINUTES", "ANIME_NEWS_FORCE_QUALITY", "ANIME_NEWS_MAX_PARALLEL_JOBS"):
        monkeypatch.delenv(name, raising=False)


def client(tmp_path):
    return TestClient(app_module.create_app(tmp_path / "media"))


def storyboard(c, headers=AUTH, minutes=0.5):
    r = c.post("/api/storyboard", json={"headline": "Mayor bans Mondays", "minutes": minutes}, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()["storyboard"]


def test_every_route_needs_the_access_token(tmp_path):
    c = client(tmp_path)
    (tmp_path / "media" / "x.mp4").write_bytes(b"0")
    for path in ("/api/health", "/api/config", "/api/episodes", "/media/x.mp4", "/"):
        assert c.get(path).status_code == 401, path
        assert c.get(path, headers={"x-access-token": "wrong"}).status_code == 401, path
    assert c.post("/api/storyboard", json={"headline": "x"}).status_code == 401
    assert c.get("/api/health", headers={"Authorization": f"Bearer {ACCESS}"}).status_code == 200
    assert c.get("/media/x.mp4", headers=AUTH).status_code == 200
    assert ACCESS not in c.get("/api/health", headers=AUTH).text


def test_no_token_configured_refuses_everything(tmp_path, monkeypatch):
    monkeypatch.delenv("ANIME_NEWS_ACCESS_TOKEN")
    c = client(tmp_path)
    assert c.get("/api/health", headers={"x-access-token": ""}).status_code == 401


def test_health_reports_limits_without_secrets(tmp_path):
    h = client(tmp_path).get("/api/health", headers=AUTH).json()
    assert h["hosted"] is True and h["ok"] is True
    assert h["limits"] == {"daily_budget_usd": 40.0, "renders_per_ip_per_day": 3, "storyboards_per_ip_per_day": 9,
                           "max_minutes": 1.0, "quality": "480p", "max_parallel_jobs": 2}
    assert h["remaining_for_you"] == 3 and h["budget_remaining_usd"] == 40.0


def test_put_config_is_forbidden(tmp_path):
    c = client(tmp_path)
    r = c.put("/api/config", json={"comfy_api_key": "sk-visitor"}, headers=AUTH)
    assert r.status_code == 403
    assert "media_dir" not in c.get("/api/config", headers=AUTH).json()


def test_legacy_spend_routes_are_off(tmp_path):
    c = client(tmp_path)
    assert c.post("/api/episode", json={"headline": "x"}, headers=AUTH).status_code == 403
    assert c.post("/api/news/refresh", headers=AUTH).status_code == 403


def test_storyboard_minutes_clamped_and_counted(tmp_path):
    c = client(tmp_path)
    sb = storyboard(c, minutes=5)
    assert sb["minutes"] == 1.0
    h = c.get("/api/health", headers=AUTH).json()
    assert h["storyboards_left_for_you"] == 8 and h["budget_remaining_usd"] == 39.9


def test_private_urls_are_not_fetched(tmp_path):
    for url in ("http://127.0.0.1:8765/api/config", "http://169.254.169.254/metadata/v1/", "http://10.0.0.2/",
                "http://localhost/", "file:///etc/passwd"):
        assert hosted.public_url(url) is False, url
    c = client(tmp_path)
    sb = c.post("/api/storyboard", json={"headline": "Mayor bans Mondays", "url": "http://169.254.169.254/x"},
                headers=AUTH).json()["storyboard"]
    assert sb["source_text_excerpt"] == ""


def test_per_ip_render_limit(tmp_path, monkeypatch):
    monkeypatch.setenv("ANIME_NEWS_IP_RENDERS_PER_DAY", "2")
    monkeypatch.setattr(rs, "render", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("not in this test")))
    c = client(tmp_path)
    sb = storyboard(c)
    for _ in range(2):
        assert c.post("/api/render", json={"storyboard": sb}, headers=AUTH).status_code == 202
    r = c.post("/api/render", json={"storyboard": sb}, headers=AUTH)
    assert r.status_code == 429 and "2 renders per day per visitor" in r.json()["detail"]
    assert 0 < int(r.headers["retry-after"]) <= 86400
    other = {**AUTH, "x-forwarded-for": "198.51.100.9"}
    assert c.post("/api/render", json={"storyboard": sb}, headers=other).status_code == 202
    usage = json.loads((tmp_path / "media" / hosted.USAGE_FILE).read_text())
    assert "203.0.113.7" not in json.dumps(usage) and sum(usage["renders"].values()) == 3


def test_daily_budget_stops_spending(tmp_path, monkeypatch):
    monkeypatch.setenv("ANIME_NEWS_DAILY_BUDGET_USD", "0.25")
    c = client(tmp_path)
    storyboard(c)
    storyboard(c)
    r = c.post("/api/storyboard", json={"headline": "Third one"}, headers=AUTH)
    assert r.status_code == 429 and "daily demo budget reached" in r.json()["detail"]
    assert r.headers["retry-after"]
    assert c.get("/api/health", headers=AUTH).json()["budget_remaining_usd"] == 0.05


def test_render_estimate_counts_against_budget(tmp_path, monkeypatch):
    monkeypatch.setenv("ANIME_NEWS_DAILY_BUDGET_USD", "1")
    monkeypatch.setattr(rs, "estimate", lambda *a, **k: {"usd": 5.0, "parts": {}})
    c = client(tmp_path)
    sb = storyboard(c)
    r = c.post("/api/render", json={"storyboard": sb}, headers=AUTH)
    assert r.status_code == 429 and "budget" in r.json()["detail"]
    assert c.get("/api/health", headers=AUTH).json()["remaining_for_you"] == 3   # a refused render is not counted


def test_quality_forced_and_size_capped(tmp_path, monkeypatch):
    seen = []
    monkeypatch.setattr(rs, "render", lambda sb, router, media, quality, **k: seen.append(quality) or
                        (_ for _ in ()).throw(RuntimeError("stop")))
    c = client(tmp_path)
    sb = storyboard(c)
    r = c.post("/api/render", json={"storyboard": sb, "quality": "720p"}, headers=AUTH)
    assert r.status_code == 202 and r.json()["quality"] == "480p"
    big = dict(sb, scenes=[dict(sc, shots=sc["shots"] * 12) for sc in sb["scenes"]])
    r = c.post("/api/render", json={"storyboard": big}, headers=AUTH)
    assert r.status_code == 400 and "shared demo engine renders up to" in r.json()["detail"]
    deadline = time.time() + 5
    while not seen and time.time() < deadline:
        time.sleep(0.05)
    assert seen == ["480p"]


def test_queue_instead_of_409(tmp_path, monkeypatch):
    monkeypatch.setenv("ANIME_NEWS_MAX_PARALLEL_JOBS", "1")
    monkeypatch.setenv("ANIME_NEWS_IP_RENDERS_PER_DAY", "5")
    gate = threading.Event()

    def slow_render(sb, router, media, **k):
        gate.wait(10)
        raise RuntimeError("done waiting")
    monkeypatch.setattr(rs, "render", slow_render)
    c = client(tmp_path)
    sb = storyboard(c)
    ids = [c.post("/api/render", json={"storyboard": sb}, headers=AUTH).json()["job_id"] for _ in range(3)]
    j1, j2, j3 = (c.get(f"/api/jobs/{i}", headers=AUTH).json() for i in ids)
    assert j1["status"] == "rendering"
    assert j2["status"] == "queued" and j2["position"] == 1
    assert j3["status"] == "queued" and j3["position"] == 2
    # cancelling a queued job frees its place and gives the visitor the render back
    before = c.get("/api/health", headers=AUTH).json()["remaining_for_you"]
    assert c.delete(f"/api/jobs/{ids[1]}", headers=AUTH).json() == {"cancelled": True}
    assert c.get(f"/api/jobs/{ids[1]}", headers=AUTH).json()["error"] == "cancelled"
    assert c.get(f"/api/jobs/{ids[2]}", headers=AUTH).json()["position"] == 1
    assert c.get("/api/health", headers=AUTH).json()["remaining_for_you"] == before + 1
    gate.set()
    deadline = time.time() + 10
    while time.time() < deadline:
        if c.get(f"/api/jobs/{ids[2]}", headers=AUTH).json()["status"] == "failed":
            break
        time.sleep(0.05)
    assert c.get(f"/api/jobs/{ids[2]}", headers=AUTH).json()["status"] == "failed"   # the queue drained


def test_hosted_full_render_returns_site_relative_media(tmp_path):
    c = client(tmp_path)
    sb = storyboard(c)
    job_id = c.post("/api/render", json={"storyboard": sb}, headers=AUTH).json()["job_id"]
    deadline = time.time() + 600
    j = {}
    while time.time() < deadline:
        j = c.get(f"/api/jobs/{job_id}", headers=AUTH).json()
        if j["status"] in ("done", "failed"):
            break
        time.sleep(0.5)
    assert j["status"] == "done", j
    assert j["episode"]["mp4_url"].startswith("/media/episode-")
    assert c.get(j["episode"]["mp4_url"], headers=AUTH).status_code == 200
