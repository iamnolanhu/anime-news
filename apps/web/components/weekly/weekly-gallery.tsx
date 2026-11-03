"use client";

import Link from "next/link";
import { useWeekly } from "./use-weekly";
import { WeeklyCard, WeeklySkeleton } from "./weekly-card";

/** The weekly briefings, newest first. `limit` shows only the newest few and links on to /weekly. */
export function WeeklyGallery({ limit }: { limit?: number }) {
  const { status, weeks } = useWeekly();
  if (status === "loading") return <WeeklySkeleton />;
  if (weeks.length === 0) {
    return (
      <div className="panel brackets p-6">
        <p className="display text-[1.6rem] text-gold">The first briefing is rendering</p>
        <p className="meta mt-2 max-w-md">
          {status === "unreachable" ? "The engine could not be reached just now. " : ""}Finished weeks appear here on their own, newest first.
        </p>
      </div>
    );
  }
  const shown = limit ? weeks.slice(0, limit) : weeks;
  return (
    <>
      <div className="space-y-14">
        {shown.map((w) => (
          <WeeklyCard key={w.week} episode={w} />
        ))}
      </div>
      {limit && weeks.length > limit ? (
        <p className="mt-8">
          <Link href="/weekly" className="btn-ghost px-4 py-3">
            All {weeks.length} weekly briefings
          </Link>
        </p>
      ) : null}
    </>
  );
}
