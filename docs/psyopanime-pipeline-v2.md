# PsyopAnime pipeline: reverse-engineering and our v2 (2026-11-02)

Extends `psyopanime-formula.md` (do not repeat it). Imitate METHOD only: fictional archetypes, no logos, no real people.
Research done 2026-11-02 evening: Grok Build lane x2 (live X/web), 2 videos frame-read + 1 loudness trace, Codex second opinion,
Router model list and Seedance schema read live (no generation calls, $0 spent).

## Headline findings (read this first)
1. **Their video engine is Seedance** (they said so: "integrated seedance 2.0 api into our internal pipeline", 22 Jun 2026). Stills come from
   Midjourney + Photoshop + Nano Banana. Seedance renders audio and video in ONE pass, with lip-synced speech when dialogue is in quotes.
2. **Our Router serves `byteplus/dreamina-seedance-2-5-260628`**: 4-30 s per clip, `generate_audio`, `audio_url` reference, 480p `draft` mode,
   `return_last_frame`. One 15 s call with a 4-beat prompt gives ~4-6 different shots AND mouths that move. That fixes complaint 1 and 2 at once.
3. Their "30+ shots a minute" is mostly **many short generations edited in DaVinci Resolve by a small team**, with an internal tool that
   auto-ranks headlines and batches images (4 per prompt). Not magic: volume + a human cutting.
4. Music is a **continuous, loud, building score with a drop synced to the first explosion**, never silence (measured, section B).
5. Router has NO music model (checked all 253 ids). Music is our biggest uncovered gap (section D4).

## A. What is actually known about their pipeline (sources)
| Claim | Evidence (quote, URL) | Confidence |
|---|---|---|
| Stills: Midjourney, Photoshop, Nano Banana, hand touch-ups | Reply 1 Feb 2026: "a lot of midjourney, photoshop, nanobanana, and sometimes hands on touch ups. scaling accordingly" https://x.com/PsyopAnime/status/2018026677360615707 | High (first party) |
| Video: Seedance 2.0 in a custom news pipeline | "Succesfully integrated seedance 2.0 api into our internal pipeline :) custom prompts based on each article with the ability to edit, reroll, and work in tandem with a larger team" https://x.com/PsyopAnime/status/2068903092507054345 | High |
| Internal tool "PSYOPQUEEN // FEED INTERCEPT": fetches headlines, scores them (overall, visual, urgency, controversy, cultural, brand fit), dispatches an agent, "4 IMGS/PROMPT" | https://x.com/PsyopAnime/status/2068028401768685781 (screenshots, no vendor labels) | High |
| Oct 2026 overhaul: "automated pipelines. agentic prompt building. clip analysis. b-roll from automated live feeds. still requires a human touch." | @psyophq https://x.com/PsyopHQ/status/2107726040655393084 | High |
| Edit bay = DaVinci Resolve; "small unit"; part-time animator $25-32/h; reports to Founder | https://www.psyopanime.ai/role-animator-editor.html , https://psyopanime.ai/careers.html | High |
| Process = "We write line by line - generation by generation -because the technology allows us to generate it in real time." | https://x.com/PsyopAnime/status/2095503537169965357 | High |
| Origin: UFO meme page; "A group of people gave me $70 to spend on credits"; collaborator Aiden Guo ("Code: White"); seeking seed funding Mar 2026 | https://x.com/PsyopAnime/status/2006528934196789309 , .../2009846589896945974 , .../2028941532946968951 | High |
| Site: "AI-driven animation studio ... Humans direct. Machines render. The signal spreads." | https://psyopanime.ai/about.html | High |
| Lip-sync tool, voice model, music source | NEVER named by them. Not findable (Grok searched X, Reddit, HN: zero hits). | Not findable |
| Imitators' stacks (not theirs): CircleVang = OiiOii + CapCut, "remade the official anchor pack, scene-by-scene prompts" https://x.com/CircleVang/status/2103396261650956442 ; davekebo = Grok video + Midjourney + ElevenLabs for dialogue and music https://x.com/davekebo/status/2028849217142476800 | Third party | Medium |
| Cadence: 17 uploads / 30 d, 101K subs, 9.6M views (Social Blade); X clips 45-66 s several times a day on hot news (Cramer clip 1.86M views) | https://socialblade.com/youtube/handle/psyopanime | Medium |
| Length curve: Venezuela 1:21 -> Ep3 4:44 -> Ep4 9:38 -> Ep5 16:55 -> PNN#3 ~22:08. Views fall as length grows (661K -> 101K) | Grok table, YouTube pages | Medium |
Note: the older X handle in the prior doc (@psyophq) is the ops account ("ms.cleo"); the main account is @PsyopAnime.

