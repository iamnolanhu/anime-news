"""Episode v2: a 60-second mini-story with a recurring cast that actually talks (docs/psyopanime-pipeline-v2.md).

headline -> writer v2 (acts of talk/action shots with spoken lines, writer.write_script_v2)
         -> one start frame per shot (FLUX 3; talk shots carry the style sheet + the speaker's character sheet)
         -> one Seedance 2.5 clip per shot (talk: the quoted line is spoken with moving lips, in the speaker's
            voice via an ElevenLabs reference sample; action: two beats joined by a hard cut)
         -> the anime cut (edit.assemble): talk shots keep their own sound, action shots are sliced, music + SFX.

Every shot is generated once and shown once (no v2v chains, no re-slicing of one clip into a whole scene).
The ledger, story pick and CLI conventions are episode.py's.
"""

import argparse
import base64
import json
import math
import os
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import audio, episode, style, writer
from .router_client import IMAGE_MODEL, SEEDANCE_MODEL

SERIES = episode.SERIES_TITLE
# Fixed shot slots for one minute (writer v2 fills them): 4 talk shots, 6 action shots.
SLOTS_1MIN = [("cold_open", [("action", "none"), ("action", "none")]),
              ("thesis", [("talk", "anchor"), ("action", "none")]),
              ("escalation", [("talk", "operative"), ("action", "none"), ("action", "none")]),
              ("vignette", [("action", "none"), ("talk", "analyst")]),
              ("button", [("talk", "anchor")])]
GENRES = ["samurai duel at dawn", "giant-robot launch sequence", "kaiju attack on a harbor city",
          "high-stakes heist", "sports final in the last second", "space-opera fleet battle", "courtroom drama",
          "street-racing showdown"]
ACTION_SECONDS = 5
SHOT_PARALLEL = 5
# ElevenLabs premade voices used only as timbre references for Seedance (the clip speaks the line itself).
VOICES = {"anchor": os.environ.get("SHOT_DIRECTOR_VOICE_ANCHOR", "onwK4e9ZLuTAKqWW03F9"),       # deep news presenter
          "operative": os.environ.get("SHOT_DIRECTOR_VOICE_OPERATIVE", "XB0fDUnXU5powFXDhCwa"),  # cool, low
          "analyst": os.environ.get("SHOT_DIRECTOR_VOICE_ANALYST", "N2lVS1w4EtoT3dr4eOWO")}      # hoarse, intense
VOICE_SAMPLE = {"anchor": "Good evening. This is Kurogane. The situation is grave, and it is getting worse.",
                "operative": "Vex here. I'm on location. Nobody saw me come in, and nobody will see me leave.",
                "analyst": "It's all connected. The pigeons, the paperwork, the pizza. I have charts. So many charts."}
STYLE = ("Flat cel-shaded 2D anime, late-1980s OVA hand-inked heavy linework, animated on twos, smear frames, "
         "film grain, VHS softness, teal shadows and burnt-orange highlights")
MOTION_TAIL = "No frozen figures, no 3D, no CGI, no photoreal textures, no slow motion, no on-screen text except what is described."
SAFETY = ("Safe for work. Every character is an original invented anime character, no likeness of any real "
          "person, no real logos or flags")


def talk_seconds(line: str) -> int:
    """Clip length for a spoken line: ~2.6 words/s plus a beat before and after, 4-8 s."""
    return max(4, min(8, math.ceil(len(line.split()) / 2.6 + 1.6)))


# -- cast assets (made once per series, cached in the media dir) ------------------------------------

def voice_sample(media: Path, speaker: str) -> Path | None:
    return audio.narrate(VOICE_SAMPLE[speaker], media / f"voice-{speaker}.mp3", voice=VOICES[speaker])


def character_sheet(router, media: Path, speaker: str) -> Path | None:
    """The anchor's sheet is the series style sheet; the others get one FLUX still each, styled by it."""
    if speaker == "anchor":
        return style.ensure_style_sheet(media, router)
    path = media / f"cast-{speaker}.png"
    if path.exists() and path.stat().st_size:
        return path
    if router.mode != "live":
        return None
    c = writer.CAST[speaker]
    prompt = (f"{writer.STYLE_LEAD}. Character model sheet still: {c['name']}, {c['role']}: {c['look']}. "
              f"Medium shot, facing the camera, neutral dark studio background, full face clearly visible. {writer.SAFETY_TAIL}.")
    try:
        style.generate_styled_frame(router, prompt, path, media)
        return path
    except Exception as e:
        print(f"[showrunner] cast sheet {speaker} failed: {e}", file=sys.stderr, flush=True)
        return None


