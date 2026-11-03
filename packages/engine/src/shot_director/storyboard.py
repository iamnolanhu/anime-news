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
        from . import hosted
        opener = hosted.open_public if hosted.enabled() else urllib.request.urlopen
        with opener(req, timeout=URL_TIMEOUT) as r:
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
        scene = {"id": sid, "act": act, "setting": _words(sc.get("setting") or "", 30),
                 "caption": "" if act == "cold_open" else _clip_chars(sc.get("caption") or "", CAPTION_MAX),
                 "narration": "" if act == "cold_open" else _words(sc.get("narration") or "", NARRATION_MAX_WORDS),
                 "shots": shots}
        topic = _slug(sc.get("topic") or "")
        if topic:
            scene["topic"] = topic  # weekly episodes: which topic segment this scene is
        scenes.append(scene)
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
    for k in ("title_card", "countdown", "style", "writer", "week"):
        if sb.get(k):
            out[k] = " ".join(str(sb[k]).split())
    if isinstance(sb.get("segments"), list) and sb["segments"]:
        out["segments"] = _norm_segments(sb["segments"])
    return out


def _norm_segments(segments: list) -> list[dict]:
    """Weekly episodes: the topic segments, each {topic, headline (<= 60 chars, the on-screen caption), source_text}."""
    out = []
    for seg in segments:
        if not isinstance(seg, dict):
            continue
        headline = _clip_chars(seg.get("headline") or "", CAPTION_MAX)
        if not headline:
            continue
        out.append({"topic": _slug(seg.get("topic") or "") or "news", "headline": headline,
                    "source_text": _clip_chars(seg.get("source_text") or "", EXCERPT_MAX)})
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
          mock: bool | None = None, segments: list[dict] | None = None, week: str = "") -> dict:
    """The storyboard for a headline. mock=None decides by the key (no key -> template).
    segments=[{topic, headline, source_text}]: a weekly episode (see weekly_template / write_weekly): the anchor
    spine around one segment per topic; `headline` is the cold open's story (default: the first segment's)."""
    from . import config
    if segments:
        segs = _norm_segments(segments)
        if not segs:
            raise StoryboardError("segments: every segment needs a headline")
        minutes = clamp_minutes(minutes)
        headline = " ".join((headline or segs[0]["headline"]).split())
        if mock is None:
            mock = config.mode() != "live"
        if mock:
            return weekly_template(segs, minutes, headline=headline, week=week)
        return write_weekly(segs, minutes, headline=headline, week=week, style=style or "")
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


# -- weekly episodes: the anchor spine around one segment per topic --------------------------------

TOPIC_LABELS = {"ai": "AI", "crypto": "crypto", "politics": "politics", "tech": "tech", "markets": "the markets"}
MARKETS_CAPTION = "MARKETS IN 10 SECONDS"
SEGMENT_ACTS = ("setup", "escalation", "turn", "climax")
# Per topic: (setting, field speaker, a screen that shows the headline, the literal prop).
_TOPIC_LOOK = {
    "ai": ("a cathedral of blinking server racks, blue cel glow, cables like vines", "analyst",
           "a giant glowing terminal", "a towering robot made of filing cabinets"),
    "crypto": ("a neon trading pit under floating holographic coins, invented city", "operative",
               "a flickering coin-price hologram", "a mountain of oversized golden coins"),
    "politics": ("an invented parliament chamber with towering marble columns and empty benches", "operative",
                 "a parliament vote board", "a gigantic rubber stamp hovering over a podium"),
    "tech": ("a gleaming launch hall full of invented gadgets on pedestals, spotlights", "analyst",
             "a wall of product launch screens", "a gadget the size of a building"),
    "markets": ("a vast trading floor under a giant ticker wall, paper confetti", "operative",
                "a ticker board", "a chart arrow crashing through the ceiling"),
}
_DEFAULT_LOOK = ("the anime newsroom, a wall of world-map monitors", "operative", "a newsroom monitor",
                 "an oversized absurd prop")


