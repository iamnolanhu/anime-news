"use client";

import { useEffect, useState } from "react";
import { parseWeekly, WEEKLY_MANIFEST_URL, WEEKLY_REFRESH_MS, type WeeklyEpisode } from "@/lib/weekly";

export type WeeklyState = { status: "loading" | "ready" | "unreachable"; weeks: WeeklyEpisode[] };

/** Reads the weekly manifest in the browser and again every 60 s. A failed refresh keeps what was already loaded. */
export function useWeekly(): WeeklyState {
  const [state, setState] = useState<WeeklyState>({ status: "loading", weeks: [] });

  useEffect(() => {
    let alive = true;
    const load = async () => {
      try {
        const res = await fetch(WEEKLY_MANIFEST_URL, { cache: "no-store" });
        if (!res.ok) throw new Error(String(res.status));
        const weeks = parseWeekly(await res.json());
        if (alive) setState({ status: "ready", weeks });
      } catch {
        if (alive) setState((s) => (s.status === "ready" ? s : { status: "unreachable", weeks: [] }));
      }
    };
    void load();
    const timer = setInterval(load, WEEKLY_REFRESH_MS);
    return () => {
      alive = false;
      clearInterval(timer);
    };
  }, []);

  return state;
}
