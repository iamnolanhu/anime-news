"""THE WEEKLY BRIEFING: one episode per ISO week with that week's top headline in five topics.

  anime-news weekly --week 2026-W44 [--weeks 2026-W01..2026-W44] [--out DIR] [--minutes 1] [--quality 480p]
                    [--mock] [--plan-only] [--retry] [--budget-usd 100] [--publish]

Sources (free, no keys): Wikipedia's "Portal:Current events/<year> <Month> <day>" page for each of the 7 days (raw
wikitext, every bullet item with its wiki links and cited sources), cached in <media>/weekly/sources/<week>.json.
Google News RSS search (the week's dates in the query) fills a topic Wikipedia left empty, best effort.
Each item is tagged by topic (ai, crypto, politics, tech, markets) with keyword lists, filtered hard for anything
somber (deaths, wars, attacks, disasters), scored (cited sources + topic keyword hits + absurd-literal potential +
recency in the week, plus last30days engagement when that engine is installed and the week is recent), and one
headline per topic is picked. The storyboard is the anchor spine around those five segments (storyboard.build with
segments=...). Output per week: <media>/weekly/<week>/ (storyboard.json, the render files, episode.mp4,
episode.9x16.mp4, poster.jpg, meta.json) and the manifest <media>/weekly/index.json. Docs: docs/WEEKLY.md.
"""

import json
import math
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from . import news, storyboard

TOPICS = ("ai", "crypto", "politics", "tech", "markets")
WIKI_RAW = "https://en.wikipedia.org/w/index.php?title={title}&action=raw"
WIKI_PAGE = "https://en.wikipedia.org/wiki/{title}"
GOOGLE_SEARCH = "https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en"
UA = {"User-Agent": "anime-news/0.1 (https://github.com/iamnolanhu/anime-news; weekly briefing sources)"}
TIMEOUT = 15
HEADLINE_MAX = storyboard.CAPTION_MAX
MANIFEST = "index.json"
LEDGER = "ledger.jsonl"
DEFAULT_BUDGET_USD = 100.0

# Keyword lists per topic (lower case; matched on word starts so "ai" never matches "said").
KEYWORDS = {
    "ai": ["artificial intelligence", "ai", "a.i.", "openai", "chatgpt", "chatbot", "machine learning", "llm",
           "large language model", "anthropic", "claude", "gemini", "deepmind", "generative", "neural network",
           "deepfake", "superintelligence", "agi", "copilot", "xai", "grok", "mistral", "deepseek"],
    "crypto": ["bitcoin", "ethereum", "crypto", "cryptocurrency", "cryptocurrencies", "blockchain", "stablecoin",
               "binance", "coinbase", "dogecoin", "nft", "digital currency", "digital asset", "solana", "token",
               "memecoin", "crypto exchange", "satoshi"],
    "politics": ["election", "elected", "parliament", "president", "prime minister", "senate", "congress",
                 "government", "minister", "referendum", "party", "legislation", "coalition", "cabinet",
                 "vote", "voter", "campaign", "governor", "mayor", "bill", "law", "supreme court", "sanction",
                 "summit", "diplomat*", "treaty", "resign", "inaugurat*", "knesset", "white house", "impeach*"],
    "tech": ["technology", "software", "apple", "google", "microsoft", "amazon", "meta", "smartphone", "iphone",
             "satellite", "spacex", "rocket", "launch", "internet", "cyber", "semiconductor", "chip", "chips",
             "robot", "robotics", "tesla", "electric vehicle", "starship", "space", "nasa", "app", "outage",
             "quantum", "telescope", "spacecraft", "moon", "mars", "data center", "nvidia", "tiktok"],
    "markets": ["stock", "stocks", "shares", "index", "dow jones", "s&p", "nasdaq", "market", "interest rate",
                "central bank", "federal reserve", "inflation", "gdp", "ipo", "initial public offering", "merger",
                "acquisition", "acquire", "earnings", "tariff", "bond", "currency", "oil price", "recession",
                "valuation", "trillion", "billion", "investor", "bank of", "economy", "trade deal"],
}
# Matching: a keyword matches a whole word plus a plural/verb ending (launch -> launches, launched); a keyword
# ending in "*" matches any word starting with it (impeach* -> impeachment).
# Wikipedia's own category headings, as a topic nudge.
CATEGORY_TOPIC = {"business and economy": "markets", "politics and elections": "politics",
                  "science and technology": "tech", "international relations": "politics"}
STRONG_CATEGORIES = ("business and economy", "politics and elections", "science and technology")
# Never a source of a comedy segment, whatever the item says.
SOMBER_CATEGORIES = ("armed conflicts and attacks", "disasters and accidents")
# news._SOMBER (deaths, violence) plus wars, attacks and disasters, applied to the item and its parent bullets.
SOMBER_EXTRA = ("kill", "dead", "death", "die", "wound", "injur*", "casualt*", "war", "warfare", "wartime",
                "attack", "strike", "airstrike", "missile", "bomb", "explosion", "explode", "shooting", "shot",
                "gunman", "gunmen", "gunfire", "stab*", "knife", "hostage", "kidnap*", "terror*", "militant",
                "troops", "invasion", "invade", "military", "army", "conflict", "ceasefire", "genocide",
                "massacr*", "execution", "executed", "earthquake", "flood", "hurricane", "cyclone", "typhoon",
                "tornado", "wildfire", "landslide", "tsunami", "eruption", "famine", "epidemic", "outbreak",
                "pandemic", "crash", "collision", "derail*", "sink", "sank", "sunk", "drown*", "disaster", "victim",
                "funeral", "murder", "rape", "abuse", "assault", "suicide", "refugee", "evacuat*", "humanitarian",
                "shelling", "gaza", "violence", "violent", "riot", "weapon", "nuclear", "shooter", "fatal*")