def _frame(router, media: Path, prompt: str, refs: list[Path], out: Path) -> Path:
    """FLUX 3 start frame; `refs` (style / character sheets) go in as image references when live."""
    refs = [r for r in refs if r and r.exists()]
    if router.mode != "live" or not refs:
        return router.generate_frame(prompt, "anime", out)
    body = {"prompt": style.REFERENCE_LEAD + prompt, "aspect_ratio": style.ASPECT_RATIO,
            "resolution": style.RESOLUTION, "grounding": False,
            "images": [base64.b64encode(r.read_bytes()).decode() for r in refs]}
    result = router._run_model(IMAGE_MODEL, body)
    return router._download(style._image_url(router, result), out)


# -- plan ---------------------------------------------------------------------------------------------

def plan_v2(script: dict) -> list[dict]:
    """Flatten the script into shots with their frame prompt, Seedance prompt and duration."""
    shots = []
    for ai, act in enumerate(script["acts"]):
        setting = act["setting"].rstrip(".")
        for s in act["shots"]:
            visual = s["visual"].rstrip(".")
            if s["kind"] == "talk":
                c = writer.CAST[s["speaker"]]
                who = f"{c['name']}, {c['look']}"
                frame_prompt = f"{visual}. {who}. Setting: {setting}. Mouth visible, facing camera. {writer.SAFETY_TAIL}."
                video_prompt = (f"{STYLE}. {visual}. Setting: {setting}. {c['name']} looks into the camera and speaks "
                                f"with a {'grave, clipped' if s['speaker'] == 'anchor' else 'tense, deadpan'} voice, exactly: "
                                f"\"{s['line']}\" The mouth moves clearly with every word; small head movement, one blink. "
                                f"Camera: {s['move'].replace('_', ' ')}. {MOTION_TAIL} {SAFETY}.")
                seconds = talk_seconds(s["line"])
            else:
                frame_prompt = f"{visual}. Setting: {setting}. {writer.SAFETY_TAIL}."
                second = f" Hard cut to: {s['second_angle'].rstrip('.')}." if s.get("second_angle") else ""
                video_prompt = (f"{STYLE}, speed lines, impact frames. {visual}.{second} Setting: {setting}. "
                                f"Camera: {s['move'].replace('_', ' ')}. Strong, readable motion. {MOTION_TAIL} {SAFETY}. "
                                f"Sound: effects and ambience only, no speech, no music.")
                seconds = ACTION_SECONDS
            shots.append({**s, "act": act["act"], "act_index": ai, "setting": setting,
                          "frame_prompt": f"{writer.STYLE_LEAD}. {frame_prompt}", "prompt": video_prompt,
                          "seconds": seconds})
    return shots


# -- render -------------------------------------------------------------------------------------------

def _render_shot(router, media: Path, shot: dict, refs: dict, voices: dict) -> dict:
    out = {**shot, "frame": None, "id": None, "error": None, "mode": router.mode, "engine": SEEDANCE_MODEL}
    try:
        frame = media / f"frame-{uuid.uuid4().hex[:8]}.png"
        talk = shot["kind"] == "talk"
        sheet_refs = [refs.get("style")] + ([refs.get(shot["speaker"])] if talk and shot["speaker"] != "anchor" else [])
        _frame(router, media, shot["frame_prompt"], sheet_refs if talk else [], frame)
        out["frame"] = frame.name
        clip = media / f"shot-{uuid.uuid4().hex[:8]}.mp4"
        router.generate_clip(shot["prompt"], "anime", shot["move"], clip, frame=frame, model=SEEDANCE_MODEL,
                             duration=shot["seconds"], voice_ref=voices.get(shot["speaker"]) if talk else None)
        out["id"] = clip.name
    except Exception as e:
        out["error"] = f"{type(e).__name__}: {e}"[:400]
    print(f"[showrunner] {shot['act']} {shot['kind']}: " + (f"FAILED {out['error']}" if out["error"] else out["id"]),
          file=sys.stderr, flush=True)
    return out


def render(script: dict, router, media: Path, parallel: int = SHOT_PARALLEL) -> list[dict]:
    shots = plan_v2(script)
    speakers = sorted({s["speaker"] for s in shots if s["kind"] == "talk"})
    refs = {"style": style.ensure_style_sheet(media, router) if router.mode == "live" else None}
    voices = {}
    if router.mode == "live":
        for sp in speakers:
            refs[sp] = character_sheet(router, media, sp)
            voices[sp] = voice_sample(media, sp)
    with ThreadPoolExecutor(max_workers=max(1, parallel)) as pool:
        return list(pool.map(lambda s: _render_shot(router, media, s, refs, voices), shots))


