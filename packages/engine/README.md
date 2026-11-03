# anime-news (engine)

The Python engine behind Anime News: a headline in, an anime news episode out, via the Comfy Router.
It runs on your own machine; the Comfy API key stays in `~/.anime-news/config.json` (0600) or `COMFY_API_KEY`.

```sh
uv run anime-news serve                      # API + UI on http://127.0.0.1:8765 (media in ~/.anime-news/media)
uv run anime-news storyboard "Mayor bans Mondays" --minutes 1 --save sb.json
uv run anime-news render sb.json --quality 480p --vertical --estimate-only   # spend estimate, nothing spent
uv run anime-news render sb.json --quality 480p --vertical --max-cost 5
uv run anime-news make "Mayor bans Mondays" --minutes 1 --mock               # offline placeholder episode
uv run anime-news render --retry ~/.anime-news/media/<episode>.shots.json   # re-render only failed shots
uv run anime-news weekly --week 2026-W44 --plan-only    # THE WEEKLY BRIEFING: five headlines + estimate (docs/WEEKLY.md)
uv run anime-news weekly --weeks 2026-W01..2026-W44 --budget-usd 100   # the year's backfill
uv run --group dev pytest                    # offline tests (mock mode, no network)
```

Storyboard schema: `docs/STORYBOARD.md`. HTTP contract: `/api/health`, `/api/config`, `/api/storyboard`,
`/api/render`, `/api/jobs/{id}`, `/api/episodes`, `/api/weekly`, `/media/{file}`. Browsers may call it only from the allowed origins:
`ANIME_NEWS_ALLOWED_ORIGINS` (comma list; default `https://anime-news-nine.vercel.app,http://localhost:3000,http://127.0.0.1:3000`);
preflights carry `Access-Control-Allow-Private-Network: true`. More in the repository README one level up.