# Words that promise a literal, absurd picture (the show's joke): a small bonus.
ABSURD = ("ban", "bans", "record", "largest", "biggest", "first", "giant", "robot", "meme", "billion", "trillion",
          "rename", "unveil", "launch", "crown", "world's", "approves", "rejects", "self-proclaim", "proclaim",
          "plurality", "embargo", "rate", "most-subscribed", "debut", "lowest", "highest", "surge", "plunge")
_MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
           "November", "December")


def _log(msg: str) -> None:
    print(f"[weekly] {msg}", file=sys.stderr, flush=True)


# -- weeks --------------------------------------------------------------------------------------------

def parse_week(text: str) -> tuple[int, int]:
    m = re.fullmatch(r"\s*(\d{4})-?W(\d{1,2})\s*", str(text or ""), re.I)
    if not m:
        raise ValueError(f"not an ISO week (use 2026-W44): {text!r}")
    year, wk = int(m.group(1)), int(m.group(2))
    try:
        date.fromisocalendar(year, wk, 1)
    except ValueError as e:
        raise ValueError(f"no such ISO week: {text!r}") from e
    return year, wk


def week_id(year: int, wk: int) -> str:
    return f"{year}-W{wk:02d}"


def week_days(week: str) -> list[date]:
    year, wk = parse_week(week)
    monday = date.fromisocalendar(year, wk, 1)
    return [monday + timedelta(days=i) for i in range(7)]


def week_range(spec: str) -> list[str]:
    """'2026-W42..2026-W44' (or a single week) -> the weeks in that order; A > B runs newest first."""
    parts = [p for p in str(spec).split("..")]
    if len(parts) == 1:
        return [week_id(*parse_week(parts[0]))]
    if len(parts) != 2:
        raise ValueError(f"use A..B, e.g. 2026-W01..2026-W44: {spec!r}")
    a, b = (date.fromisocalendar(*parse_week(p), 1) for p in parts)
    step = 7 if b >= a else -7
    out, d = [], a
    while (d <= b) if step > 0 else (d >= b):
        y, w, _ = d.isocalendar()
        out.append(week_id(y, w))
        d += timedelta(days=step)
    return out


def week_label(week: str) -> str:
    days = week_days(week)
    a, b = days[0], days[-1]
    if a.month == b.month:
        return f"{_MONTHS[a.month - 1][:3].upper()} {a.day}-{b.day}"
    return f"{_MONTHS[a.month - 1][:3].upper()} {a.day} - {_MONTHS[b.month - 1][:3].upper()} {b.day}"


def finished(week: str, today: date | None = None) -> bool:
    return week_days(week)[-1] < (today or datetime.now(timezone.utc).date())


# -- Wikipedia ----------------------------------------------------------------------------------------

def day_title(d: date) -> str:
    return f"Portal:Current_events/{d.year}_{_MONTHS[d.month - 1]}_{d.day}"


def _get(url: str) -> bytes:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return r.read(3_000_000)


_EXT = re.compile(r"\[(https?://[^\s\]]+)(?:\s+([^\]]*))?\]")
_WIKILINK = re.compile(r"\[\[([^\]|]+)(?:\|([^\]]*))?\]\]")


def _plain(wikitext: str) -> tuple[str, list[str], list[dict]]:
    """(plain text, wiki link targets, cited sources [{name, url}]) of one bullet's wikitext."""
    sources = []
    for url, label in _EXT.findall(wikitext):
        name = re.sub(r"'{2,}", "", (label or "").strip()).strip("() ").strip()
        sources.append({"name": name or urllib.parse.urlparse(url).netloc, "url": url})
    text = _EXT.sub("", wikitext)
    links = [t.strip() for t, _ in _WIKILINK.findall(text) if not t.strip().lower().startswith(("file:", "image:", "category:"))]
    text = _WIKILINK.sub(lambda m: m.group(2) if m.group(2) is not None else m.group(1), text)
    text = re.sub(r"\{\{[^{}]*\}\}", "", text)
    text = re.sub(r"<ref[^>]*/>|<ref[^>]*>.*?</ref>|<!--.*?-->|<[^>]+>", "", text, flags=re.S)
    text = re.sub(r"'{2,}", "", text)
    text = " ".join(text.split())
    text = re.sub(r"\s+([,.;:])", r"\1", text)
    return text, links, sources


