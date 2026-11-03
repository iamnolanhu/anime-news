# THE WEEKLY BRIEFING

One episode per ISO week (Monday to Sunday) with that week's top headline in each of five topics: **AI, crypto,
politics, tech, markets**. The anchor opens on the week's biggest story, briefs the five segments, runs "markets in
10 seconds" and signs off "Back next week." Code: `packages/engine/src/shot_director/weekly.py`.

## Commands

```bash
cd packages/engine
uv run anime-news weekly --week 2026-W44 --plan-only            # sources + storyboard + the five headlines + $ estimate
uv run anime-news weekly --week 2026-W44 --mock                 # offline placeholder episode, spends nothing
uv run anime-news weekly --week 2026-W44                        # live: one writer call, then the render
uv run anime-news weekly --weeks 2026-W01..2026-W44 --budget-usd 100   # the year's backfill, one week at a time
uv run anime-news weekly --weeks 2026-W01..2026-W44 --retry     # re-render only the failed shots of partial weeks
uv run anime-news weekly --weeks 2026-W01..2026-W44 --publish   # mark the rendered weeks as published
```

| Flag | Meaning |
| --- | --- |
| `--week W` / `--weeks A..B` | one ISO week (`2026-W44`) or a range, run in the order given (`2026-W44..2026-W01` runs newest first) |
| `--minutes` | episode length, default 1 (about 20 shots); 3 gives about 60 shots for the top weeks (PsyopAnime pace, 18-22 shots a minute) |
| `--quality` | `480p` (default) or `720p` |
| `--out DIR` | media dir (default `~/.anime-news/media`); weeks go to `DIR/weekly/<week>/` |
| `--mock` | template storyboard and the offline placeholder render; no Router call |
| `--plan-only` | fetch the sources, write `storyboard.json` (template shape, so no writer call), print the five headlines and the estimate; renders nothing |
| `--retry` | only weeks with status `partial`: re-render the failed shots, re-cut |
| `--budget-usd N` | default 100. Before each live week the batch adds that week's estimate to what it has spent; over N, it stops. Each week's render also gets the remaining budget as its `--max-cost` |
| `--force` | re-render weeks that are already `done` (default: skipped) |
| `--publish` | set `published: true` on every rendered week in the range |
| `--refresh-sources` | refetch Wikipedia even when the week is cached |
| `--no-google` | never fill an empty topic from Google News |
| `--no-viral` | skip the last30days engagement boost |
| `--allow-unfinished` | also run a week that has not ended yet (default: skipped) |

A live week spends one writer call (about $0.10) before its estimate is checked, like `anime-news make`.

## How the five headlines are picked

1. **Sources (free, no keys).** Wikipedia's `Portal:Current events/<year> <Month> <day>` for each of the 7 days, read
   as raw wikitext: every bullet that cites at least one source is an item, with its category heading, its parent
   bullets (context), its wiki links and its cited sources. Cached in `weekly/sources/<week>.json` (a cache with a
   failed day is refetched next time). When a topic has no acceptable Wikipedia item, a Google News RSS search for
   that topic with the week's dates fills it (best effort; also cached).
2. **Topics.** Keyword lists per topic (`KEYWORDS` in weekly.py), matched as whole words, plus a nudge from
   Wikipedia's category (Business and economy, Politics and elections, Science and technology).
3. **Somber filter, hard.** Dropped: the categories "Armed conflicts and attacks" and "Disasters and accidents", and any
   item whose text or parent bullets mention deaths, wars, attacks, disasters or violence (news.py's list plus
   `SOMBER_EXTRA`).
4. **Score.** 2 per cited source (bigger stories get more citations) + 1.5 per topic keyword + up to 2.25 for
   absurd-literal words (ban, record, largest, robot, billion...) + recency in the week (0 to 1) - a penalty for very
   long items. For weeks inside the last 30 days, when the last30days engine is installed (see viral.py), posts that
   overlap the item's words add `overlap x log10(engagement)`.
5. **Pick.** One item per topic, no item twice (scarce topics choose first). A topic with nothing acceptable is left
   out, so a week can have fewer than five segments. The cold open uses the highest-scoring pick.
6. **Headline.** Each item becomes a caption of at most 60 characters (newsroom shorthand: AI, IPO, US, $1.6bn; the
   first sentence; cut at a clause boundary). The full sentence goes to the narrator and the writer as source text.

## The storyboard

