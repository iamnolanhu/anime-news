import data from "@/content/episodes.json";

export type Episode = {
  slug: string;
  title: string;
  headline: string;
  date: string;
  duration: number;
  shots: number;
  cutsPerMinute: number;
  reusedShots: number;
  video: string;
  vertical?: string;
  poster: string;
  verticalPoster?: string;
};

/** Newest first: add an episode by adding an entry at the top of content/episodes.json and its files under public/episodes/. */
export const episodes: Episode[] = data.episodes;

export function formatDuration(seconds: number): string {
  const s = Math.round(seconds);
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}
