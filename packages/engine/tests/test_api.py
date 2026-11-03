import time

from fastapi.testclient import TestClient

from shot_director import app as app_module, config


def client(tmp_path):
    return TestClient(app_module.create_app(tmp_path / "media"))


def test_health_and_config_never_return_the_key(tmp_path):
    c = client(tmp_path)
    h = c.get("/api/health").json()
    assert h["ok"] is True and h["mode"] == "mock" and h["has_credential"] is False and h["version"]
    r = c.put("/api/config", json={"comfy_api_key": "sk-test-SECRET-123"})
    assert r.status_code == 200 and r.json()["has_credential"] is True
    assert "SECRET" not in r.text
    for path in ("/api/health", "/api/config", "/api/episodes"):
        assert "SECRET" not in c.get(path).text
    assert config.config_path().stat().st_mode & 0o777 == 0o600
    assert config.stored_key() == "sk-test-SECRET-123"
    assert c.put("/api/config", json={"comfy_api_key": ""}).json()["has_credential"] is False
    assert config.stored_key() is None


def test_storyboard_validation(tmp_path):
    c = client(tmp_path)
    assert c.post("/api/storyboard", json={"headline": "  "}).status_code == 400
    assert c.post("/api/storyboard", json={"headline": "x", "minutes": 9}).status_code == 400
    j = c.post("/api/storyboard", json={"headline": "Mayor bans Mondays", "minutes": 1}).json()
    assert len(j["storyboard"]["scenes"]) == 4


def test_cors_preflight_and_private_network(tmp_path):
    c = client(tmp_path)
    for origin in ("https://anime-news-nine.vercel.app", "http://localhost:3000", "http://127.0.0.1:3000"):
        r = c.options("/api/render", headers={"Origin": origin, "Access-Control-Request-Method": "POST",
                                               "Access-Control-Request-Private-Network": "true"})
        assert r.status_code == 204
        assert r.headers["access-control-allow-origin"] == origin
        assert r.headers["access-control-allow-private-network"] == "true"
        assert "PUT" in r.headers["access-control-allow-methods"]
        assert "content-type" in r.headers["access-control-allow-headers"]
        assert c.get("/api/health", headers={"Origin": origin}).headers["access-control-allow-origin"] == origin


def test_unlisted_origins_get_no_cors_grant(tmp_path):
    c = client(tmp_path)
    for origin in ("https://evil.example", "https://anime-news.vercel.app", "https://some-other-app.vercel.app",
                   "https://anime-news-nine.vercel.app.evil.example", "http://localhost:3001"):
        r = c.options("/api/render", headers={"Origin": origin, "Access-Control-Request-Method": "POST"})
        assert r.status_code == 403
        assert "access-control-allow-origin" not in r.headers
        r = c.put("/api/config", json={"comfy_api_key": "x"}, headers={"Origin": origin})
        assert "access-control-allow-origin" not in r.headers
        assert "access-control-allow-origin" not in c.get("/api/health", headers={"Origin": origin}).headers


def test_allowed_origins_env_override(tmp_path, monkeypatch):
    monkeypatch.setenv("ANIME_NEWS_ALLOWED_ORIGINS", "https://*.example.dev")
    c = client(tmp_path)
    r = c.options("/api/render", headers={"Origin": "https://preview-1.example.dev", "Access-Control-Request-Method": "POST"})
    assert r.headers["access-control-allow-origin"] == "https://preview-1.example.dev"
    r = c.options("/api/render", headers={"Origin": "https://anime-news-nine.vercel.app", "Access-Control-Request-Method": "POST"})
    assert "access-control-allow-origin" not in r.headers


def test_full_flow_config_storyboard_render_job_done(tmp_path):
    c = client(tmp_path)
    assert c.put("/api/config", json={"comfy_api_key": "sk-test"}).status_code == 200
    sb = c.post("/api/storyboard", json={"headline": "Mayor bans Mondays", "minutes": 0.5}).json()["storyboard"]
    sb["scenes"][0]["shots"].append({"kind": "insert", "prompt": "Close-up of a calendar with Monday crossed out",
                                     "move": "static"})
    r = c.post("/api/render", json={"storyboard": sb, "quality": "480p", "vertical": True})
    assert r.status_code == 202
    job_id = r.json()["job_id"]
    assert c.post("/api/render", json={"storyboard": sb}).status_code == 409   # one render at a time
    deadline = time.time() + 600
    j = {}
    while time.time() < deadline:
        j = c.get(f"/api/jobs/{job_id}").json()
        assert j["status"] in ("queued", "rendering", "cutting", "done", "failed")
        if j["status"] in ("done", "failed"):
            break
        time.sleep(1)
    assert j["status"] == "done", j
    ep = j["episode"]
    assert ep["mp4_url"].endswith(".mp4") and ep["vertical_url"].endswith(".9x16.mp4")
    assert ep["shots"] > 0 and ep["duration"] > 0 and ep["cuts_per_minute"] > 0
    assert ep["reuse"]["overlapping_windows"] == 0 and ep["reuse"]["still_repeats"] == 0
    media_path = ep["mp4_url"].split("/media/", 1)[1]
    assert c.get(f"/media/{media_path}").status_code == 200
    eps = c.get("/api/episodes").json()["episodes"]
    assert eps[0]["mp4_url"] == ep["mp4_url"] and eps[0]["quality"] == "480p"
    assert c.get("/api/jobs/nope").status_code == 404


def test_cancel_unknown_job_is_404(tmp_path):
    assert client(tmp_path).delete("/api/jobs/nope").status_code == 404


def test_queue_routes_are_gone(tmp_path):
    c = client(tmp_path)
    assert c.get("/api/queue").status_code == 404
    assert c.post("/api/shot", json={}).status_code in (404, 405)
