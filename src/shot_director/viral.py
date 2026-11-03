"""Viral stories: what people are actually sharing, ranked by engagement, via the last30days engine.

last30days (an installed Claude Code skill) searches Reddit, X, YouTube, TikTok and Hacker News for a topic
and returns posts with their likes / upvotes / views. We run it once per topic for the last `hours`, keep
posts that are not about deaths or violence (news.top's filter), fold near-duplicate titles together and
sort by one engagement number. Results are cached on disk for 30 minutes so repeated episode runs do not
re-query. Anything missing or failing (engine not installed, no keys, timeout, bad JSON) returns [] with
one log line; nothing here raises.

Config (env):
  SHOT_DIRECTOR_VIRAL_TOPICS        comma-separated topics (default: DEFAULT_TOPICS)
  SHOT_DIRECTOR_LAST30DAYS          path to last30days.py (default: newest installed plugin copy)
  SHOT_DIRECTOR_LAST30DAYS_PYTHON   Python 3.12+ interpreter for it (default: first found on PATH)
"""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import news

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_TOPICS = ["breaking news", "politics", "tech", "AI", "crypto", "sports", "celebrity"]
SOURCES = ["reddit", "x", "youtube", "tiktok", "hackernews"]
CACHE_SECONDS = 30 * 60
RUN_TIMEOUT = 240  # seconds per topic; a quick run takes about 45 s
WORKERS = 4
_PLUGIN_CACHE = Path.home() / ".claude/plugins/cache/last30days-skill/last30days"
_PLATFORM = {"x": "X", "reddit": "Reddit", "youtube": "YouTube", "tiktok": "TikTok",
             "hackernews": "Hacker News", "instagram": "Instagram", "polymarket": "Polymarket"}
# Headline metric per platform for the "via X, 48K likes" line: (engagement key, label).
_HEADLINE_METRIC = {"x": [("likes", "likes")], "reddit": [("score", "upvotes"), ("upvotes", "upvotes")],
                    "hackernews": [("points", "points"), ("score", "points")],
                    "youtube": [("views", "views")], "tiktok": [("views", "views"), ("plays", "plays")]}


# news._SOMBER plus words social posts use that headline feeds rarely do (a live run surfaced a post
# about Gaza that slipped past the headline list). Applied to cached items too.
_EXTRA_SOMBER = ("genocide", "gaza", "hostage", "terror", "rape", "assault", "abuse", "famine",
                 "massacr", "warfare", "airstrike", "shooter")


def _somber(title: str) -> bool:
    t = title.lower()
    return any(w in t for w in news._SOMBER + _EXTRA_SOMBER)


def _log(msg: str) -> None:
    print(f"[viral] {msg}", file=sys.stderr, flush=True)


def _script() -> Path | None:
    override = os.environ.get("SHOT_DIRECTOR_LAST30DAYS")
    if override:
        p = Path(override).expanduser()
        return p if p.is_file() else None
    found = sorted(_PLUGIN_CACHE.glob("*/skills/last30days/scripts/last30days.py"),
                   key=lambda p: [int(x) if x.isdigit() else x for x in p.parts[-5].split(".")])
    return found[-1] if found else None


def _python() -> str | None:
    override = os.environ.get("SHOT_DIRECTOR_LAST30DAYS_PYTHON")
    if override:
        return override if shutil.which(override) or Path(override).is_file() else None
    if sys.version_info >= (3, 12):
        return sys.executable
    for name in ("python3.13", "python3.12", "python3.14"):
        found = shutil.which(name)
        if found:
            return found
    return None


def available() -> bool:
    """True when the last30days engine and a Python 3.12+ to run it are both present."""
    return _script() is not None and _python() is not None


def _cache_path() -> Path:
    return Path(os.environ.get("SHOT_DIRECTOR_MEDIA", ROOT / "media")) / "viral-cache.json"


def _topics(topics: list[str] | None) -> list[str]:
    if topics:
        return [t.strip() for t in topics if t.strip()]
    env = os.environ.get("SHOT_DIRECTOR_VIRAL_TOPICS", "")
    return [t.strip() for t in env.split(",") if t.strip()] or list(DEFAULT_TOPICS)