## B. Per-video observations
Method limits, stated honestly: Gemini video engine failed on all three URLs (returned empty), so there is no cloud transcript and no
per-shot vision answers. I read 100 sampled frames each for two videos, and measured the audio of the third with ffmpeg
(EBU R128 trace). Third video (PNN "Reeducation Camp" D6qwHbGZ-LI) downloaded but did not finish in time: NOT WATCHED. No direct X clip
was obtained. Mouth-motion cannot be verified from still frames; I mark it "unverified" rather than guess.

### B1. "PsyopAnime - News Update" (https://www.youtube.com/watch?v=KGlKVkJkxM0, 1:47, 131 scene candidates = ~0.8 s/candidate)
Audio (measured, ffmpeg): integrated -7.1 LUFS (very loud, brick-walled), LRA 7.6 LU, **no silent gap anywhere** (silencedetect -45 dB: none).
Momentary loudness per 4 s: 0-4 s -23.5, 4-8 -18.5, 8-12 -14.5, 12-16 -10.5, 16-20 -8.6, **20-24 -3.0 (drop)**, then 4-8 LU hotter than the intro to the end.
= a 20 s riser/build, a drop at 0:20 exactly where frames show the first big explosion + white-flash frames, then a sustained bed.
Frames (timestamps from watch-video; ~35 of the 100 sampled frames fall between 0:20 and 0:43 = the densest burst):
- 0:00-0:20 cold open WITHOUT subtitles or narration frames: crow silhouette at dusk, goggled pilot eye, bird swarm over an airbase = story set-up in pure image.
- 0:20-0:26 explosions on the runway, white/near-white flash frames, impact frames with ink-outlined orange fireballs and debris shards (hand-drawn look).
- 0:26-0:33 extreme close-up of sunglasses; lens reflections show screens (red text, then a news feed). Reflection used as a transition into the "broadcast".
- 0:32-0:38 "Breaking News!!!" lower subtitle then a **real tweet screenshot** ("UKRAINIAN BIO-MIMETIC DRONE FLOCK STRIKES ...") glitched with
  datamosh scan-lines, subtitle "Russia's Engels-2 Air Force base are disinformation!". So: real 2D UI/screenshot elements are composited in; no 3D or photo
  footage seen anywhere in the 100 frames.
- 0:37-0:50 tunnel/vortex wipe (speed lines, eye in the tunnel) used as the scene transition, then a stern older man close-up (subtitle "they've blocked billions in investment.")
  and a suited sunglasses agent in a monitor-wall control room (recurring anchor-like set, wide static shot) with subtitle "The United States government has lifted the ban!".
- End card at ~1:42: logo only on black.
- Logo: top-left, small red/white "PSYOPANIME" with katakana and eye-triangle, on EVERY frame from 0:00. Subtitles: yellow, bottom-center, 1 clause, black outline.
- Shot reuse: none seen within 107 s (every sampled frame a different composition); the reuse is the control-room set and the sunglasses character.
- Continuity: hard cuts + flash/vortex wipes; the explosion burst shows 3-5 different shots inside one second (0:20-0:24) = fast cutting inside ONE generated action beat.

### B2. "WW3 Episode 5" (https://www.youtube.com/watch?v=i6Epuqc6Ikw, 16:55, 1175 scene candidates = ~0.86 s/candidate; no transcript, audio not analysed)
Reading two interleaved contact sheets (66 of 100 frames):
- Cold open is a **genre pastiche**: crusader-vs-Saracen cavalry battle with blood-splash close-ups, then title card "WW3" (logo plate, not over a face this time).
- Then a **named-ish cast with arcs**, not just news montage: a pilot pair in a cockpit (two faces), a bandaged soldier crawling a ridge at night
  ("He's around here somewhere!"), a parachute drop, an on-screen red digital countdown ("07:00" -> "00:02") over missile shots = a ticking-clock structure.
