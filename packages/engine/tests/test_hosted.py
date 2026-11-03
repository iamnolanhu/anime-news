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


# -- URL fetches resolve once and connect to the checked IP (DNS rebinding) -------------------------

class _Site:
    """A local HTTP server standing in for public hosts: routes by path, records the Host header of each request."""

    def __init__(self, routes):
        import http.server
        seen = self.seen = []

        class H(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                seen.append((self.headers.get("Host"), self.path))
                code, headers, body = routes[self.path]
                self.send_response(code)
                for k, v in headers.items():
                    self.send_header(k, v)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


def _rebinding_dns(monkeypatch, answers):
    """getaddrinfo that answers from a per-host list: first answer, then the next, ... (the last one repeats)."""
    calls = []

    def fake(host, port, *a, **k):
        if host not in answers:   # IP literals (and the test's own loopback dials) resolve to themselves
            fam = 10 if ":" in host else 2
            return [(fam, 1, 6, "", (host, port))]
        calls.append(host)
        seq = answers[host]
        ip = seq.pop(0) if len(seq) > 1 else seq[0]
        return [(2, 1, 6, "", (ip, port))]
    monkeypatch.setattr(hosted.socket, "getaddrinfo", fake)
    return calls


def _route_connects_to(monkeypatch, site):
    """Every pinned connection lands on the local site; records which (ip, port) the engine asked for."""
    import socket as real_socket
    dialed = []

    def connect(ip, port, timeout):
        dialed.append((ip, port))
        return real_socket.create_connection(("127.0.0.1", site.port), timeout)
    monkeypatch.setattr(hosted, "_connect", connect)
    return dialed


def _get(url):
    import urllib.request
    return hosted.open_public(urllib.request.Request(url, headers={"User-Agent": "t"}), timeout=5)


def test_fetch_connects_to_the_ip_it_checked_not_a_rebound_one(monkeypatch):
    site = _Site({"/news": (200, {"Content-Type": "text/plain; charset=utf-8"}, b"hello")})
    try:
        dns = _rebinding_dns(monkeypatch, {"rebind.example": ["93.184.216.34", "127.0.0.1"]})
        dialed = _route_connects_to(monkeypatch, site)
        with _get("http://rebind.example/news") as r:
            assert r.read() == b"hello" and r.headers.get_content_charset() == "utf-8"
        assert dns == ["rebind.example"]                    # resolved once per hop
        assert dialed == [("93.184.216.34", 80)]            # connected to the checked public IP
        assert site.seen == [("rebind.example", "/news")]   # Host header is the hostname
    finally:
        site.close()


def test_fetch_refuses_a_host_that_resolves_private(monkeypatch):
    import urllib.error
    _rebinding_dns(monkeypatch, {"inside.example": ["10.0.0.5"], "mixed.example": ["93.184.216.34", "::ffff:127.0.0.1"]})
    monkeypatch.setattr(hosted, "_connect", lambda *a: (_ for _ in ()).throw(AssertionError("must not connect")))
    for url in ("http://inside.example/", "http://169.254.169.254/latest/meta-data/", "http://[::ffff:10.0.0.1]/"):
        with pytest.raises(urllib.error.URLError):
            _get(url)


def test_every_redirect_hop_is_checked_and_pinned(monkeypatch):
    import urllib.error
    site = _Site({"/a": (302, {"Location": "http://second.example/b"}, b""),
                  "/b": (200, {}, b"ok"),
                  "/evil": (302, {"Location": "http://rebind.example/b"}, b"")})
    try:
        # second.example: public on its own lookup -> followed, pinned to that IP
        _rebinding_dns(monkeypatch, {"first.example": ["93.184.216.34"], "second.example": ["93.184.216.35"],
                                     "rebind.example": ["10.0.0.9"]})
        dialed = _route_connects_to(monkeypatch, site)
        with _get("http://first.example/a") as r:
            assert r.read() == b"ok"
        assert dialed == [("93.184.216.34", 80), ("93.184.216.35", 80)]
        assert site.seen == [("first.example", "/a"), ("second.example", "/b")]
        # a redirect to a host that resolves private is refused before connecting
        dialed.clear()
        with pytest.raises(urllib.error.URLError):
            _get("http://first.example/evil")
        assert dialed == [("93.184.216.34", 80)]
    finally:
        site.close()


def test_redirects_capped_at_three_and_body_capped(monkeypatch):
    import urllib.error
    hops = {f"/r{i}": (302, {"Location": f"/r{i + 1}"}, b"") for i in range(3)}
    loop = {f"/s{i}": (302, {"Location": f"/s{i + 1}"}, b"") for i in range(5)}
    site = _Site({**hops, "/r3": (200, {}, b"three"), **loop, "/big": (200, {}, b"x" * (hosted.MAX_BODY + 10))})
    try:
        _rebinding_dns(monkeypatch, {"a.example": ["93.184.216.34"]})
        _route_connects_to(monkeypatch, site)
        with _get("http://a.example/r0") as r:             # exactly 3 redirects: followed
            assert r.read() == b"three"
        site.seen.clear()
        with pytest.raises(urllib.error.URLError, match="redirects"):
            _get("http://a.example/s0")                    # a 4th redirect is refused
        assert [p for _, p in site.seen] == ["/s0", "/s1", "/s2", "/s3"]
        with _get("http://a.example/big") as r:
            assert len(r.read()) == hosted.MAX_BODY and r.read() == b""
    finally:
        site.close()


def test_https_pins_the_ip_and_uses_the_hostname_for_sni(monkeypatch):
    site = _Site({"/s": (200, {}, b"secure")})
    sni = []

    class FakeContext:
        def wrap_socket(self, sock, server_hostname=None):
            sni.append(server_hostname)
            return sock                                    # plaintext to the local stand-in
    try:
        monkeypatch.setattr(hosted, "_ssl_context", FakeContext)
        _rebinding_dns(monkeypatch, {"tls.example": ["93.184.216.34", "127.0.0.1"]})
        dialed = _route_connects_to(monkeypatch, site)
        with _get("https://tls.example/s") as r:
            assert r.read() == b"secure"
        assert dialed == [("93.184.216.34", 443)] and sni == ["tls.example"]
    finally:
        site.close()


# -- a failed writer is refunded only when nothing was billed -----------------------------------------

def _live_writer(monkeypatch, reply):
    """Live mode with a stubbed Router chat endpoint: `reply()` returns a response object or raises."""
    monkeypatch.delenv("ANIME_NEWS_MOCK", raising=False)
    monkeypatch.setenv("COMFY_API_KEY", "test-key-not-real")
    calls = []

    def urlopen(req, timeout=None):
        calls.append(req.full_url)
        return reply()
    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    return calls


class _Reply:
    def __init__(self, body: bytes, headers=None):
        self.body, self.headers = body, headers or {}

    def read(self):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass


def test_writer_failure_after_a_billed_reply_keeps_the_charge(tmp_path, monkeypatch):
    calls = _live_writer(monkeypatch, lambda: _Reply(b'{"content": [{"type": "text", "text": "not json at all"}]}',
                                                     {"X-Comfy-Credits-Used": "12"}))
    c = client(tmp_path)
    r = c.post("/api/storyboard", json={"headline": "Mayor bans Mondays"}, headers=AUTH)
    assert r.status_code == 502 and len(calls) == 2          # two billed replies, both rejected
    h = c.get("/api/health", headers=AUTH).json()
    assert h["storyboards_left_for_you"] == 8 and h["budget_remaining_usd"] == 39.9   # charge kept


def test_writer_failure_with_no_billed_call_is_refunded(tmp_path, monkeypatch):
    import io
    import urllib.error

    def refused():
        raise urllib.error.HTTPError("https://api.comfy.org/v2/models/x", 402, "Payment Required", {},
                                     io.BytesIO(b"insufficient_credits"))
    calls = _live_writer(monkeypatch, refused)
    c = client(tmp_path)
    r = c.post("/api/storyboard", json={"headline": "Mayor bans Mondays"}, headers=AUTH)
    assert r.status_code == 502 and "writer failed" in r.json()["detail"] and len(calls) == 1
    h = c.get("/api/health", headers=AUTH).json()
    assert h["storyboards_left_for_you"] == 9 and h["budget_remaining_usd"] == 40.0   # refunded


# -- a cancelled reservation is refunded only against the day it was charged ---------------------------

def test_cancel_after_midnight_does_not_refund_yesterday_into_today(tmp_path, monkeypatch):
    day = {"now": "2026-11-02"}
    monkeypatch.setattr(hosted.Usage, "_today", staticmethod(lambda: day["now"]))
    monkeypatch.setenv("ANIME_NEWS_MAX_PARALLEL_JOBS", "1")
    monkeypatch.setattr(rs, "estimate", lambda *a, **k: {"usd": 5.0, "parts": {}})
    gate = threading.Event()
    monkeypatch.setattr(rs, "render", lambda *a, **k: gate.wait(10) and (_ for _ in ()).throw(RuntimeError("x")))
    c = client(tmp_path)
    sb = storyboard(c)
    try:
        first, queued = (c.post("/api/render", json={"storyboard": sb}, headers=AUTH).json()["job_id"] for _ in range(2))
        assert c.get(f"/api/jobs/{queued}", headers=AUTH).json()["status"] == "queued"
        day["now"] = "2026-11-03"                       # UTC midnight passes while the job waits
        storyboard(c)                                    # today's spend: one writer call
        today = c.get("/api/health", headers=AUTH).json()
        assert today["budget_remaining_usd"] == 39.9 and today["remaining_for_you"] == 3
        assert c.delete(f"/api/jobs/{queued}", headers=AUTH).json() == {"cancelled": True}
        after = c.get("/api/health", headers=AUTH).json()
        assert after["budget_remaining_usd"] == 39.9 and after["remaining_for_you"] == 3   # today untouched
        assert after["storyboards_left_for_you"] == today["storyboards_left_for_you"]
    finally:
        gate.set()


def test_same_day_cancel_still_refunds(tmp_path, monkeypatch):
    monkeypatch.setattr(hosted.Usage, "_today", staticmethod(lambda: "2026-11-02"))
    u = hosted.Usage(tmp_path)
    day = u.charge("203.0.113.7", "renders", 5.0)
    assert day == "2026-11-02" and u.summary("203.0.113.7")["budget_remaining_usd"] == 35.0
    u.refund("203.0.113.7", "renders", 5.0, day)
    assert u.summary("203.0.113.7") == {"renders_left_for_you": 3, "storyboards_left_for_you": 9,
                                         "budget_remaining_usd": 40.0}
