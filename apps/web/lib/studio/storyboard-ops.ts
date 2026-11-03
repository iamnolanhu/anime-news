import type { CastMember, Scene, Shot, Storyboard } from "./types";

/** Pure storyboard edits. No caps: a scene takes as many shots as you add, a storyboard as many scenes. */

export function newId(prefix: string): string {
  const rand = typeof crypto !== "undefined" && "randomUUID" in crypto ? crypto.randomUUID().slice(0, 8) : Math.random().toString(36).slice(2, 10);
  return `${prefix}-${rand}`;
}

export function blankShot(): Shot {
  return { id: newId("shot"), kind: "action", cast: [], prompt: "", move: "static", seconds: 5 };
}

export function blankScene(): Scene {
  return { id: newId("scene"), act: "escalation", setting: "", caption: "", narration: "", shots: [blankShot()] };
}

export function blankCast(): CastMember {
  return { id: newId("cast"), name: "", role: "", look: "", voice: "" };
}

export function move<T>(list: T[], from: number, to: number): T[] {
  if (to < 0 || to >= list.length || from === to) return list;
  const next = list.slice();
  const [item] = next.splice(from, 1);
  next.splice(to, 0, item);
  return next;
}

export function updateScene(sb: Storyboard, sceneId: string, patch: Partial<Scene>): Storyboard {
  return { ...sb, scenes: sb.scenes.map((s) => (s.id === sceneId ? { ...s, ...patch } : s)) };
}

export function updateShot(sb: Storyboard, sceneId: string, shotId: string, patch: Partial<Shot>): Storyboard {
  return {
    ...sb,
    scenes: sb.scenes.map((s) => (s.id === sceneId ? { ...s, shots: s.shots.map((t) => (t.id === shotId ? { ...t, ...patch } : t)) } : s)),
  };
}

export function totals(sb: Storyboard): { scenes: number; shots: number; seconds: number } {
  const shots = sb.scenes.flatMap((s) => s.shots);
  return { scenes: sb.scenes.length, shots: shots.length, seconds: shots.reduce((a, s) => a + (Number(s.seconds) || 0), 0) };
}
