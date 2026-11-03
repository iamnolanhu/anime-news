"""Storyboard -> episode: one start frame per shot, Seedance talking shots, animated action shots, stills as
cut-ins, then the no-reuse anime cut (edit.assemble), optional 9:16 export, and a ledger entry.

No shot cap and no reuse: every storyboard shot is generated once, and the cut shows each source moment at
most once (edit.assemble's ledger; the cut report's `reuse` block recounts it from the final timeline).
Files live flat in the media dir, prefixed by the episode id: <epid>.storyboard.json, <epid>.shots.json
(what --retry reads), <epid>.plan.json (the edit plan), <epid>.mp4, <epid>.9x16.mp4.
"""

import hashlib
import json
import os
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import audio, episode, showrunner, storyboard, style, writer
from .router_client import CREDITS, SEEDANCE_MODEL

QUALITIES = ("480p", "720p")
DEFAULT_QUALITY = "480p"
SHOT_PARALLEL = int(os.environ.get("ANIME_NEWS_PARALLEL", "5"))
ACTION_ENGINE = os.environ.get("ANIME_NEWS_ACTION_ENGINE", SEEDANCE_MODEL)
NARRATOR_VOICE = os.environ.get("ANIME_NEWS_NARRATOR_VOICE", audio.DEFAULT_VOICE)

# Spend estimate in USD, before anything is spent. Seedance is measured (Router bills $15.30 per 1M tokens,
# tokens = s * w * h * fps / 1024 -> about $0.149/s at 480p, 2.25x at 720p). The rest are rough guesses
# (env-overridable) until the X-Comfy-Credits-Used totals of real runs replace them.
PRICE = {
    "seedance_s": {"480p": 0.149, "720p": 0.335},
    "video_s": float(os.environ.get("ANIME_NEWS_PRICE_VIDEO_S", "0.15")),     # non-Seedance action engine
    "image": float(os.environ.get("ANIME_NEWS_PRICE_IMAGE", "0.06")),         # one FLUX 3 image
    "tts": float(os.environ.get("ANIME_NEWS_PRICE_TTS", "0.02")),             # one ElevenLabs line
    "sfx": float(os.environ.get("ANIME_NEWS_PRICE_SFX", "0.02")),
    "writer": float(os.environ.get("ANIME_NEWS_PRICE_WRITER", "0.10")),
}


class CostCapExceeded(RuntimeError):
    pass


class Cancelled(RuntimeError):
    pass


def _h(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:8]


def _speakers(sb: dict) -> list[str]:
    return sorted({s["cast"][0] for sc in sb["scenes"] for s in sc["shots"] if s["kind"] == "talk" and s["cast"]})


def _cast(sb: dict) -> dict:
    return {c["id"]: c for c in sb["cast"]}


def sheet_path(media: Path, member: dict) -> Path:
    return media / f"cast-{member['id']}-{_h(member['look'])}.png"


def voice_path(media: Path, member: dict) -> Path:
    return media / f"voice-{member['id']}-{_h(_voice_id(member))}.mp3"


def _voice_id(member: dict) -> str:
    v = (member.get("voice") or "").strip()
    if len(v) >= 16 and v.isalnum():
        return v
    return storyboard.DEFAULT_VOICES.get(member["id"]) or list(storyboard.DEFAULT_VOICES.values())[
        int(_h(member["id"]), 16) % 3]


def estimate(sb: dict, quality: str = DEFAULT_QUALITY, media: Path | None = None) -> dict:
    """USD estimate of a live render of `sb` (cached sheets, voices and music kit count as free)."""
    media = Path(media) if media else None
    shots = [s for sc in sb["scenes"] for s in sc["shots"]]
    cast = _cast(sb)
    talk_s = sum(s["seconds"] for s in shots if s["kind"] == "talk")
    action_s = sum(s["seconds"] for s in shots if s["kind"] == "action")
    per_s = PRICE["seedance_s"].get(quality, PRICE["seedance_s"]["480p"])
    action_rate = per_s if ACTION_ENGINE == SEEDANCE_MODEL else PRICE["video_s"]
    images = len(shots)
    tts = sum(1 for sc in sb["scenes"] if sc.get("narration"))

    def cached(path: Path | None) -> bool:
        return path is not None and path.exists() and path.stat().st_size > 0

    if not cached(style.sheet_path(media) if media else None):
        images += 1
    for sp in _speakers(sb):
        if sp not in cast:
            continue
        if sp != "anchor" and not cached(sheet_path(media, cast[sp]) if media else None):
            images += 1
        if not cached(voice_path(media, cast[sp]) if media else None):
            tts += 1
    sfx = 0 if media is not None and (media / "music" / "impact.mp3").exists() else 5
    parts = {"talk_clips": round(talk_s * per_s, 2), "action_clips": round(action_s * action_rate, 2),
             "images": round(images * PRICE["image"], 2), "voice": round(tts * PRICE["tts"], 2),
             "sfx": round(sfx * PRICE["sfx"], 2)}
    return {"usd": round(sum(parts.values()), 2), "parts": parts, "quality": quality,
            "talk_seconds": talk_s, "action_seconds": action_s, "images": images}


