# The anime-paced cut (`shot-director-cut`)

`src/shot_director/edit.py` turns the generated clips and stills into a fast, captioned, scored edit.
It uses ffmpeg only and generates nothing. `src/shot_director/audio.py` makes the sound files through
Comfy Router. The pacing follows `docs/psyopanime-formula.md`.

```bash
shot-director-cut --plan plan.json --out final.mp4 [--no-audio] [--sheet sheet.png]
```

From Python: `edit.assemble(plan, out_path, audio=True, base=plan_dir)`. Next to the video it writes
`<out>.cuts.json`: duration, shot count, cuts per minute, average shot, gated segments, and the full timeline.

## Plan

```json
{"title": "NEON DISTRICT", "logline": "One sentence.", "fps": 24, "size": [1280, 720],
 "scenes": [{"beat": "chase", "caption": "No brakes, no turning back, full throttle.",
             "clips": ["a.mp4", "b.mp4"], "stills": ["f.png"],
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
beat; `"action": true` marks one explicitly.

## Cutting rules

1. **No clip plays straight through.** Each clip is split into 2 or 3 segments (1 if it is under 2.8 s),
   each 1.5 to 2.3 s, taken from different parts of the clip. No shot is longer than 2.5 s.
2. **Average shot is 1.3 to 1.8 s.** Action beats add a burst of four 0.33 s shots (8 frames) from
   scattered parts of the scene's clips, just before the scene's last shot. The test cut averaged 1.34 s.
3. **Stills become cutaways** of 0.9 to 1.3 s. Each one gets a Ken Burns push-in, a pull-out, or a
   decaying camera shake, in rotation. The order is clip, still, clip, clip, still, clip... When a scene
   has too few stills, freeze frames taken at the clips' sharpest moments fill in. Every supplied still
   is used at least once: leftovers go before the scene's last clip shot (or after the only one, in a
   one-clip scene), and a scene with no usable clips becomes a montage of all its stills.
4. **Cold open:** the title and logline sit over the sharpest frame of the whole episode, with a crash
   zoom, for 1.25 s. A scene opener on the same moment continues from where the title ends, so the
   moment is not shown twice.
5. **Every scene opens on its strongest shot**: the sharpest window that passes the sharpness gate
   (rule 9), with a quick push-in.
6. **Hard cuts dominate.** A 2-frame white flash marks every scene change. A 1-frame black marks the
   hard beats: before the speed ramp and before the scene's last shot.
7. **One speed ramp per scene**, on the second clip segment: `setpts` 1.4 (slow motion) on even scenes,
   0.7 (sped up) on odd ones. The ramp keeps the window's start; a speed-up that would run past the
   clip's end shortens the shot (and is skipped below 0.6 s) rather than reaching back into frames the
   gate never checked.
8. **CRT-monitor insert** (0.5 to 0.8 s) as the transition into every other scene: the shot sits inside
   a dark bezel with scanlines, barrel curvature (`lenscorrection`), a blue tint and grain.
9. **Sharpness gate.** For every candidate segment, the first, middle and last frames are sampled
   (160x90 grayscale through ffmpeg) and the Laplacian variance is computed in pure Python, so neither
   numpy nor OpenCV is needed. The segment's score is its worst frame. A segment scoring below 40 % of
   the clip's median sharpness (10 samples) is dropped and listed in `gated_segments`. The gate applies
   to every window, the scene opener included, and burst shots go through it too. If no window in a
   scene passes, the sharpest one is kept as the opener and a warning is logged. The CRT insert reuses
   a gate-passing window. The gate is relative to each clip's median, so a clip that is mostly blurred
   has a blurred median and gates little. The scene's last shot is the
   segment with the sharpest final frame, so a smeared whip-pan tail never ends a scene. In the test,
   the motion-blurred tail of `media/sequence-fe2c7f01.mp4` (13.0 to 14.5 s, sharpness 390 to 780
   against a median of 4197) was gated out.
10. **Subtitles** use the brand yellow #F5C518 on a dark translucent box, bottom-center, one clause at
    a time. The scene caption is split on commas, semicolons, colons, sentence ends and dashes, with at
    most 8 words per chunk, and the clauses are spread in order across the scene's cuts.
11. **Wordmark:** "ANIME NEWS", small and gold, top-left, after the 28 px gold cube mark
    (`static/brand/an-mark-small.png`, overlaid with ffmpeg), on every frame except the flash and
    black frames. Override the text with `SHOT_DIRECTOR_WORDMARK` and the mark with
    `SHOT_DIRECTOR_WORDMARK_MARK`; an empty value or a missing file drops the mark and the cut still renders.
12. **End card:** "ANIME NEWS · open source · #OpenSourceHiggsfield" with a gold rule.
13. Output is 1280x720 at 24 fps unless the plan's `size`/`fps` say otherwise. Video is H.264 (yuv420p),
    audio is AAC 48 kHz stereo, with `+faststart`.

The resulting pace is about 40 to 45 shots per minute. That is faster than the 20 to 35 cuts per minute
in the first brief; the research's 1.3 to 1.8 s average shot overrides it.

## Audio

- **Narration:** a single voiceover bus. Each line is pre-normalized (`loudnorm` I=-16), delayed to
  its scene start, and mixed.
- **Music bed:** looped to the cut's length, faded in and out, and ducked under the narration with
  `sidechaincompress` (threshold 0.03, ratio 10, attack 20 ms, release 400 ms).
- **SFX:** hits placed on the scene changes (whoosh leading by 0.25 s, impact on the flash).
- **Final:** mixed, then `loudnorm` I=-16 TP=-1.5 LRA=11. This is single-pass, so integrated loudness
  lands near -16 (the test measured -18.5 LUFS). With nothing provided, or with `--no-audio`, a silent
  stereo AAC track is still written so players behave.

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


**Gate exception (explicit):** a scene whose clip windows all fail the sharpness gate opens on its stills and the smeared clip is dropped. A scene with no stills whose windows all fail is skipped entirely (logged); in the episode pipeline every scene carries its start frame as a still, so this only drops scenes in hand-built plans. The gate is absolute: no smeared opener ever ships.
