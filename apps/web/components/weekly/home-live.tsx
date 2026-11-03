"use client";

import { HeroPlayer } from "@/components/hero-player";
import { fromSpecial, durationText, type WeeklyEpisode } from "@/lib/weekly";
import type { Episode } from "@/lib/episodes";
import { useWeekly } from "./use-weekly";

/** The newest finished weekly briefing from the manifest, else the newest hand-made special. */
function useLatest(fallback: Episode): WeeklyEpisode {
  const { weeks } = useWeekly();
  return weeks[0] ?? fromSpecial(fallback);
}

export function HomeTicker({ fallback }: { fallback: Episode[] }) {
  const { weeks } = useWeekly();
  const items = weeks[0]?.headlines.length ? weeks[0].headlines.map((h) => h.headline) : fallback.map((e) => e.headline);
  const ticker = [...items, ...items];
  return (
    <div className="ticker" role="marquee" aria-label="This week's headlines">
      <div className="ticker-track py-2">
        {ticker.map((headline, i) => (
          <span key={`${headline}-${i}`} className="flex items-center gap-3 px-6 font-mono text-[11px] uppercase tracking-[0.16em] text-gold-pale">
            <span className="text-cyan">This week</span>
            {headline}
          </span>
        ))}
      </div>
    </div>
  );
}

export function HomeKicker({ fallback }: { fallback: Episode }) {
  return <p className="kicker">The Weekly Briefing / {useLatest(fallback).heading}</p>;
}

export function HomeHeroBlock({ fallback }: { fallback: Episode }) {
  const latest = useLatest(fallback);
  const first = latest.headlines[0]?.headline;
  return (
    <>
      <div className="panel brackets p-2">
        <HeroPlayer key={latest.video} src={latest.video} poster={latest.poster ?? undefined} title={latest.title} />
      </div>
      <div className="mt-3 flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
        <p className="meta">
          <span className="text-gold">This week's briefing</span> / {latest.weekLabel} / {latest.title}
          {latest.duration != null ? ` / ${durationText(latest.duration)}` : ""}
          {latest.shots ? ` / ${latest.shots} shots / 0 reused` : ""}
        </p>
        {first ? <p className="meta min-w-0 max-w-full truncate">&ldquo;{first}&rdquo;</p> : null}
      </div>
    </>
  );
}