# -- per-shot prompts -----------------------------------------------------------------------------

def shot_prompts(sb: dict, sc: dict, s: dict) -> tuple[str, str]:
    """(frame prompt, video prompt) for one storyboard shot."""
    cast = _cast(sb)
    lead = writer.STYLE_LEAD + (f". {sb['style']}" if sb.get("style") else "")
    setting = (sc.get("setting") or "").rstrip(".")
    visual = s["prompt"].rstrip(".")
    who = "; ".join(f"{cast[c]['name']}: {cast[c]['look']}" for c in s["cast"] if c in cast)
    who_part = f" {who}." if who else ""
    set_part = f" Setting: {setting}." if setting else ""
    if s["kind"] == "talk":
        sp = cast.get(s["cast"][0], {"name": "the speaker", "id": ""})
        frame = f"{lead}. {visual}.{who_part}{set_part} Mouth visible, facing camera. {writer.SAFETY_TAIL}."
        tone = "grave, clipped" if sp.get("id") == "anchor" else "tense, deadpan"
        video = (f"{showrunner.STYLE}. {visual}.{set_part} {sp['name']} looks into the camera and speaks with a {tone} "
                 f"voice, exactly: \"{s['line']}\" The mouth moves clearly with every word; small head movement, one "
                 f"blink. Camera: {s['move'].replace('_', ' ')}. {showrunner.MOTION_TAIL} {showrunner.SAFETY}.")
        return frame, video
    frame = f"{lead}. {visual}.{who_part}{set_part} {writer.SAFETY_TAIL}."
    video = (f"{showrunner.STYLE}, speed lines, impact frames. {visual}.{set_part} Camera: "
             f"{s['move'].replace('_', ' ')}. Strong, readable motion. {showrunner.MOTION_TAIL} {showrunner.SAFETY}. "
             f"Sound: effects and ambience only, no speech, no music.")
    return frame, video


# -- cast assets ----------------------------------------------------------------------------------

def cast_assets(router, media: Path, sb: dict) -> tuple[dict, dict]:
    """(refs, voices): the style sheet, a character sheet per talking non-anchor member, a voice sample per
    talking member. Live only; each is made once and cached in the media dir."""
    refs, voices = {}, {}
    if router.mode != "live":
        return refs, voices
    refs["style"] = style.ensure_style_sheet(media, router)
    cast = _cast(sb)
    for sp in _speakers(sb):
        m = cast.get(sp)
        if not m:
            continue
        if sp != "anchor":
            path = sheet_path(media, m)
            if not (path.exists() and path.stat().st_size):
                prompt = (f"{writer.STYLE_LEAD}. Character model sheet still: {m['name']}, {m['role']}: {m['look']}. "
                          f"Medium shot, facing the camera, neutral dark studio background, full face clearly visible. "
                          f"{writer.SAFETY_TAIL}.")
                try:
                    style.generate_styled_frame(router, prompt, path, media)
                except Exception as e:  # noqa: BLE001 - a missing sheet only weakens consistency
                    print(f"[anime-news] cast sheet {sp} failed: {type(e).__name__}: {str(e)[:200]}", file=sys.stderr)
            refs[sp] = path if path.exists() else None
        sample = f"This is {m['name']}. I'm on the story, and the story is worse than you think. Stay with me."
        voices[sp] = audio.narrate(sample, voice_path(media, m), voice=_voice_id(m))
    return refs, voices


# -- shots ----------------------------------------------------------------------------------------

