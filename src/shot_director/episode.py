"""News -> anime episode: plan, render and stitch an episode from one headline; the self-running loop.

The plan is a deterministic template (no LLM, fully offline). Rules baked into every prompt:
SFW, no real people's likeness. Characters are original archetypes (the Anchor, invented officials); the
headline appears as on-screen text in the world (tickers, holograms), never as a depicted person.
Structure and prompt wording follow docs/psyopanime-formula.md (structure only, none of their IP).

Length: an episode of N minutes is S scenes x C clips of `clip_seconds` each. Clips inside a scene
are rendered in order and (FLUX 3 Video, live) each continues from the previous one; scenes render
in parallel. Scenes are concatenated in order into one mp4.
"""

import argparse
import hashlib
import json
import math
import os
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from . import news, presets, stitch, style, writer
from .router_client import CONTINUATION_ENGINE, ENGINES, VIDEO_MODEL, clamp_duration

ROOT = Path(__file__).resolve().parents[2]
MAX_HEADLINE = 140
MAX_CLIPS_PER_SCENE = 4      # continuity chain length inside one scene
DEFAULT_MINUTES = 1.0
DEFAULT_CLIP_SECONDS = 15    # flux-3-video takes 5-20 s per clip
DEFAULT_PARALLEL = 3         # scenes rendered at once
LEDGER_NAME = "episodes.json"
KNOWN_ENGINES = {e["id"] for e in ENGINES} | {VIDEO_MODEL, "ltx/ltx-2-5-pro"}

SERIES_TITLE = "THE HOURLY BRIEFING"  # our own series name (never theirs)
STYLE_LEAD = "2D cel anime frame, drawn animation, not a photograph, not a 3D render"
_SAFETY = ("Safe for work, no weapons, no gore. No real people, no real faces, no likeness of any "
           "public figure; every character is an original invented archetype")
# Fixed character block, reused in every prompt for consistency (original design).
_ANCHOR = ("the Anchor, an original stern middle-aged news anchor with a silver crew cut, round "
           "glasses and a gold-trimmed black blazer, drawn with heavy inked lines")
_DESK = (f"{_ANCHOR}, seated at a long news desk in front of a wall of glowing world-map monitors "
         "showing invented countries")

_LOGLINES = [
    "{title}: the Anchor reads \"{h}\" with total gravitas, and the cutaways take it literally.",
    "{title}: \"{h}\", briefed like a war-room emergency and illustrated with absurd props.",
    "{title}: one deadpan anchor, one headline, \"{h}\", and a world that overreacts on cue.",
]

