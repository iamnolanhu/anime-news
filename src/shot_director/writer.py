"""Headline -> episode script, written by one chat model on Comfy Router.

The template planner in episode.py is deterministic, so every episode reads the same. This module asks
one chat model (anthropic/claude-sonnet-5-5 by default) for a script in the deadpan war-briefing voice of
docs/psyopanime-formula.md, as STRICT JSON with exactly the requested scene and shot counts.

Contract: write_script() returns a validated dict, or None on ANY failure (no key, network, HTTP 402,
bad JSON, wrong counts). None means "use the template planner". Results are cached on disk by
(headline, minutes) so a re-run never re-spends credits. See docs/WRITER.md.
"""

import hashlib
import json
import os
import re
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path

from . import presets
from .router_client import BASE_URL, load_api_key

ROOT = Path(__file__).resolve().parents[2]
CHAT_MODEL = os.environ.get("SHOT_DIRECTOR_CHAT_MODEL", "anthropic/claude-sonnet-5-5")
CACHE_NAME = "scripts-cache.json"
MAX_TOKENS = 4000
TIMEOUT_SECONDS = 180
CAPTION_MAX = 40
VISUAL_MAX_WORDS = 25
MOVES = tuple(presets.CAMERA_MOVES)  # dolly_in, dolly_out, orbit, crane_up, handheld, whip_pan, crash_zoom, static

STYLE_LEAD = ("2D cel anime frame, drawn animation, not a photograph, not a 3D render, late-1980s OVA cel "
              "look, heavy ink line art, film grain, VHS softness, teal shadows, burnt-orange highlights")
SAFETY_TAIL = ("Safe for work, no weapons, no gore, no real logos. Every character is an original invented "
               "archetype, no likeness of any real person")

SYSTEM_PROMPT = """You write scripts for "{series}", an original 2D anime news show in the style of a late-1980s OVA: a deadpan war-room briefing that treats one real news headline with total gravitas and illustrates it with absurd literal props.

VOICE
- The narrator is a calm, clipped intelligence briefer. Short declarative sentences. Never winks, never explains the joke.
- The comedy is the contrast: grave military-briefing language applied to a mundane or absurd claim.
- Literalize the headline: turn its abstract claim into a physical, oversized, absurd prop or situation (a market "falling" is a giant chart arrow crashing through a ceiling; a "deal on the table" is a literal table piled with pizza boxes).
- Escalate scene by scene: state the claim, take it literally, escalate it, undercut it at the anchor desk, sign off with a dry cliffhanger line.

CAST (original archetypes only; never anyone else)
- "the Anchor": stern host at a news desk in front of a wall of world-map monitors. Recurring spine of the show.
- "the Field Operative": trench-coated reporter on location, treats everything like a covert mission.
- "the Analyst": sleep-deprived expert surrounded by charts and red string boards.
- Crowds, officials and staffers are invented, unnamed extras.

HARD RULES
- Never depict, describe or name a real person as a character. If the headline names people, refer to them only as roles ("the official", "the executive") or quote the headline text on a screen, ticker or poster.
- The headline text itself may appear on screens, tickers, posters and documents.
- Safe for work. No weapons, no gore, no violence against people, no real company logos or brand marks, no real flags. Invented countries and institutions only.
- Never name an anime studio, film, or artist.
- Visual descriptions are drawn-anime terms (inked, cel, painted background); never photoreal, cinematic, 4K, bokeh or lens terms.

OUTPUT: reply with ONE JSON object and nothing else (no prose, no code fences):
{{
  "logline": "one sentence pitch of this episode",
  "cold_open_caption": "title-card text for the first 1.5 seconds, <= 40 chars",
  "scenes": [
    {{
      "setting": "where the scene happens, drawn-anime terms, <= 20 words",
      "beat": "one word: cold_open | thesis | literal | escalation | anchor_desk | vignette | montage | sign_off",
      "caption": "one clause, <= 40 chars",
      "narration": "1-2 deadpan briefing sentences, <= 30 words total",
      "shots": [
        {{"move": "one of: {moves}",
          "visual": "what the frame shows, <= 25 words, start with the shot size (extreme close-up, wide, overhead...)",
          "caption": "one yellow subtitle clause, <= 40 chars"}}
      ]
    }}
  ],
  "sign_off": "the Anchor's final deadpan line, <= 15 words"
}}
The first shot of the first scene is the hook: an extreme close-up (eyes of the Anchor) with a crash_zoom."""

USER_PROMPT = """Headline: "{headline}"
Source: {source}
Episode length: {minutes} minutes.
Write exactly {scenes} scenes. Shots per scene, in order: {counts} (scene 1 has {first} shots, and so on). The shot counts are exact, not approximate.
Return the JSON object only."""

_cache_lock = threading.Lock()


def _media_dir(media_dir: Path | None) -> Path:
    return Path(media_dir or os.environ.get("SHOT_DIRECTOR_MEDIA", ROOT / "media"))


def _cache_key(headline: str, minutes: float) -> str:
    return hashlib.sha256(f"{headline.strip()}|{float(minutes)}".encode()).hexdigest()[:16]


def _read_cache(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


def _write_cache(path: Path, key: str, script: dict) -> None:
    with _cache_lock:
        data = _read_cache(path)
        data[key] = script
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f".{path.name}.tmp")
        tmp.write_text(json.dumps(data, indent=1))
        tmp.replace(path)


def _clip(text: str, limit: int) -> str:
    """Trim to `limit` chars at a word boundary (soft fix: a long caption is not worth a re-spend)."""
    text = " ".join(str(text).split())
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0].rstrip(",;:-")
    return cut or text[:limit]


