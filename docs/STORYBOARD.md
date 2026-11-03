# Storyboard schema

The storyboard is the editable plan of one episode. `POST /api/storyboard` (or `anime-news storyboard "<headline>"`)
writes it from a headline; the web app may change any field; `POST /api/render` (or `anime-news render file.json`)
turns it into an episode. `shot_director/storyboard.py:normalize()` validates both directions, so an edited
storyboard is checked by the same rules as the writer's reply.

```json
{
  "id": "sb-3f1c0a9e2b",
  "headline": "Mayor bans Mondays",
  "source_text_excerpt": "first ~600 characters of the article text, if any",
  "series": "THE WEEKLY BRIEFING",
  "logline": "one-sentence pitch",
  "minutes": 1,
  "title_card": "MONDAY IS CANCELLED",
  "countdown": "MONDAY ENDS IN",
  "cast": [
    {"id": "anchor", "name": "KUROGANE", "role": "the Anchor", "look": "a stern middle-aged news anchor ...", "voice": "onwK4e9ZLuTAKqWW03F9"}
  ],
  "scenes": [
    {"id": "s1", "act": "cold_open", "setting": "a dark invented capital at night", "caption": "", "narration": "",
     "shots": [
       {"id": "s1_1", "kind": "action", "cast": [], "prompt": "Extreme close-up of a red countdown clock ...", "move": "crash_zoom", "seconds": 5},
       {"id": "s1_2", "kind": "talk", "cast": ["anchor"], "line": "Good evening. Tonight: Mondays are gone.", "prompt": "Medium shot of KUROGANE at the desk", "move": "dolly_in", "seconds": 5}
     ]}
  ]
}
```

## Fields

| Field | Rule |
| --- | --- |
| `headline` | required, non-empty |
| `minutes` | above 0, at most 5 (default 1) |
| `cast[]` | `id` (slug), `name`, `role`, `look` (drawn-anime description, used in every frame prompt the member is in), `voice` (an ElevenLabs voice id used as Seedance's voice reference; anything else falls back to a default voice). Missing or empty: the default cast (KUROGANE, VEX, DR. HAKASE). Original characters only, never a real person. |
| `scenes[].act` | `cold_open`, `setup`, `escalation`, `turn`, `climax`, `sign_off`. A cold open has no caption and no narration. |
| `scenes[].setting` | where the scene happens; added to every frame prompt of the scene |
| `scenes[].caption` | the subtitle of the scene's non-talk cuts (60 chars max) |
| `scenes[].narration` | 1-2 sentences (30 words max) read by the narrator over the scene's first non-talk run; empty = none |
| `shots[].kind` | `talk` (a cast member speaks `line` on camera: Seedance 2.5 with the member's voice), `action` (animated clip on the action engine, Seedance by default), `still` (one frame, used as a quick Ken Burns cut-in), `insert` (one frame of a prop, screen, ticker or document; the headline text may appear) |
| `shots[].cast` | cast ids in the shot; a talk shot's first id is the speaker (default: the first cast member) |
| `shots[].line` | talk shots only, required, 60 characters max (longer lines are trimmed at a word boundary) |
| `shots[].prompt` | what the frame shows, drawn-anime terms, starting with the shot size; 60 words max |
| `shots[].move` | `dolly_in`, `dolly_out`, `orbit`, `crane_up`, `handheld`, `whip_pan`, `crash_zoom`, `static` |
| `shots[].seconds` | talk 4-10 (default from the line length), action 4-10 (default 5); stills ignore it |
| `id`s | optional on input; missing or duplicate ids are regenerated |

Extra optional fields: `title_card` (the 9:16 export's title and the cut's title card), `countdown` (label of the red
countdown on the escalation scene; default `NEXT BRIEFING IN`), `style` (extra visual direction), `writer` (which model wrote it).

## Rules

- No cap on scenes per storyboard or shots per scene. The writer targets about 4 scenes per minute
  (`max(2, round(minutes * 4))`: 0.5 min = 2, 1 min = 4, 3 min = 12) and 3-6 shots per scene, about half of them
  still or insert (cheap) and half talk or action (animated).
- Every shot gets its own start frame. Talk shots use the series style sheet and the speaker's character sheet as
  image references.
- The cut never shows a source moment twice: each clip is cut into disjoint windows, each still appears once, and a
  clip's start frame is never used as a still. The cut report (`<episode>.mp4.cuts.json`, `reuse`) recounts it from
  the final timeline.
- The last shot of the episode is the Anchor's talk shot ending in "Back next week." (the writer is told; the
  validator appends it to a writer storyboard whose last shot is a talk shot without it).

## Render outputs

In the media dir (default `~/.anime-news/media`), prefixed by the episode id:
`<epid>.storyboard.json` (as rendered), `<epid>.shots.json` (each shot's frame, clip, engine and error;
`anime-news render --retry <epid>.shots.json` re-renders only the failed ones and re-cuts), `<epid>.plan.json`
(the edit plan), `<epid>.mp4`, `<epid>.mp4.cuts.json`, and `<epid>.9x16.mp4` with `--vertical`.
The ledger (`episodes.json`) gets one entry per render: headline, duration, cut shots, cuts per minute, reuse,
quality, `cost_estimate` (USD, computed before the render) and `credits_used` (the sum of the
`X-Comfy-Credits-Used` response headers, when the Router sends them).

## Spend

`anime-news make|render --estimate-only` prints the USD estimate without spending; `--max-cost N` refuses to start a
live render whose estimate is over N (the server reads `ANIME_NEWS_MAX_COST`). Seedance is priced from the Router's
token rate (about $0.149 per second at 480p, 2.25x at 720p); the image, voice and SFX prices are rough constants
(`ANIME_NEWS_PRICE_*`) until real `credits_used` totals replace them.

## Shot order and the cut

The order of talk shots, and of the groups of non-talk shots between them, is kept exactly as written. Inside one group of
consecutive action / still / insert shots the anime cut decides the order itself (the sharpest gate-passing window opens, the rest
alternate clip / still / clip for variety; see docs/EDIT.md). Reordering shots inside such a group changes which shot leads the
group only when it is the sharpest; to force a shot to lead a beat, put a talk shot before it.