# Scene templates follow the 1- and 5-minute beat sheets in docs/psyopanime-formula.md.
# Each beat: (beat, move, picture, caption). Captions are one short clause (<= 8 words).
_SCENES = {
    "single": {
        "setting": "an anime newsroom at night, teal shadows, burnt orange monitor glow",
        "narration": "Good evening. Tonight: {h}. Experts call it unprecedented. Experts call everything unprecedented. Back in one hour.",
        "beats": [
            ("cold_open", "crash_zoom", "Extreme close-up of the Anchor's eyes behind round glasses, the title '" + SERIES_TITLE + "' slams across the frame", SERIES_TITLE),
            ("thesis", "dolly_in", "Slow push-in on " + _DESK + "; the monitors all read \"{h}\" as he delivers it as a grave briefing", "Tonight: {short}"),
            ("vignette", "handheld", "Cutaway that literalizes the headline with an absurd oversized prop on a war-room table, invented officials staring at it in silence", "Officials are reviewing the situation."),
            ("punchline", "static", "Back at the desk: the Anchor stares into the lens, completely deadpan, one monitor glitching on \"{h}\"", "Back in one hour."),
        ],
    },
    "cold_open": {
        "setting": "an anime news studio in darkness lit by monitors, teal shadows, burnt orange highlights",
        "narration": "Good evening. This is " + SERIES_TITLE + ". Tonight: {h}.",
        "beats": [
            ("cold_open", "crash_zoom", "Extreme close-up of the Anchor's eyes behind round glasses, the title '" + SERIES_TITLE + "' slams across the frame", SERIES_TITLE),
            ("anchor_intro", "static", _DESK + "; every monitor reads \"{h}\"", "Tonight: {short}"),
            ("anchor_push", "dolly_in", "Slow push-in on the Anchor as he delivers the headline as a grave briefing line", "This is not a drill."),
            ("crt_inset", "whip_pan", "Whip pan to a wall of old CRT monitors replaying \"{h}\" with glitch and scan lines", "Footage is coming in."),
        ],
    },
    "segment_a": {
        "setting": "a vast painted sunset sky over an invented capital city, teal shadows, burnt orange clouds",
        "narration": "Sources confirm it: {h}. Nobody was ready. Least of all the people in charge.",
        "beats": [
            ("establishing", "crane_up", "Wide establishing view of the invented capital; giant screens on every tower read \"{h}\"", "Sources confirm it."),
            ("face", "crash_zoom", "Crash zoom onto the face of an invented stern official archetype, sweat on his brow, hatching on his face", "Nobody was ready."),
            ("cutaway", "handheld", "Invented staffers sprint down a corridor carrying armfuls of paperwork about \"{h}\"", "Least of all them."),
            ("absurd_prop", "static", "Overhead view of a war-room table covered in pizza boxes and one absurd prop that literalizes the headline", "The situation room is ready."),
        ],
    },
    "segment_b": {
        "setting": "a dusty western main street at high noon, painted sky, teal shadows, burnt orange dust",
        "narration": "Experts call it unprecedented. Experts call everything unprecedented. The standoff continues.",
        "beats": [
            ("standoff", "orbit", "Two invented rival bureaucrats face off in a western standoff, each holding a giant rubber stamp instead of a weapon", "A standoff begins."),
            ("tumbleweed", "static", "A tumbleweed rolls past a wanted poster that reads \"{h}\"", "Experts call it unprecedented."),
            ("clash", "whip_pan", "Whip pan as the rubber stamps slam down on the same form at once, white flash, speed lines", "They call everything unprecedented."),
            ("aftermath", "dolly_out", "Pull back: the whole town is buried in stamped paperwork", "The standoff continues."),
        ],
    },
    "montage": {
        "setting": "a rapid montage across the invented city at night, CRT glow, teal and burnt orange",
        "narration": "The markets reacted. The pigeons reacted. Everyone reacted.",
        "beats": [
            ("market", "handheld", "A stock ticker board in the invented city flips wildly, every line reads \"{h}\"", "The markets reacted."),
            ("pigeons", "whip_pan", "A flock of pigeons scatters off a news screen showing the headline, speed lines", "The pigeons reacted."),
            ("crowd", "crash_zoom", "Crash zoom on a crowd of invented citizens gasping in exaggerated anime shock", "Everyone reacted."),
            ("glitch", "handheld", "Glitching CRT monitors stacked in a shop window, all replaying \"{h}\"", "Stay tuned."),
        ],
    },
    "sign_off": {
        "setting": "the anime news studio, teal shadows, burnt orange monitor glow",
        "narration": "That was {h}. It will happen again. We will be here. Back in one hour.",
        "beats": [
            ("sign_off", "static", _DESK + "; he folds his hands, perfectly deadpan", "That was the news."),
            ("moral", "dolly_in", "Slow push-in as the Anchor delivers one dry closing line, a single monitor behind him still reading \"{h}\"", "It will happen again."),
            ("lights", "crane_up", "Crane up over the desk as the studio lights click off one by one", "We will be here."),
            ("sting", "dolly_out", "Extreme close-up of the Anchor's eyes pulling back, the card 'NEXT HOUR' appears", "Back in one hour."),
        ],
    },
}
_MIDDLE_SCENES = ["segment_a", "segment_b", "montage"]


def _clean(headline: str) -> str:
    h = " ".join((headline or "").split()).replace('"', "'")
    return h[:MAX_HEADLINE].rstrip() + ("..." if len(h) > MAX_HEADLINE else "")


def _short(h: str, words: int = 6) -> str:
    w = h.split()
    return " ".join(w[:words]) + ("..." if len(w) > words else "")


def _pick(seed: str, options: list, salt: str):
    """Stable choice per headline (hashlib, not hash(), so it survives restarts)."""
    digest = hashlib.sha256(f"{salt}:{seed}".encode()).digest()
    return options[digest[0] % len(options)]


