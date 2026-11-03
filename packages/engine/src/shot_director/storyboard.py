"""Headline (+ optional article text or URL) -> storyboard: the editable plan of a whole episode.

One Router chat call writes it (writer v2's show bible and voice, extended to many scenes of mixed shots);
mock mode, or no key, returns a deterministic template storyboard of the same shape. `normalize()` is the
one validator: it checks the writer's reply and every storyboard the web app sends back after editing.
No cap on scenes or shots: the writer targets about 4 scenes a minute and 3-6 shots a scene.
Schema: docs/STORYBOARD.md.
"""

import hashlib
import html
import json
import math
import re
import sys
import urllib.request

from . import presets, writer

SERIES = "THE WEEKLY BRIEFING"
ACTS = ("cold_open", "setup", "escalation", "turn", "climax", "sign_off")
MIDDLE_ACTS = ("setup", "escalation", "turn", "climax")
KINDS = ("talk", "action", "still", "insert")
VIDEO_KINDS = ("talk", "action")
MOVES = tuple(presets.CAMERA_MOVES)
SCENES_PER_MINUTE = 4
MAX_MINUTES = 5.0
LINE_MAX = 60          # characters of one spoken line (a two-line subtitle)
CAPTION_MAX = 60
NARRATION_MAX_WORDS = 30
PROMPT_MAX_WORDS = 60
URL_TIMEOUT = 10
SOURCE_MAX = 6000
EXCERPT_MAX = 600
SIGN_OFF = "Back next week."
COUNTDOWN = "NEXT BRIEFING IN"   # the countdown clock's default label

# ElevenLabs premade voices used only as timbre references for Seedance (the clip speaks the line itself).
DEFAULT_VOICES = {"anchor": "onwK4e9ZLuTAKqWW03F9", "operative": "XB0fDUnXU5powFXDhCwa",
                  "analyst": "N2lVS1w4EtoT3dr4eOWO"}


def default_cast() -> list[dict]:
    return [{"id": cid, "name": c["name"], "role": c["role"], "look": c["look"], "voice": DEFAULT_VOICES[cid]}
            for cid, c in writer.CAST.items()]


class StoryboardError(ValueError):
    pass


# -- input ----------------------------------------------------------------------------------------

def scene_count(minutes: float) -> int:
    return max(2, round(minutes * SCENES_PER_MINUTE))


def act_plan(n: int) -> list[str]:
    """Act per scene: a cold open, the middle acts spread in order, a sign-off."""
    if n <= 1:
        return ["sign_off"]
    if n == 2:
        return ["setup", "sign_off"]
    k = n - 2
    short = {1: ["setup"], 2: ["setup", "climax"], 3: ["setup", "escalation", "climax"],
             4: ["setup", "escalation", "turn", "climax"]}
    middle = short.get(k) or [MIDDLE_ACTS[min(3, j * 4 // k)] for j in range(k)]
    return ["cold_open"] + middle + ["sign_off"]


def clamp_minutes(minutes: float | None) -> float:
    try:
        m = float(minutes if minutes is not None else 1.0)
    except (TypeError, ValueError):
        raise StoryboardError("minutes must be a number")
    if not math.isfinite(m) or m <= 0:
        raise StoryboardError("minutes must be positive")
    return min(MAX_MINUTES, m)


def strip_html(raw: str) -> str:
    raw = re.sub(r"(?is)<(script|style|noscript|svg|head)[^>]*>.*?</\1>", " ", raw)
    raw = re.sub(r"(?s)<!--.*?-->", " ", raw)
    raw = re.sub(r"(?s)<[^>]+>", " ", raw)
    return " ".join(html.unescape(raw).split())


def fetch_url(url: str) -> str:
    """The page's text (tags stripped, first ~6 k chars), or "" on any failure (fail soft)."""
    if not re.match(r"^https?://", url or "", re.I):
        return ""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (anime-news engine)",
                                                   "Accept": "text/html,text/plain;q=0.9,*/*;q=0.5"})
        with urllib.request.urlopen(req, timeout=URL_TIMEOUT) as r:
            raw = r.read(2_000_000)
            charset = r.headers.get_content_charset() or "utf-8"
        return strip_html(raw.decode(charset, errors="replace"))[:SOURCE_MAX]
    except Exception as e:  # noqa: BLE001 - any failure means "headline only"
        print(f"[anime-news] url fetch failed, using the headline only: {type(e).__name__}", file=sys.stderr)
        return ""


# -- normalize ------------------------------------------------------------------------------------

def _clip_chars(text: str, limit: int) -> str:
    text = " ".join(str(text or "").split())
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0].rstrip(",;:-")
    return cut or text[:limit]


