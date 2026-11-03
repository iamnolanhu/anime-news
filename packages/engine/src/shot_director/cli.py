"""anime-news: a headline in, an anime news episode out.

  anime-news serve [--port 8765] [--media DIR] [--mock]
  anime-news storyboard "<headline>" [--text T | --url U] [--minutes 1] [--mock]     prints the storyboard JSON
  anime-news render <storyboard.json> [--quality 480p|720p] [--vertical] [--out DIR] [--mock]
  anime-news render --retry <episode>.shots.json [--out DIR]                        re-renders failed shots only
  anime-news make "<headline>" [--text T | --url U] [--minutes 1] [--quality 480p] [--vertical] [--out DIR] [--mock]
  anime-news weekly --week 2026-W44 | --weeks 2026-W01..2026-W44 [--minutes 1] [--quality 480p] [--out DIR] [--mock]
                    [--plan-only] [--retry] [--budget-usd 100] [--publish]                THE WEEKLY BRIEFING (docs/WEEKLY.md)

The Comfy API key comes from COMFY_API_KEY or ~/.anime-news/config.json (`anime-news serve` + the UI's key
field writes it). No key, or --mock: an offline placeholder render that spends nothing.
"""

import argparse
import json
import os
import sys
from pathlib import Path


def _media(args) -> Path:
    from . import config
    out = getattr(args, "out", None) or getattr(args, "media", None)
    return Path(out).expanduser().resolve() if out else config.media_dir().resolve()


def _mock(args) -> None:
    if getattr(args, "mock", False):
        os.environ["ANIME_NEWS_MOCK"] = "1"


def _print_episode(entry: dict, media: Path) -> None:
    print(media / entry["file"])
    keep = ("id", "episode_id", "headline", "duration", "cut_shots", "cuts_per_minute", "reuse", "storyboard_shots",
            "rendered_shots", "quality", "mode", "credits_used", "cost_estimate", "errors", "vertical_file",
            "storyboard", "shots_file", "seconds_to_render")
    print(json.dumps({k: entry.get(k) for k in keep}, indent=1))


def cmd_serve(args) -> None:
    _mock(args)
    if args.media:
        os.environ["ANIME_NEWS_MEDIA"] = str(Path(args.media).expanduser().resolve())
    from . import app
    app.serve(host=args.host, port=args.port, media=_media(args))


def _storyboard(args) -> dict:
    from . import storyboard
    text = args.text or ""
    if args.text_file:
        text = Path(args.text_file).read_text()
    return storyboard.build(args.headline, text=text, url=args.url or "", minutes=args.minutes,
                            style=args.style or "", mock=True if args.mock else None)


def cmd_storyboard(args) -> None:
    _mock(args)
    try:
        sb = _storyboard(args)
    except Exception as e:  # noqa: BLE001
        print(f"storyboard failed: {type(e).__name__}: {e}", file=sys.stderr)
        sys.exit(2)
    out = json.dumps(sb, indent=1)
    if args.save:
        Path(args.save).write_text(out)
    print(out)


def _render(args, sb: dict, media: Path) -> None:
    from . import render_storyboard as rs
    from .router_client import make_router
    router = make_router(media, args.quality)
    est = rs.estimate(rs.storyboard.normalize(sb), args.quality, media)
    print(f"[anime-news] {router.mode} render: {sum(len(sc['shots']) for sc in sb['scenes'])} shots, "
          f"estimate ${est['usd']:.2f} {est['parts']}", file=sys.stderr, flush=True)
    if args.estimate_only:
        print(json.dumps(est, indent=1))
        return
    def progress(stage, done, total):
        print(f"[anime-news] {stage} {done}/{total}", file=sys.stderr, flush=True)
    try:
        entry = rs.render(sb, router, media, quality=args.quality, vertical=args.vertical, parallel=args.parallel,
                          progress=progress, max_cost=args.max_cost)
    except Exception as e:  # noqa: BLE001
        print(f"render failed: {type(e).__name__}: {e}", file=sys.stderr)
        sys.exit(1)
    _print_episode(entry, media)


def cmd_render(args) -> None:
    _mock(args)
    media = _media(args)
    media.mkdir(parents=True, exist_ok=True)
    if args.retry:
        from . import render_storyboard as rs
        from .router_client import make_router
        router = make_router(media, args.quality)
        try:
            entry = rs.retry(router, media, Path(args.retry).resolve(), args.parallel, args.vertical,
                             max_cost=args.max_cost, estimate_only=args.estimate_only)
        except Exception as e:  # noqa: BLE001
            print(f"retry failed: {type(e).__name__}: {e}", file=sys.stderr)
            sys.exit(1)
        if args.estimate_only:
            print(json.dumps(entry, indent=1))
            return
        _print_episode(entry, media)
        return
    if not args.storyboard:
        print("render needs a storyboard.json (or --retry <episode>.shots.json)", file=sys.stderr)
        sys.exit(2)
    _render(args, json.loads(Path(args.storyboard).read_text()), media)


def cmd_make(args) -> None:
    _mock(args)
    media = _media(args)
    media.mkdir(parents=True, exist_ok=True)
    try:
        sb = _storyboard(args)
    except Exception as e:  # noqa: BLE001
        print(f"storyboard failed: {type(e).__name__}: {e}", file=sys.stderr)
        sys.exit(2)
    path = media / f"storyboard-{sb['id']}.json"
    path.write_text(json.dumps(sb, indent=1))
    print(f"[anime-news] storyboard: {path}", file=sys.stderr, flush=True)
    _render(args, sb, media)