def render_shot(router, media: Path, sb: dict, sc: dict, s: dict, refs: dict, voices: dict) -> dict:
    """Frame (+ clip for talk/action). Fail-soft: returns the shot record with `error` set."""
    out = {"id": s["id"], "scene": sc["id"], "act": sc["act"], "kind": s["kind"], "cast": s["cast"],
           "line": s.get("line", ""), "seconds": s["seconds"], "frame": None, "clip": None, "error": None,
           "engine": None, "mode": router.mode}
    try:
        frame_prompt, video_prompt = shot_prompts(sb, sc, s)
        frame = media / f"frame-{uuid.uuid4().hex[:8]}.png"
        talk = s["kind"] == "talk"
        speaker = s["cast"][0] if talk and s["cast"] else None
        frame_refs = ([refs.get("style")] + ([refs.get(speaker)] if speaker and speaker != "anchor" else [])) if talk else []
        showrunner._frame(router, media, frame_prompt, [r for r in frame_refs if r], frame)
        out["frame"] = frame.name
        if s["kind"] in storyboard.VIDEO_KINDS:
            engine = SEEDANCE_MODEL if talk else ACTION_ENGINE
            clip = media / f"shot-{uuid.uuid4().hex[:8]}.mp4"
            router.generate_clip(video_prompt, "anime", s["move"], clip, frame=frame, model=engine,
                                 duration=s["seconds"], voice_ref=voices.get(speaker) if talk else None)
            out["clip"], out["engine"] = clip.name, engine
    except Exception as e:  # noqa: BLE001 - one failed shot never stops the episode
        out["error"] = f"{type(e).__name__}: {e}"[:400]
    print(f"[anime-news] {sc['id']} {s['id']} {s['kind']}: "
          + (f"FAILED {out['error']}" if out["error"] else (out["clip"] or out["frame"])), file=sys.stderr, flush=True)
    return out


def render_shots(router, media: Path, sb: dict, refs: dict, voices: dict, todo: list[tuple[dict, dict]],
                 parallel: int, progress=None, cancel: threading.Event | None = None,
                 done_before: int = 0, total: int | None = None) -> list[dict]:
    total = total if total is not None else len(todo)
    lock = threading.Lock()
    count = [done_before]

    def one(job):
        sc, s = job
        if cancel is not None and cancel.is_set():
            return {"id": s["id"], "scene": sc["id"], "act": sc["act"], "kind": s["kind"], "cast": s["cast"],
                    "line": s.get("line", ""), "seconds": s["seconds"], "frame": None, "clip": None,
                    "error": "cancelled", "engine": None, "mode": router.mode}
        r = render_shot(router, media, sb, sc, s, refs, voices)
        with lock:
            count[0] += 1
            if progress:
                progress("shots", count[0], total)
        return r

    with ThreadPoolExecutor(max_workers=max(1, parallel)) as pool:
        return list(pool.map(one, todo))


def usable(r: dict) -> bool:
    return bool(r.get("clip") if r["kind"] in storyboard.VIDEO_KINDS else r.get("frame"))


# -- the cut --------------------------------------------------------------------------------------

def edit_plan(sb: dict, shots: list[dict], media: Path, narration: dict | None = None) -> dict:
    """edit.assemble scenes: one per storyboard scene, its rendered shots in storyboard order (talk takes with their
    own sound, action clips, stills and inserts as stills), with the scene's narration, which edit.py plays in full
    over the scene's first run of pictures. Subtitles come from what is spoken; a weekly segment's headline is its
    one lower-third; the countdown is drawn only when the storyboard names one."""
    from . import music
    kit = music.music_kit(media)
    narration = narration or {}
    by_id = {r["id"]: r for r in shots}
    headlines = {seg["topic"]: seg["headline"] for seg in sb.get("segments") or []}
    scenes: list[dict] = []
    for sc in sb["scenes"]:
        items = []
        for s in sc["shots"]:
            r = by_id.get(s["id"])
            if not r or not usable(r):
                continue
            if r["kind"] == "talk":
                items.append({"kind": "talk", "clip": str(media / r["clip"]), "line": r["line"],
                              "speaker": (r["cast"] or [""])[0]})
            elif r["kind"] == "action":
                items.append({"kind": "action", "clip": str(media / r["clip"])})
            else:
                items.append({"kind": "still", "path": str(media / r["frame"])})
        if not items:
            continue
        scene = {"beat": sc["act"], "storyboard_scene": sc["id"], "shots": items}
        if sc.get("topic") in headlines:   # a weekly segment: its headline is shown once, as a lower-third
            scene["headline"] = headlines[sc["topic"]]
        if sc.get("narration"):
            scene["narration"] = sc["narration"]
            if narration.get(sc["id"]):
                scene["narration_audio"] = narration[sc["id"]]
        scenes.append(scene)
    acts = [s["beat"] for s in scenes]
    cd_beat = next((a for a in ("escalation", "climax", "turn", "setup") if a in acts), None)
    plan = {"title": sb.get("series") or storyboard.SERIES, "fps": 24, "size": [1280, 720], "scenes": scenes,
            "narration": [], "music": kit.get("bed"), "sfx": [],
            "countdown": {"label": sb["countdown"], "scene_beat": cd_beat} if cd_beat and sb.get("countdown") else None}
    if kit.get("whoosh"):
        plan["sfx"].append({"path": kit["whoosh"], "at": None, "lead": 0.25, "gain": 0.6})
    if kit.get("impact"):
        plan["sfx"].append({"path": kit["impact"], "at": None, "gain": 0.7})
    return plan