def _words(text: str, limit: int) -> str:
    return " ".join(str(text or "").split()[:limit])


def talk_seconds(line: str) -> int:
    """Clip length for a spoken line: ~2.6 words/s plus a beat before and after, 4-8 s."""
    return max(4, min(8, math.ceil(len(line.split()) / 2.6 + 1.6)))


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(text).lower()).strip("_")[:24]


def _norm_cast(cast: object) -> list[dict]:
    if not isinstance(cast, list) or not cast:
        return default_cast()
    out, seen = [], set()
    defaults = {c["id"]: c for c in default_cast()}
    for i, c in enumerate(cast):
        if not isinstance(c, dict):
            continue
        cid = _slug(c.get("id") or c.get("name") or f"cast{i + 1}") or f"cast{i + 1}"
        while cid in seen:
            cid += "_x"
        seen.add(cid)
        base = defaults.get(cid, {})
        out.append({"id": cid, "name": _clip_chars(c.get("name") or base.get("name") or cid.upper(), 40),
                    "role": _clip_chars(c.get("role") or base.get("role") or "an original character", 60),
                    "look": _words(c.get("look") or base.get("look") or "an original anime character", 45),
                    "voice": str(c.get("voice") or base.get("voice") or "").strip()})
    return out or default_cast()


def normalize(sb: object) -> dict:
    """Validate and normalize a storyboard (writer reply or an edited one from the web app).
    Raises StoryboardError with a readable reason. Never caps shots or scenes."""
    if not isinstance(sb, dict):
        raise StoryboardError("storyboard must be an object")
    headline = " ".join(str(sb.get("headline") or "").split())
    if not headline:
        raise StoryboardError("storyboard.headline is empty")
    cast = _norm_cast(sb.get("cast"))
    cast_ids = [c["id"] for c in cast]
    scenes_in = sb.get("scenes")
    if not isinstance(scenes_in, list) or not scenes_in:
        raise StoryboardError("storyboard.scenes must be a non-empty list")
    scenes, shot_ids, scene_ids = [], set(), set()
    for i, sc in enumerate(scenes_in, 1):
        if not isinstance(sc, dict):
            raise StoryboardError(f"scene {i} is not an object")
        act = str(sc.get("act") or "").strip().lower()
        act = act if act in ACTS else ("cold_open" if i == 1 and len(scenes_in) > 2 else "setup")
        sid = _slug(sc.get("id") or "") or f"s{i}"
        while sid in scene_ids:
            sid = f"{sid}_{i}"
        scene_ids.add(sid)
        shots_in = sc.get("shots")
        if not isinstance(shots_in, list):
            raise StoryboardError(f"scene {i}: shots must be a list")
        shots = []
        for j, s in enumerate(shots_in, 1):
            if not isinstance(s, dict):
                raise StoryboardError(f"scene {i} shot {j} is not an object")
            kind = str(s.get("kind") or "").strip().lower()
            if kind not in KINDS:
                raise StoryboardError(f"scene {i} shot {j}: kind must be one of {', '.join(KINDS)}")
            prompt = _words(s.get("prompt") or s.get("visual") or "", PROMPT_MAX_WORDS)
            if not prompt:
                raise StoryboardError(f"scene {i} shot {j}: prompt is empty")
            who = s.get("cast") if isinstance(s.get("cast"), list) else ([s["speaker"]] if s.get("speaker") else [])
            who = [_slug(c) for c in who if _slug(c) in cast_ids]
            line = _clip_chars(s.get("line") or "", LINE_MAX) if kind == "talk" else ""
            if kind == "talk":
                if not line:
                    raise StoryboardError(f"scene {i} shot {j}: a talk shot needs a spoken line")
                if not who:
                    who = [cast_ids[0]]
            move = str(s.get("move") or "").strip().lower()
            move = move if move in MOVES else ("dolly_in" if kind == "talk" else "static")
            try:
                seconds = int(round(float(s.get("seconds") or 0)))
            except (TypeError, ValueError):
                seconds = 0
            if kind == "talk":
                seconds = max(4, min(10, seconds or talk_seconds(line)))
            elif kind == "action":
                seconds = max(4, min(10, seconds or 5))
            else:
                seconds = 5
            shid = _slug(s.get("id") or "") or f"{sid}_{j}"
            while shid in shot_ids:
                shid = f"{shid}_{j}"
            shot_ids.add(shid)
            shot = {"id": shid, "kind": kind, "cast": who, "prompt": prompt, "move": move, "seconds": seconds}
            if kind == "talk":
                shot["line"] = line
            shots.append(shot)
        if not shots:
            continue  # an emptied scene is dropped, not an error
        scenes.append({"id": sid, "act": act, "setting": _words(sc.get("setting") or "", 30),
                       "caption": "" if act == "cold_open" else _clip_chars(sc.get("caption") or "", CAPTION_MAX),
                       "narration": "" if act == "cold_open" else _words(sc.get("narration") or "", NARRATION_MAX_WORDS),
                       "shots": shots})
    if not scenes:
        raise StoryboardError("storyboard has no shots")
    try:
        minutes = clamp_minutes(sb.get("minutes"))
    except StoryboardError:
        minutes = 1.0
    out = {"id": str(sb.get("id") or _storyboard_id(headline, minutes)),
           "headline": headline,
           "source_text_excerpt": _clip_chars(sb.get("source_text_excerpt") or "", EXCERPT_MAX),
           "series": str(sb.get("series") or SERIES),
           "logline": " ".join(str(sb.get("logline") or headline).split()),
           "minutes": minutes, "cast": cast, "scenes": scenes}
    for k in ("title_card", "countdown", "style", "writer"):
        if sb.get(k):
            out[k] = " ".join(str(sb[k]).split())
    return out


