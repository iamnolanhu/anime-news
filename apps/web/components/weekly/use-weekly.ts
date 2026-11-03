"use client";

import { useEffect, useState } from "react";
import {
  parseWeekly,
  WEEKLY_MANIFEST_URL,
  WEEKLY_REFRESH_MS,
  WEEKLY_STATIC_MANIFEST_URL,
  WEEKLY_STATIC_ONLY,
  type WeeklyEpisode,
} from "@/lib/weekly";

export type WeeklyState = { status: "loading" | "ready" | "unreachable"; weeks: WeeklyEpisode[] };

async function readManifest(url: string): Promise<WeeklyEpisode[]> {
  const res = await fetch(url, { cache: "no-store" });
  if (!res.ok) throw new Error(String(res.status));
  const weeks = parseWeekly(await res.json());
  if (weeks.length === 0) throw new Error("no weeks");
  return weeks;
}

/** Reads the weekly manifest in the browser and again every 60 s (once when static). Engine first, then the static copy; a failed refresh keeps what was already loaded. */
export function useWeekly(): WeeklyState {
  const [state, setState] = useState<WeeklyState>({ status: "loading", weeks: [] });

  useEffect(() => {
    let alive = true;
    const load = async () => {
      try {
        const weeks = WEEKLY_STATIC_ONLY
          ? await readManifest(WEEKLY_STATIC_MANIFEST_URL)
          : await readManifest(WEEKLY_MANIFEST_URL).catch(() => readManifest(WEEKLY_STATIC_MANIFEST_URL));
        if (alive) setState({ status: "ready", weeks });
      } catch {
        if (alive) setState((s) => (s.status === "ready" ? s : { status: "unreachable", weeks: [] }));
      }
    };
    void load();
    const timer = WEEKLY_STATIC_ONLY ? undefined : setInterval(load, WEEKLY_REFRESH_MS);
    return () => {
      alive = false;
      if (timer) clearInterval(timer);
    };
  }, []);

  return state;
}