def layout(minutes: float, clip_seconds: int = DEFAULT_CLIP_SECONDS) -> list[int]:
    """Minutes -> clips per scene. Exactly ceil(minutes*60 / clip_seconds) clips in total: full scenes
    of MAX_CLIPS_PER_SCENE, the last scene takes the remainder (no padding, no extra paid clips)."""
    if minutes <= 0 or clip_seconds <= 0:
        raise ValueError("minutes and clip_seconds must be positive")
    total = max(1, math.ceil(minutes * 60 / clip_seconds))
    full, rest = divmod(total, MAX_CLIPS_PER_SCENE)
    return [MAX_CLIPS_PER_SCENE] * full + ([rest] if rest else [])


def _scene_kinds(n: int) -> list[str]:
    """1 scene = the 1-minute sheet; more = cold open, middle segments in rotation, sign-off."""
    if n == 1:
        return ["single"]
    return ["cold_open"] + [_MIDDLE_SCENES[i % len(_MIDDLE_SCENES)] for i in range(n - 2)] + ["sign_off"]


def _trim(beats: list, count: int) -> list:
    """Fit a 4-beat template to `count` clips, keeping the opening and the closing beat."""
    if count >= len(beats):
        return beats
    return beats[:1] if count == 1 else beats[:count - 1] + beats[-1:]


def plan_episode(headline: str, source: str = "", body: str = "anime",
                 minutes: float = DEFAULT_MINUTES, clip_seconds: int = DEFAULT_CLIP_SECONDS,
                 engine: str | None = None, use_writer: bool = False, media: Path | None = None) -> dict:
    """Headline -> {headline, source, series, logline, scene, minutes, clip_seconds (effective, after
    the engine clamps it), clip_seconds_requested, engine,
    scenes: [{index, kind, setting, scene, caption, narration,
              shots: [{beat, move, body, scene, caption}]}], shots: (flat list)}."""
    h = _clean(headline)
    if not h:
        raise ValueError("headline is empty")
    engine = engine or VIDEO_MODEL
    requested, effective = clip_seconds, clamp_duration(engine, clip_seconds)
    counts = layout(minutes, effective)
    fill = {"h": h, "short": _short(h)}
    scenes = []
    for i, (kind, per_scene) in enumerate(zip(_scene_kinds(len(counts)), counts)):
        tpl = _SCENES[kind]
        shots = [{"beat": beat, "move": move, "body": body, "caption": caption.format(**fill),
                  "scene": f"{STYLE_LEAD}. {tpl['setting'].capitalize()}. {picture.format(**fill)}. {_SAFETY}"}
                 for beat, move, picture, caption in _trim(tpl["beats"], per_scene)]
        scenes.append({"index": i + 1, "kind": kind, "setting": tpl["setting"],
                       "scene": shots[0]["scene"],  # the start frame shows the scene's first beat
                       "caption": shots[0]["caption"], "narration": tpl["narration"].format(**fill),
                       "shots": shots})
    for sc in scenes:
        sc["anchor"] = sc["kind"] in _ANCHOR_SCENES
    plan = {"headline": h, "source": source.strip(), "series": SERIES_TITLE, "script": "template",
            "logline": _pick(h, _LOGLINES, "logline").format(h=h, title=SERIES_TITLE),
            "scene": scenes[0]["scene"], "minutes": minutes, "engine": engine,
            "clip_seconds": effective, "clip_seconds_requested": requested,
            "scenes": scenes, "shots": [s for sc in scenes for s in sc["shots"]]}
    if use_writer:
        _merge_script(plan, writer.write_script(h, plan["source"], minutes, len(counts), counts,
                                                SERIES_TITLE, media_dir=media), body)
    return plan


_ANCHOR_SCENES = ("single", "cold_open", "sign_off")          # template scenes set at the anchor desk
_ANCHOR_BEATS = ("cold_open", "anchor_desk", "sign_off", "thesis")  # writer beats set at the anchor desk