def parse_day(wikitext: str, day: str = "") -> list[dict]:
    """Every news item of one Current events day page: the bullets that cite at least one source.
    Item: {date, category, context (parent bullets, plain), text, links (wiki pages), sources [{name, url}]}."""
    items, category, stack = [], "", []
    for raw in (wikitext or "").splitlines():
        line = raw.rstrip()
        head = re.fullmatch(r"\s*'''(.+?)'''\s*", line)
        if head:
            category, stack = head.group(1).strip(), []
            continue
        m = re.match(r"^(\*+)\s*(.*)$", line)
        if not m:
            continue
        depth, body = len(m.group(1)), m.group(2)
        text, links, sources = _plain(body)
        stack = stack[:depth - 1] + [""] * max(0, depth - 1 - len(stack))
        stack.append(text)
        if not sources or len(text) < 20:
            continue
        items.append({"date": day, "category": category, "context": [c for c in stack[:-1] if c],
                      "text": text, "links": links, "sources": sources})
    return items


def fetch_week(week: str, media: Path, refresh: bool = False, fetch=None) -> dict:
    """The week's Wikipedia items (cached in <media>/weekly/sources/<week>.json; a cache with a failed day is
    refetched). Never raises on network errors: a failed day is recorded with its error."""
    path = Path(media) / "weekly" / "sources" / f"{week}.json"
    polite = fetch is None
    fetch = fetch or _get
    if not refresh and path.exists():
        try:
            doc = json.loads(path.read_text())
            if all(not d.get("error") for d in doc.get("days", [])) and len(doc.get("days", [])) == 7:
                return doc
        except (OSError, ValueError):
            pass
    days = []
    for d in week_days(week):
        title = day_title(d)
        entry = {"date": d.isoformat(), "page": WIKI_PAGE.format(title=title), "items": [], "error": None}
        try:
            raw = fetch(WIKI_RAW.format(title=urllib.parse.quote(title)))
            entry["items"] = parse_day(raw.decode("utf-8", errors="replace"), d.isoformat())
        except Exception as e:  # noqa: BLE001 - a missing day is recorded, not fatal
            entry["error"] = f"{type(e).__name__}: {e}"[:200]
            _log(f"{title}: {entry['error']}")
        days.append(entry)
        if polite:
            time.sleep(0.2)   # polite to Wikipedia
    doc = {"week": week, "fetched": datetime.now(timezone.utc).isoformat(timespec="seconds"), "days": days,
           "google": {}}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=1))
    return doc


def google_news(topic: str, week: str, fetch=None, limit: int = 10) -> list[dict]:
    """Google News RSS search for a topic within the week's dates. Best effort: [] on any error."""
    days = week_days(week)
    words = {"ai": "artificial intelligence", "markets": "stock market"}.get(topic, topic)
    fetch = fetch or _get
    q = f"{words} after:{days[0].isoformat()} before:{(days[-1] + timedelta(days=1)).isoformat()}"
    try:
        raw = fetch(GOOGLE_SEARCH.format(q=urllib.parse.quote(q)))
        if news._has_doctype_or_entity(raw):
            return []
        root = ET.fromstring(raw)
    except Exception:  # noqa: BLE001
        return []
    out = []
    for it in root.iter("item"):
        title, source = news._split_source(it.findtext("title") or "", it.findtext("source") or "")
        if not title:
            continue
        out.append({"date": "", "category": "google news", "context": [], "text": title, "links": [],
                    "sources": [{"name": source or "Google News", "url": (it.findtext("link") or "").strip()}],
                    "feed": "google_news", "published": (it.findtext("pubDate") or "").strip()})
        if len(out) >= limit:
            break
    return out


# -- tagging, filtering, scoring ----------------------------------------------------------------------

def _has(text: str, kw: str) -> bool:
    """Whole-word match with an optional ending (s, es, ed, d, ing); "stem*" matches any word starting with stem."""
    if kw.endswith("*"):
        return re.search(r"(?<![a-z0-9])" + re.escape(kw[:-1]), text) is not None
    return re.search(r"(?<![a-z0-9])" + re.escape(kw) + r"(?:s|es|ed|d|ing)?(?![a-z0-9])", text) is not None


def tag(item: dict) -> dict:
    """{topic: keyword hits} for the item's text and context (+1 for a matching Wikipedia category)."""
    text = " ".join([item.get("text", "")] + item.get("context", []) + item.get("links", [])).lower()
    hits = {t: sum(1 for kw in KEYWORDS[t] if _has(text, kw)) for t in TOPICS}
    category = (item.get("category") or "").lower()
    cat = CATEGORY_TOPIC.get(category)
    if cat and (hits[cat] or category in STRONG_CATEGORIES):
        hits[cat] += 1
    return {t: n for t, n in hits.items() if n}


def somber(item: dict) -> bool:
    """Hard filter: anything about deaths, wars, attacks or disasters, in the item or its parent bullets."""
    if (item.get("category") or "").lower() in SOMBER_CATEGORIES:
        return True
    text = " ".join([item.get("text", "")] + item.get("context", [])).lower()
    # news._SOMBER as word starts ("dies" must not catch "studies"); its "war " is SOMBER_EXTRA's whole-word "war"
    if any(_has(text, w.strip() + "*") for w in news._SOMBER if w.strip() != "war"):
        return True
    return any(_has(text, w) for w in SOMBER_EXTRA)


