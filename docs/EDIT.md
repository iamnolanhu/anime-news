# The anime-paced cut (`shot-director-cut`)

`src/shot_director/edit.py` turns the generated clips and stills into a captioned, scored edit.
It uses ffmpeg only and generates nothing. `src/shot_director/audio.py` makes the sound files through
Comfy Router. The grammar follows the art-director review of 2026-11-03 (rules R1-R19, the "stutter" audit) as
amended by the PsyopAnime cut log measured on their three most-viewed episodes.

```bash
shot-director-cut --plan plan.json --out final.mp4 [--no-audio] [--sheet sheet.png]
anime-news render --recut <episode>.shots.json [--out DIR]   # cut an episode again from the shots on disk, no spend
```

From Python: `edit.assemble(plan, out_path, audio=True, base=plan_dir)`. Next to the video it writes
`<out>.cuts.json` (`edit.cut_report`): duration, shot count, cuts per minute, average shot, gated angles, the
`reuse` check, the full timeline, every voice line as placed (`voice`: `played` against the file's `file_dur`),
each clip's internal cuts (`src_cuts`), the headlines, the countdown and the measured loudness (`I`, `TP`).
`packages/engine/tests/cut_metrics.py` scores a cuts.json with the review's stutter metrics (returns, A-B-A,
time-backwards, jump cuts, hidden cuts, cuts inside a line).

## Plan

```json
{"title": "THE WEEKLY BRIEFING", "fps": 24, "size": [1280, 720],
 "scenes": [{"beat": "setup", "storyboard_scene": "s1", "headline": "Mayor bans Mondays",
             "narration": "text read over this scene", "narration_audio": "narr-s1.mp3", "clip_audio_gain": 0.35,
             "shots": [{"kind": "talk", "clip": "t.mp4", "line": "Good evening. This week: Mondays."},
                       {"kind": "action", "clip": "a.mp4"},
                       {"kind": "still", "path": "f.png"}]}],
 "narration": [{"text": "...", "at": 0.15, "path": "narr-0.mp3"}],
 "music": "music.mp3",
 "countdown": {"label": "DEADLINE IN", "scene_beat": "escalation"},
 "sfx": [{"name": "whoosh.mp3", "at": null, "lead": 0.25, "gain": 0.7},
         {"name": "impact.mp3", "at": 12.5}]}
```

One plan scene is one storyboard scene, and its `shots` are in storyboard order: `talk` (a take with its own
lip-synced sound and its `line`), `action` (a clip) or `still` (`insert` is read as a still). Both pipelines write
this (`render_storyboard.edit_plan` per storyboard scene, `showrunner.edit_plan` per act). An older plan whose scene
is one talk take (`"talk": true`, `"line"`) or `"clips"` plus `"stills"` still loads: its clips come first, then its
stills (that format records no order between them), and a still marked `start_frame_of` a clip is used only when
that clip gives no window.

Relative paths resolve against the plan's folder; missing files are skipped. Every clip and still is used in one
place only. `title` is the series, drawn as the corner wordmark. `headline` (weekly segment scenes) is shown once
as a lower-third. A free-standing `narration` entry plays at `at`, or at its `scene`, or at 0.15 s. An SFX entry
with `"at": null` plays on every storyboard scene change and on the end card.

## Cutting rules

0. **Every source moment is on screen at most once.** Each clip angle is shown once and each still once. The only
   held frame is rule 8's last resort. `cuts.json` `reuse` recounts it from the final timeline: `overlapping_windows` and
   `still_repeats` must be 0.
1. **A clip is cut by its angles, never across one (R4).** Seedance action clips carry a hidden "Hard cut to:"
   second angle. Each clip is decoded once at 160x90; a frame whose ffmpeg scene score is over 0.25, or a spike of at
   least 0.20 that is 1.6x the frame before it, is an internal cut (the first and last 0.2 s are ignored, changes
   within 0.3 s are one cut zone). An angle is the span between cuts, at least 12 frames, minus one frame of margin
   each side. Each angle gives one window, and a window never contains a cut.
2. **A scene plays forward, in storyboard order (R1-R3, R5).** Shots appear in the order the storyboard gives them;
   a clip's angles appear in source order, each once, so a scene never returns to a clip it left, never runs
   backwards in source time and never jump-cuts within one shot. In an action run (consecutive action shots), the
   scene's opening shot establishes (1.5 s), the shots after it are short (0.8 s; PsyopAnime action shots run 0.6-1.0
   s) and the run's last shot holds (up to 2 s) to reset the beat. Three or more consecutive short shots of distinct
   compositions from at least two clips are a burst; one clip is never rotated into a burst.
3. **Stills hold in their storyboard place** for 1.9 s (PsyopAnime stills average 1.9 s) with one camera move: a
   Ken Burns push, pull or decaying shake, in rotation. A scene whose clips all fail the gate plays its stills.
4. **No opening title card.** The cut opens cold on the first scene; the series wordmark is in the corner from the
   first frame. The end card ("ANIME NEWS · open source · #OpenSourceHiggsfield", a gold rule) holds 2.5 s.
5. **Each window opens where the action is (R6).** Within an angle, a window starts at the earliest start that
   passes the sharpness gate (rule 9), or within the next 0.5 s at the start with the most motion (mean frame
   difference over its first 12 frames). A smeared tail shortens the window (never below 0.5 s) instead of moving it.
6. **Hard cuts only.** No white flashes, black frames, speed ramps, CRT inserts, punch-ins or dissolves: the movement
   comes from inside the clips (PsyopAnime cut log). The whoosh and impact SFX hit on storyboard scene changes.