def narrate_scenes(router, media: Path, epid: str, sb: dict) -> dict:
    if router.mode != "live":
        return {}
    out = {}
    for sc in sb["scenes"]:
        if sc.get("narration"):
            p = audio.narrate(sc["narration"], media / f"{epid}-narration-{sc['id']}.mp3", voice=NARRATOR_VOICE)
            if p:
                out[sc["id"]] = str(p)
    return out


def cut(router, media: Path, epid: str, sb: dict, shots: list[dict], vertical: bool) -> dict:
    from . import edit, music
    if router.mode == "live":
        try:
            music.fetch_kit(media)
        except Exception as e:  # noqa: BLE001 - a cut without music still ships
            print(f"[anime-news] music kit failed: {e}", file=sys.stderr)
    plan = edit_plan(sb, shots, media, narrate_scenes(router, media, epid, sb))
    if not plan["scenes"]:
        raise RuntimeError("nothing to cut: every shot failed")
    (media / f"{epid}.plan.json").write_text(json.dumps(plan, indent=1))
    out = media / f"{epid}.mp4"
    edit.assemble(plan, out, audio=True, base=media)
    rep = json.loads(Path(str(out) + ".cuts.json").read_text())
    report = {k: rep.get(k) for k in ("duration", "shots", "cuts_per_minute", "avg_shot_s", "audio", "reuse")}
    vfile = None
    if vertical:
        from .vertical import vertical as to_vertical
        vpath = media / f"{epid}.9x16.mp4"
        try:
            to_vertical(out, vpath, sb.get("title_card") or sb["headline"][:40].upper())
            vfile = vpath.name
        except Exception as e:  # noqa: BLE001
            print(f"[anime-news] 9:16 export failed: {e}", file=sys.stderr)
    return {"file": out.name, "vertical_file": vfile, "report": report}


# -- one episode ----------------------------------------------------------------------------------

def save_shots(media: Path, epid: str, doc: dict) -> Path:
    path = media / f"{epid}.shots.json"
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(json.dumps(doc, indent=1))
    tmp.replace(path)
    return path


def _ledger_fields(epid: str, sb: dict, shots: list[dict], cutres: dict | None, quality: str, router,
                   started: float, credits: tuple[float, int], est: dict) -> dict:
    rep = (cutres or {}).get("report") or {}
    errors = [f"{r['id']} {r['kind']}: {r['error']}" for r in shots if r.get("error")]
    used, calls = credits
    return {"status": "done" if cutres else "rendered", "episode_id": epid, "headline": sb["headline"],
            "title_card": sb.get("title_card"), "logline": sb.get("logline"),
            "file": cutres["file"] if cutres else None, "path": str(Path(cutres and cutres["file"] or "")),
            "url": f"/media/{cutres['file']}" if cutres else None,
            "vertical_file": (cutres or {}).get("vertical_file"),
            "vertical_url": f"/media/{cutres['vertical_file']}" if cutres and cutres.get("vertical_file") else None,
            "storyboard": f"{epid}.storyboard.json", "shots_file": f"{epid}.shots.json", "plan": f"{epid}.plan.json",
            "cut": rep, "duration": rep.get("duration"), "cut_shots": rep.get("shots"),
            "cuts_per_minute": rep.get("cuts_per_minute"), "reuse": rep.get("reuse"),
            "storyboard_shots": len(shots), "rendered_shots": sum(1 for r in shots if usable(r)),
            "scenes": len(sb["scenes"]), "minutes": sb["minutes"], "quality": quality, "mode": router.mode,
            "pipeline": "storyboard", "errors": errors,
            "credits_used": round(used, 4) if calls else None, "credit_headers_seen": calls,
            "cost_estimate": est["usd"], "seconds_to_render": round(time.time() - started, 1),
            "finished": episode._now()}


