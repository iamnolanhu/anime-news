import { formatDuration, type Episode } from "@/lib/episodes";

/** The hosted engine's weekly manifest, reached through this site's /engine proxy (see proxy.ts). */
export const WEEKLY_MANIFEST_URL = "/engine/api/weekly";
/** The manifest is re-read this often while a page stays open. */
export const WEEKLY_REFRESH_MS = 60_000;

export type WeeklyHeadline = {
  topic: string;
  headline: string;
  sources: { name: string; url: string }[];
};

export type WeeklyEpisode = {
  week: string;
  /** "Week 44 · Oct 26–Nov 1" */
  heading: string;
  weekLabel: string;
  title: string;
  status: "done" | "partial";
  headlines: WeeklyHeadline[];
  duration: number | null;
  shots: number | null;
  cutsPerMinute: number | null;
  video: string;
  vertical: string | null;
  poster: string | null;
};

type RawHeadline = { topic?: string; headline?: string; sources?: { name?: string; url?: string }[] };
type RawWeek = {
  week?: string;
  start?: string;
  end?: string;
  title?: string;
  status?: string;
  headlines?: RawHeadline[];
  duration?: number | null;
  shots?: number | null;
  cut_shots?: number | null;
  cuts_per_minute?: number | null;
  mp4_url?: string | null;
  vertical_url?: string | null;
  poster_url?: string | null;
  files?: { mp4?: string | null; vertical?: string | null; poster?: string | null } | null;
};

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

function day(iso: string): { m: number; d: number } | null {
  const hit = /^\d{4}-(\d{2})-(\d{2})/.exec(iso);
  return hit ? { m: Number(hit[1]) - 1, d: Number(hit[2]) } : null;
}

/** "2026-10-26", "2026-11-01" becomes "Oct 26–Nov 1"; same month: "Aug 10–16". */
export function dateRange(start?: string, end?: string): string {
  const a = start ? day(start) : null;
  const b = end ? day(end) : null;
  if (!a || !b) return "";
  return a.m === b.m ? `${MONTHS[a.m]} ${a.d}–${b.d}` : `${MONTHS[a.m]} ${a.d}–${MONTHS[b.m]} ${b.d}`;
}

function mediaUrl(week: string, relative: string | null | undefined, file: string | null | undefined): string | null {
  if (relative) return relative.startsWith("http") ? relative : `/engine${relative.startsWith("/") ? "" : "/"}${relative}`;
  if (file) return `/engine/media/weekly/${week}/${file}`;
  return null;
}

/** Keeps only weeks that are playable (done or partial) and fills the optional fields the manifest may not carry yet. */
export function parseWeekly(json: unknown): WeeklyEpisode[] {
  const weeks = (json as { weeks?: RawWeek[] } | null)?.weeks;
  if (!Array.isArray(weeks)) return [];
  const out: WeeklyEpisode[] = [];
  for (const w of weeks) {
    if (!w.week || (w.status !== "done" && w.status !== "partial")) continue;
    const video = mediaUrl(w.week, w.mp4_url, w.files?.mp4);
    if (!video) continue;
    const num = Number(w.week.split("-W")[1]);
    const range = dateRange(w.start, w.end);
    out.push({
      week: w.week,
      weekLabel: `Week ${num}`,
      heading: range ? `Week ${num} · ${range}` : `Week ${num}`,
      title: w.title ?? `Week ${num}`,
      status: w.status,
      headlines: (w.headlines ?? [])
        .filter((h) => h.headline)
        .map((h) => ({
          topic: h.topic ?? "news",
          headline: h.headline ?? "",
          sources: (h.sources ?? []).filter((s) => s.url).map((s) => ({ name: s.name ?? "source", url: s.url ?? "" })),
        })),
      duration: typeof w.duration === "number" ? w.duration : null,
      shots: w.shots ?? w.cut_shots ?? null,
      cutsPerMinute: w.cuts_per_minute ?? null,
      video,
      vertical: mediaUrl(w.week, w.vertical_url, w.files?.vertical),
      poster: mediaUrl(w.week, w.poster_url, w.files?.poster),
    });
  }
  return out;
}

export const durationText = (s: number | null) => (s == null ? "" : formatDuration(s));

/** The static newest special, shaped like a weekly episode: the fallback when the engine is unreachable. */
export function fromSpecial(e: Episode): WeeklyEpisode {
  return {
    week: e.week,
    weekLabel: `Week ${Number(e.week.split("-W")[1])}`,
    heading: `Week ${Number(e.week.split("-W")[1])} · ${e.dates}`,
    title: e.title,
    status: "done",
    headlines: [{ topic: "news", headline: e.headline, sources: [] }],
    duration: e.duration,
    shots: e.shots,
    cutsPerMinute: e.cutsPerMinute,
    video: e.video,
    vertical: e.vertical ?? null,
    poster: e.poster,
  };
}