def _merge_script(plan: dict, script: dict | None, body: str) -> None:
    """Overlay the Router writer's script (captions, narration, moves, prompts) on the template plan.
    None (mock mode, no key, bad reply) keeps the template."""
    if not script:
        print("[shot-director] script: template (writer unavailable)", file=sys.stderr, flush=True)
        return
    prompts = iter(writer.script_to_prompts(script, body))
    for sc, ws in zip(plan["scenes"], script["scenes"]):
        sc.update(caption=ws["caption"], narration=ws["narration"], setting=ws["setting"],
                  beat=ws["beat"], anchor=sc["kind"] in _ANCHOR_SCENES or ws["beat"] in _ANCHOR_BEATS)
        for shot, w in zip(sc["shots"], ws["shots"]):
            shot.update(move=w["move"], caption=w["caption"], visual=w["visual"], scene=next(prompts))
        sc["scene"] = sc["shots"][0]["scene"]  # start frame = first shot
    first = plan["scenes"][0]["shots"][0]
    first["caption"] = script.get("cold_open_caption") or first["caption"]
    if script.get("sign_off"):
        plan["scenes"][-1]["narration"] = f"{plan['scenes'][-1]['narration']} {script['sign_off']}".strip()
    plan.update(logline=script["logline"], scene=plan["scenes"][0]["scene"],
                shots=[s for sc in plan["scenes"] for s in sc["shots"]],
                script="writer" + (f":{script['model']}" if script.get("model") else ""))


# -- rendering ---------------------------------------------------------------------------------

def _render_scene(router, media: Path, sc: dict, body: str, engine: str, clip_seconds: int,
                  styled: bool = False) -> dict:
    """One start frame, then the scene's clips in order; FLUX 3 Video (live) continues clip to clip.
    Fail-soft: an error stops this scene only; the clips rendered so far are kept and `error` is set."""
    out = {"index": sc["index"], "kind": sc["kind"], "setting": sc["setting"], "caption": sc["caption"],
           "narration": sc["narration"], "frame": None, "shots": [], "error": None}
    try:
        frame_name = f"frame-{uuid.uuid4().hex[:8]}.png"
        frame_prompt = f"{sc['scene']}. {presets.CAMERA_BODIES[body]['fragment']}."
        if styled and sc.get("anchor"):  # the sheet's Anchor; crowd scenes stay plain (it clones his face)
            style.generate_styled_frame(router, frame_prompt, media / frame_name, media)
        else:
            router.generate_frame(frame_prompt, body, media / frame_name)
        out["frame"] = {"frame": frame_name, "url": f"/media/{frame_name}"}
        previous = None
        for s in sc["shots"]:
            prompt = presets.compile_prompt(body, s["move"], s["scene"])
            name = f"shot-{uuid.uuid4().hex[:8]}.mp4"
            extra = {}
            if router.mode == "live" and engine == CONTINUATION_ENGINE and previous:
                extra["previous_clip"] = media / previous
            router.generate_clip(prompt, body, s["move"], media / name, frame=media / frame_name,
                                 model=engine, duration=clip_seconds, **extra)
            out["shots"].append({"id": name, "url": f"/media/{name}", "beat": s["beat"],
                                 "caption": s["caption"], "body": body, "move": s["move"],
                                 "prompt": prompt, "mode": router.mode, "engine": engine,
                                 "continued": "previous_clip" in extra,
                                 "continued_from": previous if extra else None})
            previous = name
    except Exception as e:
        out["error"] = f"{type(e).__name__}: {e}"[:400]
    print(f"[shot-director] scene {sc['index']} ({sc['kind']}): {len(out['shots'])}/{len(sc['shots'])} clips"
          + (f", FAILED {out['error']}" if out["error"] else ", done"), file=sys.stderr, flush=True)
    return out


def _sentence(text: str) -> str:
    text = text.strip()
    return text if text[-1:] in ".!?" else f"{text}."


def _cut(result: dict, media: Path, epid: str, scenes: list[dict]) -> tuple[Path, dict]:
    media = Path(media).resolve()  # absolute, so edit.assemble never re-joins a relative base onto media-prefixed paths
    """Build the edit plan (captions, per-scene narration, music bed, whoosh + impact) and assemble the cut."""
    from . import audio, edit
    seconds = result["minutes"] * 60 + 15
    plan = {"title": SERIES_TITLE, "logline": result["logline"], "fps": 24, "size": [1280, 720],
            "scenes": [], "narration": [],
            "music": str(p) if (p := audio.music_bed(seconds, media / "music-bed.mp3")) else None,
            "sfx": []}
    whoosh = audio.sfx("fast cinematic whoosh transition", media / "sfx-whoosh.mp3", 1.0)
    impact = audio.sfx("deep punchy anime impact hit", media / "sfx-impact.mp3", 1.0)
    if whoosh:
        plan["sfx"].append({"path": str(whoosh), "at": None, "lead": 0.25, "gain": 0.7})
    if impact:
        plan["sfx"].append({"path": str(impact), "at": None, "gain": 0.8})
    for sc in scenes:
        if not sc["shots"]:
            continue
        narr = audio.narrate(sc["narration"], media / f"{epid}-narration-{sc['index']}.mp3")
        plan["scenes"].append({
            "beat": sc["kind"], "action": sc["kind"] in ("segment_b", "montage"),
            "caption": " ".join(_sentence(s["caption"]) for s in sc["shots"]),
            "clips": [str(media / s["id"]) for s in sc["shots"]],
            "stills": [str(media / sc["frame"]["frame"])] if sc["frame"] else [],
            "narration": sc["narration"], "narration_audio": str(narr) if narr else None})
    out = media / f"{epid}.mp4"
    edit.assemble(plan, out, audio=True, base=media)
    report = json.loads(Path(str(out) + ".cuts.json").read_text())
    return out, {k: report.get(k) for k in ("duration", "shots", "cuts_per_minute", "avg_shot_s", "audio")}