def score(item: dict, topic: str, day_index: int = 6, boost: float = 0.0) -> float:
    """Engagement proxy: 2 per cited source (Wikipedia editors cite more for bigger stories), the topic's
    keyword hits, the absurd-literal bonus, recency within the week (0-1), a last30days boost, and a small
    penalty for long items (they make poor captions)."""
    text = item.get("text", "").lower()
    absurd = sum(1 for w in ABSURD if _has(text, w))
    hits = tag(item).get(topic, 0)
    return round(2 * len(item.get("sources") or []) + 1.5 * hits + 0.75 * min(absurd, 3) + day_index / 6
                 + boost - max(0, len(text) - 260) / 200, 3)


# Newsroom shorthand, applied before a sentence is cut down to a caption.
_SHORTHAND = [
    (r"\s*\([^)]*\)", ""), (r"\bartificial intelligence\b", "AI"), (r"\binitial public offering\b", "IPO"),
    (r"\bUnited States\b", "US"), (r"\bUnited Kingdom\b", "UK"), (r"\bEuropean Union\b", "EU"),
    (r"\bprime minister\b", "PM"), (r"\bcryptocurrenc(?:y|ies)\b", "crypto"), (r"\bCryptocurrenc(?:y|ies)\b", "Crypto"),
    (r"\b(?:US|C|A)?\$", "$"), (r"(\d)\s*trillion\b", r"\1tn"), (r"(\d)\s*billion\b", r"\1bn"),
    (r"(\d)\s*million\b", r"\1m"), (r"\b(?:approximately|about|up to|some|nearly|around) (?=\$?\d)", ""),
    (r"\b[A-Z][\w.-]*-based ", ""), (r"\b(confirms|says|reports|announces|states) that\b", r"\1"),
    (r"\bannounces (?:an intent|plans|its intention) to\b", "plans to"), (r"\bfederal government\b", "government"),
    (r"^(?:Incumbent|Former|Current) ", ""), (r"\bformer acting\b", "former"), (r"^In [^,]{3,30}, ", ""),
    # a descriptive lead before the subject's name: "American food delivery company DoorDash" -> "DoorDash"
    (r"^(?:[A-Z][\w'-]* )?(?:[a-z][\w-]* ){0,3}(?:company|firm|exchange|startup|newspaper|retailer|maker|giant|"
     r"carrier|platform|bank|automaker|chipmaker|developer) (?=[A-Z])", ""),
]
_CUT_AT = (", ", "; ", " amid ", " after ", " following ", " while ", " as ", " which ", " with ", " before ",
           " between ", " in ", " for ", " to ", " by ", " and ")
_SMALL = {"of", "the", "a", "an", "and", "to", "in", "for", "with", "on", "at", "by", "from", "its", "their", "as",
          "that", "were", "was", "is", "are", "be", "been", "has", "have", "had", "who", "which", "will", "his", "her"}
# the subject's apposition: "Andreas Norlén, speaker of the Swedish parliament, announces" -> "Andreas Norlén announces"
_APPOSITIVE = re.compile(r"^([A-Z][^,]{2,40}), (?!(?:following|after|before|while|despite|including|in|on|at|with|"
                         r"who|which|where|when)\b)(?:the |a |an )?[a-z][^,]{2,60}, ")


def short_headline(text: str, limit: int = HEADLINE_MAX) -> str:
    """A caption-sized headline (<= limit chars) from a Wikipedia sentence: newsroom shorthand, first sentence,
    then the longest prefix that ends at a clause boundary, else a word boundary without a dangling small word."""
    t = " ".join(str(text or "").split())
    for pat, rep in _SHORTHAND:
        t = re.sub(pat, rep, t)
    t = " ".join(t.split()).replace('"', "'")
    t = re.split(r"(?<=[A-Za-z0-9%)'])\.\s+(?=[A-Z])", t)[0].rstrip(". ")
    if len(t) > limit:
        t = _APPOSITIVE.sub(r"\1 ", t, count=1)
    t = t[:1].upper() + t[1:]
    if len(t) <= limit:
        return t
    best = ""
    for marker in _CUT_AT:
        i = t.find(marker)
        while 0 < i <= limit:
            if i >= 28 and i > len(best):
                best = t[:i]
            i = t.find(marker, i + 1)
    if len(best) >= limit * 3 // 4:
        cut = best
    else:
        cut = t[:limit + 1].rsplit(" ", 1)[0]
    words = cut.rstrip(",;: ").split()
    while len(words) > 3 and words[-1].lower().strip(",;:") in _SMALL:
        words.pop()
    return " ".join(words).rstrip(",;:")


