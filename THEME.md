# Shot Director theme: the studio, cyberpunk anime, gold

The Shot Director UI uses the the studio dark monotone brand with a gold accent, a cyberpunk anime
treatment (scanlines, corner cuts, glows, speed lines) and a particle field. Motion runs on a vendored
copy of anime.js. Everything works offline: no CDN, no web fonts.

| File | What it holds |
| --- | --- |
| `src/shot_director/static/theme.css` | Every token and every visual effect |
| `src/shot_director/static/theme.js` | Particle field and the anime.js animations; exposes `window.theme` |
| `src/shot_director/static/vendor/anime.min.js` | anime.js v3.2.2 (MIT), vendored |
| `src/shot_director/static/vendor/anime.LICENSE.md` | anime.js license text |
| `src/shot_director/static/index.html` | Layout (inline `<style>`), wordmark, canvas, ticker, theme hooks |

The server must serve the static folder at `/static` (`app.mount("/static", StaticFiles(directory=STATIC))`
in `app.py`). Without it the page still works: the inline `<style>` in `index.html` carries a fallback palette
in the same black and gold, just without the effects.

## Tokens

All tokens live in `:root` at the top of `theme.css`. Change these and nothing else to reskin.

### Color

| Token | Value | Role |
| --- | --- | --- |
| `--sd-bg` | `#000000` | Page background (the 60) |
| `--sd-surface` | `#0d0d0d` | Panel base color (the 30) |
| `--sd-surface-glass` | `rgba(13,13,13,.92)` | Panels, slightly see-through so particles show faintly |
| `--sd-input` | `#000000` | Inputs, chips, queue slots |
| `--sd-text` | `#ffffff` | Body text |
| `--sd-muted` | `#a3a3a3` | Labels, captions, fragments, idle status |
| `--sd-hairline` | `rgba(255,255,255,.10)` | Neutral borders (inputs, chips, empty slots) |
| `--sd-gold` | `#F5C518` | Primary accent: buttons, selected chip, headings, brackets, scan bar |
| `--sd-gold-deep` | `#C99700` | Button pressed state |
| `--sd-gold-pale` | `#FFE566` | Button hover, ticker text, pulse ring, glow highlights |
| `--sd-gold-line` | `rgba(245,197,24,.28)` | Gold hairline on panels, header, ticker, filled slots |
| `--sd-gold-wash` | `rgba(245,197,24,.08)` | Fill behind the selected chip |
| `--sd-on-gold` | `#000000` | Text on gold buttons |
| `--sd-cyan` | `#19E6FF` | The only secondary: focus ring, links (download / continuation), LIVE badge, rare particle sparks |
| `--sd-error` | `#ff6b6b` | Error status text |

### Glow, shape, texture

| Token | Value | Role |
| --- | --- | --- |
| `--sd-glow-gold` | `0 0 0 1px gold, 0 0 14px gold@45%` | Strong gold glow (available for emphasis) |
| `--sd-glow-gold-soft` | `0 0 18px gold@18%` | Soft gold halo |
| `--sd-glow-cyan` | `0 0 0 2px cyan, 0 0 10px cyan@55%` | Keyboard focus ring (`:focus-visible`) |
| `--sd-radius` | `2px` | Inputs and badges; panels, chips and buttons are square with cut corners |
| `--sd-cut` | `12px` | Corner cut on panels |
| `--sd-cut-sm` | `6px` | Corner cut on chips and buttons |
| `--sd-bracket` | `14px` | Arm length of the gold corner brackets on panels |
| `--sd-scanline-alpha` | `.045` | Scanline strength (0 turns them off) |
| `--sd-scanline-gap` | `3px` | Scanline pitch |

### Type

| Token | Value | Role |
| --- | --- | --- |
| `--sd-font` | `"Poppins", ui-sans-serif, system-ui, ...` | Body, chips, buttons, wordmark. Poppins is used only if installed locally; no font is downloaded. It was not installed on the build Mac, so the system sans renders |
| `--sd-mono` | `ui-monospace, "SF Mono", Menlo, ...` | Shot metadata: section headings, fragments, slot captions, badges, status, ticker, ghost buttons |

Type scale: wordmark 14px bold, tracking .22em, uppercase. Section headings 11px mono, tracking .18em,
uppercase gold with a muted `// ` prefix. Body 15px. Captions 11 to 12px mono. Primary buttons 13px bold,
tracking .14em, uppercase.

### Motion

| Token | Value | Role |
| --- | --- | --- |
| `--sd-dur-fast` | `160ms` | Hover, border and press transitions |
| `--sd-ease` | `cubic-bezier(.23,1,.32,1)` | Strong ease-out for every CSS transition |
| `--sd-ticker-speed` | `40s` | One full ticker loop |
| `--sd-scanbar-speed` | `1.1s` | One pass of the render progress bar |
| `--sd-speedline-dur` | `520ms` | Hover speed-line sweep on gold buttons |

