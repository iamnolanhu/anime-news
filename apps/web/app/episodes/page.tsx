import type { Metadata } from "next";
import Link from "next/link";
import { EpisodeCard } from "@/components/episode-card";
import { WeeklyGallery } from "@/components/weekly/weekly-gallery";
import { episodes } from "@/lib/episodes";

export const metadata: Metadata = {
  title: "Episodes",
  description: "The Weekly Briefing: one anime episode per week, five headlines each, every shot rendered fresh, none reused.",
};

export default function EpisodesPage() {
  return (
    <div className="mx-auto max-w-[1240px] px-4 pt-10 sm:px-6 lg:pt-16">
      <p className="kicker">The Weekly Briefing</p>
      <div className="mt-3 flex flex-wrap items-end justify-between gap-4">
        <h1 className="display text-[clamp(3rem,8vw,6rem)]">Episodes</h1>
        <p className="meta max-w-md pb-2">
          One episode per ISO week, built from that week's top headlines. Every shot was rendered for this episode, and no moment of footage appears twice.{" "}
          <Link href="/studio" className="link-cyan">Make your own</Link>.
        </p>
      </div>
      <section aria-labelledby="weekly" className="mt-12">
        <div className="flex flex-wrap items-end justify-between gap-4">
          <h2 id="weekly" className="display text-[clamp(2rem,4vw,3rem)]">Weekly briefings</h2>
          <Link href="/weekly" className="link-cyan font-mono text-[12px] uppercase tracking-[0.14em]">Open the weekly page</Link>
        </div>
        <div className="mt-6">
          <WeeklyGallery limit={3} />
        </div>
      </section>
      <h2 className="display mt-20 text-[clamp(2rem,4vw,3rem)]">Specials</h2>
      <div className="mt-6 space-y-14">
        {episodes.map((e, i) => (
          <EpisodeCard key={e.slug} episode={e} index={episodes.length - i} />
        ))}
      </div>
    </div>
  );
}