def _viral_boosts(items: list[dict], week: str, use_viral: bool) -> list[float]:
    """last30days engagement as a boost, only for weeks inside its 30-day window and only when installed."""
    if not use_viral:
        return [0.0] * len(items)
    try:
        from . import viral
        start = week_days(week)[0]
        age_h = (datetime.now(timezone.utc).date() - start).days * 24
        if not viral.available() or age_h > 30 * 24:
            return [0.0] * len(items)
        stories = viral.viral_stories(topics=["AI", "crypto", "politics", "tech", "stock market"],
                                      hours=max(48, age_h), limit=60)
    except Exception as e:  # noqa: BLE001 - the boost is optional
        _log(f"last30days boost skipped: {type(e).__name__}: {e}"[:200])
        return [0.0] * len(items)
    if not stories:
        return [0.0] * len(items)
    from .viral import _words as vwords
    sets = [(vwords(s["title"]), s.get("engagement") or 0) for s in stories]
    out = []
    for it in items:
        w = vwords(it.get("text", ""))
        best = 0.0
        for sw, eng in sets:
            if w and sw:
                overlap = len(w & sw) / len(w | sw)
                if overlap >= 0.15:
                    best = max(best, overlap * math.log10(1 + eng))
        out.append(round(best, 3))
    return out


def candidates(doc: dict, use_viral: bool = False) -> list[dict]:
    """Every non-somber item with at least one topic, scored per topic."""
    items, day_of = [], {}
    for i, d in enumerate(doc.get("days", [])):
        for it in d.get("items", []):
            items.append(it)
            day_of[id(it)] = i
    for topic, extra in (doc.get("google") or {}).items():
        items.extend(extra)
    keep = [it for it in items if not somber(it) and tag(it)]
    boosts = _viral_boosts(keep, doc.get("week", ""), use_viral)
    out = []
    for it, b in zip(keep, boosts):
        tags = tag(it)
        out.append({**it, "tags": tags, "viral_boost": b,
                    "scores": {t: score(it, t, day_of.get(id(it), 0), b) for t in tags}})
    return out


def pick(cands: list[dict], topics=TOPICS) -> list[dict]:
    """One headline per topic, no item twice. Scarce topics pick first so a shared item goes where it is needed."""
    order = sorted(topics, key=lambda t: sum(1 for c in cands if t in c["tags"]))
    used, chosen = set(), {}
    for t in order:
        pool = sorted((c for c in cands if t in c["tags"] and id(c) not in used),
                      key=lambda c: (-c["scores"][t], len(c["text"])))
        if pool:
            used.add(id(pool[0]))
            chosen[t] = pool[0]
    out = []
    for t in topics:
        c = chosen.get(t)
        if not c:
            continue
        out.append({"topic": t, "headline": short_headline(c["text"]), "text": c["text"],
                    "score": c["scores"][t], "date": c.get("date", ""), "category": c.get("category", ""),
                    "sources": c.get("sources", []), "links": c.get("links", []),
                    "wiki_links": [WIKI_PAGE.format(title=urllib.parse.quote(l.replace(" ", "_"))) for l in c.get("links", [])[:5]],
                    "feed": c.get("feed", "wikipedia")})
    return out


def headlines(week: str, media: Path, refresh: bool = False, use_google: bool = True, use_viral: bool = False,
              fetch=None) -> tuple[list[dict], dict]:
    """The week's five picks (fewer when a topic has no acceptable story) and the sources doc."""
    doc = fetch_week(week, media, refresh=refresh, fetch=fetch)
    picks = pick(candidates(doc, use_viral))
    missing = [t for t in TOPICS if t not in {p["topic"] for p in picks}]
    if missing and use_google:
        for t in missing:
            if t not in doc.setdefault("google", {}):
                doc["google"][t] = [it for it in google_news(t, week, fetch=fetch) if not somber(it)]
        path = Path(media) / "weekly" / "sources" / f"{week}.json"
        path.write_text(json.dumps(doc, indent=1))
        picks = pick(candidates(doc, use_viral))
    return picks, doc


# -- storyboard, estimate, manifest -------------------------------------------------------------------

def segments_of(picks: list[dict]) -> list[dict]:
    return [{"topic": p["topic"], "headline": p["headline"],
             "source_text": p["text"] + (" (" + ", ".join(s["name"] for s in p["sources"][:3]) + ")" if p["sources"] else "")}
            for p in picks]


def biggest(picks: list[dict]) -> dict:
    return max(picks, key=lambda p: p["score"])


def make_storyboard(week: str, picks: list[dict], minutes: float = 1.0, mock: bool | None = None) -> dict:
    if not picks:
        raise ValueError(f"{week}: no acceptable headline in any topic")
    segs = segments_of(picks)
    cold = biggest(picks)["headline"]
    if mock is None or mock:
        sb = storyboard.weekly_template(segs, minutes, headline=cold, week=week,
                                        title_card=f"WEEK {int(week[-2:])}: {week_label(week)}")
        return sb
    sb = storyboard.build(cold, minutes=minutes, segments=segs, week=week, mock=False)
    sb["title_card"] = storyboard._clip_chars(f"WEEK {int(week[-2:])}: {week_label(week)}", 30)
    return sb


def estimate_usd(sb: dict, quality: str, media: Path | None = None) -> dict:
    """Render estimate plus the one writer call a live weekly storyboard costs."""
    from . import render_storyboard as rs
    est = rs.estimate(sb, quality, media)
    return {**est, "writer_usd": rs.PRICE["writer"], "total_usd": round(est["usd"] + rs.PRICE["writer"], 2)}