def stats(sb: dict) -> dict:
    shots = [s for sc in sb["scenes"] for s in sc["shots"]]
    return {"scenes": len(sb["scenes"]), "shots": len(shots),
            **{k: sum(1 for s in shots if s["kind"] == k) for k in KINDS}}


def _storyboard_id(headline: str, minutes: float) -> str:
    return "sb-" + hashlib.sha256(f"{headline}|{minutes}".encode()).hexdigest()[:10]


# -- the template (mock mode) ---------------------------------------------------------------------

_TEMPLATE = {
    "cold_open": ("a dark invented capital at night, CRT glow, teal shadows, burnt-orange light", "", "", [
        ("action", [], "", "Extreme close-up of a red countdown clock in a dark war room, digits flipping, sparks of light", "crash_zoom"),
        ("insert", [], "", "Close-up of a teletype printing the words \"{h}\" on yellowed paper", "static"),
        ("action", [], "", "Wide shot of searchlights sweeping across giant tower screens that all read \"{h}\"", "crane_up"),
        ("still", [], "", "Extreme close-up of KUROGANE's round glasses reflecting a glowing monitor", "dolly_in"),
    ]),
    "setup": ("the anime newsroom, a long desk before a wall of world-map monitors", "{short}",
              "Sources confirm it. {h}. Officials have convened. Nobody was ready.", [
        ("talk", ["anchor"], "Good evening. This week: {short_line}", "Medium shot of KUROGANE at the news desk, facing camera", "dolly_in"),
        ("action", [], "", "Overhead shot of a war-room table where an oversized absurd prop literalizes the headline, staffers recoil", "handheld"),
        ("still", [], "", "Low angle of invented officials staring in silence at a glowing map", "static"),
        ("insert", [], "", "Close-up of a classified folder stamped URGENT with \"{h}\" typed on the cover", "static"),
        ("action", [], "", "Medium shot of staffers sprinting down a corridor with armfuls of paperwork, speed lines", "whip_pan"),
    ]),
    "escalation": ("a rooftop over the invented capital at dusk, painted sky, wind", "It is getting bigger.",
                   "The situation escalated. Then it escalated again. The clock is still running.", [
        ("talk", ["operative"], "Vex here. It's worse than we thought.", "Close-up of VEX whispering into her wrist on a windy rooftop", "handheld"),
        ("action", [], "", "Wide shot of the absurd prop growing to the size of a building, citizens point upward in shock", "crane_up"),
        ("still", [], "", "Extreme close-up of a citizen's wide shocked eyes, hatching, sweat drop", "crash_zoom"),
        ("insert", [], "", "Close-up of a ticker board flipping wildly, every line reads \"{short}\"", "static"),
        ("action", [], "", "Medium shot of pigeons scattering off a giant news screen, speed lines, debris", "whip_pan"),
    ]),
    "turn": ("a cramped lab wallpapered with charts and red string", "It's all connected.",
             "The analysts have a theory. The theory has charts.", [
        ("talk", ["analyst"], "It's all connected. I have charts. So many charts.", "Close-up of DR. HAKASE before a red-string evidence board", "dolly_in"),
        ("action", [], "", "Medium shot of red string snapping taut across a wall of pinned photos of invented landmarks", "orbit"),
        ("still", [], "", "Overhead shot of a desk buried in coffee mugs and annotated maps", "static"),
        ("insert", [], "", "Close-up of a chart whose arrow crashes straight down off the page", "crash_zoom"),
        ("action", [], "", "Wide shot of the lab lights flickering as a monitor wall glitches on \"{short}\"", "handheld"),
    ]),
    "climax": ("a dusty western main street at high noon, painted sky", "The standoff begins.",
               "Two bureaucracies. One form. High noon.", [
        ("action", [], "", "Wide shot of two invented rival bureaucrats in a standoff, each holding a giant rubber stamp", "orbit"),
        ("insert", [], "", "Close-up of a wanted poster that reads \"{short}\" flapping in the wind", "static"),
        ("action", [], "", "Medium shot as both stamps slam down on one form at once, white flash, speed lines", "crash_zoom"),
        ("talk", ["operative"], "Nobody blinks. Nobody files. Nobody wins.", "Close-up of VEX watching from behind a water trough", "dolly_in"),
        ("still", [], "", "Wide shot of the whole town buried in stamped paperwork", "dolly_out"),
    ]),
    "sign_off": ("the anime newsroom after hours, one monitor still glowing", "That was the news.",
                 "It will happen again. We will be here.", [
        ("action", [], "", "Medium shot of the countdown clock hitting zero, a tiny paper flag pops out", "crash_zoom"),
        ("still", [], "", "Wide shot of the dark studio, one monitor glowing with \"{short}\"", "crane_up"),
        ("talk", ["anchor"], "It will happen again. " + SIGN_OFF, "Close-up of KUROGANE folding his hands, perfectly deadpan", "dolly_out"),
    ]),
}


