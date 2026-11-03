import type { Metadata } from "next";
import Link from "next/link";
import { EpisodeCard } from "@/components/episode-card";
import { episodes } from "@/lib/episodes";

export const metadata: Metadata = {
  title: "Episodes",
  description: "Finished Anime News episodes: one headline each, every shot rendered fresh, none reused.",
};

export default function EpisodesPage() {
  return (
    <div className="mx-auto max-w-[1240px] px-4 pt-10 sm:px-6 lg:pt-16">
      <p className="kicker">The Hourly Briefing</p>
      <div className="mt-3 flex flex-wrap items-end justify-between gap-4">
        <h1 className="display text-[clamp(3rem,8vw,6rem)]">Episodes</h1>
        <p className="meta max-w-md pb-2">
          Each one started as a single headline. Every shot was rendered for this episode, and no moment of footage appears twice.{" "}
          <Link href="/studio" className="link-cyan">Make your own</Link>.
        </p>
      </div>
      <div className="mt-10 space-y-14">
        {episodes.map((e, i) => (
          <EpisodeCard key={e.slug} episode={e} index={episodes.length - i} />
        ))}
      </div>
    </div>
  );
}
