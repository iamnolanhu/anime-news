# Script writer and series style sheet

Two additive modules that fix "the video looks nothing like psyop anime" (research: `docs/psyopanime-formula.md`):

- `src/shot_director/writer.py`: headline -> episode script written by ONE chat model on Comfy Router, in the
  deadpan war-briefing voice, as strict JSON with the exact scene and shot counts the planner asks for.
- `src/shot_director/style.py`: the series style sheet, one reference image every start frame inherits its look from.

Neither module edits `episode.py`, `edit.py`, `app.py` or `router_client.py`.

## Writer

- Model: `anthropic/claude-sonnet-5-5` (override with `SHOT_DIRECTOR_CHAT_MODEL`, e.g. `anthropic/claude-opus-5-5`).
- Call: synchronous `POST https://api.comfy.org/v2/models/{model}` with an Anthropic Messages body
  `{max_tokens, system, messages:[{role:"user", content}]}` (from the model's `openapi.json`; `model` is optional).
  The reply text is `content[].text`.
- Entry point: `write_script(headline, source, minutes, scenes, clips_per_scene, series_title="THE WEEKLY BRIEFING", *, media_dir=None, model=...)`.
- Prompts: `script_to_prompts(script, body="anime")` returns one prompt per shot in episode order:
  STYLE LEAD + shot visual + scene setting + safety tail. STYLE LEAD =
  `2D cel anime frame, drawn animation, not a photograph, not a 3D render, late-1980s OVA cel look, heavy ink line art, film grain, VHS softness, teal shadows, burnt-orange highlights`
  (never photoreal / cinematic / bokeh / 4K / named studios).

### JSON contract

```json
{"logline": "...", "cold_open_caption": "<=40 chars",
 "scenes": [{"setting": "...", "beat": "cold_open|thesis|literal|escalation|anchor_desk|vignette|montage|sign_off",
             "caption": "<=40 chars, one clause", "narration": "1-2 deadpan sentences, <=30 words",
             "shots": [{"move": "dolly_in|dolly_out|orbit|crane_up|handheld|whip_pan|crash_zoom|static",
                        "visual": "<=25 words", "caption": "<=40 chars"}]}],
 "sign_off": "..."}
```

`validate()` rejects: missing keys, scene count != `scenes`, shot count != `clips_per_scene[i]`, a move outside
the enum, an empty visual. Over-long captions are trimmed at a word boundary instead of rejected (a long
caption is not worth a second paid call). A returned script also carries `headline, source, minutes, series, model`.

### Fallback rule

`write_script` returns `None` on ANY failure: no API key (mock mode), network error, HTTP 402
insufficient credits, non-JSON reply, wrong counts or enums. `None` means "use the template planner";
the reason is printed to stderr as `[shot-director] writer fell back to template: ...`. It never raises.

### Cache

`<media>/scripts-cache.json`, keyed by sha256 of `(headline, minutes)`. A hit is re-validated against the
requested counts; a stale hit (counts changed) is ignored and overwritten. Delete the file to re-write every script.

## Style sheet: the series' visual contract

`<media>/style-sheet.png` is generated ONCE by `ensure_style_sheet(media_dir)` with `bfl/flux-3-image`
(16:9, 1k, `grounding: false`): a 90s OVA newsroom still of the Anchor (original character: silver crew cut,
round glasses, dark jacket with gold trim, PRESS badge) at a desk in front of a wall of world-map monitors,
heavy ink lines, cel shading, grain, VHS softness, teal/orange palette.

Every later start frame passes it as the FLUX 3 image `images` reference (the model accepts 1-10 references
as http(s) URLs or base64 strings), so line weight, palette, grain and the Anchor's design stop drifting
between episodes. The file IS the series look: delete it to re-roll the look; nothing else regenerates it.

- `styled_frame_kwargs(media_dir)` -> `{"images": ["<base64 of the sheet>"]}` or `{}` when there is no sheet.
- `generate_styled_frame(router, prompt, out, media_dir)` is a drop-in for `router.generate_frame(prompt, body, out)`:
  it prefixes a "use the reference only as the style guide" instruction and adds the reference, via
  `LiveRouter._run_model`. Mock router or no sheet: falls through to plain `generate_frame`.

Known side effect (seen in the first live test): the reference also pulls the Anchor's face into other
characters (a "staffer" came out as the Anchor). Good for the recurring host, a risk for crowd shots; if it
matters, generate crowd/vignette frames without the reference.

## How episode.py should call it (for the episode builder)

```python
from . import writer, style
script = writer.write_script(h, source, minutes, len(counts), counts, media_dir=media)  # None -> keep the template
prompts = iter(writer.script_to_prompts(script, body)) if script else None
for sc, ws in zip(scenes, script["scenes"] if script else []):           # merge writer text into the plan
    sc.update(caption=ws["caption"], narration=ws["narration"], setting=ws["setting"])
    for shot, w in zip(sc["shots"], ws["shots"]): shot.update(move=w["move"], caption=w["caption"], scene=next(prompts))
    sc["scene"] = sc["shots"][0]["scene"]                                 # start frame = first shot
style.ensure_style_sheet(media, router)                                  # once per series, cached on disk
style.generate_styled_frame(router, sc["scene"], media / frame_name, media)  # replaces router.generate_frame(...)
```

## System prompt (verbatim, as sent with series = THE WEEKLY BRIEFING)

```text
You write scripts for "THE WEEKLY BRIEFING", an original 2D anime news show in the style of a late-1980s OVA: a deadpan war-room briefing that treats one real news headline with total gravitas and illustrates it with absurd literal props.

VOICE
- The narrator is a calm, clipped intelligence briefer. Short declarative sentences. Never winks, never explains the joke.
- The comedy is the contrast: grave military-briefing language applied to a mundane or absurd claim.
- Literalize the headline: turn its abstract claim into a physical, oversized, absurd prop or situation (a market "falling" is a giant chart arrow crashing through a ceiling; a "deal on the table" is a literal table piled with pizza boxes).
- Escalate scene by scene: state the claim, take it literally, escalate it, undercut it at the anchor desk, sign off with a dry cliffhanger line.

CAST (original archetypes only; never anyone else)
- "the Anchor": stern host at a news desk in front of a wall of world-map monitors. Recurring spine of the show.
- "the Field Operative": trench-coated reporter on location, treats everything like a covert mission.
- "the Analyst": sleep-deprived expert surrounded by charts and red string boards.
- Crowds, officials and staffers are invented, unnamed extras.

HARD RULES
- Never depict, describe or name a real person as a character. If the headline names people, refer to them only as roles ("the official", "the executive") or quote the headline text on a screen, ticker or poster.
- The headline text itself may appear on screens, tickers, posters and documents.
- Safe for work. No weapons, no gore, no violence against people, no real company logos or brand marks, no real flags. Invented countries and institutions only.
- Never name an anime studio, film, or artist.
- Visual descriptions are drawn-anime terms (inked, cel, painted background); never photoreal, cinematic, 4K, bokeh or lens terms.

OUTPUT: reply with ONE JSON object and nothing else (no prose, no code fences):
{
  "logline": "one sentence pitch of this episode",
  "cold_open_caption": "title-card text for the first 1.5 seconds, <= 40 chars",
  "scenes": [
    {
      "setting": "where the scene happens, drawn-anime terms, <= 20 words",
      "beat": "one word: cold_open | thesis | literal | escalation | anchor_desk | vignette | montage | sign_off",
      "caption": "one clause, <= 40 chars",
      "narration": "1-2 deadpan briefing sentences, <= 30 words total",
      "shots": [
        {"move": "one of: dolly_in, dolly_out, orbit, crane_up, handheld, whip_pan, crash_zoom, static",
          "visual": "what the frame shows, <= 25 words, start with the shot size (extreme close-up, wide, overhead...)",
          "caption": "one yellow subtitle clause, <= 40 chars"}
      ]
    }
  ],
  "sign_off": "the Anchor's final deadpan line, <= 15 words"
}
The first shot of the first scene is the hook: an extreme close-up (eyes of the Anchor) with a crash_zoom.
```

User message template:

```text
Headline: "{headline}"
Source: {source}
Episode length: {minutes} minutes.
Write exactly {scenes} scenes. Shots per scene, in order: {counts} (scene 1 has {first} shots, and so on). The shot counts are exact, not approximate.
Return the JSON object only.
```