def template(headline: str, minutes: float = 1.0, text: str = "") -> dict:
    """Deterministic storyboard of the full shape (mock mode and the writer's fallback in tests)."""
    h = " ".join(headline.split()).replace('"', "'")
    words = h.split()
    short = " ".join(words[:6]) + ("..." if len(words) > 6 else "")
    fill = {"h": h, "short": short, "short_line": _clip_chars(short, LINE_MAX - 25)}
    scenes = []
    for i, act in enumerate(act_plan(scene_count(minutes)), 1):
        setting, caption, narration, shots = _TEMPLATE[act]
        scenes.append({"id": f"s{i}", "act": act, "setting": setting, "caption": caption.format(**fill),
                       "narration": narration.format(**fill),
                       "shots": [{"id": f"s{i}_{j}", "kind": k, "cast": cast, "line": line.format(**fill),
                                  "prompt": prompt.format(**fill), "move": move}
                                 for j, (k, cast, line, prompt, move) in enumerate(shots, 1)]})
    return normalize({"headline": h, "minutes": minutes, "source_text_excerpt": text[:EXCERPT_MAX],
                      "logline": f"{SERIES}: \"{h}\", briefed like a war-room emergency and taken literally.",
                      "title_card": _clip_chars(short.upper(), 30), "countdown": COUNTDOWN,
                      "cast": default_cast(), "scenes": scenes, "writer": "template"})


