# The anime-paced cut (`shot-director-cut`)

`src/shot_director/edit.py` turns the generated clips and stills into a fast, captioned, scored edit.
It uses ffmpeg only and generates nothing. `src/shot_director/audio.py` makes the sound files through
Comfy Router. The pacing follows `docs/psyopanime-formula.md`.

```bash
shot-director-cut --plan plan.json --out final.mp4 [--no-audio] [--sheet sheet.png]
```

From Python: `edit.assemble(plan, out_path, audio=True, base=plan_dir)`. Next to the video it writes
`<out>.cuts.json`: duration, shot count, cuts per minute, average shot, gated segments, the `reuse` check
(rule 14), and the full timeline.

## Plan

```json
{"title": "NEON DISTRICT", "logline": "One sentence.", "fps": 24, "size": [1280, 720],
 "scenes": [{"beat": "chase", "caption": "No brakes, no turning back, full throttle.",
             "clips": ["a.mp4", "b.mp4"], "stills": ["f.png", {"path": "s.png", "start_frame_of": "a.mp4"}],
             "narration": "text read over this scene", "narration_audio": "narr-1.mp3",
             "action": true}],
 "narration": [{"text": "...", "scene": -1, "path": "narr-0.mp3"}],
 "music": "music.mp3",
 "sfx": [{"name": "whoosh.mp3", "at": null, "lead": 0.25, "gain": 0.7},
         {"name": "impact.mp3", "at": 12.5}]}
```

Relative paths resolve against the plan's folder. Missing files are skipped. Each narration entry
starts at its scene (`scene: -1` means the title card), or at an explicit `at`. An SFX entry with
`"at": null` plays on every scene change (white flash). `name` and `path` both work for the file.
A beat containing chase, fight, action, escalate, montage, battle, attack, clash or run is an action
beat; `"action": true` marks one explicitly. A still marked `start_frame_of` a clip is that clip's first
moment, so it is left out while the scene has a usable clip window (rule 3).

## Cutting rules

0. **Every source moment is on screen at most once.** Each window of clip footage (clip segment, the
   source span of a speed ramp, burst shot, CRT shot, title background) is disjoint from every other
   window of the same file, and a window that cannot be placed without overlap is skipped. Each still is
   used at most once in the whole cut, and no freeze frames are taken from clips.
1. **No clip plays straight through.** Each clip's unused source is cut into as many slots of at least
   1.45 s as fit, at most 3, spread over the clip; each window is 1.6 to 2.5 s or its slot, whichever is
   shorter, in whole frames. A 5 s clip gives 3 windows of about 1.6 s, a 15 s clip 3 windows of 1.6 to
   2.5 s. The window whose slot holds the clip's strongest frame moves onto it, inside its slot only.
   No shot is longer than 2.5 s.
2. **Average shot is 1.3 to 1.8 s.** Action beats add a burst of four 0.33 s shots (8 frames), one per
   clip in rotation, carved only from source no other window uses (an action scene keeps a 0.38 s strip
   of each clip free for it); a shot that does not fit is skipped. They go just before the scene's last
   shot. The 5 s mock episode averaged 1.58 s.
3. **Stills become cutaways** of 0.9 to 1.3 s. Each one gets a Ken Burns push-in, a pull-out, or a
   decaying camera shake, in rotation. The order is clip, still, clip, clip, still, clip... Leftover
   stills go before the scene's last clip shot (or after the only one, in a one-clip scene), and a scene
   with no usable clips becomes a montage of its stills. Stills that are the start frames of the scene's
   clips are not used while the scene has a usable clip window; they are the fallback when clips fail.
4. **Cold open:** the title and logline sit over the sharpest frame of the whole episode, with a crash
   zoom, for 1.25 s. That 1.25 s window is reserved before any scene is planned, so no scene shows it again.
5. **Every scene opens on its strongest shot**: the sharpest window that passes the sharpness gate
   (rule 9), with a quick push-in.
6. **Hard cuts dominate.** A 2-frame white flash marks every scene change. A 1-frame black marks the
   hard beats: before the speed ramp and before the scene's last shot.
7. **One speed ramp per scene**, on the second clip segment: `setpts` 1.4 (slow motion) on even scenes,
   0.7 (sped up) on odd ones. The ramp keeps the window's start; a speed-up that would run past the
   clip's end shortens the shot (and is skipped below 0.6 s) rather than reaching back into frames the
   gate never checked.
8. **CRT-monitor shot** (0.5 to 0.8 s) as the transition into every other scene: one of the scene's own
   planned windows (never the opener, the ramp or the closing shot) is shortened, moved to the front and
   shown inside a dark bezel with scanlines, barrel curvature (`lenscorrection`), a blue tint and grain.
   It adds no repeated footage. A scene with fewer than 3 windows gets no CRT shot.
9. **Sharpness gate.** For every candidate segment, the first, middle and last frames are sampled
   (160x90 grayscale through ffmpeg) and the Laplacian variance is computed in pure Python, so neither
   numpy nor OpenCV is needed. The segment's score is its worst frame. A segment scoring below 40 % of
   the clip's median sharpness (10 samples) is dropped and listed in `gated_segments`. The gate applies
   to every window, the scene opener included, and burst shots go through it too. If no window in a
   scene passes, the sharpest one is kept as the opener and a warning is logged. The CRT shot is one of
   the scene's gate-passing windows. The gate is relative to each clip's median, so a clip that is mostly blurred
   has a blurred median and gates little. The scene's last shot is the
   segment with the sharpest final frame, so a smeared whip-pan tail never ends a scene. In the test,
   the motion-blurred tail of `media/sequence-fe2c7f01.mp4` (13.0 to 14.5 s, sharpness 390 to 780
   against a median of 4197) was gated out.