def render(sb: dict, router, media: Path, quality: str = DEFAULT_QUALITY, vertical: bool = False,
           parallel: int = SHOT_PARALLEL, progress=None, cancel: threading.Event | None = None,
           max_cost: float | None = None) -> dict:
    """Render a whole storyboard into an episode. Returns the ledger entry (with `id`)."""
    media = Path(media).resolve()
    media.mkdir(parents=True, exist_ok=True)
    if quality not in QUALITIES:
        raise ValueError(f"quality must be one of {', '.join(QUALITIES)}")
    sb = storyboard.normalize(sb)
    router.seedance_resolution = quality
    est = estimate(sb, quality, media)
    if router.mode == "live" and max_cost is not None and est["usd"] > max_cost:
        raise CostCapExceeded(f"estimated ${est['usd']:.2f} is over the ${max_cost:.2f} cap: {est['parts']}")
    progress = progress or (lambda *a: None)
    entry_id, _, _, _ = episode._reserve(media, sb["headline"], sb.get("source_text_excerpt", ""))
    epid = f"episode-{uuid.uuid4().hex[:8]}"
    started = time.time()
    credits0 = CREDITS.snapshot()
    episode._update_ledger(media, entry_id, {"episode_id": epid, "headline": sb["headline"], "quality": quality,
                                             "pipeline": "storyboard", "cost_estimate": est["usd"]})
    (media / f"{epid}.storyboard.json").write_text(json.dumps(sb, indent=1))
    todo = [(sc, s) for sc in sb["scenes"] for s in sc["shots"]]
    shots: list[dict] = []
    try:
        progress("cast", 0, len(todo))
        refs, voices = cast_assets(router, media, sb)
        shots = render_shots(router, media, sb, refs, voices, todo, parallel, progress, cancel)
        save_shots(media, epid, {"episode_id": epid, "entry_id": entry_id, "quality": quality,
                                 "storyboard": sb, "shots": shots})
        if cancel is not None and cancel.is_set():
            raise Cancelled("cancelled")
        if not any(usable(r) for r in shots):
            raise RuntimeError("every shot failed: " + "; ".join(r["error"] or "" for r in shots)[:300])
        progress("cutting", len(todo), len(todo))
        cutres = cut(router, media, epid, sb, shots, vertical)
    except BaseException as e:
        used = CREDITS.snapshot()
        episode._update_ledger(media, entry_id, {
            "status": "failed", "error": f"{type(e).__name__}: {e}"[:300], "finished": episode._now(),
            "shots_file": f"{epid}.shots.json" if shots else None,
            "credits_used": round(used[0] - credits0[0], 4) if used[1] > credits0[1] else None})
        raise
    used = CREDITS.snapshot()
    fields = _ledger_fields(epid, sb, shots, cutres, quality, router, started,
                            (used[0] - credits0[0], used[1] - credits0[1]), est)
    fields["path"] = str(media / cutres["file"])
    episode._update_ledger(media, entry_id, fields)
    return {"id": entry_id, **fields}


def recut(media: Path, shots_file: Path, vertical: bool = False) -> dict:
    """Cut an episode again from the shots already on disk: no generation, no Router call. Reads <epid>.shots.json
    (either pipeline: a storyboard render or showrunner's script), rebuilds <epid>.plan.json with the current
    edit plan, uses the scene narration files already in the media dir (<epid>-narration-<scene>.mp3), and writes
    <epid>.mp4 (+ .cuts.json, and the 9:16 export with `vertical`). Updates the episode's ledger entry if any."""
    from . import edit
    media, shots_file = Path(media).resolve(), Path(shots_file).resolve()
    doc = json.loads(shots_file.read_text())
    epid = doc.get("episode_id") or shots_file.name.removesuffix(".shots.json")
    if "script" in doc:
        plan, title_card = showrunner.edit_plan(doc["script"], doc["shots"], media), doc["script"].get("title_card")
    else:
        sb = doc["storyboard"]
        narr = {sc["id"]: str(media / f"{epid}-narration-{sc['id']}.mp3") for sc in sb["scenes"]
                if (media / f"{epid}-narration-{sc['id']}.mp3").exists()}
        plan, title_card = edit_plan(sb, doc["shots"], media, narr), sb.get("title_card") or sb["headline"][:40].upper()
    if not plan["scenes"]:
        raise RuntimeError("nothing to cut: no usable shot on disk")
    (media / f"{epid}.plan.json").write_text(json.dumps(plan, indent=1))
    out = media / f"{epid}.mp4"
    edit.assemble(plan, out, audio=True, base=media)
    rep = json.loads(Path(str(out) + ".cuts.json").read_text())
    report = {k: rep.get(k) for k in ("duration", "shots", "cuts_per_minute", "avg_shot_s", "audio", "reuse")}
    vfile = None
    if vertical:
        from .vertical import vertical as to_vertical
        to_vertical(out, media / f"{epid}.9x16.mp4", title_card)
        vfile = f"{epid}.9x16.mp4"
    entry = next((e for e in episode.read_ledger(media) if e.get("episode_id") == epid or e.get("file") == out.name), None)
    if entry and entry.get("id"):
        episode._update_ledger(media, entry["id"], {"cut": report, "duration": report["duration"],
                                                    "cut_shots": report["shots"],
                                                    "cuts_per_minute": report["cuts_per_minute"],
                                                    "reuse": report["reuse"], "recut": episode._now()})
    return {"episode_id": epid, "file": out.name, "path": str(out), "vertical_file": vfile, "report": report}


