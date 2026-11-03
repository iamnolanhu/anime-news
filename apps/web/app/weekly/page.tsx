import type { Metadata } from "next";
import Link from "next/link";
import { WeeklyGallery } from "@/components/weekly/weekly-gallery";

export const metadata: Metadata = {
  title: "Weekly briefings",
  description: "One anime briefing per week: the top headline in AI, crypto, politics, tech and markets, rendered shot by shot.",
};

export default function WeeklyPage() {
  return (
    <div className="mx-auto max-w-[1240px] px-4 pt-10 sm:px-6 lg:pt-16">
      <p className="kicker">The Weekly Briefing</p>
      <div className="mt-3 flex flex-wrap items-end justify-between gap-4">
        <h1 className="display text-[clamp(2.6rem,8vw,6rem)]">Weekly briefings</h1>
        <p className="meta max-w-md pb-2">
          Five headlines a week: AI, crypto, politics, tech and markets. Newest first. The three hand-made{" "}
          <Link href="/episodes" className="link-cyan">specials</Link> live on the episodes page.
        </p>
      </div>
      <div className="mt-10">
        <WeeklyGallery />
      </div>
    </div>
  );
}
