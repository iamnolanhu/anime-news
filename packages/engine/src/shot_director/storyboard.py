"""Headline (+ optional article text or URL) -> storyboard: the editable plan of a whole episode.

One Router chat call writes it (writer v2's show bible and voice, extended to many scenes of mixed shots);
mock mode, or no key, returns a deterministic template storyboard of the same shape. `normalize()` is the
one validator: it checks the writer's reply and every storyboard the web app sends back after editing.
No cap on scenes or shots: the writer targets PsyopAnime pace, about 20 shots a minute (5 talk, 9 still or insert,
6 action) in 4-5 scenes a minute; `shot_plan()` is that plan, and the mock template follows it shot for shot.
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
SCENES_PER_MINUTE = 4.5   # 4-5 scenes a minute: 0.5 min = 2, 1 min = 5, 2 min = 9, 3 min = 14
# PsyopAnime pace (median shot 1.4 s, 27-50 cuts a minute): 18-22 shots per minute of target length. On screen a talk
# shot holds 2.5-3 s, a still or insert 1.2-1.5 s, an action shot 1-2 s per angle (two angles: "Hard cut to:").
SHOTS_PER_MINUTE = 20
MIX_PER_MINUTE = {"talk": 5, "cutaway": 9, "action": 6}   # cutaway = a still or an insert
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


CAST_FILE = "cast.json"         # optional, in the media dir: replaces the built-in cast (schema: docs/STORYBOARD.md)
CORE_CAST = tuple(writer.CAST)  # anchor, operative, analyst: a cast file must keep these ids
POSE_KINDS = ("talk", "action", "still")
POSE_MAX_WORDS = 25
TRAIT_MAX_WORDS = 30


def builtin_cast() -> list[dict]:
    return [{"id": cid, "name": c["name"], "role": c["role"], "look": c["look"], "voice": DEFAULT_VOICES[cid]}
            for cid, c in writer.CAST.items()]


def cast_file(media=None):
    from pathlib import Path

    from . import config
    return Path(media or config.media_dir()) / CAST_FILE


def load_cast_file(media=None) -> list[dict] | None:
    """The cast from <media>/cast.json, or None when there is no file or it is invalid (with a warning:
    a broken cast file never stops an episode, it falls back to the built-in cast)."""
    path = cast_file(media)
    if not path.exists():
        return None
    try:
        doc = json.loads(path.read_text())
        members = doc.get("cast") if isinstance(doc, dict) else doc
        cast = _norm_members(members, {c["id"]: c for c in builtin_cast()})
        missing = [cid for cid in CORE_CAST if cid not in {c["id"] for c in cast}]
        if missing:
            raise ValueError(f"missing cast ids {', '.join(missing)}")
        return cast
    except (OSError, ValueError) as e:
        print(f"[anime-news] {path} ignored ({e}); using the built-in cast", file=sys.stderr)
        return None


def default_cast() -> list[dict]:
    """<media>/cast.json when present and valid, else the built-in cast (writer.CAST)."""
    return load_cast_file() or builtin_cast()


class StoryboardError(ValueError):
    pass


# -- input ----------------------------------------------------------------------------------------

def _half_up(x: float) -> int:
    return int(math.floor(x + 0.5))


def scene_count(minutes: float) -> int:
    return max(2, _half_up(minutes * SCENES_PER_MINUTE))


def shot_budget(minutes: float, floor: int = 1) -> dict:
    """Shots for `minutes` of episode at PsyopAnime pace: {"shots", "talk", "action", "cutaway"} (at least `floor`
    shots and one talk shot, the sign-off)."""
    total = max(floor, 1, _half_up(minutes * SHOTS_PER_MINUTE))
    talk = min(total, max(1, _half_up(minutes * MIX_PER_MINUTE["talk"])))
    action = min(total - talk, max(1, _half_up(minutes * MIX_PER_MINUTE["action"])))
    return {"shots": total, "talk": talk, "action": action, "cutaway": total - talk - action}


def _kinds(scenes: list[dict], actions: int) -> list[list[str]]:
    """Shot kinds per scene. Each scene is {"shots", "talks", "talk_last" (talk ends it, else opens it),
    "cutaways_only"}. `actions` of the free picture slots are action shots, spread evenly over the episode (the first
    free slot is one); the rest are cutaways alternating still and insert. Talks are spread through their scene."""
    free = sum(sc["shots"] - sc["talks"] for sc in scenes if not sc.get("cutaways_only"))
    actions = min(actions, free)
    k = cut = 0
    out = []
    for sc in scenes:
        n, t = sc["shots"], min(sc["talks"], sc["shots"])
        pics = []
        for _ in range(n - t):
            if not sc.get("cutaways_only") and (k * actions) // free > ((k - 1) * actions) // free:
                pics.append("action")
            else:
                pics.append(("still", "insert")[cut % 2])
                cut += 1
            k += 0 if sc.get("cutaways_only") else 1
        at = {(i * n) // t for i in range(t)} if t else set()
        if sc.get("talk_last"):
            at = {n - 1 - p for p in at}
        it = iter(pics)
        out.append(["talk" if j in at else next(it) for j in range(n)])
    return out


def _spread_talks(sizes: list[int], talks: list[int], extra: int, where: list[int]) -> None:
    """Hand `extra` talk shots round-robin to the scenes `where`, at most half of a scene's shots."""
    while extra > 0 and where:
        room = [j for j in where if talks[j] < max(1, sizes[j] // 2)]
        for j in room[:extra]:
            talks[j] += 1
        extra -= len(room[:extra])
        if not room:
            break


def shot_plan(minutes: float) -> list[tuple[str, list[str]]]:
    """(act, shot kinds) per scene of a headline episode: shot_budget's shots spread evenly over act_plan's scenes;
    no talk in the cold open, one talk opening each middle scene (extras round-robin), the sign-off talk last."""
    acts = act_plan(scene_count(minutes))
    n = len(acts)
    b = shot_budget(minutes, n)
    sizes = [b["shots"] // n + (1 if i < b["shots"] % n else 0) for i in range(n)]
    talks = [0] * n
    talks[-1] = 1
    _spread_talks(sizes, talks, b["talk"] - 1, [i for i, a in enumerate(acts) if a not in ("cold_open", "sign_off")])
    kinds = _kinds([{"shots": sizes[i], "talks": talks[i], "talk_last": acts[i] == "sign_off"} for i in range(n)],
                   b["action"])
    return list(zip(acts, kinds))


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


def _norm_poses(poses: object) -> dict:
    """{kind: {"phrase", "id"?, "ref_image"?}} for talk / action / still; a bare string is the phrase."""
    out = {}
    if not isinstance(poses, dict):
        return out
    for kind in POSE_KINDS:
        v = poses.get(kind)
        phrase = _words((v.get("phrase") if isinstance(v, dict) else v) or "", POSE_MAX_WORDS)
        if not phrase:
            continue
        entry = {"phrase": phrase}
        if isinstance(v, dict):
            entry.update({k: str(v[k]).strip() for k in ("id", "ref_image") if str(v.get(k) or "").strip()})
        out[kind] = entry
    return out


def _norm_members(cast: object, defaults: dict) -> list[dict]:
    out, seen = [], set()
    if not isinstance(cast, list):
        return out
    for i, c in enumerate(cast):
        if not isinstance(c, dict):
            continue
        cid = _slug(c.get("id") or c.get("name") or f"cast{i + 1}") or f"cast{i + 1}"
        while cid in seen:
            cid += "_x"
        seen.add(cid)
        base = defaults.get(cid, {})
        m = {"id": cid, "name": _clip_chars(c.get("name") or base.get("name") or cid.upper(), 40),
             "role": _clip_chars(c.get("role") or base.get("role") or "an original character", 60),
             "look": _words(c.get("look") or base.get("look") or "an original anime character", 45),
             "voice": str(c.get("voice") or base.get("voice") or "").strip()}
        trait = _words(c.get("trait") or base.get("trait") or "", TRAIT_MAX_WORDS)
        poses = _norm_poses(c.get("poses")) or base.get("poses")
        if trait:
            m["trait"] = trait
        if poses:
            m["poses"] = poses
        out.append(m)
    return out


def _norm_cast(cast: object) -> list[dict]:
    defaults = {c["id"]: c for c in default_cast()}
    return _norm_members(cast, defaults) or list(defaults.values())


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

# Per act: (setting, caption, narration, pools). A scene of the template takes its shot plan from shot_plan() and fills
# each slot from its act's pool of that kind, in order (an act that comes back continues where it left off). Talk:
# (speaker, line <= 60 chars, prompt, move); the others: (prompt, move). Every action prompt is one clear motion and a
# "Hard cut to:" second angle, so its clip gives the cut two compositions.
_TEMPLATE = {
    "cold_open": ("a dark invented capital at night, CRT glow, teal shadows, burnt-orange light", "", "", {
        "talk": [],
        "action": [
            ("Extreme close-up of a red alarm light spinning in a dark war room. Hard cut to: wide shot of the room flooding with red light", "crash_zoom"),
            ("Wide shot of searchlights sweeping across giant tower screens. Hard cut to: close-up of one screen flaring white", "crane_up"),
            ("Medium shot of a teletype arm hammering out paper. Hard cut to: overhead shot of the paper spilling onto the floor", "handheld"),
        ],
        "still": [
            ("Extreme close-up of {anchor}'s eyes reflecting a glowing monitor", "dolly_in"),
            ("Wide shot of the invented capital skyline at night, every window dark but one", "static"),
        ],
        "insert": [
            ("Close-up of a teletype printing the words \"{h}\" on yellowed paper", "static"),
            ("Close-up of a red telephone on a desk, its cord swinging", "static"),
        ],
    }),
    "setup": ("the anime newsroom, a long desk before a wall of world-map monitors", "{short}",
              "Sources confirm it. {h}. Officials have convened. Nobody was ready.", {
        "talk": [
            ("anchor", "Good evening. This week: {short_line}", "Medium shot of {anchor} at the news desk, facing camera", "dolly_in"),
            ("anchor", "We checked. Then we checked again. It's real.", "Close-up of {anchor} lowering a sheet of paper, facing camera", "static"),
            ("anchor", "Officials say stay calm. Officials are not calm.", "Medium close-up of {anchor} at the desk, one eyebrow raised, facing camera", "dolly_in"),
        ],
        "action": [
            ("Overhead shot of a war-room table where an oversized absurd prop slams down. Hard cut to: close-up of staffers recoiling", "handheld"),
            ("Medium shot of staffers sprinting down a corridor with armfuls of paperwork, speed lines. Hard cut to: low angle of papers raining down", "whip_pan"),
            ("Wide shot of a press room where every chair spins round at once. Hard cut to: close-up of a dropped pen bouncing", "orbit"),
        ],
        "still": [
            ("Low angle of invented officials staring in silence at a glowing map", "static"),
            ("Extreme close-up of a staffer's trembling coffee cup, ripples in the coffee", "dolly_in"),
            ("Wide shot of the newsroom frozen mid-motion, every head turned to the monitors", "dolly_out"),
        ],
        "insert": [
            ("Close-up of a classified folder stamped URGENT with \"{h}\" typed on the cover", "static"),
            ("Close-up of a monitor in the newsroom showing \"{short}\" in red letters", "static"),
            ("Close-up of a stamp pressing BREAKING onto a memo", "static"),
        ],
    }),
    "escalation": ("a rooftop over the invented capital at dusk, painted sky, wind", "It is getting bigger.",
                   "The situation escalated. Then it escalated again. The clock is still running.", {
        "talk": [
            ("operative", "{operative_spoken} here. It's worse than we thought.", "Close-up of {operative} whispering into her wrist on a windy rooftop", "handheld"),
            ("operative", "It doubled. Then it doubled again.", "Medium shot of {operative} crouched behind a rooftop vent, facing camera", "static"),
            ("operative", "I need backup. And a much bigger net.", "Close-up of {operative} gripping her earpiece, wind in her hair, facing camera", "handheld"),
        ],
        "action": [
            ("Wide shot of the absurd prop growing to the size of a building. Hard cut to: close-up of citizens pointing upward", "crane_up"),
            ("Medium shot of pigeons scattering off a giant news screen, speed lines. Hard cut to: wide shot of feathers drifting over the street", "whip_pan"),
            ("Low angle of a billboard toppling in the wind, debris. Hard cut to: close-up of a citizen ducking", "crash_zoom"),
        ],
        "still": [
            ("Extreme close-up of a citizen's wide shocked eyes, hatching, sweat drop", "crash_zoom"),
            ("Wide shot of a crowd frozen in the street, all looking up", "dolly_out"),
            ("Low angle of the prop's shadow falling across the whole city", "static"),
        ],
        "insert": [
            ("Close-up of a ticker board flipping wildly, every line reads \"{short}\"", "static"),
            ("Close-up of a weather vane spinning out of control", "static"),
            ("Close-up of a rooftop antenna sparking", "static"),
        ],
    }),
    "turn": ("a cramped lab wallpapered with charts and red string", "It's all connected.",
             "The analysts have a theory. The theory has charts.", {
        "talk": [
            ("analyst", "It's all connected. I have charts. So many charts.", "Close-up of {analyst} before a red-string evidence board", "dolly_in"),
            ("analyst", "Follow the string. The string always knows.", "Medium shot of {analyst} tapping the evidence board, facing camera", "static"),
            ("analyst", "I ran the numbers. The numbers ran away.", "Close-up of {analyst} pushing up his glasses, facing camera", "dolly_in"),
        ],
        "action": [
            ("Medium shot of red string snapping taut across a wall of pinned photos. Hard cut to: close-up of a pin popping loose", "orbit"),
            ("Wide shot of the lab lights flickering as a monitor wall glitches. Hard cut to: close-up of a monitor showing static", "handheld"),
            ("Medium shot of a stack of charts toppling off a desk. Hard cut to: overhead shot of charts fanned across the floor", "crash_zoom"),
        ],
        "still": [
            ("Overhead shot of a desk buried in coffee mugs and annotated maps", "static"),
            ("Extreme close-up of {analyst}'s glasses reflecting a web of red string", "dolly_in"),
            ("Wide shot of the lab at night, one desk lamp, walls of charts", "dolly_out"),
        ],
        "insert": [
            ("Close-up of a chart whose arrow crashes straight down off the page", "crash_zoom"),
            ("Close-up of a pinned photo of an invented landmark circled in red", "static"),
            ("Close-up of a notebook page covered in arrows and question marks", "static"),
        ],
    }),
    "climax": ("a dusty western main street at high noon, painted sky", "The standoff begins.",
               "Two bureaucracies. One form. High noon.", {
        "talk": [
            ("operative", "Nobody blinks. Nobody files. Nobody wins.", "Close-up of {operative} watching from behind a water trough", "dolly_in"),
            ("anchor", "This is the moment the forms were made for.", "Medium shot of {anchor} at the desk, perfectly still, facing camera", "static"),
            ("operative", "Somebody has to sign. Nobody will.", "Close-up of {operative} squinting into the sun, facing camera", "handheld"),
        ],
        "action": [
            ("Wide shot of two invented rival bureaucrats in a standoff, each raising a giant rubber stamp. Hard cut to: close-up of their eyes narrowing", "orbit"),
            ("Medium shot as both stamps slam down on one form at once, white flash, speed lines. Hard cut to: overhead shot of the form", "crash_zoom"),
            ("Wide shot of a tumbleweed of paperwork rolling down the street. Hard cut to: low angle of saloon doors swinging", "whip_pan"),
        ],
        "still": [
            ("Wide shot of the whole town buried in stamped paperwork", "dolly_out"),
            ("Extreme close-up of a bureaucrat's hand hovering over a stamp", "dolly_in"),
            ("Low angle of the two rivals silhouetted against the sun", "static"),
        ],
        "insert": [
            ("Close-up of a wanted poster that reads \"{short}\" flapping in the wind", "static"),
            ("Close-up of an inkpad, glistening, untouched", "static"),
            ("Close-up of a form with two empty signature lines", "static"),
        ],
    }),
    "sign_off": ("the anime newsroom after hours, one monitor still glowing", "That was the news.",
                 "It will happen again. We will be here.", {
        "talk": [
            ("anchor", "It will happen again. " + SIGN_OFF, "Close-up of {anchor} at the desk, hands folded, perfectly deadpan", "dolly_out"),
        ],
        "action": [
            ("Medium shot of a giant red alarm button slammed down. Hard cut to: close-up of a tiny paper flag popping out", "crash_zoom"),
            ("Wide shot of the newsroom lights switching off row by row. Hard cut to: close-up of the last lamp", "dolly_out"),
        ],
        "still": [
            ("Wide shot of the dark studio, one monitor glowing with \"{short}\"", "crane_up"),
            ("Extreme close-up of {anchor}'s folded hands on the desk", "static"),
        ],
        "insert": [
            ("Close-up of the night's script dropped in a wastebasket", "static"),
            ("Close-up of a desk nameplate under the glow of one monitor", "static"),
        ],
    }),
}


def _fill_shots(pools: dict, kinds: list[str], used: dict, fill: dict | None = None) -> list[tuple]:
    """Shots of one scene: each slot takes the next item of its kind's pool (wrapping); `used` counts per kind.
    `fill` formats the pool's {placeholders}; without it the pool's text is used as is."""
    fmt = (lambda t: t.format(**fill)) if fill is not None else (lambda t: t)
    out = []
    for kind in kinds:
        pool = pools[kind]
        item = pool[used.get(kind, 0) % len(pool)]
        used[kind] = used.get(kind, 0) + 1
        cast, line, (prompt, move) = ([item[0]], item[1], item[2:]) if kind == "talk" else ([], "", item)
        out.append((kind, cast, fmt(line), fmt(prompt), move))
    return out


def template(headline: str, minutes: float = 1.0, text: str = "") -> dict:
    """Deterministic storyboard of the full shape (mock mode, the writer's shot plan, and tests): shot_plan()'s
    kinds, filled from each act's pools."""
    h = " ".join(headline.split()).replace('"', "'")
    words = h.split()
    short = " ".join(words[:6]) + ("..." if len(words) > 6 else "")
    members = default_cast()
    names = {c["id"]: c["name"] for c in members}
    fill = {"h": h, "short": short, "short_line": _clip_chars(short, LINE_MAX - 25), **names,
            "operative_spoken": names["operative"].title()}
    scenes, used = [], {}
    for i, (act, kinds) in enumerate(shot_plan(minutes), 1):
        setting, caption, narration, pools = _TEMPLATE[act]
        shots = _fill_shots(pools, kinds, used.setdefault(act, {}), fill)
        scenes.append({"id": f"s{i}", "act": act, "setting": setting, "caption": caption.format(**fill),
                       "narration": narration.format(**fill),
                       "shots": [{"id": f"s{i}_{j}", "kind": k, "cast": cast, "line": line, "prompt": prompt, "move": move}
                                 for j, (k, cast, line, prompt, move) in enumerate(shots, 1)]})
    return normalize({"headline": h, "minutes": minutes, "source_text_excerpt": text[:EXCERPT_MAX],
                      "logline": f"{SERIES}: \"{h}\", briefed like a war-room emergency and taken literally.",
                      "title_card": _clip_chars(short.upper(), 30), "countdown": COUNTDOWN,
                      "cast": members, "scenes": scenes, "writer": "template"})


# -- the writer -----------------------------------------------------------------------------------

SYSTEM_PROMPT = """You are the head writer and storyboard artist of "{series}", an original 2D anime news show (late-1980s OVA look). Each episode is this week's briefing: it turns the week's real headline(s) into a short episode people share. The anchor signs off with "back next week".

THE SHOW
- Deadpan war-room gravitas applied to an absurd or mundane claim. The characters never wink; the joke is the contrast.
- Literalize the headline: its abstract claim becomes an oversized physical prop or situation.
- The episode is a STORY in acts: cold_open (image only, a gorgeous mystery hook), setup (the claim, briefed gravely), escalation (bigger and dumber, against a ticking countdown the cut draws as an overlay), turn (a twist or conspiracy), climax (a genre pastiche that dramatizes the moral: samurai duel, heist, kaiju attack, sports final, space battle, courtroom...), sign_off (the button).

RECURRING CAST (original characters; use their NAMES in lines and prompts; the cast ids are fixed)
{cast}
When a member listed with Poses appears in a talk, action or still shot, put her pose for that shot kind into the prompt right after the shot size (it keeps poses natural and varied).
You may add at most two new invented characters (original archetypes, never a real person) to "cast".

PACE: a fast anime short, hard cuts, every shot shown once, a shot on screen about 1.4 s. Many short shots, never a few long ones.
SHOT KINDS (mix them in every scene; no two consecutive shots show the same composition)
- talk: one cast member speaks one line on camera, mouth visible, facing camera. "cast": [speaker id], "line": the spoken words. Held 2.5-3 s on screen, so the line is short.
- action: one strong subject doing ONE clear motion (one verb), then "Hard cut to:" and a second, different composition of the same moment (a reaction close-up, the prop, a wide), so the clip gives two shots of 1-2 s. Impact frames, speed lines, debris, wind, light. Example: "Wide shot of a giant stamp slamming onto a desk, speed lines. Hard cut to: close-up of the clerk flinching".
- still: a single striking composition (face, eyes, reaction, wide establishing) used as a quick 1.2-1.5 s cut-in.
- insert: a close-up of a prop, screen, ticker or document, a quick 1.2-1.5 s cutaway; the headline text may appear on it.

HARD RULES
- Exactly {scenes} scenes, acts in this order: {acts}.
- {shots} shots in total ({per_minute} a minute): about {talk} talk, {cutaway} still or insert, {action} action. Follow this shot plan (you may swap kinds within a scene, never drop shots):
{plan}
- cold_open: no talk shots, empty caption and narration.
- The last shot of the last scene is a talk shot by the anchor whose line ends exactly with "{sign_off}"
- Spoken lines: at most 60 characters, punchy, quotable, funny on their own as a subtitle, no stage directions.
- Prompts: at most 40 words (action: at most 55 with its "Hard cut to:" angle), drawn-anime terms only (inked, cel, painted background), start with the shot size (extreme close-up, close-up, medium, wide, overhead, low angle). Talk prompts frame the speaker clearly. Never photoreal, cinematic, 4K or lens terms.
- caption: the scene's one-clause subtitle (<= 40 chars). narration: 1-2 deadpan briefing sentences (<= 30 words) or "" when the scene is all dialogue.
- move: one of {moves}. seconds (the generated clip's length, not its time on screen; the cut trims it): talk 4-8 (by line length), action 5, still/insert 5.
- Never depict, describe or name a real person; refer to people in the headline only by role ("the official"). Invented countries and institutions only. No real logos or flags. Safe for work: no weapons pointed at people, no gore. Never name an anime studio, film or artist.
- """ + writer.NO_CLOCKS + """
{style}
OUTPUT: ONE JSON object, nothing else (no prose, no code fences):
{{"logline": "one-sentence pitch", "title_card": "<= 30 chars, ALL CAPS", "countdown": "the countdown overlay's label, <= 24 chars (default: NEXT BRIEFING IN)",
  "cast": [{{"id", "name", "role", "look"}}],
  "scenes": [{{"act", "setting": "<= 20 words", "caption", "narration",
              "shots": [{{"kind": "talk|action|still|insert", "cast": ["id"], "line": "only for talk", "prompt", "move", "seconds"}}]}}]}}"""

USER_PROMPT = """Headline: "{headline}"
Source text (may be empty; use it for facts, never for real names of people):
{source}
Episode length: {minutes} minutes. Write the JSON object only."""


def _cast_block(cast: list[dict]) -> str:
    """One line per member; a member with poses gets its pose phrase per shot kind (cast[].poses)."""
    lines = []
    for c in cast:
        trait = c.get("trait") or writer.CAST.get(c["id"], {}).get("trait") or ""
        line = f"- {c['name']} (id \"{c['id']}\", {c['role']}): {c['look']}" + (f"; {trait}" if trait else "") + "."
        poses = c.get("poses") or {}
        if poses:
            line += " Poses: " + "; ".join(f"{k} shot: she {poses[k]['phrase']}" for k in POSE_KINDS if k in poses) + "."
        lines.append(line)
    return "\n".join(lines)


def write(headline: str, text: str = "", minutes: float = 1.0, style: str = "") -> dict:
    """One Router chat call (one retry with the validation error on a bad reply). Raises on failure."""
    shape = template(headline, minutes)
    n = len(shape["scenes"])
    base_cast = default_cast()
    base_ids = {c["id"] for c in base_cast}
    system = _system_prompt(shape, base_cast, f"- Visual style direction from the user: {style}\n" if style else "")
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
            cast = base_cast + [c for c in (doc.get("cast") or []) if isinstance(c, dict)
                                and _slug(c.get("id") or c.get("name") or "") not in base_ids]
            sb = normalize({**doc, "headline": headline, "minutes": minutes, "cast": cast[:5],
                            "source_text_excerpt": text[:EXCERPT_MAX], "writer": writer.CHAT_MODEL,
                            "style": style})
            if len(sb["scenes"]) < max(1, math.ceil(n * 0.75)):
                raise StoryboardError(f"expected {n} scenes, got {len(sb['scenes'])}")
            _check_density(sb, shape)
            return _finish(sb)
        except (ValueError, StoryboardError) as e:
            last_error = str(e)[:300]
            print(f"[anime-news] writer reply rejected ({attempt + 1}/2): {last_error}", file=sys.stderr, flush=True)
    raise StoryboardError(f"writer failed: {last_error}")


def _plan_text(scenes: list[dict]) -> str:
    """The shot plan as the writer reads it: one line per scene with its act, topic/caption and shot kinds."""
    lines = []
    for i, sc in enumerate(scenes, 1):
        extra = f', topic "{sc["topic"]}", caption "{sc["caption"]}"' if sc.get("topic") else ""
        lines.append(f"  {i}. {sc['act']}{extra}: {len(sc['shots'])} shots ({', '.join(s['kind'] for s in sc['shots'])})")
    return "\n".join(lines)


def _system_prompt(shape: dict, cast: list[dict], style: str) -> str:
    """SYSTEM_PROMPT for a storyboard of `shape`'s plan (a template storyboard: headline or weekly)."""
    st = stats(shape)
    return SYSTEM_PROMPT.format(series=SERIES, cast=_cast_block(cast), scenes=st["scenes"],
                                acts=", ".join(sc["act"] for sc in shape["scenes"]), shots=st["shots"],
                                per_minute=f"{SHOTS_PER_MINUTE - 2}-{SHOTS_PER_MINUTE + 2}", talk=st["talk"],
                                cutaway=st["still"] + st["insert"], action=st["action"],
                                plan=_plan_text(shape["scenes"]), sign_off=SIGN_OFF, moves=", ".join(MOVES),
                                style=style)


DENSITY_FLOOR = 0.8   # a writer reply with fewer than 80 % of the planned shots is sent back (edits have no floor)


def _check_density(sb: dict, shape: dict) -> None:
    want, got = stats(shape)["shots"], stats(sb)["shots"]
    if got < math.floor(want * DENSITY_FLOOR):
        raise StoryboardError(f"expected about {want} shots (the shot plan), got {got}: write every planned shot")


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
    """Shot kinds per scene of a weekly episode at PsyopAnime pace (shot_budget: about 20 shots a minute):
    {"cold_open", "segments": [one list per segment], "markets", "sign_off"}. Each segment opens on a talk shot
    (extra talks round-robin), the markets insert is cutaways only, the sign-off ends on the anchor."""
    long = minutes >= 2
    cold, markets, sign = (4, 3, 3) if long else (3, 2, 2)
    b = shot_budget(minutes, cold + markets + sign + 2 * n_segments)
    seg_total = b["shots"] - cold - markets - sign
    sizes = [seg_total // n_segments + (1 if i < seg_total % n_segments else 0) for i in range(n_segments)]
    talks = [1] * n_segments
    _spread_talks(sizes, talks, b["talk"] - n_segments - 1, list(range(n_segments)))
    kinds = _kinds([{"shots": cold, "talks": 0}]
                   + [{"shots": sizes[i], "talks": talks[i]} for i in range(n_segments)]
                   + [{"shots": markets, "talks": 0, "cutaways_only": True},
                      {"shots": sign, "talks": 1, "talk_last": True}], b["action"])
    return {"cold_open": kinds[0], "segments": kinds[1:-2], "markets": kinds[-2], "sign_off": kinds[-1]}


def _short(text: str, words: int = 6) -> str:
    w = text.split()
    return " ".join(w[:words]) + ("..." if len(w) > words else "")


def _segment_pools(seg: dict) -> tuple[str, dict]:
    """(setting, pools) of one topic segment, in _TEMPLATE's pool shape (text final, no placeholders)."""
    setting, field, screen, prop = _TOPIC_LOOK.get(seg["topic"], _DEFAULT_LOOK)
    h = seg["headline"].replace('"', "'")
    label = TOPIC_LABELS.get(seg["topic"], seg["topic"])
    field_name = "VEX" if field == "operative" else "DR. HAKASE"
    field_lines = {"operative": ("Vex here. Nobody saw this coming. Nobody.", "Vex again. It got bigger while I talked."),
                   "analyst": ("It's all connected. I have charts. So many charts.", "The charts agree. That worries me.")}[field]
    return setting, {
        "talk": [
            ("anchor", _clip_chars(f"In {label}: {_short(h)}", LINE_MAX),
             "Medium shot of KUROGANE at the news desk, facing camera, a monitor behind him", "dolly_in"),
            (field, field_lines[0], f"Close-up of {field_name} reporting, facing camera", "handheld"),
            ("anchor", "We are told this is normal. It is not.", "Close-up of KUROGANE, one eyebrow raised, facing camera", "static"),
            (field, field_lines[1], f"Medium shot of {field_name} on location, facing camera", "handheld"),
        ],
        "action": [
            (f"Wide shot of {prop} rising over invented officials, speed lines. Hard cut to: close-up of an official recoiling", "crane_up"),
            (f"Medium shot of staffers sprinting past {screen}, paperwork flying, impact frames. Hard cut to: overhead shot of papers settling", "whip_pan"),
            (f"Low angle of {prop} toppling toward the camera. Hard cut to: wide shot of a dust cloud rolling out", "crash_zoom"),
        ],
        "still": [
            ("Extreme close-up of an invented citizen's wide shocked eyes, hatching, sweat drop", "crash_zoom"),
            (f"Wide shot of {prop} looming in silence, tiny figures below", "dolly_out"),
            ("Low angle of invented officials frozen mid-gesture under harsh light", "static"),
        ],
        "insert": [
            (f"Close-up of {screen} that reads \"{h}\"", "static"),
            ("Close-up of a stamped memo sliding across a desk", "static"),
            ("Close-up of a press badge swinging on a lanyard", "static"),
        ],
    }


def weekly_template(segments: list[dict], minutes: float = 1.0, headline: str = "", week: str = "",
                    title_card: str = "") -> dict:
    """Deterministic weekly storyboard (mock mode and the shape the writer must match): cold open on the week's
    biggest story, one scene per topic segment (a talk line + cutaways, the headline verbatim as the caption),
    a "markets in 10 seconds" insert, the sign-off; shot kinds from weekly_plan()."""
    segs = _norm_segments(segments)
    if not segs:
        raise StoryboardError("segments: every segment needs a headline")
    plan = weekly_plan(len(segs), minutes)
    big = (headline or segs[0]["headline"]).replace('"', "'")
    cold = {"talk": [], "action": [
        ("Extreme close-up of a red alarm light spinning in a dark war room. Hard cut to: wide shot of the room flooding with red light", "crash_zoom"),
        ("Wide shot of searchlights sweeping across giant tower screens. Hard cut to: close-up of one screen flaring white", "crane_up"),
    ], "still": [
        ("Extreme close-up of KUROGANE's round glasses reflecting a glowing monitor", "dolly_in"),
        ("Wide shot of the invented capital skyline at night, every window dark but one", "static"),
    ], "insert": [
        (f"Close-up of a teletype printing the words \"{big}\" on yellowed paper", "static"),
        ("Close-up of a red telephone on a desk, its cord swinging", "static"),
    ]}
    scenes = [{"act": "cold_open", "setting": "a dark invented capital at night, CRT glow, teal shadows",
               "caption": "", "narration": "", "shots": _fill_shots(cold, plan["cold_open"], {})}]
    for i, seg in enumerate(segs):
        setting, pools = _segment_pools(seg)
        used = {"still": i, "insert": 0, "action": i, "talk": 0}   # vary the shared compositions between segments
        scenes.append({"act": SEGMENT_ACTS[min(i, len(SEGMENT_ACTS) - 1)], "topic": seg["topic"], "setting": setting,
                       "caption": seg["headline"], "narration": _words(seg["source_text"] or seg["headline"], NARRATION_MAX_WORDS),
                       "shots": _fill_shots(pools, plan["segments"][i], used)})
    markets = {"still": [
        ("Wide shot of traders frozen mid-shout on a trading floor, paper confetti hanging in the air", "dolly_out"),
        ("Extreme close-up of a trader's eyes reflecting red and green arrows", "dolly_in"),
    ], "insert": [
        ("Close-up of a ticker board flipping wildly, arrows up and down, invented currency symbols", "static"),
        ("Close-up of a chart arrow zigzagging across a wall of screens", "static"),
    ]}
    scenes.append({"act": "turn", "topic": "markets_10s", "setting": "a vast trading floor under a giant ticker wall",
                   "caption": MARKETS_CAPTION, "narration": "Markets in ten seconds. Up. Down. Sideways. Nobody knows why.",
                   "shots": _fill_shots(markets, plan["markets"], {})})
    sign = {"talk": [("anchor", "That was the week. " + SIGN_OFF, "Close-up of KUROGANE folding his hands, perfectly deadpan", "dolly_out")],
            "action": [("Medium shot of a giant red alarm button slammed down. Hard cut to: close-up of a tiny paper flag popping out", "crash_zoom")],
            "still": [("Wide shot of the dark studio, one monitor glowing with the week's headlines", "crane_up"),
                      ("Extreme close-up of KUROGANE's folded hands on the desk", "static")],
            "insert": [("Close-up of the week's scripts stacked in a wastebasket", "static")]}
    scenes.append({"act": "sign_off", "setting": "the anime newsroom after hours, one monitor still glowing",
                   "caption": "That was the week.", "narration": "", "shots": _fill_shots(sign, plan["sign_off"], {})})
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
THIS IS A WEEKLY EPISODE: this week's briefing covers {n} headlines, one segment each, in the scene plan above (exact, in order).
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
    rules = WEEKLY_RULES.format(n=len(shape["segments"]), markets=MARKETS_CAPTION, sign_off=SIGN_OFF)
    n = len(shape["scenes"])
    system = _system_prompt(shape, default_cast(),
                            (f"- Visual style direction from the user: {style}\n" if style else "") + rules)
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
            _check_density(sb, shape)
            return _finish(sb)
        except (ValueError, StoryboardError) as e:
            last_error = str(e)[:300]
            print(f"[anime-news] weekly writer reply rejected ({attempt + 1}/2): {last_error}", file=sys.stderr, flush=True)
    raise StoryboardError(f"weekly writer failed: {last_error}")
