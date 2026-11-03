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
from .router_client import BASE_URL, CREDITS, load_api_key

ROOT = Path(__file__).resolve().parents[2]
CHAT_MODEL = os.environ.get("SHOT_DIRECTOR_CHAT_MODEL", "anthropic/claude-sonnet-5-5")
CACHE_NAME = "scripts-cache.json"
SERIES_TITLE = "THE WEEKLY BRIEFING"
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
- "the Field Reporter": a hoodie-and-press-vest reporter on location, fearless, reports from inside the disaster with a straight face.
- "the Analyst": an unbothered expert with a chart for everything and a tiny smug smile when she is right.
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


_PAID = threading.local()


def paid_calls() -> int:
    """How many Router chat calls on THIS thread may have been billed: the Router answered 2xx, or reported credits
    used on an error reply. Callers snapshot it before a writer step to know whether a refund is honest."""
    return getattr(_PAID, "n", 0)


def _count_paid() -> None:
    _PAID.n = paid_calls() + 1


def _credits_in(headers) -> float:
    try:
        return float((headers or {}).get("X-Comfy-Credits-Used") or 0)
    except (TypeError, ValueError, AttributeError):
        return 0.0


def _chat(system: str, user: str, model: str = CHAT_MODEL, max_tokens: int = MAX_TOKENS,
          timeout: float = TIMEOUT_SECONDS) -> str:
    """One synchronous Messages call on Router (POST /v2/models/{model}). Returns the text reply."""
    key = load_api_key()
    if not key:
        raise RuntimeError("no COMFY_API_KEY (mock mode)")
    body = {"max_tokens": max_tokens, "system": system, "messages": [{"role": "user", "content": user}]}
    req = urllib.request.Request(
        f"{BASE_URL}/v2/models/{model}", data=json.dumps(body).encode(), method="POST",
        headers={"X-API-Key": key, "Content-Type": "application/json", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            _count_paid()
            CREDITS.record(r.headers)
            doc = json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        if _credits_in(e.headers) > 0:
            _count_paid()
        raise RuntimeError(f"Router HTTP {e.code}: {e.read().decode(errors='replace')[:300]}") from e
    parts = doc.get("content") or []
    text = "".join(p.get("text", "") for p in parts if isinstance(p, dict) and p.get("type") == "text")
    if not text:
        raise RuntimeError(f"empty reply (stop_reason={doc.get('stop_reason')})")
    return text


def write_script(headline: str, source: str, minutes: float, scenes: int, clips_per_scene: list[int],
                 series_title: str = SERIES_TITLE, *, media_dir: Path | None = None,
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


# -- v2: a story with a recurring cast, spoken lines and acts (docs/psyopanime-pipeline-v2.md, D2) ----------

CAST = {
    "anchor": {"name": 'SHION', "role": 'the Anchor',
              "look": 'adult woman, mid-20s, crimson-red hair in a sleek high ponytail with long side-swept bangs, sharp amber eyes, no glasses, small gold earpiece, deep navy suit jacket with gold piping buttoned over a black turtleneck, a gold fountain pen',
              "trait": 'perfectly composed; reads the most absurd fact with total gravity, and one raised eyebrow is the whole punchline',
              "voice": 'EXAVITQu4vr4xnSDxMaL',
              "poses": {"talk": {"id": "upper80v2_P019", "phrase": "tucks a strand of hair behind her ear"}, "action": {"id": "stage-gesture", "phrase": "speaks with one hand open in a gesture, the other holding a microphone or notes, mid-sentence"}, "still": {"id": "upper80v2_P013", "phrase": "rests her chin on both palms, a flower shape"}}},
    "operative": {"name": 'HINA', "role": 'the Field Reporter',
                 "look": 'adult woman, mid-20s, short messy copper-orange bob with one upright cowlick strand, bright green eyes, light freckles, oversized mustard-yellow hoodie under a navy press vest with a PRESS patch, cargo pants, chunky sneakers, a handheld microphone with a fuzzy grey windscreen',
                 "trait": 'fearless and all-in; reports from inside the disaster with a straight face, then gives a thumbs-up',
                 "voice": 'FGY2WhTYpPnrIDTdsKH5',
                 "poses": {"talk": {"id": "upper80v2_P022", "phrase": "points at the camera, grinning"}, "action": {"id": "full_single_P017", "phrase": "stands turned away from the camera, glancing back over her shoulder"}, "still": {"id": "full_single_P061", "phrase": "crouches down with her hands on her knees"}}},
    "analyst": {"name": 'DR. RENA', "role": 'the Analyst',
               "look": 'adult woman, mid-20s, long wavy dark-teal hair worn loose over one shoulder, red half-rim glasses, calm golden eyes, charcoal blazer with rolled sleeves over a white shirt and a thin black tie, a clipboard of charts',
               "trait": 'unbothered and half-lidded; has a chart for everything, connects everything to everything, and allows herself one tiny smug smile when the chart is right',
               "voice": 'pFZP5JQG7iQjIQuC4Bku',
               "poses": {"talk": {"id": "full_single_P015", "phrase": "stands with her weight on one leg, hip relaxed, one hand on her hip"}, "action": {"id": "turning-off-frame", "phrase": "turns her head and shoulders toward something off frame, caught mid-turn, hair swinging"}, "still": {"id": "upper80v2_P010", "phrase": "holds one finger to her lips, shh"}}},
}
SPEAKERS = tuple(CAST)
LINE_MAX_WORDS = 14

# Frames never show a clock: the cut draws the countdown as an overlay, and a generated clock contradicts it.
NO_CLOCKS = "Never draw clocks, timers, countdowns or on-screen numbers in frames: the overlay carries the countdown."
SYSTEM_PROMPT_V2 = """You are the head writer of "{series}", an original 2D anime news show (late-1980s OVA look) that turns one real headline into a 60-second mini-episode people share.

THE SHOW
- Deadpan war-room gravitas applied to an absurd or mundane claim. The characters never wink; the joke is the contrast.
- Literalize the headline: its abstract claim becomes an oversized physical prop or situation (a market "falling" is a giant chart arrow crashing through a ceiling).
- Every episode is a tiny STORY, not a list: a hook, a claim, an escalation against a ticking countdown (the cut draws it as an overlay), a genre-pastiche vignette that dramatizes the moral (western standoff, samurai duel, heist, kaiju attack, sports final...), then a twist and a button.

RECURRING CAST (original characters; use their NAMES in lines and visuals)
{cast}
Running gag every episode: the countdown reaches zero and something tiny and anticlimactic happens.

ACT STRUCTURE (fixed; the user message gives the exact shot slots)
1. cold_open: image only, NO words, no captions. A mysterious, gorgeous hook that makes you need the next shot.
2. thesis: SHION states the headline as a grave briefing line, then a literal visual.
3. escalation: HINA reports from the field, the countdown starts, things get bigger and dumber.
4. vignette: a genre pastiche dramatizes the moral; DR. RENA explains the conspiracy in one line.
5. button: SHION's twist line, ending EXACTLY with "Back next week."
{callback}
WRITING RULES
- Spoken lines: <= {max_words} words, one or two short sentences, punchy, quotable, no stage directions inside the line. Each line must be funny on its own as a subtitle.
- Visuals: drawn-anime terms only. Start with the shot size (extreme close-up, close-up, medium, wide, overhead, low angle). Talk shots frame the speaker clearly facing camera, mouth visible. Action shots: one strong subject, one clear motion verb, impact frames, speed lines, debris, wind, light.
- second_angle: for action shots only, a DIFFERENT composition of the same moment after a hard cut (e.g. close-up of a reaction, an insert of the prop). Empty for talk shots.
- Captions: talk shots: the caption is the line itself. Action shots: "" or <= 6 words of on-screen text (a stamp, a ticker).
- Never depict, describe or name a real person. Refer to people in the headline only by role ("the official"). Invented countries and institutions only. No real logos or flags. Safe for work: no weapons pointed at people, no gore. Never name an anime studio, film or artist. Never photoreal, cinematic, 4K or lens terms.
- """ + NO_CLOCKS + """

OUTPUT: ONE JSON object, nothing else:
{{"logline": "one-sentence pitch",
  "title_card": "episode title, <= 30 chars, ALL CAPS",
  "countdown": "the countdown overlay's label, <= 24 chars, e.g. NAP MANDATE IN",
  "acts": [{{"act": "cold_open|thesis|escalation|vignette|button", "setting": "<= 20 words, drawn-anime terms",
             "shots": [{{"kind": "talk|action", "speaker": "anchor|operative|analyst|none", "line": "spoken words or empty",
                         "visual": "<= 30 words", "second_angle": "<= 20 words or empty",
                         "move": "one of: {moves}", "caption": "see rules"}}]}}]}}"""

USER_PROMPT_V2 = """Headline: "{headline}"
Source: {source}
Vignette genre for this episode: {genre} (dramatize the moral as that genre; not a western unless the genre says so).
Shot slots, in order (act: kind/speaker per shot). Match them EXACTLY, same number of acts and shots, same kinds and speakers:
{slots}
Return the JSON object only."""


def _cast_block() -> str:
    return "\n".join(f"- {c['name']} ({c['role']}, speaker id \"{k}\"): {c['look']}; {c['trait']}."
                     for k, c in CAST.items())


def validate_v2(script: object, slots: list[tuple[str, list[tuple[str, str]]]]) -> dict:
    """Raise ValueError unless acts/shots/kinds/speakers match `slots`; returns a normalized copy."""
    if not isinstance(script, dict):
        raise ValueError("script is not an object")
    for k in ("logline", "title_card", "countdown", "acts"):
        if k not in script:
            raise ValueError(f"missing key: {k}")
    acts = script["acts"]
    if not isinstance(acts, list) or len(acts) != len(slots):
        raise ValueError(f"expected {len(slots)} acts")
    out_acts = []
    for i, (a, (act_name, want)) in enumerate(zip(acts, slots), 1):
        shots = a.get("shots") if isinstance(a, dict) else None
        if not isinstance(shots, list) or len(shots) != len(want):
            raise ValueError(f"act {i}: expected {len(want)} shots")
        out_shots = []
        for j, (s, (kind, speaker)) in enumerate(zip(shots, want), 1):
            if not isinstance(s, dict) or s.get("move") not in MOVES:
                raise ValueError(f"act {i} shot {j}: bad move")
            if not str(s.get("visual", "")).strip():
                raise ValueError(f"act {i} shot {j}: empty visual")
            line = " ".join(str(s.get("line") or "").split())
            if kind == "talk":
                if not line:
                    raise ValueError(f"act {i} shot {j}: talk shot without a line")
                words = line.split()
                line = " ".join(words[:LINE_MAX_WORDS + 4])
            else:
                line = ""
            out_shots.append({"kind": kind, "speaker": speaker if kind == "talk" else "none", "line": line,
                              "visual": " ".join(str(s["visual"]).split()[:40]),
                              "second_angle": "" if kind == "talk" else " ".join(str(s.get("second_angle") or "").split()[:25]),
                              "move": s["move"],
                              "caption": line if kind == "talk" else ("" if act_name == "cold_open"
                                                                      else _clip(s.get("caption") or "", CAPTION_MAX))})
        out_acts.append({"act": act_name, "setting": " ".join(str(a.get("setting", "")).split()), "shots": out_shots})
    last = out_acts[-1]["shots"][-1]
    if last["kind"] == "talk":   # check the untrimmed line, so a long line is not cut mid-phrase and re-tagged
        full = " ".join(str(acts[-1]["shots"][-1].get("line") or "").split())
        if full.rstrip(".! ").lower().endswith("back next week"):
            last["line"] = last["caption"] = full
    if last["kind"] == "talk" and not last["line"].rstrip(".! ").lower().endswith("back next week"):
        last["line"] = last["caption"] = f"{last['line'].rstrip()} Back next week."
    return {"logline": " ".join(str(script["logline"]).split()),
            "title_card": _clip(str(script["title_card"]).upper(), 30),
            "countdown": _clip(str(script["countdown"]).upper(), 24), "acts": out_acts}


def write_script_v2(headline: str, source: str, slots: list[tuple[str, list[tuple[str, str]]]],
                    series_title: str = SERIES_TITLE, *, previous: dict | None = None,
                    genre: str = "heist movie", media_dir: Path | None = None,
                    model: str = CHAT_MODEL) -> dict | None:
    """Headline -> validated v2 script (acts of talk/action shots with spoken lines), or None on any failure.
    `previous` = {"logline", "title_card"} of the last episode, for the callback rule. Cached like v1."""
    headline = " ".join((headline or "").split())
    if not headline:
        return None
    cache = _media_dir(media_dir) / CACHE_NAME
    key = "v2:" + _cache_key(f"{headline}|{genre}", len(slots))
    cached = _read_cache(cache).get(key)
    if cached:
        try:
            return {**cached, **validate_v2(cached, slots)}
        except ValueError:
            pass
    callback = (f"CALLBACK: the previous episode was \"{previous.get('title_card', '')}\" "
                f"({previous.get('logline', '')}). Reference it once, in a line or a background detail.\n"
                if previous else "")
    system = SYSTEM_PROMPT_V2.format(series=series_title, cast=_cast_block(), callback=callback,
                                     max_words=LINE_MAX_WORDS, moves=", ".join(MOVES))
    slot_text = "\n".join(f"{i}. {act}: " + ", ".join(f"{k}/{sp}" for k, sp in shots)
                          for i, (act, shots) in enumerate(slots, 1))
    user = USER_PROMPT_V2.format(headline=headline.replace('"', "'"), source=source or "unknown", slots=slot_text,
                                 genre=genre)
    try:
        script = validate_v2(json.loads(_strip_fences(_chat(system, user, model))), slots)
    except Exception as e:
        print(f"[shot-director] writer v2 failed: {type(e).__name__}: {str(e)[:300]}", file=sys.stderr, flush=True)
        return None
    script.update(headline=headline, source=source, series=series_title, model=model)
    _write_cache(cache, key, script)
    return script