def week_dir(media: Path, week: str) -> Path:
    return Path(media) / "weekly" / week


def read_meta(media: Path, week: str) -> dict:
    try:
        return json.loads((week_dir(media, week) / "meta.json").read_text())
    except (OSError, ValueError):
        return {}


def _write_json(path: Path, doc) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(json.dumps(doc, indent=1))
    tmp.replace(path)


def _url(week: str, name: str | None) -> str | None:
    return f"/media/weekly/{week}/{name}" if name else None


def manifest_entry(meta: dict) -> dict:
    week, files = meta["week"], meta.get("files") or {}
    return {"week": week, "start": meta.get("start"), "end": meta.get("end"), "label": meta.get("label"),
            "title": meta.get("title"), "status": meta.get("status"), "published": bool(meta.get("published")),
            "headlines": [{"topic": h["topic"], "headline": h["headline"]} for h in meta.get("headlines", [])],
            "duration": meta.get("duration"), "minutes": meta.get("minutes"), "quality": meta.get("quality"),
            "mp4_url": _url(week, files.get("mp4")), "vertical_url": _url(week, files.get("vertical")),
            "poster_url": _url(week, files.get("poster")), "meta_url": _url(week, "meta.json"),
            "updated": meta.get("updated")}


def update_manifest(media: Path) -> dict:
    """Rebuild <media>/weekly/index.json from every week's meta.json, newest week first."""
    root = Path(media) / "weekly"
    weeks = []
    for meta_path in sorted(root.glob("*-W*/meta.json")):
        try:
            meta = json.loads(meta_path.read_text())
        except (OSError, ValueError):
            continue
        if meta.get("week"):
            weeks.append(manifest_entry(meta))
    weeks.sort(key=lambda w: w["week"], reverse=True)
    doc = {"series": storyboard.SERIES, "updated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "weeks": weeks}
    _write_json(root / MANIFEST, doc)
    return doc


def read_manifest(media: Path) -> dict:
    try:
        return json.loads((Path(media) / "weekly" / MANIFEST).read_text())
    except (OSError, ValueError):
        return {"series": storyboard.SERIES, "updated": None, "weeks": []}


def _ledger(media: Path, line: dict) -> None:
    path = Path(media) / "weekly" / LEDGER
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        f.write(json.dumps({"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), **line}) + "\n")


def base_meta(week: str, picks: list[dict], sb: dict, minutes: float, quality: str, est: dict) -> dict:
    days = week_days(week)
    return {"week": week, "series": storyboard.SERIES, "start": days[0].isoformat(), "end": days[-1].isoformat(),
            "dates": [d.isoformat() for d in days], "label": week_label(week), "title": sb.get("title_card"),
            "cold_open": sb["headline"],
            "headlines": [{k: p[k] for k in ("topic", "headline", "text", "date", "category", "score", "sources",
                                             "wiki_links", "feed")} for p in picks],
            "minutes": minutes, "quality": quality, "storyboard_shots": storyboard.stats(sb)["shots"],
            "cost_estimate": est["total_usd"], "estimate": est,
            "files": {"storyboard": "storyboard.json"}, "published": False,
            "updated": datetime.now(timezone.utc).isoformat(timespec="seconds")}


# -- the shared cache: style sheet, cast sheets, voices and music are made once for every week ----------

def _cache_names(d: Path) -> list[Path]:
    out = [d / "style-sheet.png", d / "music"]
    out += list(d.glob("cast-*.png")) + list(d.glob("voice-*.mp3"))
    return [p for p in out if p.exists()]


def share_cache(media: Path, wdir: Path) -> None:
    """Link the media dir's cached cast assets into the week dir so a week never pays for them again."""
    for p in _cache_names(Path(media)):
        dst = wdir / p.name
        if not dst.exists() and not dst.is_symlink():
            dst.symlink_to(p.resolve())


def collect_cache(media: Path, wdir: Path) -> None:
    """Move cast assets a week render created into the media dir (and link them back) for the next week."""
    for p in _cache_names(wdir):
        if p.is_symlink():
            continue
        dst = Path(media) / p.name
        if dst.exists():
            continue
        shutil.move(str(p), str(dst))
        p.symlink_to(dst.resolve())


def poster(mp4: Path, out: Path) -> Path | None:
    """One frame of the episode, just after the cold open, as poster.jpg."""
    try:
        probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(mp4)],
                               capture_output=True, text=True, timeout=60)
        dur = float(probe.stdout.strip() or 0)
        t = min(6.0, max(0.5, dur / 4))
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", f"{t:.2f}", "-i", str(mp4), "-frames:v", "1", "-q:v", "3",
                        str(out)], check=True, capture_output=True, timeout=120)
        return out if out.exists() else None
    except Exception as e:  # noqa: BLE001 - a missing poster never fails the week
        _log(f"poster failed: {type(e).__name__}: {e}"[:200])
        return None