10. **Subtitles** use the brand yellow #F5C518 on a dark translucent box, bottom-center, one clause at
    a time. The scene caption is split on commas, semicolons, colons, sentence ends and dashes, with at
    most 8 words per chunk, and the clauses are spread in order across the scene's cuts.
11. **Wordmark:** "SHOT DIRECTOR", small and gold, top-left, on every frame except
    the flash and black frames. Override it with the `SHOT_DIRECTOR_WORDMARK` environment variable.
12. **End card:** "Shot Director · open source · #OpenSourceHiggsfield" with a gold rule.
13. Output is 1280x720 at 24 fps unless the plan's `size`/`fps` say otherwise. Video is H.264 (yuv420p),
    audio is AAC 48 kHz stereo, with `+faststart`.
14. **Reuse check.** `<out>.cuts.json` carries `reuse`: `overlapping_windows` (pairs of footage windows of
    one file overlapping by more than one frame), `still_repeats`, `unique_sources` (clips plus stills on
    screen) and `source_seconds_used` (clip footage seconds). It is recounted from the final timeline,
    not taken from the planner, and both counts must be 0.

The resulting pace is about 40 to 45 shots per minute. That is faster than the 20 to 35 cuts per minute
in the first brief; the research's 1.3 to 1.8 s average shot overrides it.

## Talking-cast plan fields (episode v2, `showrunner.edit_plan`)

- **`"talk": true`** scene (one clip, `"line"`, `"speaker"`): the take is shown nearly whole, from 0.15 s to
  its end minus 0.1 s, as 2 or 3 windows of 1.6 to 3.0 s that abut exactly in source time. Cuts fall on a
  clause boundary of the line (by word count) when one is within 0.6 s of the even split. Framing
  alternates wide, punch-in (1.3x, centred, upper third), wide. The clauses are captioned for their
  word-count share of the take, across the cuts. No speed ramp, burst, CRT, black frame or still; the white
  flash before the scene stays. Talk takes are never used for the title background.
- **`"clip_audio_gain"`** (action scenes, default 0): each shown window's own sound, same source span as the
  picture (speed ramps time-stretched), on the SFX bus at that gain. Clips without audio are skipped.
- **`"music_intro"`**: a riser from 0; the bed comes in on the title card (the drop) with a 0.15 s crossfade.
  If the riser is shorter than the cold open, the bed starts where the riser ends, so there is never a gap.
- **`"title_after_scene"`**: the title card goes right after that scene (cold open first). Every SFX entry
  with `"at": null` also hits the title.
- **`"countdown": {"label", "scene_beat"}`**: the first non-talk scene with that beat gets a big red
  (#FF2A2A) monospace countdown top-right, the label above, `00:SS` running down to `00:00` over the
  scene; the last second flashes white. `cuts.json` notes it under `countdown`, and the title time under `title_at`.

## Audio

Each bus is rendered to its own WAV stem with a length check, then the stems are mixed. One graph with
every input in it ended the mix early (loudnorm inside a multi-input graph truncated the bus).

- **Voice bus:** narration lines plus the dialogue (each talk take's own sound for exactly the source span
  on screen). Each line is loudnorm'd to -16 LUFS on its own, then delayed to its place.
- **Music bus:** the bed (volume 0.8), looped to the end with no gaps, optional intro riser, ducked under
  the voice bus only (never under SFX) with `sidechaincompress` (threshold 0.03, ratio 10, attack 20 ms,
  release 400 ms).
- **SFX bus:** hits on the scene changes (whoosh leading by 0.25 s, impact on the flash) and the action
  clips' own sound.
- **Final:** mixed, then `loudnorm` I=-14 TP=-1.0 LRA=11 (single-pass; the mock v2 cut measured -13.5 LUFS).
  With nothing provided, or with `--no-audio`, a silent stereo AAC track is still written so players behave.

## Sound generation (`audio.py`, Comfy Router)

| Function | Router model | Body |
| --- | --- | --- |
| `narrate(text, out_mp3, voice=None)` | `elevenlabs/eleven_v3` (`SHOT_DIRECTOR_TTS_MODEL` overrides) | `{"inputs": [{"text", "voice_id"}]}`; the `[excited]` audio tag carries the anime-reporter delivery (no SSML) |
| `sfx(prompt, out, seconds, loop=False)` | `elevenlabs/eleven_sfx_v2` | `{"text", "duration_seconds", "loop", "prompt_influence"}` |
| `music_bed(seconds, out)` | one `eleven_sfx_v2` call | 22 s loopable "upbeat synthwave anime opening loop", looped with ffmpeg |

**Music source tonight:** the bed is a single 22 s sound-effect generation, looped. A proper music
model is a follow-up.

Calls go to `POST /v2/models/elevenlabs/<model>`. Router answers 200 with the raw MP3 bytes, as the
model's `openapi.json` documents. If it ever answers with a queued `request_id`, the code polls the
`/requests/{id}/status` route using router_client's `LiveRouter` helpers. Each function fails soft: it
logs one warning and returns None, so the cut still renders. Results are cached by output path, so
re-running does not spend credits again. The default voice is `21m00Tcm4TlvDq8ikWAM`, from the Router
openapi example; `SHOT_DIRECTOR_VOICE` overrides it.


**Gate exception (explicit):** a scene whose clip windows all fail the sharpness gate opens on its stills and the smeared clip is dropped. Only a scene with no stills at all keeps its sharpest window, marked `gate_exception: true` in the cuts JSON and logged as a warning, because a scene must show something.