The variables the base layout uses (`--bg`, `--panel`, `--line`, `--text`, `--dim`, `--accent`) are mapped
onto these tokens at the bottom of `:root`, so the layout and the theme never disagree.

## The 60/30/10 split

| Share | What | Where |
| --- | --- | --- |
| 60 | Black `#000000` | Page background, inputs, chips, slots, header |
| 30 | Near-black panels `#0d0d0d` with white text | The seven panels and their text |
| 10 | Gold | Primary buttons, selected chips, section headings, corner brackets, panel hairlines, wordmark product name, particles |
| under 2 | Cyan | Focus ring, links, LIVE badge, about 6% of particles |

## Effects

| Effect | Entry point | What it does | Tune or disable |
| --- | --- | --- | --- |
| Scanlines | `theme.css` `body::after` | Fixed full-screen 1px white lines every 3px at 4.5% alpha, above the UI, ignores the pointer | `--sd-scanline-alpha` / `--sd-scanline-gap`; set alpha to `0` to remove |
| Corner cuts | `theme.css` `main section`, `.chip`, `button.primary` `clip-path` | Panels lose the top-right and bottom-left corners; chips the top-right; buttons two corners | `--sd-cut`, `--sd-cut-sm`; set to `0px` for square |
| Corner brackets | `theme.css` `main section::before/::after` | 2px gold L-brackets on each panel's top-left and bottom-right | `--sd-bracket`; delete the two rules to remove |
| Gold hairline panels | `theme.css` `main section` border | 1px `--sd-gold-line` border plus a faint inner gold glow | `--sd-gold-line` |
| Selected-chip glow | `theme.css` `.chip.on` | Gold border, gold text, gold wash fill, inner gold glow, text glow | `--sd-gold-wash`, `--sd-gold` |
| Speed lines (hover) | `theme.css` `button.primary::before` + `@keyframes sd-speedlines` | On hover (mouse only), diagonal white streaks sweep across the gold button once | `--sd-speedline-dur`; delete the hover rule to remove |
| Speed-line burst (click) | `theme.js` `theme.burst(el, evt)` + `.fx-speedline` | 14 gold lines shoot out radially from the click point, then are removed | `N` and distance in `burst()`; drop the `fx("burst", ...)` calls in `index.html` |
| Scan bar | `theme.css` `.fx-scanning::before` + `@keyframes sd-scanbar` | A thin gold bar runs along the top edge of the slot (or sequence panel) being rendered. Pure CSS, so it works without anime.js | `--sd-scanbar-speed` |
| Scan sweep | `theme.js` `theme.scan(el, on)` + `.fx-sweep` | A translucent gold light band sweeps across the card on a loop while it renders | duration in `scan()` |
| Particles | `theme.js` particle field on `<canvas id="fx-particles">` | Gold motes drift upward with a slight sway, fading in and out; about 6% are flickering cyan sparks | `MAX_PARTICLES`, `CYAN_CHANCE` in `theme.js`; remove the canvas to disable |
| Ticker | `index.html` `<div id="ticker" class="ticker" hidden>`, `theme.css` `.ticker*` | Holographic strip under the header: mono uppercase pale-gold text, gold/cyan gradient wash, edge fade, marquee that pauses on hover | `--sd-ticker-speed`; stays hidden while `[hidden]` is set or it is empty |
| Wordmark | `index.html` `h1.wordmark`, `theme.css` `.wordmark` | `SHOT DIRECTOR`, white brand, glowing gold `//`, gold product name | `.wordmark` rules |

### Filling the ticker

The news feature fills the ticker like this, then removes `hidden`:

```html
<div id="ticker" class="ticker">
  <div class="ticker-track">
    <span class="ticker-item"><a href="...">Headline one</a></span>
    <span class="ticker-item">Headline two</span>
    <!-- list the items twice: the marquee moves the track by -50% for a seamless loop -->
  </div>
</div>
```

## anime.js animations

All live in `theme.js`. Each one does nothing (and throws nothing) when anime.js is missing or
reduced motion is on.

| Name | Trigger | Duration | Easing | What it animates |
| --- | --- | --- | --- | --- |
| Panel entrance | Page load (`DOMContentLoaded`) | 520ms, 60ms stagger | `easeOutQuint` | Header and every panel: opacity 0 to 1, translateY 14px to 0. `html.fx-enter` hides them only while this runs |
| Chip pulse `theme.pulse(el)` | Clicking a body, move or engine chip | 380ms scale, 460ms ring | `easeOutBack` / `easeOutQuad` | Chip scales 0.94 to 1; a pale-gold inner ring fades out |
| Burst `theme.burst(el, evt)` | Clicking Generate shot or Stitch sequence | 420ms, 8ms stagger | `easeOutExpo` | 14 speed lines fly 60 to 96px outward from the click point and fade |
| Scan `theme.scan(el, on)` | Generate shot (the next empty slot) and Stitch (the sequence panel), from click until the request ends | 1600ms per pass, looping | `easeInOutSine` | Gold light band translateX -100% to 250% |
| Land `theme.land(el)` | A new shot appears in the queue | 560ms | `easeOutQuint` | New slot: opacity 0 to 1, translateX -24px to 0, scale 0.96 to 1 |

