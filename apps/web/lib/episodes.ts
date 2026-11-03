import data from "@/content/episodes.json";

export type Episode = {
  slug: string;
  title: string;
  headline: string;
  date: string;
  /** ISO week the episode covers, e.g. "2026-W45". */
  week: string;
  /** The week as a date range for display, e.g. "Nov 2–8". */
  dates: string;
  duration: number;
  shots: number;
  cutsPerMinute: number;
  reusedShots: number;
  video: string;
  vertical?: string;
  poster: string;
  verticalPoster?: string;
};

/** "2026-W45" becomes "Week 45". */
export function weekLabel(week: string): string {
  return `Week ${Number(week.split("-W")[1])}`;
}

/** "Week 45 · Nov 2–8". */
export function weekHeading(e: Pick<Episode, "week" | "dates">): string {
  return `${weekLabel(e.week)} · ${e.dates}`;
}

/** Newest first: add an episode by adding an entry at the top of content/episodes.json and its files under public/episodes/. */
export const episodes: Episode[] = data.episodes;

export function formatDuration(seconds: number): string {
  const s = Math.round(seconds);
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}