- Subtitles appear on only about a third of sampled frames (several clauses of narration/dialogue, e.g. "Tel Aviv reporting ballistic missiles have hit the city!",
  "We still have Hezbollah!"). Between them are long subtitle-free action runs. So **it is not wall-to-wall narration**: story beats are carried by images + character lines.
- Frames with a face and a subtitle (e.g. "Eventually, they won't blame Iran...", t~0:33) show a CLOSED mouth in the sampled still, consistent with voice-over or
  off-screen speech. I could not verify any true lip-sync from stills: UNVERIFIED. Their own Seedance integration implies it is available to them.
- Visual vocabulary reused ep to ep: extreme eye close-ups, glasses reflecting fire, aircraft carrier/jet low angles, explosion impact frames (hand-inked orange puffs), white flash,
  flag-draped skeleton (political-metaphor insert), monitor/countdown inserts. Logo top-left on every frame. No lower thirds.
- Shot cadence: 1175 candidates / 1015 s. With ~50% of candidates being camera-move artifacts, true cut rate is ~1 cut / 1.5-2 s, in bursts of 0.3-0.5 s on explosions.

### B3. Answers to the per-video questions (what I can and cannot say)
| Question | Answer |
|---|---|
| Mouths move when speaking? | UNVERIFIED (no audio-synced frames). Sampled faces with subtitles had closed mouths. Treat their talking shots as mostly cut-away/VO, with some in-model speech (Seedance). |
| Unique shots/min | 30-45 (a candidate every ~0.8-1.0 s; ~half are real cuts). No repeated shot seen in B1; repeated SETS and CHARACTERS across shots. |
| Talking vs b-roll length | Talking beats are held 2-4 s on a face with a subtitle; b-roll/action 0.3-1.5 s. Mixed 20/80 by screen time (estimate from sampled frames). |
| 3D / photo | None seen. Only 2D UI elements: real tweet screenshots, CRT/monitor frames, red countdown digits. |
| Anchor desk reuse | Control-room/monitor-wall plate recurs as a wide establishing shot (B1 ~0:45); in PNN Ep2 (prior doc) the anchor desk returns every few minutes. |
| Continuity between shots | Hard cuts and wipes (vortex, flash, reflection-dissolve). Not chained camera moves. Continuity is carried by character, palette, logo, music. |
| Text overlays | Logo top-left permanent; yellow bottom-center one-clause subtitles; "Breaking News!!!" lower tag; real-post screenshot with glitch. |
| Sound | Continuous loud score, 20 s build + drop (B1). Voice style/SFX density: not measurable without transcript. |

## C. Gap list vs our pipeline, ranked by what a viewer notices first
1. **Mouths do not move / talking shots missing.** Fix: Seedance 2.5 speech-in-quotes (native) with our ElevenLabs line as `audio_url` reference. Anchor + 2-3 other talkers per minute.
2. **Reused / repeated content.** Our v2v chains re-show the same frames. They show 30+ different compositions/min. Fix: multi-beat Seedance prompts + sheet-sliced stills; hard cap on source reuse.
3. **No music, flat audio.** We loop an SFX file as a "bed". They run a continuous riser->drop score. Biggest audible gap; no Router model (D4).
4. **Not a story, only a beat template.** They have a cold open set-up, recurring faces, ticking clock/countdown, genre pastiche vignette, button ending (D2).
5. **Motion reads as drifting photos / photoreal drift.** They get inked impact frames, flash frames, smear and speed-line action (D3).
6. **Branding persistence.** Logo top-left from frame 1 for the whole video; we show it only on cards. Free fix in ffmpeg.
7. **Real-UI inserts.** Tweet screenshot with datamosh glitch = free, high "news" signal. We have none.
8. **Cut tempo and flash frames.** Burst cuts of 0.3-0.5 s on explosions; white flash between. Free in ffmpeg.