The particle field uses its own `requestAnimationFrame` loop, not anime.js, so it keeps working if
anime.js fails to load.

Public API: `window.theme = { burst, scan, pulse, land, enabled }`. `index.html` calls it through
`fx(name, ...args)`, which swallows any error so a theme failure can never break the app.

## Accessibility

Contrast ratios, computed with the WCAG 2 relative-luminance formula:

| Foreground | Background | Ratio | WCAG |
| --- | --- | --- | --- |
| White `#ffffff` | `#000000` | 21.00:1 | AAA |
| White `#ffffff` | `#0d0d0d` | 19.44:1 | AAA |
| Muted `#a3a3a3` | `#000000` | 8.33:1 | AAA |
| Muted `#a3a3a3` | `#0d0d0d` | 7.70:1 | AAA |
| Gold `#F5C518` | `#000000` | 12.88:1 | AAA |
| Gold `#F5C518` | `#0d0d0d` | 11.92:1 | AAA |
| Black on gold button | `#F5C518` | 12.88:1 | AAA |
| Deep gold `#C99700` | `#0d0d0d` | 7.32:1 | AAA |
| Pale gold `#FFE566` | `#000000` | 16.65:1 | AAA |
| Cyan `#19E6FF` | `#0d0d0d` | 12.78:1 | AAA |
| Error `#ff6b6b` | `#0d0d0d` | 7.00:1 | AAA |

Panels are 92% opaque over the particles and the scanlines add at most 4.5% white, so the effective
background stays within a few percent of `#0d0d0d`.

- Reduced motion (`prefers-reduced-motion: reduce`): the particle canvas is hidden and its loop never
  starts (it also stops if the setting changes while the page is open); every anime.js call returns
  early; CSS animations are off; transitions keep only color, background, border and opacity; the scan
  bar becomes a static full-width gold bar so "rendering" is still visible; the ticker stops and can be
  scrolled instead; press-scale is off.
- Keyboard focus shows a cyan ring (`:focus-visible`). Hover effects are gated behind
  `(hover: hover) and (pointer: fine)` so taps do not leave stuck hover states.
- The canvas, burst lines, sweep band and pulse ring are `aria-hidden`. The ticker has `role="marquee"`
  and `aria-label="Headlines"`.

## Performance budget

- Particles: at most 80, scaled to screen area (one per 16,000 px²), drawn as plain arcs on one canvas
  capped at 2x device pixel ratio.
- The particle loop stops when the tab is hidden (`visibilitychange`) and resumes when it is visible.
- The burst creates 14 short-lived elements and removes them after the animation, with a 900ms
  timeout as a backstop.
- Only transform and opacity animate (plus box-shadow on the one pulse ring). No layout-triggering
  properties animate.

## anime.js (vendored)

- Version: 3.2.2, from the npm package `animejs@3.2.2` (`npm pack animejs@3.2.2`, file `lib/anime.min.js`).
- SHA-256 of `vendor/anime.min.js`: `b5ce1be3c3f530f192e0f2571d1942846096d66119cbada34bfdc912c4873f35`.
- License: MIT, (c) 2019 Julian Garnier. Full text in `vendor/anime.LICENSE.md`.
- Why 3.2.2 and not 4.x: v3 ships one UMD file with a global `anime()` call, which fits a no-build,
  no-module page. v4 is ESM-first with a different API (`animate`, `stagger` imports).

## How to reskin

Change tokens only, in `:root` at the top of `theme.css`:

1. Swap the accent: set `--sd-gold`, `--sd-gold-deep`, `--sd-gold-pale`, then update the alpha versions
   `--sd-gold-line` and `--sd-gold-wash` (same RGB, keep the alphas). A few glow rules repeat gold as
   `rgba(245,197,24,a)`; search for `245, 197, 24` and replace the RGB.
2. Swap the secondary: `--sd-cyan` (and `25, 230, 255` in the cyan glows and the LIVE badge).
3. Light or dark base: `--sd-bg`, `--sd-surface`, `--sd-surface-glass`, `--sd-input`, `--sd-text`,
   `--sd-muted`, `--sd-hairline`. Re-check contrast with the table above.
4. Calmer look: `--sd-scanline-alpha: 0`, `--sd-cut: 0px`, `--sd-cut-sm: 0px`, and `MAX_PARTICLES` in
   `theme.js`.
5. Particle colors follow `--sd-gold` and `--sd-cyan` automatically (read at load time).
6. Keep the fallback palette in `index.html`'s inline `:root` in step if you change the brand colors.