def cmd_weekly(args) -> None:
    _mock(args)
    from . import config, weekly
    media = _media(args)
    media.mkdir(parents=True, exist_ok=True)
    try:
        weeks = weekly.week_range(args.weeks or args.week)
    except ValueError as e:
        print(f"weekly: {e}", file=sys.stderr)
        sys.exit(2)
    router = None
    if not args.plan_only:
        from .router_client import make_router
        router = make_router(media, args.quality)
        print(f"[anime-news] weekly {router.mode}: {len(weeks)} week(s) into {media / 'weekly'}, "
              f"budget ${args.budget_usd:.2f}", file=sys.stderr, flush=True)
    result = weekly.run(weeks, media, router, minutes=args.minutes, quality=args.quality, plan_only=args.plan_only,
                        retry=args.retry, budget_usd=args.budget_usd, force=args.force, do_publish=args.publish,
                        refresh=args.refresh_sources, use_google=not args.no_google,
                        use_viral=not (args.no_viral or args.mock or config.mock_forced()), allow_unfinished=args.allow_unfinished)
    print(json.dumps(result, indent=1))
    if any(w["status"] == "failed" for w in result["weeks"]):
        sys.exit(1)


def _render_opts(p: argparse.ArgumentParser) -> None:
    from .render_storyboard import QUALITIES, SHOT_PARALLEL
    p.add_argument("--quality", choices=QUALITIES, default="480p", help="Seedance resolution (default 480p)")
    p.add_argument("--vertical", action="store_true", help="also export a 9:16 copy")
    p.add_argument("--out", help="media dir (default ~/.anime-news/media or ANIME_NEWS_MEDIA)")
    p.add_argument("--parallel", type=int, default=SHOT_PARALLEL, help="shots rendered at once")
    p.add_argument("--max-cost", type=float, default=None,
                   help="stop before rendering if the USD estimate is over this (live only). `make` and `storyboard` still "
                        "spend the one writer call (about $0.10) that produces the storyboard the estimate is made from")
    p.add_argument("--estimate-only", action="store_true", help="print the render spend estimate and stop (after the writer call, for make)")
    p.add_argument("--mock", action="store_true", help="offline placeholder render, no Router calls")


def _story_opts(p: argparse.ArgumentParser) -> None:
    p.add_argument("headline")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--text", help="article text or notes to write from")
    g.add_argument("--text-file", help="read the article text from a file")
    g.add_argument("--url", help="fetch the article from a URL (fails soft to the headline only)")
    p.add_argument("--minutes", type=float, default=1.0, help="episode length in minutes (default 1, max 5)")
    p.add_argument("--style", help="extra visual style direction for the writer")


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="anime-news", description="A headline in, an anime news episode out.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("serve", help="run the local engine API + UI")
    p.add_argument("--host", default=os.environ.get("HOST", "127.0.0.1"))
    p.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8765")))
    p.add_argument("--media", help="media dir (default ~/.anime-news/media)")
    p.add_argument("--mock", action="store_true", help="never call the Router, even with a key")
    p.set_defaults(fn=cmd_serve)
    p = sub.add_parser("storyboard", help="write a storyboard and print it as JSON")
    _story_opts(p)
    p.add_argument("--save", help="also write the JSON to this file")
    p.add_argument("--mock", action="store_true", help="template storyboard, no Router call")
    p.set_defaults(fn=cmd_storyboard)
    p = sub.add_parser("render", help="render a storyboard JSON into an episode")
    p.add_argument("storyboard", nargs="?")
    p.add_argument("--retry", help="re-render only the failed shots of an <episode>.shots.json, then re-cut")
    _render_opts(p)
    p.set_defaults(fn=cmd_render)
    p = sub.add_parser("make", help="storyboard + render in one go")
    _story_opts(p)
    _render_opts(p)
    p.set_defaults(fn=cmd_make)
    p = sub.add_parser("weekly", help="THE WEEKLY BRIEFING: a week's five headlines in, one episode out (batch backfill)")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--week", help="one ISO week, e.g. 2026-W44")
    g.add_argument("--weeks", help="a range A..B, e.g. 2026-W01..2026-W44, run one at a time in that order")
    p.add_argument("--minutes", type=float, default=1.0, help="episode length (default 1, about 20 shots; 3 for the top weeks)")
    from .render_storyboard import QUALITIES
    p.add_argument("--quality", choices=QUALITIES, default="480p", help="Seedance resolution (default 480p)")
    p.add_argument("--out", help="media dir (default ~/.anime-news/media); weeks go to <out>/weekly/<week>/")
    p.add_argument("--mock", action="store_true", help="offline placeholder render and template storyboard, no Router calls")
    p.add_argument("--plan-only", action="store_true",
                   help="fetch the sources, write the storyboard, print the five headlines and the spend estimate; "
                        "renders nothing and never calls the writer")
    p.add_argument("--retry", action="store_true", help="re-render only the failed shots of partial weeks, then re-cut")
    p.add_argument("--budget-usd", type=float, default=100.0,
                   help="stop before a week whose estimate would take the batch's live spend over this (default 100)")
    p.add_argument("--publish", action="store_true", help="mark the rendered weeks as published in the manifest")
    p.add_argument("--force", action="store_true", help="re-render weeks that are already done")
    p.add_argument("--refresh-sources", action="store_true", help="refetch Wikipedia even when the week is cached")
    p.add_argument("--no-google", action="store_true", help="never use Google News to fill a topic Wikipedia left empty")
    p.add_argument("--no-viral", action="store_true", help="skip the last30days engagement boost for recent weeks")
    p.add_argument("--allow-unfinished", action="store_true", help="also plan or render a week that is not over yet")
    p.set_defaults(fn=cmd_weekly)
    args = ap.parse_args(argv)
    args.fn(args)


if __name__ == "__main__":
    main()
