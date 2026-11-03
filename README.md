# Anime News

Anime News turns the day's top headline into a short anime news episode, and its studio lets you direct single shots by hand. Live at https://your-site.example. The engine and CLI underneath were built as **Shot Director**, its former working name, so the commands (`shot-director`, `shot-director-episode`, `shot-director-cut`), the Python package and the `SHOT_DIRECTOR_*` variables keep that name. Brand rules: THEME.md, "Brand: Anime News".

An open-source mini "Cinema Studio": pick a start frame, a camera body and a camera move, and get an AI video shot. Queue three shots and stitch them into one sequence.

Built for **Open Source Higgsfield: ComfyUI Build Night** (NYC, 2026-11-02). Models run through **Comfy Router**: `bfl/flux-3-image` for start frames, **FLUX 3 Video** (`bfl/flux-3-video`) as the default video engine, and LTX 2.5 or MiniMax H3 selectable per shot.

**Why it is new:** a camera-preset compiler turns a body + move into a model-ready prompt, and shot-to-shot continuation (FLUX 3 Video `v2v`) feeds each shot the previous one, so a 3-shot sequence holds continuity; each shot can pick the engine that suits it.

**ComfyUI workflows:** `workflows/` holds the same shot as ComfyUI API-node graphs (FLUX 3 image → FLUX 3 Video, and an LTX 2.5 preview); see `workflows/README.md`.

## Engines (pick per shot)

| Engine | Best for | Time per shot (live) |
| --- | --- | --- |
| `bfl/flux-3-video` (default) | Takes the start frame; continues from the previous shot. | about 2 min |
| `ltx/ltx-2-5-fast` | Fastest. Text-only, ignores the frame. Previews and establishing shots. | about 30 s |
| `minimax/minimax-h3` | Takes the frame, returns sound. A hero shot with audio. | about 5 min |

Continuation applies per shot: a FLUX 3 Video shot continues from the last queued shot; LTX and MiniMax shots start fresh.

## How it works

1. **Start frame:** upload an image, or generate one with FLUX 3.
2. **Camera body:** Modern digital, 35mm film, Super 8, or DV camcorder. Each one adds a look to the prompt.
3. **Camera move:** Dolly in, Dolly out, Orbit, Crane up, Handheld, Whip pan, Crash zoom, or Static. Each one adds a motion phrase to the prompt.
4. **Engine:** FLUX 3 Video (default), LTX 2.5 Fast or MiniMax H3, chosen per shot.
5. **Generate shot:** the compiled prompt (plus the start frame, on models that take one) goes to the video model on Comfy Router. The clip joins a queue of up to 3 shots.
6. **Stitch:** ffmpeg joins the queued shots into one H.264 mp4.

| File | Job |
| --- | --- |
| `src/shot_director/presets.py` | Camera bodies, camera moves, and the prompt compiler |
| `src/shot_director/router_client.py` | Comfy Router client (live) and an offline mock with the same interface |
| `src/shot_director/stitch.py` | ffmpeg: stitch clips, render mock frames and clips |
| `src/shot_director/app.py` | FastAPI: API, static UI, generated media |
| `src/shot_director/static/index.html` | The whole UI, one file, no build step |

## Setup

