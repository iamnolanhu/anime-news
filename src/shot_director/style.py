"""The series style sheet: one reference image that every start frame inherits its look from.

Text prompts alone let the look drift per episode (line weight, palette, how "anime" it reads). So the
series has a visual contract: <media>/style-sheet.png, generated ONCE with bfl/flux-3-image and then
passed as the `images` reference on every FLUX 3 image call (the model accepts 1-10 reference images as
http(s) URLs or base64 strings). Delete the file to re-roll the series look; nothing else regenerates it.
See docs/WRITER.md.
"""

import base64
from pathlib import Path

from .router_client import IMAGE_MODEL, LiveRouter, RouterError, load_api_key

SHEET_NAME = "style-sheet.png"
ASPECT_RATIO = "16:9"
RESOLUTION = "1k"

SHEET_PROMPT = (
    "2D cel anime frame, drawn animation, not a photograph, not a 3D render. A still from an original "
    "late-1980s OVA anime: an anime newsroom at night. The Anchor, an original character, a stern "
    "middle-aged man with a short silver crew cut and round glasses, dark jacket with gold trim and a PRESS "
    "badge on the lapel, sits at a long news "
    "desk in front of a wall of glowing world-map monitors showing invented countries. Heavy black ink line "
    "art, hatching on the face, flat cel shading, flat painted background, film grain, VHS softness, teal "
    "shadows against burnt-orange highlights, high-contrast dramatic lighting. Safe for work, no logos, "
    "no text except the word PRESS on the badge, no real people.")

REFERENCE_LEAD = (
    "Use the reference image only as the style guide: match its hand-inked heavy line art, flat cel shading, "
    "film grain, VHS softness and teal-shadow / burnt-orange-highlight palette exactly. Do not copy its "
    "composition unless the scene below is the newsroom. New scene: ")


def sheet_path(media_dir: Path) -> Path:
    return Path(media_dir) / SHEET_NAME


def _image_url(router: LiveRouter, result: dict) -> str:
    url = router._dig(result, "result", "sample") or router._find_url(result, (".png", ".jpg", ".jpeg", ".webp"))
    if not url:
        raise RouterError("no image URL in Router result")
    return url


def ensure_style_sheet(media_dir: Path, router: LiveRouter | None = None) -> Path | None:
    """Return <media>/style-sheet.png, generating it once (one FLUX 3 image call) if missing.
    None when there is no API key (mock mode) or the call fails: callers then render unstyled."""
    path = sheet_path(media_dir)
    if path.exists() and path.stat().st_size > 0:
        return path
    if router is None:
        key = load_api_key()
        if not key:
            return None
        router = LiveRouter(key, Path(media_dir))
    if getattr(router, "mode", "") != "live":
        return None
    try:
        result = router._run_model(IMAGE_MODEL, {"prompt": SHEET_PROMPT, "aspect_ratio": ASPECT_RATIO,
                                                 "resolution": RESOLUTION, "grounding": False})
        tmp = path.with_name(f".{SHEET_NAME}.tmp")
        router._download(_image_url(router, result), tmp)
        tmp.replace(path)
        return path
    except Exception as e:
        print(f"[shot-director] style sheet failed: {type(e).__name__}: {str(e)[:300]}")
        return None


def styled_frame_kwargs(media_dir: Path) -> dict:
    """{"images": [<base64 of the style sheet>]} for a FLUX 3 image body, or {} when there is no sheet."""
    path = sheet_path(media_dir)
    if not path.exists():
        return {}
    return {"images": [base64.b64encode(path.read_bytes()).decode()]}


def generate_styled_frame(router, prompt: str, out: Path, media_dir: Path) -> Path:
    """Drop-in for router.generate_frame(prompt, body, out) that adds the style sheet as a reference.
    Mock router, or no sheet on disk: falls through to the plain generate_frame."""
    ref = styled_frame_kwargs(media_dir) if getattr(router, "mode", "") == "live" else {}
    if not ref:
        return router.generate_frame(prompt, "anime", out)
    body = {"prompt": REFERENCE_LEAD + prompt, "aspect_ratio": ASPECT_RATIO, "resolution": RESOLUTION,
            "grounding": False, **ref}
    result = router._run_model(IMAGE_MODEL, body)
    return router._download(_image_url(router, result), out)