def render_episode(plan: dict, router, media: Path, body: str = "anime", engine: str | None = None,
                   parallel: int = DEFAULT_PARALLEL, cut: bool = True, style_sheet: bool = True) -> dict:
    """Render every scene (scenes in parallel, a failed scene keeps what it rendered), stitch the raw
    sequence, then (cut=True) assemble the anime cut with captions, narration, SFX and music.
    Uses its own shot list, not the UI queue."""
    media = Path(media).resolve()
    engine = engine or VIDEO_MODEL
    if engine not in KNOWN_ENGINES:
        raise ValueError(f"unknown engine: {engine}")
    media.mkdir(parents=True, exist_ok=True)
    styled = bool(style_sheet and router.mode == "live" and style.ensure_style_sheet(media, router))
    with ThreadPoolExecutor(max_workers=max(1, parallel)) as pool:
        scenes = list(pool.map(lambda sc: _render_scene(router, media, sc, body, engine,
                                                        plan["clip_seconds"], styled), plan["scenes"]))
    shots = [s for sc in scenes for s in sc["shots"]]
    if not shots:
        raise RuntimeError("every scene failed: " + "; ".join(f"scene {sc['index']}: {sc['error']}" for sc in scenes))
    epid = f"episode-{uuid.uuid4().hex[:8]}"
    raw = f"{epid}.sequence_raw.mp4"
    stitch.stitch([media / s["id"] for s in shots], media / raw)
    result = {**plan, "scenes": scenes, "shots": shots,
              "frame": next((sc["frame"] for sc in scenes if sc["frame"]), None),
              "errors": [f"scene {sc['index']}: {sc['error']}" for sc in scenes if sc["error"]],
              "sequence_raw": {"url": f"/media/{raw}", "file": raw},
              "sequence": {"url": f"/media/{raw}", "file": raw}, "cut": None}
    if cut:
        try:
            final, report = _cut(result, media, epid, scenes)
            result["sequence"] = {"url": f"/media/{final.name}", "file": final.name}
            result["cut"] = report
        except Exception as e:  # the raw stitch is still a watchable episode
            result["errors"].append(f"cut: {type(e).__name__}: {e}"[:400])
            print(f"[shot-director] cut failed, keeping the raw stitch: {e}", file=sys.stderr, flush=True)
    return result


# -- ledger + one production run --------------------------------------------------------------

_LEDGER_LOCK = threading.Lock()


