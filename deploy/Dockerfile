# Shot Director public deploy image (deploy-host, behind Traefik). Build context = repo root:
#   docker compose -f deploy/compose.yml up -d --build
FROM python:3.12-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg ca-certificates \
 && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:0.8 /uv /usr/local/bin/uv

ENV UV_LINK_MODE=copy UV_COMPILE_BYTECODE=1 UV_PYTHON_DOWNLOADS=never \
    PYTHONUNBUFFERED=1 SHOT_DIRECTOR_MEDIA=/data/media
WORKDIR /app

# dependencies first (cached layer), then the project itself
COPY pyproject.toml uv.lock README.md LICENSE ./
RUN uv sync --frozen --no-dev --no-install-project
COPY . .
RUN uv sync --frozen --no-dev \
 && useradd --uid 10001 --create-home app \
 && mkdir -p /data/media \
 && chown -R app:app /data /app

USER app
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/presets', timeout=4)" || exit 1
CMD ["/app/.venv/bin/shot-director", "--host", "0.0.0.0", "--port", "8000"]
