"""Trending headlines with no API key and no X/Twitter.

Primary: Google News RSS top stories. Fallback: Hacker News front page (Algolia API).
Every network or parse error becomes an empty list; nothing here raises.
"""

import json
import urllib.request
import xml.etree.ElementTree as ET

GOOGLE_NEWS_RSS = "https://news.google.com/rss?hl=en-US&gl=US&ceid=US:en"
HN_FRONT_PAGE = "https://hn.algolia.com/api/v1/search?tags=front_page"
TIMEOUT = 10
_UA = {"User-Agent": "shot-director/0.1"}


def _get(url: str) -> bytes:
    req = urllib.request.Request(url, headers=_UA)
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return r.read()


def _split_source(title: str, source: str) -> tuple[str, str]:
    """Google appends ' - Source' to every title; move it into the source field."""
    title = (title or "").strip()
    if " - " in title:
        head, tail = title.rsplit(" - ", 1)
        if not source or tail.strip() == source.strip():
            return head.strip(), (source or tail).strip()
    return title, (source or "").strip()


def google_news(limit: int = 10) -> list[dict]:
    try:
        raw = _get(GOOGLE_NEWS_RSS)
        if b"<!DOCTYPE" in raw or b"<!ENTITY" in raw:  # RSS needs neither; refuse entity tricks
            return []
        root = ET.fromstring(raw)
    except Exception:
        return []
    out = []
    for item in root.iter("item"):
        title, source = _split_source(item.findtext("title") or "", item.findtext("source") or "")
        if not title:
            continue
        out.append({"title": title, "link": (item.findtext("link") or "").strip(),
                    "source": source, "published": (item.findtext("pubDate") or "").strip(),
                    "feed": "google_news"})
        if len(out) >= limit:
            break
    return out


def hacker_news(limit: int = 10) -> list[dict]:
    try:
        hits = json.loads(_get(HN_FRONT_PAGE)).get("hits") or []
    except Exception:
        return []
    out = []
    for h in hits:
        title = (h.get("title") or "").strip()
        if not title:
            continue
        out.append({"title": title,
                    "link": h.get("url") or f"https://news.ycombinator.com/item?id={h.get('objectID')}",
                    "source": "Hacker News", "published": h.get("created_at") or "",
                    "feed": "hacker_news"})
        if len(out) >= limit:
            break
    return out


def trending(limit: int = 10) -> list[dict]:
    """Top headlines: Google News first, Hacker News if Google returns nothing. Never raises."""
    try:
        return google_news(limit) or hacker_news(limit)
    except Exception:
        return []


# Satire of a tragedy is not the joke: top() skips stories about deaths and violence.
_SOMBER = ("dead", "death", "dies", "died", "killed", "deadly", "fatal", "murder",
           "shooting", "shot dead", "crash", "massacre", "war ", "bomb", "victim", "suicide",
           "child abuse", "funeral", "missing")


def top(exclude: set[str] | None = None) -> dict:
    """The top story that is not somber (first story if all are), skipping titles in `exclude`.
    {} when no feed answered or every story is excluded."""
    items = [it for it in trending(10) if it["title"] not in (exclude or set())]
    for it in items:
        if not any(w in it["title"].lower() for w in _SOMBER):
            return it
    return items[0] if items else {}