def read_ledger(media: Path) -> list[dict]:
    try:
        return json.loads((media / LEDGER_NAME).read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return []


_PICK_LOCK = threading.Lock()  # one pick + reservation at a time (auto thread vs API vs loop)


def _write_ledger(media: Path, items: list[dict]) -> None:
    tmp = media / f".{LEDGER_NAME}.tmp"
    tmp.write_text(json.dumps(items, indent=1))
    tmp.replace(media / LEDGER_NAME)


def _append_ledger(media: Path, entry: dict) -> None:
    with _LEDGER_LOCK:
        _write_ledger(media, read_ledger(media) + [entry])


def _update_ledger(media: Path, entry_id: str, fields: dict) -> None:
    with _LEDGER_LOCK:
        _write_ledger(media, [{**e, **fields} if e.get("id") == entry_id else e for e in read_ledger(media)])


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _reserve(media: Path, headline: str, source: str) -> tuple[str, str, str, str]:
    """Under the pick lock: choose the story and write a 'rendering' ledger entry before any paid call.
    Auto-pick skips every headline already in the ledger (rendering, done or failed); an explicit
    headline is refused only while another run is rendering it."""
    with _PICK_LOCK:
        ledger = read_ledger(media)
        link = ""
        if not headline:
            story = news.top(exclude={e["headline_raw"] for e in ledger if e.get("headline_raw")})
            if not story:
                raise LookupError("no acceptable new headline (feeds unreachable, all rendered, or only "
                                  "stories about deaths/violence): pass a headline")
            headline, source, link = story["title"], story.get("source", ""), story.get("link", "")
        elif any(e.get("headline_raw") == headline and e.get("status") == "rendering" for e in ledger):
            raise LookupError(f"already rendering: {headline}")
        entry_id = uuid.uuid4().hex[:12]
        _append_ledger(media, {"id": entry_id, "status": "rendering", "headline_raw": headline,
                               "source": source, "link": link, "created": _now()})
    return entry_id, headline, source, link


def produce(router, media: Path, headline: str | None = None, source: str = "",
            engine: str | None = None, body: str = "anime", minutes: float = DEFAULT_MINUTES,
            clip_seconds: int = DEFAULT_CLIP_SECONDS, parallel: int = DEFAULT_PARALLEL,
            render: bool = True, cut: bool = True, use_writer: bool = True,
            style_sheet: bool = True) -> dict:
    """Pick the story (top one not yet in the ledger unless `headline` is given), plan, render, record.
    Raises LookupError (no story), ValueError (bad input), RuntimeError/RouterError (render)."""
    media = Path(media).resolve()
    headline = (headline or "").strip()
    presets.compile_prompt(body, "static", "")  # validates the body before anything is spent
    if engine and engine not in KNOWN_ENGINES:
        raise ValueError(f"unknown engine: {engine}")
    if minutes <= 0 or clip_seconds <= 0:
        raise ValueError("minutes and clip_seconds must be positive")
    if not render:
        if not headline:
            story = news.top(exclude={e["headline_raw"] for e in read_ledger(media) if e.get("headline_raw")})
            if not story:
                raise LookupError("no acceptable new headline: pass a headline")
            headline, source = story["title"], story.get("source", "")
        return plan_episode(headline, source, body=body, minutes=minutes, clip_seconds=clip_seconds,
                            engine=engine, use_writer=use_writer, media=media)
    media.mkdir(parents=True, exist_ok=True)
    entry_id, headline, source, link = _reserve(media, headline, source)
    started = time.time()
    try:
        plan = plan_episode(headline, source, body=body, minutes=minutes, clip_seconds=clip_seconds,
                            engine=engine, use_writer=use_writer, media=media)
        result = render_episode(plan, router, media, body=body, engine=engine, parallel=parallel, cut=cut,
                                style_sheet=style_sheet)
    except BaseException as e:
        _update_ledger(media, entry_id, {"status": "failed", "error": f"{type(e).__name__}: {e}"[:300],
                                         "finished": _now()})
        raise
    _update_ledger(media, entry_id, {
        "status": "done", "headline": result["headline"],
        "file": result["sequence"]["file"], "path": str(media / result["sequence"]["file"]),
        "url": result["sequence"]["url"], "raw_file": result["sequence_raw"]["file"],
        "raw_path": str(media / result["sequence_raw"]["file"]), "cut": result["cut"],
        "errors": result["errors"], "minutes": minutes, "scenes": len(result["scenes"]),
        "clips": len(result["shots"]), "clip_seconds": result["clip_seconds"],
        "clip_seconds_requested": result["clip_seconds_requested"],
        "engine": result["shots"][0]["engine"], "mode": router.mode,
        "seconds_to_render": round(time.time() - started, 1), "finished": _now()})
    return result


def parse_every(text: str) -> float:
    """'60m', '1h', '90s' or a bare number of minutes -> seconds."""
    t = str(text).strip().lower()
    units = {"s": 1, "m": 60, "h": 3600}
    if t and t[-1] in units:
        return float(t[:-1]) * units[t[-1]]
    return float(t) * 60


def run_loop(router, media: Path, every_seconds: float, stop: threading.Event | None = None,
             log=print, **kwargs) -> None:
    """Self-running channel: every tick, render the current top story not already in the ledger."""
    stop = stop or threading.Event()
    while not stop.is_set():
        started = time.time()
        try:
            r = produce(router, media, **kwargs)
            log(json.dumps({"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), "ok": True,
                            "headline": r["headline"], "episode": str(media / r["sequence"]["file"]),
                            "scenes": len(r["scenes"]), "clips": len(r["shots"]), "mode": router.mode}))
        except Exception as e:  # one bad tick must not stop the channel
            log(json.dumps({"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), "ok": False,
                            "error": f"{type(e).__name__}: {e}"}))
        stop.wait(max(0.0, every_seconds - (time.time() - started)))


# -- CLI -----------------------------------------------------------------------------------------

def _load_repo_env_key() -> None:
    """Put COMFY_API_KEY from the repo .env into the environment if it is not already set. Never printed."""
    if os.environ.get("COMFY_API_KEY"):
        return
    env = ROOT / ".env"
    if not env.exists():
        return
    for line in env.read_text().splitlines():
        line = line.strip().removeprefix("export ").strip()
        if line.startswith("COMFY_API_KEY="):
            os.environ["COMFY_API_KEY"] = line.split("=", 1)[1].strip().strip("'\"")
            return


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Pick the top trending story, plan an anime episode, render it and stitch it.")
    ap.add_argument("--headline", help="use this headline instead of the top trending story")
    ap.add_argument("--source", default="", help="source name shown with --headline")
    ap.add_argument("--engine", default=None, help="video engine id (default: bfl/flux-3-video)")
    ap.add_argument("--body", default="anime", help="camera body preset (default: anime)")
    ap.add_argument("--minutes", type=float, default=DEFAULT_MINUTES, help="episode length (default 1)")
    ap.add_argument("--clip-seconds", type=int, default=DEFAULT_CLIP_SECONDS,
                    help="seconds per clip (default 15; FLUX 3 Video 5-20)")
    ap.add_argument("--parallel", type=int, default=DEFAULT_PARALLEL, help="scenes rendered at once (default 3)")
    ap.add_argument("--mock", action="store_true", help="offline placeholder render, no Router calls")
    ap.add_argument("--out", default=str(ROOT / "media"), help="media dir for frames, clips, episodes and the ledger")
    ap.add_argument("--no-cut", action="store_true", help="skip the anime cut: the episode is the plain stitch")
    ap.add_argument("--no-writer", action="store_true", help="template script only, no Router script writer")
    ap.add_argument("--no-style-sheet", action="store_true", help="plain start frames, no series style sheet")
    ap.add_argument("--loop", action="store_true", help="keep running: one new episode every --every")
    ap.add_argument("--every", default="60m", help="loop interval, e.g. 60m, 1h, 30s (default 60m)")
    args = ap.parse_args()

    if args.mock:
        os.environ["SHOT_DIRECTOR_MOCK"] = "1"
    else:
        _load_repo_env_key()
    from .router_client import make_router
    media = Path(args.out).resolve()
    media.mkdir(parents=True, exist_ok=True)
    router = make_router(media)
    opts = dict(engine=args.engine, body=args.body, minutes=args.minutes,
                clip_seconds=args.clip_seconds, parallel=args.parallel, cut=not args.no_cut,
                use_writer=not args.no_writer, style_sheet=not args.no_style_sheet)
    if args.loop:
        print(f"[shot-director] {router.mode} channel: one episode every {args.every} into {media}",
              file=sys.stderr)
        try:
            run_loop(router, media, parse_every(args.every), log=lambda line: print(line, flush=True), **opts)
        except KeyboardInterrupt:
            pass
        return
    try:
        result = produce(router, media, headline=args.headline, source=args.source, **opts)
    except Exception as e:
        print(f"episode failed: {type(e).__name__}: {e}", file=sys.stderr)
        sys.exit(1)
    path = media / result["sequence"]["file"]
    print(path)
    print(json.dumps({"headline": result["headline"], "source": result["source"],
                      "logline": result["logline"], "mode": router.mode,
                      "engine": result["shots"][0]["engine"], "scenes": len(result["scenes"]),
                      "clips": len(result["shots"]), "moves": [s["move"] for s in result["shots"]],
                      "script": result["script"], "cut": result["cut"], "errors": result["errors"],
                      "episode": str(path),
                      "raw": str(media / result["sequence_raw"]["file"])}))


if __name__ == "__main__":
    main()
