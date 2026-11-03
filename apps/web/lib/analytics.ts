/**
 * Analytics helpers shared by the site chrome and the Studio.
 *
 * Everything here is a no-op on a local host (localhost, 127.0.0.1, *.localhost) and on the server.
 * Nothing sent from here may contain a Comfy API key, a prompt or a storyboard body: counts, ids and
 * short error classes only.
 *
 * Funnel events the Studio should emit with track():
 *   studio_opened       {}
 *   engine_detected     { mode: "local" | "hosted" | "none" }
 *   storyboard_written  { scenes, shots, minutes }
 *   render_started      { quality }
 *   render_done         { duration, shots, seconds_to_render }
 *   render_failed       { stage, error }
 *   episode_played      { id }
 */

export type FunnelEvents = {
  studio_opened: Record<string, never>;
  engine_detected: { mode: "local" | "hosted" | "none" };
  storyboard_written: { scenes: number; shots: number; minutes: number };
  render_started: { quality: string };
  render_done: { duration: number; shots: number; seconds_to_render: number };
  render_failed: { stage: string; error: string };
  episode_played: { id: string };
};

export type EventName = keyof FunnelEvents;

const LOCAL_HOSTS = new Set(["localhost", "127.0.0.1", "[::1]", "0.0.0.0"]);

export function isLocalHost(hostname: string): boolean {
  return LOCAL_HOSTS.has(hostname) || hostname.endsWith(".localhost");
}

/** True in a browser on a real (non-local) host. */
export function trackingAllowed(): boolean {
  return typeof window !== "undefined" && !isLocalHost(window.location.hostname);
}

/** Studio mode from the Studio's own localStorage key (read only): "none", "local" or "hosted". */
export function studioMode(): "local" | "hosted" | "none" {
  try {
    const url = window.localStorage.getItem("anime-news:engine-url");
    if (!url) return "none";
    return isLocalHost(new URL(url).hostname) ? "local" : "hosted";
  } catch {
    return "none";
  }
}

type PostHogLike = { capture: (event: string, props?: Record<string, unknown>) => void };
let posthogRef: PostHogLike | null = null;

/** Called by the Analytics component once PostHog has initialised. */
export function attachPostHog(client: PostHogLike): void {
  posthogRef = client;
}

type Gtag = (command: "event", name: string, params?: Record<string, unknown>) => void;

export function track<E extends EventName>(event: E, props?: FunnelEvents[E]): void {
  if (!trackingAllowed()) return;
  const payload = { ...(props ?? {}) } as Record<string, unknown>;
  posthogRef?.capture(event, payload);
  const gtag = (window as unknown as { gtag?: Gtag }).gtag;
  if (typeof gtag === "function" && process.env.NEXT_PUBLIC_GA_ID) gtag("event", event, payload);
}
