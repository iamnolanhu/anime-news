"""Local engine settings: ~/.anime-news/config.json (0600) holds {"comfy_api_key": ...}; media in ~/.anime-news/media.

Precedence for the Comfy API key: COMFY_API_KEY in the environment, then the config file. The key is never
logged, echoed or returned by any endpoint: callers only ever see `has_key()`.
Mock mode (ANIME_NEWS_MOCK=1 or the legacy SHOT_DIRECTOR_MOCK=1) hides the key everywhere, so nothing is spent.
"""

import json
import os
from pathlib import Path

KEY_FIELD = "comfy_api_key"


def home() -> Path:
    return Path(os.environ.get("ANIME_NEWS_HOME", Path.home() / ".anime-news")).expanduser()


def config_path() -> Path:
    return home() / "config.json"


def media_dir() -> Path:
    """ANIME_NEWS_MEDIA, else ~/.anime-news/media."""
    return Path(os.environ.get("ANIME_NEWS_MEDIA") or home() / "media").expanduser()


def mock_forced() -> bool:
    return os.environ.get("ANIME_NEWS_MOCK") == "1" or os.environ.get("SHOT_DIRECTOR_MOCK") == "1"


def read() -> dict:
    try:
        doc = json.loads(config_path().read_text())
        return doc if isinstance(doc, dict) else {}
    except (OSError, ValueError):
        return {}


def write(doc: dict) -> None:
    """Atomic write, 0600 from the first byte (the file holds the key)."""
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    tmp = path.with_name(f".{path.name}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(doc, f, indent=1)
    os.chmod(tmp, 0o600)
    tmp.replace(path)


def stored_key() -> str | None:
    key = str(read().get(KEY_FIELD) or "").strip()
    return key or None


def set_key(value: str) -> None:
    """Store the key; an empty string clears it."""
    doc = read()
    value = (value or "").strip()
    if value:
        doc[KEY_FIELD] = value
    else:
        doc.pop(KEY_FIELD, None)
    write(doc)


def api_key() -> str | None:
    """Environment first, then the config file. None in mock mode."""
    if mock_forced():
        return None
    return os.environ.get("COMFY_API_KEY", "").strip() or stored_key()


def has_key() -> bool:
    return bool(os.environ.get("COMFY_API_KEY", "").strip() or stored_key())


def mode() -> str:
    return "live" if api_key() else "mock"
