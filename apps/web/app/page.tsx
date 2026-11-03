import Link from "next/link";
import { CopyCommand } from "@/components/copy-command";
import { HeroPlayer } from "@/components/hero-player";
import { episodes, formatDuration, weekHeading, weekLabel } from "@/lib/episodes";
import { INSTALL_TOOLS, REPO_URL, UVX_MAKE, UVX_SERVE } from "@/lib/site";

const STEPS = [
  {
    n: "01",
    title: "Headlines",
    body: "Paste a headline or a week's worth of headlines, plus the article text or link if you have it. Pick a length from 30 seconds to 5 minutes.",
    tech: "you type",
  },
  {
    n: "02",
    title: "Storyboard",
    body: "One writer call turns it into a cast, scenes and shots: a deadpan anchor, the thesis, the absurd literal cutaways. Edit any of it.",
    tech: "writer model, Comfy Router",
  },
  {
    n: "03",
    title: "Stills + Seedance shots",
    body: "Every shot gets its own FLUX start frame, then a Seedance clip: talking takes with their own voice, action takes with motion.",
    tech: "FLUX + Seedance, Comfy Router",
  },
  {
    n: "04",
    title: "Anime cut",
    body: "About 39 cuts a minute, subtitles, music riser and drop, title card. No source moment is shown twice. 16:9 and 9:16.",
    tech: "ffmpeg, on your machine",
  },
];

