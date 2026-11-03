# ComfyUI workflows

Shot Director runs its models through Comfy Router. These two workflows rebuild one Shot Director shot inside ComfyUI with Comfy's partner (API) nodes, so the same shot can be made, inspected and tweaked as a node graph.

Both files are in **ComfyUI API (prompt) format**: a map of node id to `class_type` + `inputs`, the same format the Comfy Hub stores for its cloud examples.

| File | What it does | Nodes (`class_type`) | Price per run (from the node price badges) |
| --- | --- | --- | --- |
| `shot-director-flux3-shot.json` | Text prompt → FLUX 3 start frame (16:9, 1K) → Flux 3 Image to Video (5 s, 720p, with audio) → saved frame + saved mp4 | `Flux3ImageNode` → `SaveImage`; `Flux3ImageNode` → `Flux3ImageToVideoNode` → `SaveVideo` | about $0.07 (frame) + $1.22 (5 s × $0.2431/s) |
| `shot-director-ltx-preview.json` | Text prompt → LTX 2.5 Fast text-to-video (4 s, 1280x720, 25 fps) → saved mp4. The cheap preview shot | `LtxApi25TextToVideo` → `SaveVideo` | about $0.74 (4 s × $0.1859/s) |

## How it maps to the app's three stages

| App stage | In the app | In `shot-director-flux3-shot.json` |
| --- | --- | --- |
| 1. Preset | `presets.compile_prompt(body, move, scene)` joins scene + camera move + camera body | Already applied: node 3's `prompt` is the exact output of `compile_prompt("35mm", "dolly_in", "A lone courier in a yellow rain jacket ...")` (checked by running the compiler). Node 1's prompt carries the same scene and the 35mm look for the still. To change the preset, swap the move and body sentences in node 3's prompt for another pair from `src/shot_director/presets.py`. |
| 2. Frame | `bfl/flux-3-image` on Comfy Router, `FRAME_RESOLUTION = "1k"` | Node 1 `Flux3ImageNode` (`resolution: "1K"`, `aspect_ratio: "16:9"`), saved by node 2 `SaveImage` |
| 3. Clip | `bfl/flux-3-video` on Comfy Router, `FLUX_VIDEO_DURATION = 5` | Node 3 `Flux3ImageToVideoNode` with the frame on `keyframes.image_0`, `duration: "5"`, saved by node 4 `SaveVideo` |

The LTX file matches the app's `ltx/ltx-2-5-fast` engine (text only, ignores the frame, `LTX_DURATION = 4`).

## Run it

1. Open ComfyUI (Desktop, a local install recent enough to have the FLUX 3 nodes, or Comfy Cloud).
2. Drag the JSON file onto the canvas (or Workflow → Open). ComfyUI loads API-format files and lays the nodes out.
3. Partner nodes bill against Comfy credits: sign in to your Comfy account in ComfyUI, or add a Comfy API key in Settings (keys are created at platform.comfy.org, Profile → API keys).
4. Edit the prompt in node 1 / node 3 if you like, then Queue (Run). Output lands in `output/shot-director/frame_*.png` and `output/video/shot_director_flux3_i2v_*.mp4`.

Headless, against a running local ComfyUI: POST `{"prompt": <file contents>, "extra_data": {"api_key_comfy_org": "<your Comfy API key>"}}` to `http://127.0.0.1:8188/prompt`.

## Where the graphs come from

- **Base graph and file format:** the Comfy Hub's official FLUX 3 Video example, https://comfy.org/hub/workflows/flux-3-video-text-to-video-182021fcf3dc/ (creator: ComfyUI). Its embedded cloud workflow is `Flux3TextToVideoNode` → `SaveVideo`. `SaveVideo` (`filename_prefix`, `format: "auto"`, `format.codec: "auto"`) and the Flux 3 video settings (`aspect_ratio`, `duration`, `resolution`, `generate_audio`, `safety_tolerance`, `seed`) are kept as they are there. The hub has no FLUX 3 *image-to-video* or FLUX 3 *Image* (non-Klein) example, so the start-frame node was added and the video node swapped for its image-to-video sibling, with input names taken from the node source below.
- **LTX preview:** the Comfy Hub's LTX 2.5 Pro text-to-video example, https://comfy.org/hub/workflows/ltx-2-5-pro-text-to-video-93c182d3aefb/. Graph kept as is; only the prompt, `model` (Pro → Fast), `model.duration` (5 → 4) and the filename prefix changed.
- **Node contracts:** docs https://docs.comfy.org/built-in-nodes/Flux3ImageNode.md, https://docs.comfy.org/built-in-nodes/Flux3ImageToVideoNode.md and https://docs.comfy.org/built-in-nodes/LtxApi25TextToVideo.md, checked against the node source in `Comfy-Org/ComfyUI` (`comfy_api_nodes/nodes_bfl.py`, `comfy_api_nodes/nodes_ltxv.py`, `comfy_api/latest/_io.py`, fetched 2026-11-02).

Two details the docs page alone gets wrong or leaves open, settled from the source:

- The keyframe slot of `Flux3ImageToVideoNode` is an Autogrow input with `prefix="image_"`, named `image_0 ... image_9` (`range(max)`), so in API format the first keyframe is `keyframes.image_0`. The docs page says `image_1`.
- `placement` is a DynamicCombo: in API format its value is the plain option string (`"spread across the clip"`), and option-specific fields are dotted (`placement.times`), the same pattern as `model` / `model.duration` in the hub's LTX example.

`grounding` on `Flux3ImageNode` is set to `false` (default `true`) so the frame follows the preset prompt without a web/image-search pass first.

## Tested status

**Not run in ComfyUI.** No ComfyUI install or `comfy` CLI exists on the build machine, and running a full workflow on Comfy Cloud needs a Comfy Cloud sign-in (`comfy cloud login`, browser OAuth) that was not done during the hackathon. No credits were spent on these files.

What was checked:

- Both files parse as JSON.
- Every node's inputs were checked against the node schemas in the ComfyUI source: every required input present, no unknown input names, combo values inside the allowed options (`"16:9"`, `"1K"`, `"5"`, `"720p"`, `"LTX-2.5 (Fast)"`, `"4"`, `"1280x720"`, `"25"`), ints in range, and every link (`["1", 0]`, `["3", 0]`) points at an existing node id whose output type matches (IMAGE into `images` / `keyframes.image_0`, VIDEO into `video`). The same check flags a planted bad link, a wrong slot name and an out-of-range duration.
- The app calls the same models through Comfy Router (`bfl/flux-3-image`, `bfl/flux-3-video`, `ltx/ltx-2-5-fast` in `src/shot_director/router_client.py`); these workflows were not used to make the app's clips.