## D. v2 pipeline design
### D0. Stage map with Router model per stage
| Stage | Model (Router id) | Notes |
|---|---|---|
| Script + story bible | `anthropic/claude-sonnet-5-5` | Writer v2 prompt (D2). One call per episode + one per beat sheet. |
| Style sheet / character sheets (once per series) | `bfl/flux-3-image` (reference images OK) | Locked once, never regenerated (Seedance bible rule). 1 per character, front + 3/4. |
| Contact sheets: 3x2 panels per call, sliced | `bfl/flux-3-image` | 6 distinct compositions per generation. Codex plan + Seedance bible agree. |
| Hero motion + speaking shots | `byteplus/dreamina-seedance-2-5-260628` (i2v, `generate_audio`, `audio_url`) | 15 s per call, 4-beat prompt. Iterate at `draft:true` 480p, then final with `draft_task`. |
| Fallback motion engines | `minimax/minimax-h3` (fight choreography), `kling/kling-v3`, `bfl/flux-3-video` | Per bake-off result (D5). |
| Lip-sync fix-up for a non-Seedance clip | `kling/videos-lip-sync` first, else `synclabs/sync-3`, `veed/lipsync-2.0` | Video + exact audio. Test one 8 s sample first (Codex). |
| Voice | `elevenlabs/eleven_v3` (or `eleven_v4`, same Router namespace) | One narrator + 2-3 character voices. Same file drives Seedance `audio_url` and subtitles. |
| SFX | `elevenlabs/eleven_sfx_v2` | Pack once: impact, riser, CRT click, static, radio. |
| Music | NONE on Router (D4) | Bundled licensed tracks + ffmpeg stems. |
| Edit | ffmpeg | Cuts, flash frames, impact shake, glitch, logo, subtitles, mix, grade. |

### D1. Shot budget per finished minute (target 35-45 cuts, <=25 generations)
Assume 4 Seedance calls x 15 s (each 4 beats -> 4-6 trimmed shots) plus sheet stills. Seedance price/clip is UNKNOWN until the bake-off: read it first.
| Shot type | Cuts | Screen time | Source | Gens |
|---|---:|---:|---|---:|
| Talking (anchor + 2 characters), true mouth motion | 6 | 12 s | Seedance 2.5 i2v + our voice as `audio_url`; 2 calls of 15 s, cut into 3 angles each by re-cropping + CRT insert | 2 |
| Action beats (explosion, jet, chase) | 12 | 14 s | 2 Seedance calls x 15 s, 4 beats each, trim to hits | 2 |
| Cutaways: faces, eyes, props, maps | 14 | 18 s | 3 contact sheets (6 panels each), ffmpeg zoompan/parallax/shake | 3 |
| Real-UI inserts: headline card, tweet-style card, countdown | 4 | 6 s | Rendered in HTML/ffmpeg, glitch filter | 0 |
| Flash / impact / wipe frames | 6 | 4 s | ffmpeg (white flash, vortex wipe = one reusable generated clip) | 0-1 |
| Style/char sheets amortised, script, VO, SFX | n/a | n/a | once per episode | ~6 |
| **Total** | **42** | **54 s + 6 s logo/end card** | | **~14-17** |
Spare 8 gens for retakes. 5-min episode = repeat the template 5x, sharing sheets and voices.

### D2. Storyline (what they do; writer-prompt v2)
Evidence: cold open that sets a mystery in images (crow/airbase; crusader battle), a recurring cast (suited sunglasses agent, stern old statesman, pilot pair, bandaged soldier),
a countdown clock, a genre-pastiche vignette that stands in for the moral, headline claim literalised as "briefing", subtitles saying the absurd with a straight face.
Writer v2 contract (Sonnet 5.5, one structured call):
- Series bible (fixed): 4 recurring fictional characters with name, trait, want, visual tag line, voice id. Example roles: the Handler (suited, sunglasses), the General, the Pilot, the Anchor.
- Episode = 5 acts: (1) 0-8 s image-only cold open, no words; (2) title + brand + thesis sentence; (3) escalation with a visible clock or counter; (4) pastiche vignette that dramatises the moral
  with a different genre look; (5) twist line + button + "next hour" cliffhanger. One running gag carried across episodes (e.g. a recurring countdown).
- Each beat: `{id, act, speaker, line (<= 12 words), visual_subject, shot_type, source: seedance|sheet|ui|ffmpeg, camera, sfx, music_cue}`.
- Callback rule: every episode must reuse >=1 element from the previous episode (a face, a gag, a clock).
Script JSON v2 sketch: `{series:{characters:[{id,name,want,look,voice}]}, episode:{headline,thesis,acts:[{n,goal,beats:[...]}], cliffhanger, callback}}`.

