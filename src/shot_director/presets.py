"""Camera bodies and camera-move presets, and the prompt compiler.

Each preset expands to a prompt fragment. The compiled prompt is what gets
sent to the video model (LTX 2.5 by default).
"""

CAMERA_BODIES: dict[str, dict[str, str]] = {
    "modern": {
        "label": "Modern cinema (digital)",
        "fragment": "shot on a modern digital cinema camera, clean sharp image, natural colour, shallow depth of field",
        "color": "0x1f3b57",
    },
    "35mm": {
        "label": "35mm film",
        "fragment": "shot on 35mm film, fine film grain, warm halation, anamorphic lens flare, cinematic colour grade",
        "color": "0x5a3a1a",
    },
    "8mm": {
        "label": "Super 8 home movie",
        "fragment": "shot on Super 8 film, heavy grain, light leaks, slightly overexposed, vintage home-movie look",
        "color": "0x6b4f1d",
    },
    "dv": {
        "label": "DV camcorder (2003)",
        "fragment": "shot on a 2000s DV camcorder, interlaced video, low dynamic range, slight blur, timestamp-era look",
        "color": "0x2e4a2e",
    },
}

CAMERA_MOVES: dict[str, dict[str, str]] = {
    "dolly_in": {
        "label": "Dolly in",
        "fragment": "slow smooth dolly-in toward the subject, the camera pushes forward steadily",
    },
    "dolly_out": {
        "label": "Dolly out (reveal)",
        "fragment": "slow dolly-out pulling back from the subject, revealing the wider scene",
    },
    "orbit": {
        "label": "Orbit",
        "fragment": "the camera orbits 90 degrees around the subject at eye level, parallax in the background",
    },
    "crane_up": {
        "label": "Crane up",
        "fragment": "crane shot rising up and over the subject, ending high and looking down",
    },
    "handheld": {
        "label": "Handheld follow",
        "fragment": "handheld camera following the subject, subtle natural shake, documentary feel",
    },
    "whip_pan": {
        "label": "Whip pan",
        "fragment": "fast whip pan to the right with motion blur, energetic transition",
    },
    "crash_zoom": {
        "label": "Crash zoom",
        "fragment": "sudden fast crash zoom into the subject's face, dramatic",
    },
    "static": {
        "label": "Locked-off static",
        "fragment": "locked-off static tripod shot, no camera movement, only the subject moves",
    },
}


def compile_prompt(body: str, move: str, scene: str) -> str:
    """Join scene text + camera move + camera body into one cinematic prompt."""
    if body not in CAMERA_BODIES:
        raise ValueError(f"unknown camera body: {body}")
    if move not in CAMERA_MOVES:
        raise ValueError(f"unknown camera move: {move}")
    scene = (scene or "").strip().rstrip(".") or "a cinematic scene"
    return f"{scene}. {CAMERA_MOVES[move]['fragment']}. {CAMERA_BODIES[body]['fragment']}."


def as_json() -> dict:
    return {
        "bodies": [{"id": k, "label": v["label"], "fragment": v["fragment"]} for k, v in CAMERA_BODIES.items()],
        "moves": [{"id": k, "label": v["label"], "fragment": v["fragment"]} for k, v in CAMERA_MOVES.items()],
    }