def _strip_fences(text: str) -> str:
    text = text.strip()
    m = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if m:
        text = m.group(1).strip()
    start, end = text.find("{"), text.rfind("}")
    return text[start:end + 1] if start != -1 and end > start else text


def validate(script: object, scenes: int, clips_per_scene: list[int]) -> dict:
    """Raise ValueError unless counts and move enums match; returns a normalized copy (captions trimmed)."""
    if not isinstance(script, dict):
        raise ValueError("script is not an object")
    for k in ("logline", "cold_open_caption", "scenes", "sign_off"):
        if k not in script:
            raise ValueError(f"missing key: {k}")
    sc_list = script["scenes"]
    if not isinstance(sc_list, list) or len(sc_list) != scenes:
        raise ValueError(f"expected {scenes} scenes, got {len(sc_list) if isinstance(sc_list, list) else '?'}")
    out_scenes = []
    for i, (sc, want) in enumerate(zip(sc_list, clips_per_scene), 1):
        for k in ("setting", "beat", "caption", "narration", "shots"):
            if not isinstance(sc, dict) or k not in sc:
                raise ValueError(f"scene {i} missing {k}")
        shots = sc["shots"]
        if not isinstance(shots, list) or len(shots) != want:
            raise ValueError(f"scene {i}: expected {want} shots, got {len(shots) if isinstance(shots, list) else '?'}")
        out_shots = []
        for j, s in enumerate(shots, 1):
            if not isinstance(s, dict) or s.get("move") not in MOVES:
                raise ValueError(f"scene {i} shot {j}: bad move {s.get('move') if isinstance(s, dict) else s!r}")
            if not str(s.get("visual", "")).strip():
                raise ValueError(f"scene {i} shot {j}: empty visual")
            words = str(s["visual"]).split()
            out_shots.append({"move": s["move"], "visual": " ".join(words[:VISUAL_MAX_WORDS + 10]),
                              "caption": _clip(s.get("caption") or sc["caption"], CAPTION_MAX)})
        out_scenes.append({"setting": " ".join(str(sc["setting"]).split()), "beat": str(sc["beat"]).strip(),
                           "caption": _clip(sc["caption"], CAPTION_MAX),
                           "narration": " ".join(str(sc["narration"]).split()), "shots": out_shots})
    return {"logline": " ".join(str(script["logline"]).split()),
            "cold_open_caption": _clip(script["cold_open_caption"], CAPTION_MAX),
            "scenes": out_scenes, "sign_off": " ".join(str(script["sign_off"]).split())}


def _chat(system: str, user: str, model: str = CHAT_MODEL) -> str:
    """One synchronous Messages call on Router (POST /v2/models/{model}). Returns the text reply."""
    key = load_api_key()
    if not key:
        raise RuntimeError("no COMFY_API_KEY (mock mode)")
    body = {"max_tokens": MAX_TOKENS, "system": system, "messages": [{"role": "user", "content": user}]}
    req = urllib.request.Request(
        f"{BASE_URL}/v2/models/{model}", data=json.dumps(body).encode(), method="POST",
        headers={"X-API-Key": key, "Content-Type": "application/json", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as r:
            doc = json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Router HTTP {e.code}: {e.read().decode(errors='replace')[:300]}") from e
    parts = doc.get("content") or []
    text = "".join(p.get("text", "") for p in parts if isinstance(p, dict) and p.get("type") == "text")
    if not text:
        raise RuntimeError(f"empty reply (stop_reason={doc.get('stop_reason')})")
    return text


def write_script(headline: str, source: str, minutes: float, scenes: int, clips_per_scene: list[int],
                 series_title: str = "THE HOURLY BRIEFING", *, media_dir: Path | None = None,
                 model: str = CHAT_MODEL) -> dict | None:
    """Headline -> validated script dict, or None (caller falls back to the template planner).
    Cached by (headline, minutes) in <media>/scripts-cache.json; a cached script whose counts no longer
    match the request is ignored and rewritten."""
    headline = " ".join((headline or "").split())
    if not headline or scenes < 1 or len(clips_per_scene) != scenes:
        return None
    cache = _media_dir(media_dir) / CACHE_NAME
    key = _cache_key(headline, minutes)
    cached = _read_cache(cache).get(key)
    if cached:
        try:
            return validate(cached, scenes, clips_per_scene)
        except ValueError:
            pass
    system = SYSTEM_PROMPT.format(series=series_title, moves=", ".join(MOVES))
    user = USER_PROMPT.format(headline=headline.replace('"', "'"), source=source or "unknown",
                              minutes=minutes, scenes=scenes, counts=clips_per_scene,
                              first=clips_per_scene[0])
    try:
        script = validate(json.loads(_strip_fences(_chat(system, user, model))), scenes, clips_per_scene)
    except Exception as e:  # network, 402, bad JSON, wrong counts: all mean "use the template"
        print(f"[shot-director] writer fell back to template: {type(e).__name__}: {str(e)[:300]}",
              file=sys.stderr, flush=True)
        return None
    script.update(headline=headline, source=source, minutes=minutes, series=series_title, model=model)
    _write_cache(cache, key, script)
    return script


def script_to_prompts(script: dict, body: str = "anime") -> list[str]:
    """One image/video prompt per shot, in episode order: STYLE LEAD + shot visual + scene setting.
    Non-anime bodies use their preset fragment as the lead instead."""
    lead = STYLE_LEAD if body == "anime" else presets.CAMERA_BODIES[body]["fragment"]
    out = []
    for sc in script["scenes"]:
        setting = sc["setting"].rstrip(".")
        for s in sc["shots"]:
            out.append(f"{lead}. {s['visual'].rstrip('.')}. Setting: {setting}. {SAFETY_TAIL}.")
    return out