7. **Talk takes hold (R11, R12).** A take is one framing, nearly whole (0.15 s to its end minus 0.1 s), never
   punched into after the fact. A take over 4 s holds 2-3.7 s on the speaker and lends the rest of its line to the
   scene's next pictures (L-cut), or, when it ends the scene, opens its line under the previous pictures (J-cut).
   The cut falls only on a real pause in the take's sound (ffmpeg silencedetect, -32 dB for 0.12 s) that lies within
   0.5 s of a clause boundary (placed by word count) and at least 2 s from the cut-away end; a take with no such pause,
   or no sound, holds whole. A J-cut's voice starts on a cut of the previous run,
   and no cut falls in a line's first 0.8 s. The take's sound always plays whole.
8. **Lines are never cut off (R13).** A scene's narration plays in full over its first run of pictures; the run
   grows to fit it: every shot up to 2.5 s first, then clip windows to their angle's end, then stills up to 6 s. When
   that is still too short, the run's last still holds as long as it takes, or, with no still, its last shot holds its
   last frame (`extra.freeze`), until the line ends plus 0.2 s. A voice is never trimmed: `voice` `played` always
   equals `file_dur`. Free-standing `narration` lines also play in full.
9. **Sharpness gate.** For every candidate window, the first, middle and last frames are scored (Laplacian
   variance of the 160x90 grayscale frame, pure Python). A window must reach 40 % of its angle's median (10
   samples; each angle is its own shot, so a soft close-up is not judged against a sharp wide) and never less than
   `SHARP_FLOOR` (250). An angle with no passing window is dropped and listed in `gated_segments`; a scene with nothing
   left is skipped, and its narration is dropped with it.
10. **Subtitles follow the voice, not the cuts.** Yellow #F5C518 on a dark translucent band, bottom centre, one
    clause at a time (split on commas, semicolons, colons, sentence ends and dashes, at most 8 words per chunk).
    Each clause of a spoken line (dialogue or narration) is on screen for its word-count share of the line as it
    plays, across whatever cuts it spans. Wordless pictures carry no subtitle, so storyboard captions over action are
    not drawn.
11. **Headline lower-third.** A scene with a `headline` shows it once, in the subtitle style, for 2.5 s on its first
    run of pictures (at the scene's start, or right after an opening talk take). No voice plays under it: that run
    lends no time to a talk take, and the scene's narration starts on the next cut after it (or once it is off). It
    ends at the earlier of 2.5 s and 0.1 s before the next line starts (and inside its scene); if that leaves under
    1.2 s it moves to the scene's next quiet stretch (0.1 s after a line ends), and with none it is dropped (logged).
12. **Countdown** (only when the plan has `countdown`; `render_storyboard` sets it only when the storyboard names a
    label): a big red (#FF2A2A) monospace `00:SS` top-right, its label above, over the last run of pictures of the
    first scene with that beat, reaching `00:00` at the run's end; the last second flashes white. One clock on screen
    (R18): a shot whose prompt mentions a clock, timer or countdown gets no overlay (both edit plans pass each shot's
    prompt), and no subtitle under the overlay states a time. The writers are told never to draw clocks, timers,
    countdowns or numbers in frames (`writer.NO_CLOCKS`, in the script and storyboard prompts).
13. **Wordmark:** the plan's `title` (the series), small and gold, top-left, after the 28 px gold cube mark
    (`static/brand/an-mark-small.png`, overlaid with ffmpeg), on every frame. `SHOT_DIRECTOR_WORDMARK` overrides the
    text (empty = none), `SHOT_DIRECTOR_WORDMARK_MARK` the mark; a missing mark file is skipped.
14. Output is 1280x720 at 24 fps unless the plan's `size`/`fps` say otherwise. Video is H.264 (yuv420p), audio is
    AAC 48 kHz stereo, with `+faststart`.

The pace that results depends on what was generated: on the four reviewed episodes, 15-30 cuts per minute, median
shot 1.4-3.0 s, every return, A-B-A, time-backwards, jump cut and hidden cut at 0 (PsyopAnime: 27-50 cuts per
minute, median 1.4 s). More angles per action clip (more "Hard cut to:" beats) is what raises it.

## Audio

Each bus is rendered to its own WAV stem with a length check, then the stems are mixed. One graph with
every input in it ended the mix early (loudnorm inside a multi-input graph truncated the bus).

- **Voice bus:** every line where the planner placed it: narration in full, and each talk take's own sound for its
  whole line, also where its picture has cut away. Each line is loudnorm'd to -16 LUFS on its own, then delayed
  to its place.
- **Music bus:** the bed (volume 0.8), looped to the end with no gaps; quiet (0.35 of that) through the first
  scene, then building in a straight line to full at two thirds of the cut (PsyopAnime: a quiet open, the loudest
  part in the last third); ducked under the voice bus only (never under SFX) with `sidechaincompress` (threshold
  0.03, ratio 10, attack 20 ms, release 400 ms).
- **SFX bus:** hits on storyboard scene changes and the end card (whoosh leading by 0.25 s, impact on the cut),
  never at 0:00, and the action clips' own sound at `clip_audio_gain`.
- **Final:** mixed, `loudnorm` I=-14 TP=-1.5 LRA=11, then the delivered file is measured (ebur128); while its true
  peak is over -1.0 dBTP (the AAC encode overshoots), the audio is turned down by the excess. With nothing provided,
  or with `--no-audio`, a silent stereo AAC track is still written so players behave.

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