def retry_estimate(sb: dict, shots: list[dict], quality: str, media: Path) -> dict:
    """Spend estimate of a retry: only the failed shots, as a sub-storyboard (cast assets count as cached)."""
    failed = {(r["scene"], r["id"]) for r in shots if not usable(r)}
    sub = dict(sb, scenes=[dict(sc, shots=[s for s in sc["shots"] if (sc["id"], s["id"]) in failed], narration="")
                           for sc in sb["scenes"]])
    return estimate(sub, quality, media)


def retry(router, media: Path, shots_file: Path, parallel: int = SHOT_PARALLEL, vertical: bool = False,
          progress=None, max_cost: float | None = None, estimate_only: bool = False) -> dict:
    """Re-render only the failed shots of <epid>.shots.json (fresh frame + clip), then re-cut and update the
    episode's ledger entry. Same spend rules as render: `estimate_only` returns the estimate of the failed
    shots without rendering; `max_cost` refuses a live retry whose estimate is over the cap."""
    media = Path(media).resolve()
    doc = json.loads(Path(shots_file).read_text())
    sb, shots, epid = doc["storyboard"], doc["shots"], doc["episode_id"]
    quality = doc.get("quality", DEFAULT_QUALITY)
    router.seedance_resolution = quality
    by_id = {(sc["id"], s["id"]): (sc, s) for sc in sb["scenes"] for s in sc["shots"]}
    todo_idx = [i for i, r in enumerate(shots) if not usable(r)]
    est = retry_estimate(sb, shots, quality, media)
    if estimate_only:
        return {"estimate": est, "failed_shots": len(todo_idx), "episode_id": epid}
    if router.mode == "live" and max_cost is not None and est["usd"] > max_cost:
        raise CostCapExceeded(f"retry estimated ${est['usd']:.2f} is over the ${max_cost:.2f} cap: {est['parts']}")
    started = time.time()
    credits0 = CREDITS.snapshot()
    refs, voices = cast_assets(router, media, sb)
    redone = render_shots(router, media, sb, refs, voices, [by_id[(shots[i]["scene"], shots[i]["id"])] for i in todo_idx],
                          parallel, progress)
    for i, r in zip(todo_idx, redone):
        shots[i] = r
    doc["shots"] = shots
    save_shots(media, epid, doc)
    cutres = cut(router, media, epid, sb, shots, vertical or bool(next(
        (e.get("vertical_file") for e in episode.read_ledger(media) if e.get("episode_id") == epid), None)))
    used = CREDITS.snapshot()
    prev = next((e for e in episode.read_ledger(media) if e.get("episode_id") == epid), {})
    est = estimate(sb, quality, media)
    fields = _ledger_fields(epid, sb, shots, cutres, quality, router, started,
                            (used[0] - credits0[0] + float(prev.get("credits_used") or 0),
                             used[1] - credits0[1] + int(prev.get("credit_headers_seen") or 0)), est)
    fields["path"] = str(media / cutres["file"])
    fields["retried"] = int(prev.get("retried") or 0) + 1
    entry_id = doc.get("entry_id") or prev.get("id")
    if entry_id:
        episode._update_ledger(media, entry_id, fields)
    return {"id": entry_id, **fields}