### D3. B-roll and animation quality
What they do: ink-outlined fireballs and impact frames, white flash between shots, speed-line whips, extreme eye/glasses close-ups, vortex wipe,
screens-in-reflections, CRT glitch, camera push on static plates. Mostly real model motion for action beats; editing motion for cutaways.
Our split: generated motion = action beats + talking shots (4 Seedance calls/min). Editing motion = everything else.
Cheap ffmpeg moves (all free): `zoompan` slow push; two-layer parallax; 1-2 px cel shake on impacts; 2-frame white flash; step to 12 fps on action
("posterize time", ScreenWeaver) and 8 fps on dialogue to fake limited animation; datamosh/scanline glitch for UI; vortex wipe as one reusable generated clip; speed-line overlay PNG pack; film grain + teal/orange grade.
Prompt pattern (community, Iancu_ai https://x.com/Iancu_ai/status/2117254651112485286): "Animated on twos and threes, smear frames, speed lines, impact frames... No 3D, no CG, no photographic textures, no soft glossy AI polish, no slow motion." One verb per shot, one camera move, add "no frozen figures" (Seedance bible p.10).

### D4. Music (no model on Router)
Checked live: 253 Router ids, none music/lyria/suno. `byteplus/seed-audio-1.0*` is TTS. `elevenlabs/eleven_sfx_v2` is SFX only. Our ElevenLabs key is FREE tier (10K chars), so ElevenLabs Music is unavailable.
Evidence they do: riser then drop at 0:20, loud and continuous (B1).
Options ranked for noon tomorrow:
1. **Bundle 3-4 existing synthwave/cyberpunk tracks with licenses printed on the page** (Grok found; none is CC0): Pixabay "Dark Synthwave" by Kulakovka
   https://pixabay.com/music/synthwave-dark-synthwave-624644/ (Pixabay license, no attribution, not Content-ID-registered), Pixabay "Dark Cyberpunk Trailer" (Content ID registered: claim risk),
   Karl Casey "Burnt Circuit" (credit required, YouTube description). Recommended: Kulakovka as bed. License caveat is mine to flag once: Pixabay licence is not CC0; fine for a hackathon video, keep a credits line.
2. **Build our own riser/drop with ffmpeg + eleven_sfx_v2**: generate 3 loops ("low analog synth drone pulse, ominous 90s anime OST", "taiko hits", "rising tension sweep 20 s") and layer them with `acrossfade`/sidechain ducking; guarantees license, 100% in-Router, weaker than a real track.
   Exact call: `POST /v2/models/elevenlabs/eleven_sfx_v2` body `{"text": "...", "duration_seconds": 20, "prompt_influence": 0.6}` (check schema first, same wrapper as `audio.py`).
3. Seedance native audio can generate music, but the bible says generated music is unlicensed/timbre-drifts; use only as scaffolding.
Recommendation: **option 1 for the bed + option 2 for stingers/risers**, structure the edit as a 4-part score: riser (0-8 s), drop on first impact, sustained bed ducked under VO (`mix-voice-over-music`), hard cut at the button.
Not recommended by tomorrow: Suno (paid tier needed), MusicGen on Modal (setup time, NC license).

### D5. Bake-off for tomorrow 8 AM (same still, same prompt, 5 s)
Why: community says Seedance 2.5 wins anime lock + facial acting (Vidney scored 22.5/25 vs H3 21, Kling 18, Veo 17; https://vidney.ai/blog/best-ai-anime-video-generator),
H3 better for fight choreography, Kling for stable motion. This is vendor/blog evidence, not our footage, so confirm with our own still.
| # | Model id | Mode |
|---|---|---|
| 1 | `byteplus/dreamina-seedance-2-5-260628` | i2v, `generate_audio:true`, draft 480p first |
| 2 | `minimax/minimax-h3` | first frame |
| 3 | `kling/kling-v3` | i2v |
| 4 | `bfl/flux-3-video` | i2v (our current engine) |
| 5 | `ltx/ltx-2-5-pro` | t2v (no i2v on our list) |
| 6 | `wan/wan2.7-i2v` | i2v |
| 7 | `veo/veo-3.1-fast-generate-001` | i2v |
| (8) | `kling/kling-3.0-turbo` | i2v, only if cheap |
Fixed inputs: one FLUX 3 anime still of a talking character with an unambiguous face; prompt "Flat cel-shaded 2D anime, 1990s OVA ink linework... animated on twos, smear frames. The character speaks exactly: '...' (12 words). Slow push-in. No frozen figures." Send the same ElevenLabs line as audio ref where supported.
Rubric (score 0-5 each, judged by us + a vision model on 16 frames, blind to model name): (a) mouth visibly moves and matches audio, (b) stays 2D cel, no photoreal/3D drift, (c) face/outfit identity holds frame 1 vs 5 s,
(d) motion has acting (blink, head turn, hair/cloth) not just camera drift, (e) readable at 12 fps posterise, (f) cost and latency per 5 s. Winner = highest (a)+(b)+(d); tie goes to cheaper. Log cost per clip first: abort anything above ~$1 per 5 s without telling the operator.
Failure modes to watch: Seedance may reframe/change the pose from the still (CinemaDrop); photoreal reference faces refused ~4 in 5 (r/Seedance_AI), anime stills had 0 rejections in Vidney's 38 runs;
Seedance output is 24 fps and the edit adds the chop; edit mode breaks lip sync (r/Seedance_AI 1 Sep 2026), so use i2v not edit.

### D6. Codex second opinion, merged (full answer was captured; key deltas)
- Codex (terra, read-only, no Seedance knowledge) designed 39 cuts / 19 gens: 5 anchor shots from ONE lip-synced plate (`kling/videos-lip-sync`, video+audio) with different crops, 4 FLUX i2v beats, 22 sheet-sliced cutaways across five 3x2 sheets, 8 wide-frame crops.
  Its rules I kept: render VO first and cut to word timestamps; no v2v chaining (each shot from its own reference; face drift); log crop/panel IDs and cap reuse; sheets with hard gutters, no lettering; test lip-sync on an 8 s sample before building;
  alternate face -> evidence -> geography -> consequence; replace failed motion with the still + ffmpeg movement.
- Where I differ: Codex did not know Router serves Seedance 2.5 with native speech and `audio_url`; if the bake-off confirms it, Seedance replaces the "anchor plate + lip-sync model" route and also yields the multi-beat shots. Keep Codex's plan as the **fallback (Plan B)** if Seedance fails on cost or refusal.

## E. Build order for tomorrow (submission 12:00 ET Nov 3). Times are builder-hours; assume one builder + Router calls in the background.
| When | Task | Done when |
|---|---|---|
| 07:30-08:00 (0.5 h) | Read Router price for Seedance 2.5 and the 7 others; load schema into `router_client.py` (Seedance body: `content[]`, `duration`, `generate_audio`, `ratio`, `draft`). | One draft call returns a 480p mp4 |
| 08:00-09:00 (1 h) | Bake-off (D5): same still, 7 clips, rubric scored, engine chosen. In parallel: write one ElevenLabs line. | Table with scores in `docs/` |
| 09:00-10:00 (1 h) | Writer v2: series bible + 5-act script JSON, Sonnet call, validator for schema. | Script for tonight's news item parses |
| 10:00-10:30 (0.5 h) | Music kit: download Kulakovka track (license URL in credits.txt), generate 3 `eleven_sfx_v2` stingers, build 4-part score cue. | 60 s score file at -14 LUFS |
| 10:30-11:15 (0.75 h) | Style + character sheets once (FLUX 3), 3 contact sheets, auto-slice, reject bad panels. | 18 usable stills with IDs |
| 11:15-12:15 (1 h, overlaps) | Seedance calls: 2 talking + 2 action, draft first then final. | 4 clips trimmed to beats |
| 12:15-13:15 (1 h) | ffmpeg v2: persistent logo top-left, yellow subtitles, flash frames, impact shake, glitch UI cards, 12 fps step, ducked mix. | Rendered 60 s master |
| 13:15-14:00 (0.75 h) | Watch full render with the 16-frame checker + eyes: reuse count, mouth moves, music drop. Fix top 3 issues. | Checklist passes |
That is ~6 h; leaves slack to 12 pm even with Router delays. Cut order if late: (1) drop the 5-min version, ship 1-min, (2) drop UI inserts, (3) drop bake-off to 3 models.

## Open items and unknowns (not guessed)
- Whether their talking shots are true lip-sync, and their lip-sync/voice/music tools: not public, not verified.
- Seedance 2.5 price per second on the Router: unread (needs one `catalog_get`-style call or the first draft bill).
- Third video (PNN "Reeducation Camp") and any X clip not watched. Gemini engine returned empty for YouTube URLs this session.
- Router Seedance behaviour on anime stills with `audio_url` is from docs and community, not yet tested by us.