def save_shots(media: Path, epid: str, script: dict, shots: list[dict]) -> Path:
    path = media / f"{epid}.shots.json"
    path.write_text(json.dumps({"script": script, "shots": shots}, indent=1))
    return path


def retry_failed(router, media: Path, shots_file: Path, parallel: int = SHOT_PARALLEL) -> Path:
    """Re-render only the shots that failed (fresh frame + clip), then rewrite <epid>.plan.json for --recut."""
    doc = json.loads(shots_file.read_text())
    script, shots = doc["script"], doc["shots"]
    todo = [i for i, s in enumerate(shots) if not s.get("id")]
    refs = {"style": style.ensure_style_sheet(media, router)}
    voices = {}
    for sp in {shots[i]["speaker"] for i in todo if shots[i]["kind"] == "talk"}:
        refs[sp], voices[sp] = character_sheet(router, media, sp), voice_sample(media, sp)
    with ThreadPoolExecutor(max_workers=max(1, parallel)) as pool:
        redone = list(pool.map(lambda i: _render_shot(router, media, {k: shots[i][k] for k in shots[i]
                                                                      if k not in ("frame", "id", "error", "mode", "engine")},
                                                      refs, voices), todo))
    for i, r in zip(todo, redone):
        shots[i] = r
    epid = shots_file.name.removesuffix(".shots.json")
    save_shots(media, epid, script, shots)
    plan = media / f"{epid}.plan.json"
    plan.write_text(json.dumps(edit_plan(script, shots, media), indent=1))
    return plan


# -- the cut ------------------------------------------------------------------------------------------

def edit_plan(script: dict, shots: list[dict], media: Path) -> dict:
    """Scenes for edit.assemble: one scene per act, its talk and action shots in script order (talk takes keep
    their own sound; action clips are cut by their angles). Subtitles come from the spoken lines only, so the
    action shots' captions are not shown; the countdown is drawn when the script names one."""
    from . import music
    kit = music.music_kit(media) if hasattr(music, "music_kit") else {}
    scenes: list[dict] = []
    for s in shots:
        if not s["id"]:
            continue
        if not scenes or scenes[-1]["beat"] != s["act"]:
            scenes.append({"beat": s["act"], "shots": [], "clip_audio_gain": 0.35})
        shot = {"kind": s["kind"], "clip": str(media / s["id"]),
                "prompt": f"{s.get('visual', '')} {s.get('second_angle', '')}".strip()}
        if s["kind"] == "talk":
            shot.update(line=s["line"], speaker=s["speaker"])
        scenes[-1]["shots"].append(shot)
    plan = {"title": SERIES, "fps": 24, "size": [1280, 720], "scenes": scenes, "narration": [],
            "music": kit.get("bed"), "sfx": [],
            "countdown": {"label": script["countdown"], "scene_beat": "escalation"} if script.get("countdown") else None}
    if kit.get("whoosh"):
        plan["sfx"].append({"path": kit["whoosh"], "at": None, "lead": 0.25, "gain": 0.6})
    if kit.get("impact"):
        plan["sfx"].append({"path": kit["impact"], "at": None, "gain": 0.7})
    return plan


