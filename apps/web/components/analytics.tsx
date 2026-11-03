"use client";

import { useEffect } from "react";

const LOCAL_HOSTS = new Set(["localhost", "127.0.0.1", "[::1]", "0.0.0.0"]);

/**
 * PostHog with session replay. Loads only when NEXT_PUBLIC_POSTHOG_KEY is set and never on a local host.
 * Every input is masked in replays, so a pasted Comfy API key never reaches PostHog.
 */
export function Analytics() {
  useEffect(() => {
    const key = process.env.NEXT_PUBLIC_POSTHOG_KEY;
    if (!key || LOCAL_HOSTS.has(window.location.hostname) || window.location.hostname.endsWith(".localhost")) return;
    let cancelled = false;
    import("posthog-js").then(({ default: posthog }) => {
      if (cancelled) return;
      posthog.init(key, {
        api_host: process.env.NEXT_PUBLIC_POSTHOG_HOST || "https://us.i.posthog.com",
        person_profiles: "identified_only",
        capture_pageview: "history_change",
        disable_session_recording: false,
        session_recording: { maskAllInputs: true },
        mask_all_text: false,
      });
      posthog.register({ app: "anime-news" });
    });
    return () => {
      cancelled = true;
    };
  }, []);
  return null;
}