export default function Home() {
  const latest = episodes[0];
  const thisWeek = episodes.filter((e) => e.week === latest.week);
  const ticker = [...thisWeek, ...thisWeek];

  return (
    <>
      <div className="ticker" role="marquee" aria-label="This week's headlines">
        <div className="ticker-track py-2">
          {ticker.map((e, i) => (
            <span key={`${e.slug}-${i}`} className="flex items-center gap-3 px-6 font-mono text-[11px] uppercase tracking-[0.16em] text-gold-pale">
              <span className="text-cyan">This week</span>
              {e.headline}
            </span>
          ))}
        </div>
      </div>

      <section className="mx-auto max-w-[1240px] px-4 pb-16 pt-10 sm:px-6 lg:pt-14">
        <p className="kicker">The Weekly Briefing / {weekHeading(latest)}</p>
        <h1 className="display mt-5 text-[clamp(3rem,6.6vw,6.4rem)] text-white">
          A week of headlines in.
          <span className="block text-gold [text-shadow:0_0_28px_rgba(245,197,24,.25)]">One anime briefing out.</span>
        </h1>
        <div className="mt-10 grid grid-cols-1 gap-10 lg:grid-cols-12 lg:gap-12">
          <div className="min-w-0 lg:col-span-4">
            <div className="rule-gold w-24" />
            <p className="mt-6 text-[16px] leading-relaxed text-white/80">
              Every week, five headlines, one episode. Anime News writes the storyboard, renders a start frame and a Seedance shot for every
              beat through Comfy Router, and cuts it like an anime briefing. It runs on your machine with your own Comfy API key.
            </p>
            <div className="mt-8 flex flex-wrap gap-3">
              <Link href="/studio" className="btn-gold">
                Open the Studio
              </Link>
              <Link href="/episodes" className="btn-ghost px-4 py-3">
                Watch the briefings
              </Link>
              <a href={REPO_URL} target="_blank" rel="noreferrer" className="btn-ghost px-4 py-3">
                Source on GitHub
              </a>
            </div>
          </div>
          <div className="min-w-0 lg:col-span-8">
            <div className="panel brackets p-2">
              <HeroPlayer src={latest.video} poster={latest.poster} title={latest.title} />
            </div>
            <div className="mt-3 flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
              <p className="meta">
                <span className="text-gold">This week's briefing</span> / {weekLabel(latest.week)} / {latest.title} / {formatDuration(latest.duration)} / {latest.shots} shots / 0 reused
              </p>
              <p className="meta min-w-0 max-w-full truncate">&ldquo;{latest.headline}&rdquo;</p>
            </div>
          </div>
        </div>
      </section>

      <section aria-label="Episode numbers" className="border-y border-[var(--sd-gold-line)] bg-panel/60">
        <dl className="mx-auto grid max-w-[1240px] grid-cols-2 px-4 sm:px-6 md:grid-cols-4">
          {[
            [String(latest.shots), "shots in a minute"],
            [String(Math.round(latest.cutsPerMinute)), "cuts per minute"],
            ["0", "reused shots"],
            ["5", "headlines per week"],
          ].map(([value, label], i) => (
            <div key={label} className={`flex flex-col-reverse py-7 ${i > 0 ? "md:border-l md:border-white/10 md:pl-8" : ""} ${i % 2 === 1 ? "border-l border-white/10 pl-6 md:pl-8" : ""}`}>
              <dt className="meta">{label}</dt>
              <dd className="display text-[3.4rem] text-gold">{value}</dd>
            </div>
          ))}
        </dl>
      </section>

      <section className="mx-auto max-w-[1240px] px-4 pt-20 sm:px-6" aria-labelledby="how">
        <div className="flex flex-wrap items-end justify-between gap-4">
          <div>
            <p className="kicker">Rundown</p>
            <h2 id="how" className="display mt-3 text-[clamp(2.4rem,5vw,3.8rem)]">How it works</h2>
          </div>
          <p className="meta max-w-sm">Four segments, one pass. A one-minute episode is about ten shots rendered in parallel.</p>
        </div>
        <ol className="mt-8 border-t border-white/10">
          {STEPS.map((s) => (
            <li key={s.n} className="grid grid-cols-[3.5rem_1fr] gap-x-4 gap-y-1 border-b border-white/10 py-6 md:grid-cols-[6rem_16rem_1fr_14rem] md:items-baseline md:gap-x-8">
              <span className="display row-span-2 text-[2.6rem] text-gold md:row-span-1 md:text-[3.2rem]">{s.n}</span>
              <h3 className="display text-[1.7rem] tracking-[0.02em]">{s.title}</h3>
              <p className="text-white/75 md:col-auto">{s.body}</p>
              <p className="meta col-start-2 md:col-auto md:text-right">{s.tech}</p>
            </li>
          ))}
        </ol>
      </section>

      <section className="mx-auto grid max-w-[1240px] gap-8 px-4 pt-20 sm:px-6 grid-cols-1 lg:grid-cols-12" aria-labelledby="run">
        <div className="min-w-0 lg:col-span-7">
          <p className="kicker">Run it yourself</p>
          <h2 id="run" className="display mt-3 text-[clamp(2.4rem,5vw,3.8rem)]">Three commands</h2>
          <ol className="mt-6 space-y-4">
            <li>
              <p className="meta mb-1.5">1. Install uv and ffmpeg (macOS; on Linux use your package manager)</p>
              <CopyCommand command={INSTALL_TOOLS} />
            </li>
            <li>
              <p className="meta mb-1.5">2. Start the engine on 127.0.0.1:8765, then open the Studio</p>
              <CopyCommand command={UVX_SERVE} />
            </li>
            <li>
              <p className="meta mb-1.5">3. Or skip the browser: one headline in, one episode out</p>
              <CopyCommand command={UVX_MAKE} />
            </li>
          </ol>
        </div>
        <aside className="panel brackets self-start p-6 lg:col-span-5 lg:mt-24">
          <p className="kicker">Your key stays on your machine</p>
          <p className="mt-4 text-white/85">
            The Studio talks to the engine on your own computer. Your Comfy API key is kept in your browser and in{" "}
            <code className="font-mono text-[13px] text-gold-pale">~/.anime-news/config.json</code>, and your storyboards and videos stay in{" "}
            <code className="font-mono text-[13px] text-gold-pale">~/.anime-news/</code>.
          </p>
          <p className="mt-3 text-white/60">This website never receives your key, your scripts or your renders.</p>
        </aside>
      </section>

      <section className="mx-auto max-w-[1240px] px-4 pt-20 sm:px-6" aria-labelledby="latest">
        <div className="flex items-end justify-between gap-4">
          <h2 id="latest" className="display text-[clamp(2rem,4vw,3rem)]">Weekly briefings</h2>
          <Link href="/episodes" className="link-cyan font-mono text-[12px] uppercase tracking-[0.14em]">
            All weeks
          </Link>
        </div>
        <div className="mt-6 grid gap-5 sm:grid-cols-2 lg:grid-cols-3">
          {episodes.map((e) => (
            <Link key={e.slug} href={`/episodes#${e.slug}`} className="group block">
              <div className="relative aspect-video overflow-hidden border border-white/10 transition-[border-color] duration-150 group-hover:border-gold">
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img src={e.poster} alt="" className="h-full w-full object-cover" />
                <span className="absolute bottom-2 right-2 bg-black/80 px-1.5 py-0.5 font-mono text-[11px] text-white">{formatDuration(e.duration)}</span>
              </div>
              <p className="meta mt-3 text-gold">{weekHeading(e)}</p>
              <p className="display mt-1 text-[1.5rem] group-hover:text-gold">{e.title}</p>
              <p className="meta mt-1 line-clamp-2">{e.headline}</p>
            </Link>
          ))}
        </div>
      </section>
    </>
  );
}
