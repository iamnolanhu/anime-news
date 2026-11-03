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
       {"id": "s1_2", "kind": "talk", "cast": ["anchor"], "line": "Welcome to The Weekly Briefing. This week: Mondays are gone.", "prompt": "Medium shot of KUROGANE at the desk", "move": "dolly_in", "seconds": 5}
     ]}
  ]
}
```

## Fields

| Field | Rule |
| --- | --- |
| `headline` | required, non-empty |
| `minutes` | above 0, at most 5 (default 1) |
| `cast[]` | `id` (slug), `name`, `role`, `look` (drawn-anime description, used in every frame prompt the member is in), `voice` (an ElevenLabs voice id used as Seedance's voice reference; anything else falls back to a default voice). Optional: `trait` (personality, given to the writer), `poses` (see "The cast file"). Missing or empty: the default cast (`<media>/cast.json` when present, else KUROGANE, VEX, DR. HAKASE). Original characters only, never a real person. |
| `scenes[].act` | `cold_open`, `setup`, `escalation`, `turn`, `climax`, `sign_off`. A cold open has no caption and no narration. |
| `scenes[].setting` | where the scene happens; added to every frame prompt of the scene |
| `scenes[].caption` | the scene's one-line summary (60 chars max); the cut draws no caption over wordless pictures (subtitles are what is spoken), so it is not shown on screen; a weekly segment's headline is, as a lower-third |
| `scenes[].narration` | 1-2 sentences (30 words max) read by the narrator over the scene's first non-talk run; empty = none |
| `shots[].kind` | `talk` (a cast member speaks `line` on camera: Seedance 2.5 with the member's voice), `action` (animated clip on the action engine, Seedance by default), `still` (one frame, used as a quick Ken Burns cut-in), `insert` (one frame of a prop, screen, ticker or document; the headline text may appear) |
| `shots[].cast` | cast ids in the shot; a talk shot's first id is the speaker (default: the first cast member) |
| `shots[].line` | talk shots only, required, 60 characters max (longer lines are trimmed at a word boundary) |
| `shots[].prompt` | what the frame shows, drawn-anime terms, starting with the shot size; 60 words max |
| `shots[].move` | `dolly_in`, `dolly_out`, `orbit`, `crane_up`, `handheld`, `whip_pan`, `crash_zoom`, `static` |
| `shots[].seconds` | talk 4-10 (default from the line length), action 4-10 (default 5); stills ignore it |
| `id`s | optional on input; missing or duplicate ids are regenerated |

Extra optional fields: `title_card` (the 9:16 export's title; the 16:9 cut has no title card), `countdown` (label of the
red countdown on the escalation scene; the cut draws a countdown only when this is set), `style` (extra visual direction), `writer` (which model wrote it).
Weekly episodes add `week` (`2026-W44`), `segments` (`[{topic, headline, source_text}]`) and a scene-level `topic`
(`ai`, `crypto`, `politics`, `tech`, `markets`, or `markets_10s` for the markets insert); see [WEEKLY.md](WEEKLY.md).

## The cast file

`<media>/cast.json` (media dir: `ANIME_NEWS_MEDIA`, default `~/.anime-news/media`) replaces the built-in cast with no
code change: the writer's cast list, the mock template's names, every new storyboard's `cast`, and the series style
sheet's anchor all come from it. It must keep the three fixed ids `anchor`, `operative` (the field reporter) and
`analyst`; a file that is unreadable or misses one of them is ignored with a warning (the built-in cast is used).
Fields a member leaves out fall back to the built-in member with the same id.

```json
{"cast": [
  {"id": "anchor", "name": "SHION", "role": "the Anchor",
   "look": "adult woman, mid-20s, long straight silver-lavender hair in a hime cut, thin gold-rimmed glasses, black blazer with gold trim",
   "trait": "perfectly composed; one raised eyebrow is the whole punchline",
   "voice": "<ElevenLabs voice id>",
   "poses": {"talk":   {"id": "upper80v2_P019", "phrase": "tucks a strand of hair behind her ear",
                        "ref_image": "/abs/path/to/a/small/copy/of/upper80v2_P019_front.png"},
             "action": "speaks with one hand open in a gesture, the other holding her notes, mid-sentence",
             "still":  {"id": "upper80v2_P013", "phrase": "rests her chin on both palms"}}},
  {"id": "operative", "name": "HINA", "role": "the Field Reporter", "look": "..."},
  {"id": "analyst", "name": "DR. RENA", "role": "the Analyst", "look": "..."}
]}
```

- `poses` (optional): a pose per shot kind (`talk`, `action`, `still`), from the pose-library skill (`pose-library pick
  --framing hero --scene "news desk" --with-ref`). A string is the phrase; an object adds the pose `id` and
  `ref_image`. The phrase is written after "She" (25 words max). The writer gets each member's poses and puts the
  pose for the shot kind into the shot prompt.
- `ref_image` (optional): a pose-library mannequin drawing on this machine. When a non-anchor speaker's character
  sheet is generated, it goes to FLUX 3 image as a second reference after the style sheet (the model takes 1-10
  references), with the instruction to copy the pose only. A missing file means text only. The drawings may be used
  as references but not redistributed: point at the local gallery clone (or a shrunk copy, about 0.12 MP), never
  commit them.
- Changing a member's `look` or talk pose gives a new character-sheet file name, so the sheet is made again on the
  next live render. The series style sheet is made once: after changing the anchor, delete
  `<media>/style-sheet.png` and the next live render regenerates it around the new anchor (one FLUX image).

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
`X-Comfy-Credits-Used` response headers, when the Router sends them; a queued job reports its charge on both the
completed status poll and the result fetch, and the meter counts it once per job. Entries written before
2026-11-03 counted it twice: halve their `credits_used`).

## Spend

`anime-news make|render --estimate-only` prints the USD estimate without spending; `--max-cost N` refuses to start a
live render whose estimate is over N (the server reads `ANIME_NEWS_MAX_COST`). Seedance is priced from the Router's
token rate (about $0.149 per second at 480p, 2.25x at 720p); the image, voice and SFX prices are rough constants
(`ANIME_NEWS_PRICE_*`) until real `credits_used` totals replace them.

## Shot order and the cut

The cut keeps every shot in the order written: a scene is cut as one piece, talk, action, stills and inserts in
storyboard order, and an action clip's angles (the "Hard cut to:" beats inside a Seedance clip) in their own order,
each once. The editor trims and chooses windows; it never reorders. A talk take may lend part of its line to the shots
right after it (or before it, when it ends the scene), so put the pictures that illustrate a line next to it. See
docs/EDIT.md.