# -- the writer -----------------------------------------------------------------------------------

SYSTEM_PROMPT = """You are the head writer and storyboard artist of "{series}", an original 2D anime news show (late-1980s OVA look). Each episode is this week's briefing: it turns the week's real headline(s) into a short episode people share. The anchor signs off with "back next week".

THE SHOW
- Deadpan war-room gravitas applied to an absurd or mundane claim. The characters never wink; the joke is the contrast.
- Literalize the headline: its abstract claim becomes an oversized physical prop or situation.
- The episode is a STORY in acts: cold_open (image only, a gorgeous mystery hook), setup (the claim, briefed gravely), escalation (bigger and dumber, a visible ticking countdown), turn (a twist or conspiracy), climax (a genre pastiche that dramatizes the moral: samurai duel, heist, kaiju attack, sports final, space battle, courtroom...), sign_off (the button).

RECURRING CAST (original characters; use their NAMES in lines and prompts; the cast ids are fixed)
{cast}
You may add at most two new invented characters (original archetypes, never a real person) to "cast".

SHOT KINDS (mix them in every scene; no two consecutive shots show the same composition)
- talk: one cast member speaks one line on camera, mouth visible, facing camera. "cast": [speaker id], "line": the spoken words.
- action: one strong subject, one clear motion verb; impact frames, speed lines, debris, wind, light. Becomes a short animated clip.
- still: a single striking composition (face, eyes, reaction, wide establishing) used as a quick cut-in.
- insert: a close-up of a prop, screen, ticker, document or countdown; the headline text may appear on it.
Budget: talk and action shots are animated and expensive; still and insert are cheap. About half the shots of a scene are still or insert.

HARD RULES
- Exactly {scenes} scenes, acts in this order: {acts}.
- 3 to 6 shots per scene; about {shots} shots in total.
- cold_open: no talk shots, empty caption and narration.
- The last shot of the last scene is a talk shot by the anchor whose line ends exactly with "{sign_off}"
- Spoken lines: at most 60 characters, punchy, quotable, funny on their own as a subtitle, no stage directions.
- Prompts: at most 40 words, drawn-anime terms only (inked, cel, painted background), start with the shot size (extreme close-up, close-up, medium, wide, overhead, low angle). Talk prompts frame the speaker clearly. Never photoreal, cinematic, 4K or lens terms.
- caption: the scene's one-clause subtitle (<= 40 chars). narration: 1-2 deadpan briefing sentences (<= 30 words) or "" when the scene is all dialogue.
- move: one of {moves}. seconds: talk 4-8 (by line length), action 5, still/insert 5.
- Never depict, describe or name a real person; refer to people in the headline only by role ("the official"). Invented countries and institutions only. No real logos or flags. Safe for work: no weapons pointed at people, no gore. Never name an anime studio, film or artist.
{style}
OUTPUT: ONE JSON object, nothing else (no prose, no code fences):
{{"logline": "one-sentence pitch", "title_card": "<= 30 chars, ALL CAPS", "countdown": "the clock's label, <= 24 chars (default: NEXT BRIEFING IN)",
  "cast": [{{"id", "name", "role", "look"}}],
  "scenes": [{{"act", "setting": "<= 20 words", "caption", "narration",
              "shots": [{{"kind": "talk|action|still|insert", "cast": ["id"], "line": "only for talk", "prompt", "move", "seconds"}}]}}]}}"""

