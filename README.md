# Shot Director

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
