import { EngineError, type Engine } from "./engine";
import { newId } from "./storyboard-ops";
import type { Act, CastMember, Job, Scene, Shot, Storyboard } from "./types";

/**
 * In-browser stand-in for the local engine (`/studio?mock=1`). Same interface, no network:
 * the key is only remembered as "set" in memory, renders finish in a few seconds and play a sample episode.
 */

const ACT_PLAN: Act[] = ["cold_open", "setup", "escalation", "turn", "climax", "sign_off"];
const MOVES = ["slow push in", "static", "whip pan", "crash zoom", "orbit", "handheld", "crane up", "dolly out"];

function cast(): CastMember[] {
  return [
    { id: "anchor", name: "The Anchor", role: "deadpan news anchor", look: "silver hair, dark suit with gold piping, PRESS badge", voice: "low, grave, unhurried" },
    { id: "reporter", name: "Field Reporter", role: "breathless correspondent", look: "yellow raincoat, headset, notepad", voice: "fast, urgent" },
    { id: "analyst", name: "The Analyst", role: "overconfident expert", look: "round glasses, chalk-dusted cardigan", voice: "nasal, certain" },
  ];
}

function shotsFor(sceneIndex: number, headline: string): Shot[] {
  const kinds: Shot["kind"][] = ["talk", "action", "insert", "action"];
  return kinds.map((kind, i) => ({
    id: newId("shot"),
    kind,
    cast: kind === "talk" ? [i === 0 && sceneIndex % 2 === 0 ? "anchor" : "reporter"] : [],
    line: kind === "talk" ? (sceneIndex === 0 ? `This week: ${headline}.` : "Sources confirm it is worse than it sounds.") : undefined,
    prompt:
      kind === "talk"
        ? "2D cel anime frame, news desk in front of a wall of world-map monitors, the speaker looks straight into the lens"
        : kind === "insert"
          ? "2D cel anime frame, extreme close-up of a glowing monitor showing the headline as on-screen text"
          : `2D cel anime frame, a literal and absurd staging of: ${headline}`,
    move: MOVES[(sceneIndex * 4 + i) % MOVES.length],
    seconds: 5,
  }));
}

function template(headline: string, minutes: number): Storyboard {
  const sceneCount = Math.max(2, Math.round(minutes * 4));
  const scenes: Scene[] = Array.from({ length: sceneCount }, (_, i) => {
    const act = i === 0 ? "cold_open" : i === sceneCount - 1 ? "sign_off" : ACT_PLAN[1 + ((i - 1) % 4)];
    return {
      id: newId("scene"),
      act,
      setting: i % 2 === 0 ? "News desk, night, monitor glow" : "City street, rain, neon signs",
      caption: i === 0 ? headline : `Segment ${i + 1}`,
      narration: i === 0 ? `Welcome to The Weekly Briefing. ${headline}.` : "The situation, our sources say, is developing.",
      shots: shotsFor(i, headline),
    };
  });
  return {
    id: newId("sb"),
    headline,
    source_text_excerpt: "",
    series: "THE WEEKLY BRIEFING",
    logline: `The Anchor treats "${headline}" as the gravest story of the century.`,
    minutes,
    cast: cast(),
    scenes,
  };
}

type MockJob = { id: string; started: number; total: number; vertical: boolean; cancelled: boolean };

export function createMockEngine(): Engine {
  let hasKey = false;
  const jobs = new Map<string, MockJob>();
  const wait = (ms: number) => new Promise((r) => setTimeout(r, ms));

  function snapshot(j: MockJob): Job {
    const queued = 600;
    const perShot = Math.min(350, 7000 / Math.max(1, j.total));
    const renderEnd = queued + perShot * j.total;
    const cutEnd = renderEnd + 1500;
    const t = Date.now() - j.started;
    const base = { job_id: j.id, total: j.total, started: new Date(j.started).toISOString() };
    if (j.cancelled) return { ...base, status: "failed", stage: "cancelled", done: 0, error: "Cancelled" };
    if (t < queued) return { ...base, status: "queued", stage: "queued", done: 0 };
    if (t < renderEnd) {
      const done = Math.min(j.total, Math.floor((t - queued) / perShot));
      return { ...base, status: "rendering", stage: `rendering shot ${done + 1} of ${j.total}`, done };
    }
    if (t < cutEnd) return { ...base, status: "cutting", stage: "cutting: subtitles, music, title card", done: j.total };
    return {
      ...base,
      status: "done",
      stage: "done",
      done: j.total,
      episode: {
        id: j.id,
        mp4_url: "/episodes/spam-factory.mp4",
        vertical_url: j.vertical ? "/episodes/spam-factory-9x16.mp4" : undefined,
        duration: 57.6,
        shots: j.total,
        cuts_per_minute: 40.6,
      },
    };
  }

  const running = () => [...jobs.values()].some((j) => !["done", "failed"].includes(snapshot(j).status));

  return {
    mock: true,
    async health() {
      return { ok: true, version: "mock", mode: "mock", has_credential: hasKey };
    },
    async setKey(key) {
      hasKey = key.length > 0;
      return { has_credential: hasKey };
    },
    async storyboard(req) {
      const headline = req.headline.trim();
      if (!headline) throw new EngineError("headline is empty", 400);
      await wait(700);
      return template(headline, Math.min(5, Math.max(0.5, req.minutes ?? 1)));
    },
    async render(req) {
      if (running()) throw new EngineError("A render is already running", 409);
      const id = newId("job");
      const total = req.storyboard.scenes.reduce((a, s) => a + s.shots.length, 0);
      jobs.set(id, { id, started: Date.now(), total, vertical: req.vertical, cancelled: false });
      return id;
    },
    async job(id) {
      const j = jobs.get(id);
      if (!j) throw new EngineError("job not found", 404);
      return snapshot(j);
    },
    async cancel(id) {
      const j = jobs.get(id);
      if (j) j.cancelled = true;
    },
    mediaUrl: (path) => path,
  };
}