def weekly_plan(n_segments: int, minutes: float) -> dict:
    """Shot counts of a weekly episode: about 20 shots a minute-long episode, 40 at three minutes."""
    total = max(2 * n_segments, min(6 * n_segments, round(12 * minutes * n_segments / 5)))
    per = [total // n_segments + (1 if i < total % n_segments else 0) for i in range(n_segments)]
    long = minutes >= 2
    return {"cold_open": 4 if long else 3, "segments": per, "markets": 3 if long else 2, "sign_off": 3 if long else 2}


def _short(text: str, words: int = 6) -> str:
    w = text.split()
    return " ".join(w[:words]) + ("..." if len(w) > words else "")


def _segment_shots(i: int, seg: dict, n: int) -> list[tuple]:
    setting, field, screen, prop = _TOPIC_LOOK.get(seg["topic"], _DEFAULT_LOOK)
    h = seg["headline"].replace('"', "'")
    label = TOPIC_LABELS.get(seg["topic"], seg["topic"])
    anchor_line = _clip_chars(f"In {label}: {_short(h)}", LINE_MAX)
    field_line = {"operative": "Vex here. Nobody saw this coming. Nobody.",
                  "analyst": "It's all connected. I have charts. So many charts."}[field]
    pool = [
        ("talk", ["anchor"], anchor_line, "Medium shot of KUROGANE at the news desk, facing camera, a monitor behind him", "dolly_in"),
        ("action", [], "", f"Wide shot of {prop} literalizing the story, invented officials recoil, speed lines", "crane_up"),
        ("insert", [], "", f"Close-up of {screen} that reads \"{h}\"", "static"),
        ("talk", [field], field_line, f"Close-up of {'VEX' if field == 'operative' else 'DR. HAKASE'} reporting, facing camera", "handheld"),
        ("still", [], "", "Extreme close-up of an invented citizen's wide shocked eyes, hatching, sweat drop", "crash_zoom"),
        ("action", [], "", f"Medium shot of staffers sprinting past {screen}, paperwork flying, impact frames", "whip_pan"),
    ]
    return [setting] + pool[:max(2, min(6, n))]


def weekly_template(segments: list[dict], minutes: float = 1.0, headline: str = "", week: str = "",
                    title_card: str = "") -> dict:
    """Deterministic weekly storyboard (mock mode and the shape the writer must match): cold open on the week's
    biggest story, one scene per topic segment (a talk line + cutaways, the headline verbatim as the caption),
    a "markets in 10 seconds" insert, the sign-off."""
    segs = _norm_segments(segments)
    if not segs:
        raise StoryboardError("segments: every segment needs a headline")
    plan = weekly_plan(len(segs), minutes)
    big = (headline or segs[0]["headline"]).replace('"', "'")
    cold = [
        ("action", [], "", "Extreme close-up of a red countdown clock in a dark war room, digits flipping, sparks of light", "crash_zoom"),
        ("insert", [], "", f"Close-up of a teletype printing the words \"{big}\" on yellowed paper", "static"),
        ("still", [], "", "Extreme close-up of KUROGANE's round glasses reflecting a glowing monitor", "dolly_in"),
        ("action", [], "", f"Wide shot of searchlights sweeping across giant tower screens that all read \"{big}\"", "crane_up"),
    ][:plan["cold_open"]]
    scenes = [{"id": "s1", "act": "cold_open", "setting": "a dark invented capital at night, CRT glow, teal shadows",
               "caption": "", "narration": "", "shots": cold}]
    for i, seg in enumerate(segs):
        setting, *shots = _segment_shots(i, seg, plan["segments"][i])
        scenes.append({"act": SEGMENT_ACTS[min(i, len(SEGMENT_ACTS) - 1)], "topic": seg["topic"], "setting": setting,
                       "caption": seg["headline"], "narration": _words(seg["source_text"] or seg["headline"], NARRATION_MAX_WORDS),
                       "shots": shots})
    markets = [
        ("insert", [], "", "Close-up of a ticker board flipping wildly, arrows up and down, invented currency symbols", "static"),
        ("still", [], "", "Wide shot of traders frozen mid-shout on a trading floor, paper confetti hanging in the air", "dolly_out"),
        ("action", [], "", "Medium shot of a giant chart arrow zigzagging across a wall of screens, speed lines", "whip_pan"),
    ][:plan["markets"]]
    scenes.append({"act": "turn", "topic": "markets_10s", "setting": "a vast trading floor under a giant ticker wall",
                   "caption": MARKETS_CAPTION, "narration": "Markets in ten seconds. Up. Down. Sideways. Nobody knows why.",
                   "shots": markets})
    sign = [
        ("still", [], "", "Wide shot of the dark studio, one monitor glowing with the week's headlines", "crane_up"),
        ("action", [], "", "Medium shot of the countdown clock hitting zero, a tiny paper flag pops out", "crash_zoom"),
        ("talk", ["anchor"], "That was the week. " + SIGN_OFF, "Close-up of KUROGANE folding his hands, perfectly deadpan", "dolly_out"),
    ]
    sign = sign[-plan["sign_off"]:]
    scenes.append({"act": "sign_off", "setting": "the anime newsroom after hours, one monitor still glowing",
                   "caption": "That was the week.", "narration": "", "shots": sign})
    for i, sc in enumerate(scenes, 1):
        sc["id"] = f"s{i}"
        sc["shots"] = [{"id": f"s{i}_{j}", "kind": k, "cast": cast, "line": line, "prompt": prompt, "move": move}
                       for j, (k, cast, line, prompt, move) in enumerate(sc["shots"], 1)]
    return normalize({"headline": headline or segs[0]["headline"], "minutes": minutes, "week": week,
                      "source_text_excerpt": " | ".join(s["headline"] for s in segs)[:EXCERPT_MAX],
                      "logline": f"{SERIES}: this week's briefing, {len(segs)} headlines taken literally.",
                      "title_card": _clip_chars((title_card or f"THIS WEEK {week}").upper(), 30),
                      "countdown": COUNTDOWN, "cast": default_cast(), "scenes": scenes, "segments": segs,
                      "writer": "template"})


WEEKLY_RULES = """
THIS IS A WEEKLY EPISODE: this week's briefing covers {n} headlines, one segment each. Ignore the act list above; use EXACTLY this scene plan, in order:
{plan}
- A segment scene opens with a talk shot that briefs its headline, then cutaways that literalize it. Its "caption" is its headline VERBATIM (copy it character for character; it is shown on screen). Set "topic" on every segment scene as given.
- The markets scene: caption exactly "{markets}", still and insert shots only, no talk.
- The last shot of the sign_off is the anchor's talk shot ending exactly with "{sign_off}"
"""

WEEKLY_USER = """This week's briefing ({week}). The cold open teases the biggest story: "{headline}"
Segments (topic, headline, source):
{segments}
Episode length: {minutes} minutes. Write the JSON object only (scenes carry "topic" where the plan gives one)."""


def write_weekly(segments: list[dict], minutes: float = 1.0, headline: str = "", week: str = "",
                 style: str = "") -> dict:
    """One Router chat call for a weekly episode (one retry on a bad reply). The scene plan, captions and topics
    are enforced from weekly_template's shape; the writer supplies lines, prompts, settings and narration."""
    shape = weekly_template(segments, minutes, headline=headline, week=week)
    plan_lines = []
    for i, sc in enumerate(shape["scenes"], 1):
        kinds = ", ".join(s["kind"] for s in sc["shots"])
        extra = f', topic "{sc["topic"]}", caption "{sc["caption"]}"' if sc.get("topic") else ""
        plan_lines.append(f"{i}. act {sc['act']}{extra}: {len(sc['shots'])} shots ({kinds})")
    rules = WEEKLY_RULES.format(n=len(shape["segments"]), plan="\n".join(plan_lines), markets=MARKETS_CAPTION,
                                sign_off=SIGN_OFF)
    n = len(shape["scenes"])
    system = SYSTEM_PROMPT.format(series=SERIES, cast=_cast_block(), scenes=n,
                                  acts=", ".join(sc["act"] for sc in shape["scenes"]),
                                  shots=sum(len(sc["shots"]) for sc in shape["scenes"]), sign_off=SIGN_OFF,
                                  moves=", ".join(MOVES),
                                  style=(f"- Visual style direction from the user: {style}\n" if style else "") + rules)
    user = WEEKLY_USER.format(week=week or "this week", headline=shape["headline"].replace('"', "'"), minutes=minutes,
                              segments="\n".join(f'- {s["topic"]}: "{s["headline"]}" ({s["source_text"][:400]})'
                                                 for s in shape["segments"]))
    last_error = None
    for attempt in range(2):
        prompt = user if not last_error else (
            f"{user}\n\nYour previous reply was rejected: {last_error}. Fix it and return the JSON object only.")
        reply = writer._chat(system, prompt, max_tokens=min(32000, 2500 + n * 900), timeout=420)
        try:
            doc = json.loads(writer._strip_fences(reply))
            if not isinstance(doc, dict):
                raise StoryboardError("reply is not a JSON object")
            got = doc.get("scenes")
            if not isinstance(got, list) or len(got) != n:
                raise StoryboardError(f"expected {n} scenes, got {len(got) if isinstance(got, list) else 0}")
            for want, sc in zip(shape["scenes"], got):
                if not isinstance(sc, dict):
                    raise StoryboardError("a scene is not an object")
                sc["act"] = want["act"]
                if want.get("topic"):
                    sc["topic"], sc["caption"] = want["topic"], want["caption"]   # verbatim headline captions
                    if want["topic"] != "markets_10s" and not any(
                            isinstance(s, dict) and s.get("kind") == "talk" for s in sc.get("shots") or []):
                        raise StoryboardError(f"segment {want['topic']} has no talk shot")
            cast = default_cast() + [c for c in (doc.get("cast") or []) if isinstance(c, dict)
                                     and _slug(c.get("id") or c.get("name") or "") not in writer.CAST]
            sb = normalize({**doc, "headline": shape["headline"], "minutes": minutes, "cast": cast[:5],
                            "source_text_excerpt": shape["source_text_excerpt"], "writer": writer.CHAT_MODEL,
                            "style": style, "week": week, "segments": shape["segments"],
                            "countdown": doc.get("countdown") or COUNTDOWN,
                            "title_card": shape["title_card"]})
            return _finish(sb)
        except (ValueError, StoryboardError) as e:
            last_error = str(e)[:300]
            print(f"[anime-news] weekly writer reply rejected ({attempt + 1}/2): {last_error}", file=sys.stderr, flush=True)
    raise StoryboardError(f"weekly writer failed: {last_error}")
