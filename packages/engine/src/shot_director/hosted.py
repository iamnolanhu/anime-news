"""Hosted mode: one shared engine behind the public site, paid for by one Comfy account, so it is capped.

ANIME_NEWS_HOSTED=1 turns it on. Then:
- every request needs ANIME_NEWS_ACCESS_TOKEN as `x-access-token` or `Authorization: Bearer` (401 otherwise);
  the site's server-side proxy adds it, so browsers never see it;
- PUT /api/config is refused (403): the Comfy key comes only from the box's environment or config file;
- each visitor (client IP) gets ANIME_NEWS_IP_RENDERS_PER_DAY renders (default 3) and
  ANIME_NEWS_IP_STORYBOARDS_PER_DAY storyboards (default 3x the renders) per UTC day;
- the whole engine stops spending at ANIME_NEWS_DAILY_BUDGET_USD a day (default 40): the sum of the estimate of
  every render accepted today plus every writer call;
- storyboards are clamped to ANIME_NEWS_MAX_MINUTES (default 1), renders are forced to ANIME_NEWS_FORCE_QUALITY
  (default 480p), and at most ANIME_NEWS_MAX_PARALLEL_JOBS renders (default 2) run at once; the rest queue.

Usage persists in <media>/hosted-usage.json (client IPs are stored hashed). Nothing here ever returns the key or the
access token.
"""

import hashlib
import hmac
import http.client
import ipaddress
import json
import os
import socket
import threading
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

USAGE_FILE = "hosted-usage.json"
JOBS_FILE = "jobs.json"


def enabled() -> bool:
    return os.environ.get("ANIME_NEWS_HOSTED") == "1"


def _num(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, "") or default)
    except ValueError:
        return default


def limits() -> dict:
    renders = int(_num("ANIME_NEWS_IP_RENDERS_PER_DAY", 3))
    return {
        "daily_budget_usd": _num("ANIME_NEWS_DAILY_BUDGET_USD", 40.0),
        "renders_per_ip_per_day": renders,
        "storyboards_per_ip_per_day": int(_num("ANIME_NEWS_IP_STORYBOARDS_PER_DAY", renders * 3)),
        "max_minutes": _num("ANIME_NEWS_MAX_MINUTES", 1.0),
        "quality": os.environ.get("ANIME_NEWS_FORCE_QUALITY", "480p") or "480p",
        "max_parallel_jobs": max(1, int(_num("ANIME_NEWS_MAX_PARALLEL_JOBS", 2))),
        "writer_usd": _num("ANIME_NEWS_PRICE_WRITER", 0.10),
    }


def access_ok(headers) -> bool:
    """True when the request carries the access token. Hosted mode with no token configured refuses everything."""
    expected = os.environ.get("ANIME_NEWS_ACCESS_TOKEN", "").strip()
    if not expected:
        return False
    given = headers.get("x-access-token") or ""
    auth = headers.get("authorization") or ""
    if not given and auth.lower().startswith("bearer "):
        given = auth[7:].strip()
    return hmac.compare_digest(given.encode(), expected.encode())


def client_ip(headers, fallback: str | None) -> str:
    """The visitor's IP: the site proxy's x-client-ip, else the first x-forwarded-for hop, else x-real-ip."""
    for name in ("x-client-ip", "x-forwarded-for", "x-real-ip"):
        v = (headers.get(name) or "").split(",")[0].strip()
        if v:
            return v
    return fallback or "unknown"


def seconds_to_midnight(now: datetime | None = None) -> int:
    now = now or datetime.now(timezone.utc)
    tomorrow = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return max(1, int((tomorrow - now).total_seconds()))


class LimitReached(Exception):
    def __init__(self, message: str, retry_after: int):
        super().__init__(message)
        self.message, self.retry_after = message, retry_after