def produce(router, media: Path, headline: str | None = None, source: str = "", parallel: int = SHOT_PARALLEL,
            cut: bool = True, plan_only: bool = False, genre: str | None = None) -> dict:
    """Pick (or take) a headline, write, render, cut; recorded in the episode ledger like episode.produce."""
    from . import edit
    media = Path(media).resolve()
    media.mkdir(parents=True, exist_ok=True)
    previous = next((e for e in reversed(episode.read_ledger(media))
                     if e.get("status") == "done" and e.get("title_card")), None)
    if plan_only:
        script = writer.write_script_v2(headline, source, SLOTS_1MIN, SERIES, previous=previous, media_dir=media,
                                        genre=genre or episode._pick(headline, GENRES, "genre"))
        return {"script": script, "shots": plan_v2(script) if script else []}
    entry_id, headline, source, link = episode._reserve(media, (headline or "").strip(), source)
    started = time.time()
    try:
        script = writer.write_script_v2(headline, source, SLOTS_1MIN, SERIES, previous=previous, media_dir=media,
                                        genre=genre or episode._pick(headline, GENRES, "genre"))
        if not script:
            raise RuntimeError("writer v2 returned no script (mock mode or a bad reply)")
        shots = render(script, router, media, parallel)
        done = [s for s in shots if s["id"]]
        if not done:
            raise RuntimeError("every shot failed: " + "; ".join(s["error"] or "" for s in shots)[:300])
        epid = f"episode-{uuid.uuid4().hex[:8]}"
        save_shots(media, epid, script, shots)
        plan = edit_plan(script, shots, media)
        (media / f"{epid}.plan.json").write_text(json.dumps(plan, indent=1))
        out = media / f"{epid}.mp4"
        report = None
        if cut:
            edit.assemble(plan, out, audio=True, base=media)
            rep = json.loads(Path(str(out) + ".cuts.json").read_text())
            report = {k: rep.get(k) for k in ("duration", "shots", "cuts_per_minute", "avg_shot_s", "audio", "reuse")}
    except BaseException as e:
        episode._update_ledger(media, entry_id, {"status": "failed", "error": f"{type(e).__name__}: {e}"[:300],
                                                 "finished": episode._now()})
        raise
    errors = [f"{s['act']} {s['kind']}: {s['error']}" for s in shots if s["error"]]
    episode._update_ledger(media, entry_id, {
        "status": "done" if cut else "rendered", "plan": str(media / f"{epid}.plan.json"),
        "headline": script["headline"], "title_card": script["title_card"],
        "logline": script["logline"], "file": out.name, "path": str(out), "url": f"/media/{out.name}",
        "cut": report, "errors": errors, "minutes": 1, "clips": len(done), "engine": SEEDANCE_MODEL,
        "mode": router.mode, "pipeline": "v2", "seconds_to_render": round(time.time() - started, 1),
        "finished": episode._now()})
    return {"episode": str(out), "script": script, "shots": shots, "cut": report, "errors": errors}


def main() -> None:
    ap = argparse.ArgumentParser(description="Episode v2: writer v2 + cast + Seedance talking shots + anime cut.")
    ap.add_argument("--headline")
    ap.add_argument("--source", default="")
    ap.add_argument("--out", required=True, help="media dir (frames, clips, voices, cast sheets, ledger)")
    ap.add_argument("--parallel", type=int, default=SHOT_PARALLEL)
    ap.add_argument("--plan-only", action="store_true", help="write the script and print the shot plan; no render")
    ap.add_argument("--recut", help="re-run only the cut from an existing <episode>.plan.json")
    ap.add_argument("--genre", help="vignette genre (default: picked from the headline)")
    ap.add_argument("--retry", help="re-render the failed shots of an <episode>.shots.json and rewrite its plan")
    ap.add_argument("--no-cut", action="store_true", help="render the shots and write the plan; cut later with --recut")
    ap.add_argument("--mock", action="store_true")
    args = ap.parse_args()
    if args.mock:
        os.environ["SHOT_DIRECTOR_MOCK"] = "1"
    else:
        episode._load_repo_env_key()
    from .router_client import make_router
    media = Path(args.out).resolve()
    if args.recut:
        from . import edit
        plan = json.loads(Path(args.recut).read_text())
        out = Path(args.recut.replace(".plan.json", ".mp4"))
        edit.assemble(plan, out, audio=True, base=media)
        # the --no-cut run left a "rendered" ledger entry for this plan: the episode now exists
        rep = json.loads(Path(str(out) + ".cuts.json").read_text())
        with episode._LEDGER_LOCK:
            episode._write_ledger(media, [{**e, "status": "done", "file": out.name, "path": str(out),
                                           "url": f"/media/{out.name}", "finished": episode._now(),
                                           "cut": {k: rep.get(k) for k in ("duration", "shots", "cuts_per_minute",
                                                                            "avg_shot_s", "audio", "reuse")}}
                                          if e.get("plan") == str(Path(args.recut).resolve()) else e
                                          for e in episode.read_ledger(media)])
        print(out)
        return
    router = make_router(media)
    if args.retry:
        print(retry_failed(router, media, Path(args.retry).resolve(), args.parallel))
        return
    r = produce(router, media, args.headline, args.source, args.parallel, cut=not args.no_cut, plan_only=args.plan_only,
                genre=args.genre)
    print(json.dumps({k: r[k] for k in r if k != "shots"} | {"shots": [
        {k: s.get(k) for k in ("act", "kind", "speaker", "line", "seconds", "id", "error")} for s in r["shots"]]},
        indent=1)[:6000])


if __name__ == "__main__":
    main()
