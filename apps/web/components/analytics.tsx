"use client";

import { GoogleAnalytics } from "@next/third-parties/google";
import { Analytics as VercelAnalytics } from "@vercel/analytics/next";
import { SpeedInsights } from "@vercel/speed-insights/next";
import { useEffect, useState } from "react";
import { attachPostHog, studioMode, trackingAllowed } from "@/lib/analytics";

let posthogStarted = false;

/**
 * Four trackers, all off on a local host (checked in the browser, after mount):
 * Vercel Web Analytics, Vercel Speed Insights, Google Analytics 4 (only when NEXT_PUBLIC_GA_ID is set)
 * and PostHog (only when NEXT_PUBLIC_POSTHOG_KEY is set). PostHog keeps session replay with every input
 * masked, so a pasted Comfy API key never reaches it.
 */
export function Analytics() {
  const [live, setLive] = useState(false);
  const gaId = process.env.NEXT_PUBLIC_GA_ID;

  useEffect(() => {
    if (!trackingAllowed()) return;
    setLive(true);
    const key = process.env.NEXT_PUBLIC_POSTHOG_KEY;
    if (!key || posthogStarted) return; // init exactly once per page load, real hosts only
    posthogStarted = true;
    import("posthog-js").then(({ default: posthog }) => {
      posthog.init(key, {
        api_host: process.env.NEXT_PUBLIC_POSTHOG_HOST || "https://us.i.posthog.com",
        person_profiles: "identified_only",
        capture_pageview: "history_change",
        capture_pageleave: true,
        capture_exceptions: true, // window error + unhandledrejection
        capture_performance: { web_vitals: true },
        disable_session_recording: false,
        session_recording: { maskAllInputs: true },
        mask_all_text: false,
      });
      const tagMode = () => {
        const mode = studioMode();
        posthog.register({ app: "anime-news", studio_mode: mode });
        posthog.setPersonProperties({ studio_mode: mode });
      };
      tagMode();
      window.addEventListener("storage", tagMode);
      attachPostHog(posthog);
    });
  }, []);

  if (!live) return null;
  return (
    <>
      <VercelAnalytics mode="production" />
      <SpeedInsights />
      {gaId ? <GoogleAnalytics gaId={gaId} /> : null}
    </>
  );
}
