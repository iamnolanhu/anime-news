import os

import pytest


@pytest.fixture(autouse=True)
def offline(tmp_path, monkeypatch):
    """Every test: mock mode, no key from the environment, a throwaway engine home and media dir. No network."""
    monkeypatch.setenv("ANIME_NEWS_MOCK", "1")
    monkeypatch.delenv("COMFY_API_KEY", raising=False)
    monkeypatch.delenv("SHOT_DIRECTOR_ACCESS_TOKEN", raising=False)
    monkeypatch.setenv("ANIME_NEWS_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("ANIME_NEWS_MEDIA", str(tmp_path / "media"))

    def no_network(*a, **k):
        raise AssertionError("network call in an offline test")
    monkeypatch.setattr("urllib.request.urlopen", no_network)
    yield