USER_PROMPT = """Headline: "{headline}"
Source text (may be empty; use it for facts, never for real names of people):
{source}
Episode length: {minutes} minutes. Write the JSON object only."""


def _cast_block() -> str:
    return "\n".join(f"- {c['name']} (id \"{c['id']}\", {c['role']}): {c['look']}; {writer.CAST[c['id']]['trait']}."
                     for c in default_cast())


def write(headline: str, text: str = "", minutes: float = 1.0, style: str = "") -> dict:
    """One Router chat call (one retry with the validation error on a bad reply). Raises on failure."""
    n = scene_count(minutes)
    acts = act_plan(n)
    system = SYSTEM_PROMPT.format(series=SERIES, cast=_cast_block(), scenes=n, acts=", ".join(acts),
                                  shots=round(n * 4.5), sign_off=SIGN_OFF, moves=", ".join(MOVES),
                                  style=f"- Visual style direction from the user: {style}\n" if style else "")
    user = USER_PROMPT.format(headline=headline.replace('"', "'"), source=text[:SOURCE_MAX] or "(none)",
                              minutes=minutes)
    max_tokens = min(32000, 2500 + n * 900)
    last_error = None
    for attempt in range(2):
        prompt = user if not last_error else (
            f"{user}\n\nYour previous reply was rejected: {last_error}. Fix it and return the JSON object only.")
        reply = writer._chat(system, prompt, max_tokens=max_tokens, timeout=420)
        try:
            doc = json.loads(writer._strip_fences(reply))
            if not isinstance(doc, dict):
                raise StoryboardError("reply is not a JSON object")
            cast = default_cast() + [c for c in (doc.get("cast") or []) if isinstance(c, dict)
                                     and _slug(c.get("id") or c.get("name") or "") not in writer.CAST]
            sb = normalize({**doc, "headline": headline, "minutes": minutes, "cast": cast[:5],
                            "source_text_excerpt": text[:EXCERPT_MAX], "writer": writer.CHAT_MODEL,
                            "style": style})
            if len(sb["scenes"]) < max(1, math.ceil(n * 0.75)):
                raise StoryboardError(f"expected {n} scenes, got {len(sb['scenes'])}")
            return _finish(sb)
        except (ValueError, StoryboardError) as e:
            last_error = str(e)[:300]
            print(f"[anime-news] writer reply rejected ({attempt + 1}/2): {last_error}", file=sys.stderr, flush=True)
    raise StoryboardError(f"writer failed: {last_error}")


def _finish(sb: dict) -> dict:
    """The button rule: the episode ends on the anchor saying the sign-off."""
    last = sb["scenes"][-1]["shots"][-1]
    if last["kind"] == "talk" and not last["line"].rstrip(".! ").lower().endswith(SIGN_OFF.rstrip(".").lower()):
        line = f"{last['line'].rstrip()} {SIGN_OFF}"
        last["line"] = line if len(line) <= LINE_MAX else SIGN_OFF
        last["seconds"] = talk_seconds(last["line"])
    return sb


def build(headline: str, text: str = "", url: str = "", minutes: float | None = 1.0, style: str = "",
          mock: bool | None = None) -> dict:
    """The storyboard for a headline. mock=None decides by the key (no key -> template)."""
    from . import config
    headline = " ".join((headline or "").split())
    if not headline:
        raise StoryboardError("headline is empty")
    minutes = clamp_minutes(minutes)
    text = " ".join((text or "").split())
    if url:
        fetched = fetch_url(url)
        text = f"{text}\n{fetched}".strip() if fetched else text
    if mock is None:
        mock = config.mode() != "live"
    if mock:
        return template(headline, minutes, text)
    return write(headline, text, minutes, style or "")