Needs [uv](https://docs.astral.sh/uv/) and `ffmpeg` on PATH (macOS: `brew install ffmpeg`).

```bash
uv sync
uv run shot-director            # http://127.0.0.1:8000  (--port 8010 to change)
```

**Mock mode (default, works offline):** with no `COMFY_API_KEY`, every frame and clip is a placeholder rendered locally by ffmpeg (a solid colour with the preset name drawn on it, 2 s per clip). The whole flow, including stitching, works without network or credits.

**Live mode:** set a Comfy platform API key (create one at https://platform.comfy.org, API Keys):

```bash
export COMFY_API_KEY=...        # or put COMFY_API_KEY=... in ~/.claude/.secrets/services/comfy/default.env
uv run shot-director
```

The header badge shows `MOCK MODE` or `LIVE`. `SHOT_DIRECTOR_MOCK=1` forces mock mode even when a key is set.

Optional env: `SHOT_DIRECTOR_VIDEO_MODEL` (default `bfl/flux-3-video`), `SHOT_DIRECTOR_IMAGE_MODEL` (default `bfl/flux-3-image`), `COMFY_ROUTER_BASE` (default `https://api.comfy.org`), `SHOT_DIRECTOR_MEDIA` (default `./media`).

## API

| Method | Path | Body | Returns |
| --- | --- | --- | --- |
| GET | `/api/presets` | | bodies, moves, engines, mode, model ids |
| POST | `/api/frame` | multipart `file` | `{frame, url}` |
| POST | `/api/frame/generate` | `{prompt, body}` | `{frame, url}` |
| POST | `/api/shot` | `{body, move, scene?, frame?, engine?}` | shot with `engine`, `continued` (409 when 3 are queued, 400 on an unknown engine) |
| GET / DELETE | `/api/queue` | | queued shots / clear |
| POST | `/api/stitch` | | `{url, file, shots}` |

## News → Episode (a self-running anime news channel)

Shot Director can direct itself from the news. It pulls the top trending headline (Google News top-stories RSS, falling back to the Hacker News front page; no API key, no X/Twitter), plans an anime satire episode around it, renders it and stitches it into one mp4. Left running, it is a news-directed anime channel in the spirit of @PsyopAnime's anime satire of current events (structure only; see `docs/psyopanime-formula.md`).

### Where the stories come from

Two strategies pick the story when no `--headline` is given:

- **`viral`** (default when the last30days engine is installed): `viral.py` runs [last30days](https://github.com/mvanhorn/last30days-skill) once per topic (`breaking news, politics, tech, AI, crypto, sports, celebrity`; override with `SHOT_DIRECTOR_VIRAL_TOPICS`, comma-separated) over the last 48 hours across Reddit, X, YouTube, TikTok and Hacker News, four topics at a time (about 2 minutes cold). Each post's likes, upvotes, points, reshares, comments and views fold into one engagement number; posts nobody engaged with, replies and fragments are dropped, near-duplicate titles are merged, and the death/violence filter from `news.top()` applies (plus a few words social posts use, such as genocide or hostage). The pick is the most-engaged story not already in the ledger, and its platform and engagement reach the writer as the episode's source line, e.g. `via Reddit, 16.9K upvotes`. If last30days is missing, has no keys or fails, it logs one `[viral]` line and the headline feeds take over.
- **`headline`**: Google News top stories, then the Hacker News front page (no keys).

Switch: `--news-strategy viral|headline` (CLI), `"news_strategy"` (POST `/api/episode`), `GET /api/news?strategy=viral` (public; serves only the warm cache with `cached` and `fetched_at`, never starts the engine; cold cache answers 202 with no items), `POST /api/news/refresh` (access token + per-token call limit; starts one background fetch, 409 while one is running), or `SHOT_DIRECTOR_NEWS_STRATEGY`. **Cache:** viral results are kept in `<media>/viral-cache.json` for 30 minutes (media = `SHOT_DIRECTOR_MEDIA`, default `./media`), so repeated episode runs and the hourly loop do not re-query. The engine is found at the newest `~/.claude/plugins/cache/last30days-skill/last30days/*/skills/last30days/scripts/last30days.py` (override `SHOT_DIRECTOR_LAST30DAYS`; its Python 3.12+ via `SHOT_DIRECTOR_LAST30DAYS_PYTHON`). It never routes through OpenRouter: the engine's OpenRouter endpoint is pointed at a closed local port, so its optional LLM rerank falls back to its local scorer.

**The episode.** A deterministic template, fully offline (no LLM). The spine is an original deadpan Anchor at a desk in front of world-map monitors who reads the headline as a grave briefing line; the cutaways take it literally with absurd props (gravitas vs absurdity is the joke). Scene 1 opens on an extreme close-up of the Anchor's eyes with our series title, `THE HOURLY BRIEFING`. Every prompt leads with "2D cel anime frame, drawn animation, not a photograph" and uses the `anime` camera body (late-80s/90s OVA cel look, heavy ink lines, film grain, VHS softness, teal shadows vs burnt-orange highlights). Each scene carries a `caption` (one short clause) and a `narration` line, each shot its own `caption`, for subtitles and a voiceover track.

**Rule: SFW and no real people's likeness.** Every character is an original archetype; the headline is on-screen text, never a depicted person. No weapons, no gore, no named anime or studio styles, none of PsyopAnime's logo, series names or character designs. `news.top()` skips stories about deaths and violence (satire of a tragedy is not the joke); if nothing else is left it returns nothing, so `/api/episode` answers 503 and the CLI exits 1, rather than render a somber story.

**Length.** An episode of N minutes is S scenes × C clips of `--clip-seconds` (default 15; FLUX 3 Video takes 5 to 20 s per clip). Total clips = exactly ceil(N × 60 / clip seconds): full scenes of 4 clips, the last scene takes the remainder (no padding). The clip length is first clamped to what the engine renders (FLUX 3 Video 5 to 20 s, MiniMax H3 4 to 15 s, LTX 2.5 snapped to its allowed values), and the plan reports both `clip_seconds` (effective) and `clip_seconds_requested`. Clips inside a scene render in order and (FLUX 3 Video, live) each continues from the previous one; scenes render in parallel (`--parallel`, default 3) and are stitched in order. One scene uses the 1-minute beat sheet (cold open, thesis, vignette, punchline); longer episodes are cold open → segments (literal headline, genre-pastiche standoff, rapid montage) → sign-off.

| Minutes | Clips (15 s) | Scenes × clips | Router calls (frames + clips) | Clips rendered back to back (parallel 3) |
| --- | --- | --- | --- | --- |
| 1 | 4 | 1 × 4 | 5 | 4 |
| 2 | 8 | 2 × 4 | 10 | 4 |
| 3 | 12 | 3 × 4 | 15 | 4 |
| 5 | 20 | 5 × 4 | 25 | 8 (two waves of scenes) |

Wall-clock ≈ one start frame (about 45 s) + "back to back" × the time of one clip. A 5 s FLUX 3 Video clip took 1.5 to 2 min live; 15 s clips are not measured yet, so expect several minutes each. A 1-minute episode is therefore tens of minutes, a 5-minute one roughly twice that.

| Method | Path | Body | Returns |
| --- | --- | --- | --- |
| GET | `/api/news?strategy=viral\|headline` | | `{items: [{title, link, source, published, feed}], strategy}` (10; viral items add `engagement`, `source_text`, `topic`) |
| POST | `/api/news/refresh` | | 202 `{refreshing: true}`; 409 while a fetch runs; token-gated, rate-limited |
| POST | `/api/episode` | `{headline?, source?, engine?, body? = "anime", minutes? = 1, clip_seconds? = 15, parallel? = 3, cut? = true, writer? = true, style_sheet? = true, render? = true, news_strategy? = "viral" \| "headline"}` | `{headline, source, series, logline, scenes: [{kind, caption, narration, frame, shots}], shots, sequence: {url, file}, sequence_raw: {url, file}, cut: {duration, shots, cuts_per_minute, avg_shot_s, audio}, errors}`; with `render: false`, the plan only. Uses its own shot list, never the 3-slot UI queue. |
| GET | `/api/episodes` | | `{episodes: [ledger entries], auto}` |

**Script writer and style sheet.** Before rendering, the template plan is rewritten by the Router script writer (`writer.py`, one chat-model call, cached per headline in `<media>/scripts-cache.json`, see `docs/WRITER.md`): logline, each scene's setting, caption and narration, each shot's move, caption and prompt. Any writer failure (mock mode, no key, bad reply, wrong counts) keeps the template; the plan's `script` field says which one ran. Anchor-desk scenes (cold open, sign-off, the 1-minute single scene, writer beats `anchor_desk`/`thesis`) take their start frame from the series style sheet (`style.py`, `<media>/style-sheet.png`, made once), so the Anchor and the look stay the same across episodes; crowd and vignette scenes use a plain frame, because the reference copies the Anchor's face onto everyone. `--no-writer` / `"writer": false` and `--no-style-sheet` / `"style_sheet": false` turn them off.

**The cut.** After the clips exist, the episode goes through the anime cut (`edit.assemble`, see `docs/EDIT.md`): yellow one-clause subtitles from each scene's shot captions, the scene's narration line read by ElevenLabs on the Router, one music bed and two SFX (whoosh + impact on scene changes), fast cuts (about 35 a minute). The plain stitch is kept next to it as `episode-<id>.sequence_raw.mp4`. Sound fails soft (silent track in mock mode or without a key); if the cut itself fails, the raw stitch is the episode. A scene whose Router job fails keeps the clips it rendered and the episode still cuts; failures are listed in `errors`. `--no-cut` (CLI) or `"cut": false` (API) skips the cut; it adds about 1 to 2 minutes of ffmpeg time, inside the same synchronous request.

**CLI** (one episode, or the channel itself):

```bash
uv run shot-director-episode                                  # top new story -> 1-minute episode
uv run shot-director-episode --minutes 5                      # target length
uv run shot-director-episode --headline "Mayor bans Mondays" --mock --out /tmp/ep
uv run shot-director-episode --loop --every 60m               # self-running: a new story every hour
# flags: --headline --source --news-strategy viral|headline --engine --body --minutes --clip-seconds --parallel --mock --out DIR --no-cut --no-writer --no-style-sheet --loop --every
# one-shot prints the episode mp4 path, then one JSON summary line; --loop prints one JSON line per tick
```

**Inside the app:** `uv run shot-director --auto` (or `SHOT_DIRECTOR_AUTO=1`) runs the same loop in a background thread; `SHOT_DIRECTOR_AUTO_EVERY` (default `60m`) and `SHOT_DIRECTOR_AUTO_MINUTES` (default `1`) tune it.

**Ledger:** `episodes.json` in the media dir (`--out`). Before any paid call, the story is picked and reserved under a lock as a `rendering` entry; it becomes `done` (file, path, url, minutes, scenes, clips, clip seconds, engine, mode, render seconds) or `failed` (error). Auto-pick skips every headline already in the ledger, so concurrent runs (auto thread, API, loop) never pay for the same story; an explicit `headline` is refused only while it is still rendering. The lock is per process: run the loop either in the app (`--auto`) or as the CLI, not both on one media dir.

**Alternative: cron** instead of `--loop`:

```cron
0 * * * * cd /path/to/shot-director && uv run shot-director-episode >> episodes.log 2>&1
```

**Prior art:** fal.live / Rehan Sheikh's "infinite interdimensional cable" (chat-directed endless AI TV on MiniMax H3 Max) and levelsio's Infinite Slop run an endless stream steered by viewers or chat. Shot Director is news-directed and episodic instead: one episode per story, hourly or daily, with cinematography presets and shot-to-shot continuity, open source on ComfyUI's Router.

## Live-mode notes (Comfy Router, verified 2026-11-02)

- Pick the video model with `SHOT_DIRECTOR_VIDEO_MODEL` (or `engine` per `/api/shot` request; `model` still works as an alias): `ltx/ltx-2-5-fast`, `ltx/ltx-2-5-pro`, `bfl/flux-3-video`, `minimax/minimax-h3`. Anything else is rejected as unsupported.
- Start frames: **FLUX 3 Video** (`i2v`, frame sent as `keyframes`) and **MiniMax H3** (`first_frame` content item) use it. **LTX 2.5** on Router is text-to-video only, so the frame is ignored (a warning is logged).
- With `bfl/flux-3-video`, shots 2 and 3 continue from the previous queued shot (`v2v`, `start_video`), so the sequence flows as one take.
- Defaults (tunable constants at the top of `router_client.py`): FLUX 3 image 1k 16:9 without web grounding; LTX 4 s 1280x720 24 fps; FLUX 3 Video 5 s `hd` 16:9; MiniMax 4 s 768P. Audio generation is off for LTX and FLUX; MiniMax H3 returns its own AAC track (the shot keeps it, the stitched sequence is video-only).
- Router output URLs expire (BFL images in about 10 minutes, everything within 24 h), so the app downloads each result into `media/` as soon as the job completes.

## Verified

Run on 2026-11-02 (macOS 13, ffmpeg 7, uv 0.7.8), mock mode, no API key:

```bash
uv sync
uv run shot-director --port 8765 > /tmp/sd_server.log 2>&1 & echo $! > /tmp/sd_server.pid
B=http://127.0.0.1:8765
curl -s $B/api/presets | grep -o '"mode":"[a-z]*"'                     # "mode":"mock"
F=$(curl -s -X POST $B/api/frame/generate -H 'Content-Type: application/json' \
  -d '{"prompt":"astronaut on a neon rooftop","body":"35mm"}' | python3 -c 'import sys,json;print(json.load(sys.stdin)["frame"])')
curl -s -X POST $B/api/shot -H 'Content-Type: application/json' -d "{\"body\":\"35mm\",\"move\":\"dolly_in\",\"frame\":\"$F\"}"
curl -s -X POST $B/api/shot -H 'Content-Type: application/json' -d '{"body":"8mm","move":"orbit"}'
curl -s -X POST $B/api/shot -H 'Content-Type: application/json' -d '{"body":"dv","move":"crash_zoom"}'
curl -s -X POST $B/api/shot -H 'Content-Type: application/json' -d '{"body":"dv","move":"static"}'   # 409, queue full
OUT=$(curl -s -X POST $B/api/stitch | python3 -c 'import sys,json;print(json.load(sys.stdin)["file"])')
ffprobe -v error -show_entries format=duration:stream=codec_name,width,height -of compact media/$OUT
#   stream|codec_name=h264|width=1280|height=720
#   format|duration=6.000000
curl -s -X POST $B/api/frame -F "file=@media/$F"                       # upload path works too
kill $(cat /tmp/sd_server.pid)
```

Live mode, 2026-11-02, one call each through `LiveRouter`: FLUX 3 image (768sq, 42 s, 1024x576 PNG); LTX 2.5 fast (2 s, 28 s, 1280x720 MP4); FLUX 3 Video i2v from that PNG (5 s `hd`, 125 s, 1280x704 MP4); MiniMax H3 first frame (4 s 768P, 294 s, 1344x768 MP4). FLUX 3 Video `v2v` from that 5 s clip (raw base64 `start_video`, 5 s `hd`, 93 s, 1280x704 MP4): its first frame matches the input's last frame (SSIM 0.85), so the next shot picks up where the last one ended.

## License

MIT, see `LICENSE`.

## Public deploy

Live as Anime News at https://your-site.example; finished episodes at https://your-site.example/episodes (https://your-site.example is an alias). Browsing is open; anything that spends Comfy Router credits needs the access token (`SHOT_DIRECTOR_ACCESS_TOKEN`, sent as `X-Access-Token`). Each token gets 6 episode requests and 60 frame or shot calls per hour (`SHOT_DIRECTOR_EPISODES_PER_HOUR`, `SHOT_DIRECTOR_CALLS_PER_HOUR`). With the variable unset (local dev) nothing changes. The public pages load PostHog (project 530416, `app: shot-director`); it never loads on localhost. Docker image, compose file, redeploy and token rotation: [deploy/README.md](deploy/README.md).