`storyboard.build(headline, segments=[{topic, headline, source_text}], minutes, week=...)`. Scenes in order:
`cold_open` (image only, the biggest headline on a teletype), one scene per segment (`topic` set, `caption` = the
headline verbatim, a talk shot first, then 1 to 5 cutaways), the markets insert (`topic: "markets_10s"`, caption
`MARKETS IN 10 SECONDS`, stills and inserts only), `sign_off` (the anchor's last line ends "Back next week.").
Segment scenes reuse the existing acts (setup, escalation, turn, climax) so the cut, the countdown and the web app's
schema work unchanged. Live, the writer gets this exact scene plan; captions, topics and acts are enforced from it.
Extra storyboard fields: `week`, `segments`, scene `topic` (all optional; see STORYBOARD.md).

## Files

```
<media>/weekly/
  index.json                 the manifest (below)
  ledger.jsonl               one line per week and action (plan, render, retry, budget_stop), with cost
  sources/<week>.json        the Wikipedia (and Google News) items of the week
  <week>/
    meta.json                the week's record (below)
    storyboard.json          the storyboard (storyboard.next.json when a rendered week is planned again)
    episode.mp4              16:9 cut          episode.9x16.mp4   vertical cut      poster.jpg   one frame after the cold open
    episode-<id>.*           render files: .storyboard.json, .shots.json (what --retry reads), .plan.json, .mp4.cuts.json
    frame-*.png, shot-*.mp4  every shot's start frame and clip; episodes.json is the render ledger of this week
```

The style sheet, cast sheets, voice samples and music kit stay in `<media>/` and are linked into each week dir, so
they are made once for the whole backfill.

### meta.json

| Field | |
| --- | --- |
| `week`, `start`, `end`, `dates`, `label` | `2026-W44`, `2026-10-26`, `2026-11-01`, the 7 dates, `OCT 26 - NOV 1` |
| `series`, `title`, `cold_open` | `THE WEEKLY BRIEFING`, the title card (`WEEK 44: OCT 26 - NOV 1`), the cold open's headline |
| `headlines[]` | `{topic, headline, text, date, category, score, sources: [{name, url}], wiki_links: [url], feed}` |
| `status` | `planned`, `rendering`, `done`, `partial` (some shots failed; `--retry`), `failed` |
| `minutes`, `quality`, `storyboard_shots` | as requested |
| `cost_estimate`, `estimate` | USD before rendering (render + writer), and its parts |
| `duration`, `cut_shots`, `rendered_shots`, `failed_shots`, `errors` | from the render |
| `credits_used`, `mode`, `seconds_to_render`, `finished`, `retried` | from the render |
| `files` | `{storyboard, shots, plan, mp4, vertical, poster}`, names inside the week dir |
| `published` | `true` after `--publish` |

### index.json (`GET /api/weekly`)

```json
{"series": "THE WEEKLY BRIEFING", "updated": "2026-11-03T10:00:00+00:00",
 "weeks": [{"week": "2026-W44", "start": "2026-10-26", "end": "2026-11-01", "label": "OCT 26 - NOV 1",
            "title": "WEEK 44: OCT 26 - NOV 1", "status": "done", "published": true,
            "headlines": [{"topic": "ai", "headline": "Canada's government invests $1.6bn in AI company Cohere"}],
            "duration": 61.2, "minutes": 1, "quality": "480p",
            "mp4_url": "/media/weekly/2026-W44/episode.mp4", "vertical_url": "/media/weekly/2026-W44/episode.9x16.mp4",
            "poster_url": "/media/weekly/2026-W44/poster.jpg", "meta_url": "/media/weekly/2026-W44/meta.json",
            "updated": "..."}]}
```

Newest week first. Every week with a `meta.json` is listed, planned ones too (their `mp4_url` is null); the site
shows `status == "done"` (or `partial`) weeks with `published: true`. URLs are relative to the engine: prefix the
engine's origin, or the site's `/engine` proxy. The engine serves the files from its `/media` mount
(`GET /media/weekly/<week>/<file>`).

## Adding a week

`anime-news weekly --week 2026-W45 --plan-only` once the week is over (Monday), read the five headlines, then run it
without `--plan-only` and with `--publish`. The manifest is rebuilt from every `meta.json` after each step, so adding,
re-rendering or deleting a week dir and running any weekly command keeps `index.json` right.

## The backfill

1. `--plan-only` for the whole range: free, prints every week's headlines and estimate, writes the ledger.
2. Render newest first, one render at a time (the box has 4 vCPUs): `--weeks 2026-W44..2026-W01 --budget-usd 100 --publish`.
   Done weeks are skipped on a re-run, so the same command resumes after a stop.
3. `--retry` over the range for partial weeks; then `--publish` again.

Spend (estimate, cast assets already cached): about **$11 per week at 1 minute / 480p** (31 s of talk and 30 s of
action clips at $0.149 per second, 21 images) and about **$62 per week at 3 minutes / 720p**. The full 2026 backfill
(44 weeks) is about $500 at 1 minute / 480p, so the default $100 budget stops after 8 or 9 weeks.