class Usage:
    """Per-day spend and per-IP counts, persisted as JSON in the media dir."""

    def __init__(self, media: Path):
        self.path = Path(media) / USAGE_FILE
        self.lock = threading.Lock()

    @staticmethod
    def _today() -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")

    @staticmethod
    def _who(ip: str) -> str:
        return hashlib.sha256(f"anime-news|{ip}".encode()).hexdigest()[:16]

    def _load(self) -> dict:
        try:
            doc = json.loads(self.path.read_text())
        except (OSError, ValueError):
            doc = {}
        if not isinstance(doc, dict) or doc.get("day") != self._today():
            doc = {"day": self._today(), "spent_usd": 0.0, "renders": {}, "storyboards": {}}
        doc.setdefault("renders", {})
        doc.setdefault("storyboards", {})
        doc.setdefault("spent_usd", 0.0)
        return doc

    def _save(self, doc: dict) -> None:
        tmp = self.path.with_name(f".{self.path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
        tmp.write_text(json.dumps(doc, indent=1))
        tmp.replace(self.path)

    def charge(self, ip: str, kind: str, usd: float) -> str:
        """Count one `kind` ("renders" or "storyboards") for this IP and add `usd` to today's spend, or raise
        LimitReached without changing anything. Returns the UTC day charged: pass it to refund()."""
        lim = limits()
        cap = lim["renders_per_ip_per_day"] if kind == "renders" else lim["storyboards_per_ip_per_day"]
        with self.lock:
            doc = self._load()
            who = self._who(ip)
            used = int(doc[kind].get(who, 0))
            if used >= cap:
                noun = "renders" if kind == "renders" else "storyboards"
                raise LimitReached(f"{cap} {noun} per day per visitor on the shared demo engine; back tomorrow, "
                                   f"or run your own engine", seconds_to_midnight())
            if float(doc["spent_usd"]) + usd > lim["daily_budget_usd"]:
                raise LimitReached("daily demo budget reached, back tomorrow (or run your own engine)",
                                   seconds_to_midnight())
            doc[kind][who] = used + 1
            doc["spent_usd"] = round(float(doc["spent_usd"]) + usd, 4)
            self._save(doc)
            return doc["day"]

    def refund(self, ip: str, kind: str, usd: float, day: str | None = None) -> None:
        """Undo a charge whose work never started (writer failed, queued render cancelled). `day` is what charge()
        returned: a reservation from a day that has since rolled over is not refunded against today's budget."""
        self.refund_hashed(self._who(ip), kind, usd, day)

    def refund_hashed(self, who: str, kind: str, usd: float, day: str | None = None) -> None:
        """refund() for a visitor known only by the hash that _who() makes (jobs saved across a restart)."""
        with self.lock:
            doc = self._load()
            if day is not None and day != doc["day"]:
                return
            if doc[kind].get(who):
                doc[kind][who] = int(doc[kind][who]) - 1
            doc["spent_usd"] = round(max(0.0, float(doc["spent_usd"]) - usd), 4)
            self._save(doc)

    def summary(self, ip: str) -> dict:
        lim = limits()
        with self.lock:
            doc = self._load()
        who = self._who(ip)
        return {
            "renders_left_for_you": max(0, lim["renders_per_ip_per_day"] - int(doc["renders"].get(who, 0))),
            "storyboards_left_for_you": max(0, lim["storyboards_per_ip_per_day"] - int(doc["storyboards"].get(who, 0))),
            "budget_remaining_usd": round(max(0.0, lim["daily_budget_usd"] - float(doc["spent_usd"])), 2),
        }


MAX_REDIRECTS = 3
MAX_BODY = 2_000_000          # bytes; storyboard.fetch_url reads at most this much
REDIRECT_CODES = (301, 302, 303, 307, 308)


def _is_public(addr: str) -> bool:
    ip = ipaddress.ip_address(addr.split("%")[0])
    mapped = getattr(ip, "ipv4_mapped", None)
    return (mapped or ip).is_global


def _resolve_public(url: str) -> tuple[urllib.parse.SplitResult, str, int]:
    """(parts, ip, port) for an http(s) URL whose host resolves ONLY to public addresses. One DNS lookup; the caller
    connects to the returned ip, so a second (rebound) answer can never be used. Raises URLError otherwise."""
    try:
        parts = urllib.parse.urlsplit(url)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise urllib.error.URLError("not an http(s) URL")
        port = parts.port or (443 if parts.scheme == "https" else 80)
        infos = socket.getaddrinfo(parts.hostname, port, type=socket.SOCK_STREAM)
        addrs = [info[4][0] for info in infos]
        if not addrs or not all(_is_public(a) for a in addrs):
            raise urllib.error.URLError("not a public address")
    except (OSError, ValueError, UnicodeError) as e:
        if isinstance(e, urllib.error.URLError):
            raise
        raise urllib.error.URLError(f"cannot resolve: {type(e).__name__}") from e
    return parts, addrs[0], port


def public_url(url: str) -> bool:
    """True when every address the URL's host resolves to is a public one (no loopback, private, link-local or
    cloud metadata addresses): the shared box must not fetch its own network for a visitor."""
    try:
        _resolve_public(url)
    except urllib.error.URLError:
        return False
    return True


def _connect(ip: str, port: int, timeout: float) -> socket.socket:
    return socket.create_connection((ip, port), timeout)


def _ssl_context():
    import ssl
    return ssl.create_default_context()


class _PinnedHTTP(http.client.HTTPConnection):
    """Connects to an already-checked IP; the Host header stays the URL's hostname."""

    def __init__(self, host: str, port: int, ip: str, timeout: float):
        super().__init__(host, port, timeout=timeout)
        self._ip = ip

    def connect(self) -> None:
        self.sock = _connect(self._ip, self.port, self.timeout)


class _PinnedHTTPS(http.client.HTTPSConnection):
    """TLS to an already-checked IP; SNI and certificate checks use the URL's hostname."""

    def __init__(self, host: str, port: int, ip: str, timeout: float):
        ctx = _ssl_context()
        super().__init__(host, port, timeout=timeout, context=ctx)
        self._ip, self._ctx = ip, ctx

    def connect(self) -> None:
        self.sock = self._ctx.wrap_socket(_connect(self._ip, self.port, self.timeout), server_hostname=self.host)


class _Capped:
    """The final response, read capped at MAX_BODY bytes; closing it closes the connection."""

    def __init__(self, resp: http.client.HTTPResponse, conn: http.client.HTTPConnection, url: str):
        self._resp, self._conn, self.url = resp, conn, url
        self.status, self.headers, self._left = resp.status, resp.headers, MAX_BODY

    def read(self, amt: int | None = None) -> bytes:
        n = self._left if amt is None or amt < 0 else min(amt, self._left)
        data = self._resp.read(n) if n > 0 else b""
        self._left -= len(data)
        return data

    def geturl(self) -> str:
        return self.url

    def close(self) -> None:
        self._resp.close()
        self._conn.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> None:
        self.close()


def open_public(req: urllib.request.Request, timeout: float):
    """GET for hosted mode, safe against DNS rebinding: each hop resolves the host once, refuses anything that is
    not public, and connects to that very IP (Host header and TLS SNI/certificate use the hostname). Up to
    MAX_REDIRECTS redirects, each checked the same way; the body is capped at MAX_BODY bytes. Proxies are not used."""
    url = req.full_url
    headers = {k: v for k, v in req.header_items() if k.lower() not in ("host", "connection")}
    headers["Connection"] = "close"
    for hop in range(MAX_REDIRECTS + 1):
        parts, ip, port = _resolve_public(url)
        cls = _PinnedHTTPS if parts.scheme == "https" else _PinnedHTTP
        conn = cls(parts.hostname, port, ip, timeout)
        path = urllib.parse.urlunsplit(("", "", parts.path or "/", parts.query, ""))
        try:
            conn.request("GET", path, headers=headers)
            resp = conn.getresponse()
        except BaseException:
            conn.close()
            raise
        if resp.status in REDIRECT_CODES and resp.headers.get("Location"):
            location = resp.headers["Location"]
            resp.close()
            conn.close()
            if hop == MAX_REDIRECTS:
                raise urllib.error.URLError(f"more than {MAX_REDIRECTS} redirects")
            url = urllib.parse.urljoin(url, location)
            continue
        if resp.status >= 400:
            hdrs = resp.headers
            resp.close()
            conn.close()
            raise urllib.error.HTTPError(url, resp.status, resp.reason, hdrs, None)
        return _Capped(resp, conn, url)
    raise urllib.error.URLError(f"more than {MAX_REDIRECTS} redirects")