def _finish_files(wdir: Path, entry: dict) -> dict:
    """Give the render's outputs stable names: episode.mp4, episode.9x16.mp4, poster.jpg."""
    files = {"storyboard": "storyboard.json", "shots": entry.get("shots_file"), "plan": entry.get("plan")}
    if entry.get("file") and (wdir / entry["file"]).exists():
        os.replace(wdir / entry["file"], wdir / "episode.mp4")
        files["mp4"] = "episode.mp4"
    elif (wdir / "episode.mp4").exists():
        files["mp4"] = "episode.mp4"
    if entry.get("vertical_file") and (wdir / entry["vertical_file"]).exists():
        os.replace(wdir / entry["vertical_file"], wdir / "episode.9x16.mp4")
        files["vertical"] = "episode.9x16.mp4"
    elif (wdir / "episode.9x16.mp4").exists():
        files["vertical"] = "episode.9x16.mp4"
    if files.get("mp4") and poster(wdir / "episode.mp4", wdir / "poster.jpg"):
        files["poster"] = "poster.jpg"
    return files


def _render_fields(entry: dict) -> dict:
    failed = len(entry.get("errors") or [])
    return {"status": "done" if not failed else "partial", "episode_id": entry.get("episode_id"),
            "duration": entry.get("duration"), "cut_shots": entry.get("cut_shots"),
            "rendered_shots": entry.get("rendered_shots"), "failed_shots": failed, "errors": entry.get("errors"),
            "mode": entry.get("mode"), "credits_used": entry.get("credits_used"),
            "seconds_to_render": entry.get("seconds_to_render"), "finished": entry.get("finished")}


# -- one week -----------------------------------------------------------------------------------------

def plan_week(week: str, media: Path, minutes: float = 1.0, quality: str = "480p", mock: bool | None = None,
              refresh: bool = False, use_google: bool = True, use_viral: bool = False, fetch=None) -> dict:
    """Sources + storyboard + meta (status "planned" unless the week already rendered). Nothing is rendered."""
    picks, _ = headlines(week, media, refresh=refresh, use_google=use_google, use_viral=use_viral, fetch=fetch)
    sb = make_storyboard(week, picks, minutes, mock)
    wdir = week_dir(media, week)
    est = estimate_usd(sb, quality, Path(media))
    meta = base_meta(week, picks, sb, minutes, quality, est)
    prev = read_meta(media, week)
    if prev.get("status") in ("done", "partial"):   # keep the rendered week as it is; the new plan sits beside it
        _write_json(wdir / "storyboard.next.json", sb)
        meta = {**prev, "next_storyboard": "storyboard.next.json", "next_estimate": est["total_usd"]}
    else:
        _write_json(wdir / "storyboard.json", sb)
        meta["status"] = "planned"
    _write_json(wdir / "meta.json", meta)
    update_manifest(media)
    return {"week": week, "picks": picks, "storyboard": sb, "estimate": est, "meta": meta}