def _human(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M".replace(".0M", "M")
    if n >= 1_000:
        return f"{n / 1_000:.1f}K".replace(".0K", "K")
    return str(n)


def _num(v) -> int:
    try:
        return max(0, int(v or 0))
    except (TypeError, ValueError):
        return 0


def _engagement(eng: dict) -> int:
    """One number across platforms: reactions + 2x reshares + comments + views/100."""
    g = lambda *keys: sum(_num(eng.get(k)) for k in keys)
    return (g("likes", "score", "points", "upvotes") + 2 * g("reposts", "retweets", "shares")
            + g("num_comments", "comments", "replies") + g("views", "plays") // 100)


def _engagement_text(platform: str, eng: dict, total: int) -> str:
    for key, label in _HEADLINE_METRIC.get(platform, []):
        if _num(eng.get(key)):
            return f"{_human(_num(eng[key]))} {label}"
    return f"{_human(total)} engagement"


def _clean_title(text: str) -> str:
    line = next((ln for ln in (text or "").splitlines() if ln.strip()), "")
    line = re.sub(r"https?://\S+", "", line)
    line = " ".join(line.split()).strip(" -:|")
    if len(line) > 160:
        line = line[:160].rsplit(" ", 1)[0] + "..."
    return line


def _words(title: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", title.lower()) if len(w) > 2}


def _near_dup(a: set[str], b: set[str]) -> bool:
    if not a or not b:
        return False
    return len(a & b) / len(a | b) >= 0.6


def _run_topic(topic: str, days: int) -> list[dict]:
    """One last30days run for one topic. Returns raw result rows tagged with the topic, or []."""
    script, py = _script(), _python()
    if not script or not py:
        return []
    plan = {"intent": "breaking_news", "freshness_mode": "strict_recent", "cluster_mode": "story",
            "subqueries": [{"label": "primary", "search_query": topic,
                            "ranking_query": f"What {topic} stories went viral in the last {days * 24} hours?",
                            "sources": SOURCES, "weight": 1.0}]}
    env = dict(os.environ)
    # Never route through OpenRouter: the engine picks OpenRouter for its LLM rerank when that key is in its
    # config. Point the endpoint at a closed local port so the call fails fast and the engine falls back to
    # its local scorer. We pass our own --plan, so no planner call is made either.
    env["OPENROUTER_BASE_URL"] = "http://127.0.0.1:9/v1"
    env["LAST30DAYS_MEMORY_DIR"] = ""  # no saved briefings
    env["LAST30DAYS_DISCOVERY_QUEUE"] = "off"
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump(plan, f)
        plan_file = f.name
    try:
        proc = subprocess.run(
            [py, str(script), topic, "--emit=json", "--quick", "--days", str(days),
             "--no-browser-cookies", "--save-dir", "", "--plan", plan_file],
            capture_output=True, text=True, timeout=RUN_TIMEOUT, env=env, stdin=subprocess.DEVNULL)
        if proc.returncode != 0:
            tail = (proc.stderr or "").strip().splitlines()[-1:] or ["no output"]
            raise RuntimeError(f"exit {proc.returncode}: {tail[0][:160]}")
        rows = json.loads(proc.stdout).get("results") or []
    finally:
        Path(plan_file).unlink(missing_ok=True)
    return [dict(r, _topic=topic) for r in rows if isinstance(r, dict)]


# Every text field the engine may return for a post. The somber filter reads all of them, not only the
# shortened first line used as the title: a post can open clean and turn to a tragedy in its body.
_TEXT_FIELDS = ("title", "summary", "selftext", "body", "text", "description", "snippet", "content")


def _to_item(row: dict) -> dict | None:
    if _somber(" ".join(str(row.get(k) or "") for k in _TEXT_FIELDS)):
        return None
    platform = (row.get("source") or "").lower()
    title = _clean_title(row.get("title") or "")
    if len(title) < 25 or title.startswith("@"):
        return None  # replies and fragments are not headlines
    eng = row.get("engagement") or {}
    total = _engagement(eng)
    if total <= 0:
        return None  # nobody engaged: not viral
    name = _PLATFORM.get(platform, platform.title() or "web")
    eng_text = _engagement_text(platform, eng, total)
    return {"title": title, "link": row.get("url") or "", "source": name, "engagement": total,
            "engagement_text": eng_text, "source_text": f"via {name}, {eng_text}",
            "published": row.get("published_at") or "", "topic": row.get("_topic", ""), "feed": "last30days"}


def _published(value) -> datetime | None:
    """Timezone-aware UTC time of a post, or None. Naive values are UTC; a date-only value (what the
    engine usually emits) is the start of that UTC day, the oldest the post can be, so the window stays
    strict at the cost of dropping some posts from the boundary day."""
    if isinstance(value, (int, float)) and value > 0:
        return datetime.fromtimestamp(value, timezone.utc)
    text = str(value or "").strip()
    if not text:
        return None
    try:
        ts = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return ts.replace(tzinfo=timezone.utc) if ts.tzinfo is None else ts.astimezone(timezone.utc)


def _fresh(item: dict, hours: int) -> bool:
    """True only when the post's time is known and inside the last `hours`; undated posts are dropped."""
    ts = _published(item.get("published"))
    return ts is not None and ts >= datetime.now(timezone.utc) - timedelta(hours=hours)


_RUN_LOCK = threading.Lock()  # single flight: at most one engine fetch per process


def refreshing() -> bool:
    """True while an engine fetch is running."""
    return _RUN_LOCK.locked()


def _key(topics: list[str], hours: int) -> dict:
    return {"topics": topics, "hours": hours}


def _read_cache(key: dict) -> dict | None:
    try:
        cached = json.loads(_cache_path().read_text())
        if cached.get("key") == key and time.time() - cached.get("fetched", 0) < CACHE_SECONDS:
            return cached
    except (OSError, ValueError, AttributeError):
        pass
    return None


def _select(items: list[dict], hours: int, exclude: set[str] | None, limit: int) -> list[dict]:
    """Exclusions, somber and freshness filters run BEFORE the limit, so a long exclude list never hides
    an eligible story further down."""
    exclude = exclude or set()
    return [it for it in items if it["title"] not in exclude and not _somber(it["title"])
            and _fresh(it, hours)][:limit]


def _fetch(topics: list[str], hours: int) -> list[dict]:
    """Run the engine for every topic, rank and dedupe, write the cache. Caller holds _RUN_LOCK."""
    days = max(1, -(-hours // 24))
    errors: list[str] = []

    def one(topic: str) -> list[dict]:
        try:
            return _run_topic(topic, days)
        except Exception as e:  # timeout, bad JSON, engine error: this topic just contributes nothing
            errors.append(f"{topic}: {type(e).__name__}: {e}"[:200])
            return []

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        rows = [r for batch in pool.map(one, topics) for r in batch]
    items = [it for it in (_to_item(r) for r in rows) if it and _fresh(it, hours)
             and not _somber(it["title"])]
    items.sort(key=lambda it: it["engagement"], reverse=True)
    kept: list[dict] = []
    seen: list[set[str]] = []
    for it in items:
        w = _words(it["title"])
        if any(_near_dup(w, s) for s in seen):
            continue
        seen.append(w)
        kept.append(it)
    if not kept:
        _log(f"last30days returned no usable stories ({len(errors)} topic errors"
             + (f", first: {errors[0]}" if errors else "") + ")")
        return []
    try:
        _cache_path().parent.mkdir(parents=True, exist_ok=True)
        _cache_path().write_text(json.dumps({"key": _key(topics, hours), "fetched": time.time(),
                                             "items": kept}, indent=1))
    except OSError:
        pass
    return kept


def cached(topics: list[str] | None = None, hours: int = 48, limit: int = 10) -> dict | None:
    """The warm cache only, never the engine: {items, fetched (epoch s)} or None when cold or disabled.
    This is what the public GET /api/news?strategy=viral serves."""
    if not available():
        return None
    hit = _read_cache(_key(_topics(topics), hours))
    if not hit:
        return None
    return {"items": _select(hit.get("items", []), hours, None, limit), "fetched": hit["fetched"]}


def start_refresh(topics: list[str] | None = None, hours: int = 48) -> bool:
    """Start one engine fetch in a background thread. False when one is already running (single flight)
    or the engine is not installed. For the token-gated POST /api/news/refresh."""
    if not available() or not _RUN_LOCK.acquire(blocking=False):
        return False
    topics = _topics(topics)

    def run() -> None:
        try:
            _fetch(topics, hours)
        except Exception as e:
            _log(f"refresh failed: {type(e).__name__}: {e}"[:200])
        finally:
            _RUN_LOCK.release()

    threading.Thread(target=run, name="viral-refresh", daemon=True).start()
    return True


def viral_stories(topics: list[str] | None = None, hours: int = 48, limit: int = 10,
                  exclude: set[str] | None = None) -> list[dict]:
    """Most-engaged non-somber stories across `topics` from the last `hours` that are not in `exclude`,
    deduped, engagement desc. Serves the 30-minute cache (media/viral-cache.json) when warm; otherwise
    runs the engine, waiting for any fetch already in flight instead of starting a second one.
    [] when the engine is missing or fails. For the token-gated episode pick, not public routes."""
    if not available():
        _log("last30days not found (set SHOT_DIRECTOR_LAST30DAYS / SHOT_DIRECTOR_LAST30DAYS_PYTHON); no viral stories")
        return []
    topics = _topics(topics)
    key = _key(topics, hours)
    hit = _read_cache(key)
    if hit:
        return _select(hit.get("items", []), hours, exclude, limit)
    with _RUN_LOCK:
        hit = _read_cache(key)  # a fetch that finished while we waited
        items = hit.get("items", []) if hit else _fetch(topics, hours)
    return _select(items, hours, exclude, limit)