def render_week(week: str, media: Path, router, minutes: float = 1.0, quality: str = "480p",
                max_cost: float | None = None, plan: dict | None = None, progress=None) -> dict:
    from . import render_storyboard as rs
    plan = plan or plan_week(week, media, minutes, quality, mock=router.mode != "live")
    sb, wdir = plan["storyboard"], week_dir(media, week)
    wdir.mkdir(parents=True, exist_ok=True)
    share_cache(Path(media), wdir)
    meta = {**base_meta(week, plan["picks"], sb, minutes, quality, plan["estimate"]), "status": "rendering"}
    _write_json(wdir / "meta.json", meta)
    update_manifest(media)
    try:
        entry = rs.render(sb, router, wdir, quality=quality, vertical=True, max_cost=max_cost, progress=progress)
    except BaseException as e:
        meta.update(status="failed", error=f"{type(e).__name__}: {e}"[:300])
        _write_json(wdir / "meta.json", meta)
        update_manifest(media)
        raise
    finally:
        collect_cache(Path(media), wdir)
    meta.update(_render_fields(entry), files=_finish_files(wdir, entry))
    meta["updated"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    _write_json(wdir / "meta.json", meta)
    update_manifest(media)
    return meta


def retry_week(week: str, media: Path, router, max_cost: float | None = None) -> dict:
    """Re-render only the failed shots of a partial week, then re-cut (render_storyboard.retry)."""
    from . import render_storyboard as rs
    meta = read_meta(media, week)
    wdir = week_dir(media, week)
    shots = (meta.get("files") or {}).get("shots")
    if not shots or not (wdir / shots).exists():
        raise LookupError(f"{week}: no shots file to retry")
    share_cache(Path(media), wdir)
    try:
        entry = rs.retry(router, wdir, wdir / shots, vertical=True, max_cost=max_cost)
    finally:
        collect_cache(Path(media), wdir)
    meta.update(_render_fields(entry), files=_finish_files(wdir, entry))
    meta["retried"] = int(meta.get("retried") or 0) + 1
    meta["updated"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    _write_json(wdir / "meta.json", meta)
    update_manifest(media)
    return meta


def publish(media: Path, weeks: list[str]) -> list[str]:
    """Mark rendered weeks as published (the site lists published weeks). Returns the weeks it marked."""
    done = []
    for week in weeks:
        meta = read_meta(media, week)
        if meta.get("status") in ("done", "partial") and (meta.get("files") or {}).get("mp4"):
            meta["published"] = True
            _write_json(week_dir(media, week) / "meta.json", meta)
            done.append(week)
    update_manifest(media)
    return done


def print_plan(plan: dict, out=None) -> None:
    out = out or sys.stdout
    est = plan["estimate"]
    print(f"{plan['week']} ({week_label(plan['week'])}): {len(plan['picks'])} headlines, "
          f"{storyboard.stats(plan['storyboard'])['shots']} shots, estimate ${est['total_usd']:.2f} at {est['quality']} "
          f"(render ${est['usd']:.2f} + writer ${est['writer_usd']:.2f})", file=out)
    for p in plan["picks"]:
        src = ", ".join(s["name"] for s in p["sources"][:3])
        print(f"  {p['topic']:<9} {p['headline']}   [{p['date'] or 'news'}; {src}]", file=out)
    print(f"  cold open: {plan['storyboard']['headline']}", file=out)


def run(weeks: list[str], media: Path, router=None, minutes: float = 1.0, quality: str = "480p",
        plan_only: bool = False, retry: bool = False, budget_usd: float = DEFAULT_BUDGET_USD, force: bool = False,
        do_publish: bool = False, refresh: bool = False, use_google: bool = True, use_viral: bool = False,
        allow_unfinished: bool = False, out=None) -> dict:
    """The batch: weeks in order, one render at a time. Skips done weeks (unless force), retries partial weeks
    with --retry, stops before a week whose estimate would push the spend over the budget (live only)."""
    out = out or sys.stdout
    media = Path(media)
    spent, results = 0.0, []
    live = router is not None and router.mode == "live"
    for week in weeks:
        if not allow_unfinished and not finished(week):
            print(f"{week}: not over yet, skipped", file=out)
            results.append({"week": week, "status": "skipped", "reason": "unfinished"})
            continue
        meta = read_meta(media, week)
        try:
            if retry:
                if meta.get("status") != "partial":
                    results.append({"week": week, "status": "skipped", "reason": f"status {meta.get('status')}"})
                    continue
                est = meta.get("cost_estimate") or 0
                if live and spent + est > budget_usd:
                    print(f"{week}: budget stop (spent ${spent:.2f} of ${budget_usd:.2f})", file=out)
                    results.append({"week": week, "status": "budget_stop"})
                    break
                m = retry_week(week, media, router, max_cost=budget_usd - spent if live else None)
            else:
                if not plan_only and not force and meta.get("status") == "done":
                    print(f"{week}: done, skipped", file=out)
                    results.append({"week": week, "status": "skipped", "reason": "done"})
                    continue
                # plan-only never calls the writer: the template storyboard has the same shape and shot count
                plan = plan_week(week, media, minutes, quality, mock=plan_only or not live, refresh=refresh,
                                 use_google=use_google, use_viral=use_viral)
                print_plan(plan, out)
                if plan_only:
                    results.append({"week": week, "status": "planned", "estimate": plan["estimate"]["total_usd"]})
                    _ledger(media, {"week": week, "action": "plan", "estimate": plan["estimate"]["total_usd"],
                                    "headlines": [p["headline"] for p in plan["picks"]]})
                    continue
                est = plan["estimate"]["total_usd"]
                if live and spent + est > budget_usd:
                    print(f"{week}: budget stop (spent ${spent:.2f}, this week ${est:.2f}, budget ${budget_usd:.2f})",
                          file=out)
                    results.append({"week": week, "status": "budget_stop"})
                    _ledger(media, {"week": week, "action": "budget_stop", "spent": round(spent, 2)})
                    break
                m = render_week(week, media, router, minutes, quality,
                                max_cost=(budget_usd - spent) if live else None, plan=plan)
            # the budget counts USD estimates: the Router's X-Comfy-Credits-Used header is in credits, not dollars
            # (it is recorded in meta.json and the ledger as credits_used)
            cost = (m.get("cost_estimate") or 0) if live else 0
            spent += float(cost or 0)
            print(f"{week}: {m['status']} {m.get('duration')} s, {m.get('failed_shots', 0)} failed shots, "
                  f"{(m.get('files') or {}).get('mp4')}", file=out)
            results.append({"week": week, "status": m["status"], "cost": cost})
            _ledger(media, {"week": week, "action": "retry" if retry else "render", "status": m["status"],
                            "mode": m.get("mode"), "duration": m.get("duration"), "failed_shots": m.get("failed_shots"),
                            "cost_estimate": m.get("cost_estimate"), "credits_used": m.get("credits_used"),
                            "spent_total": round(spent, 2)})
        except Exception as e:  # noqa: BLE001 - one bad week never stops the batch
            print(f"{week}: FAILED {type(e).__name__}: {e}"[:300], file=out)
            results.append({"week": week, "status": "failed", "error": f"{type(e).__name__}: {e}"[:300]})
            _ledger(media, {"week": week, "action": "retry" if retry else ("plan" if plan_only else "render"),
                            "status": "failed", "error": f"{type(e).__name__}: {e}"[:300]})
    published = publish(media, weeks) if do_publish else []
    if published:
        print(f"published: {', '.join(published)}", file=out)
    update_manifest(media)
    return {"weeks": results, "spent_usd": round(spent, 2), "published": published}
