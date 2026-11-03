import type { Project } from "./types";

/** Everything the Studio keeps lives in this browser's localStorage. Nothing is sent to the website. */
export const KEYS = {
  engineUrl: "anime-news:engine-url",
  /** "local" when the visitor switched from the shared demo engine to their own. */
  engineChoice: "anime-news:engine-choice",
  comfyKey: "anime-news:comfy-api-key",
  projects: "anime-news:projects",
  currentProject: "anime-news:current-project",
} as const;

export function read(key: string): string | null {
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
}

export function write(key: string, value: string | null): void {
  try {
    if (value === null || value === "") window.localStorage.removeItem(key);
    else window.localStorage.setItem(key, value);
  } catch {
    /* storage full or blocked: the Studio still works for this session */
  }
}

export function loadProjects(): Project[] {
  const raw = read(KEYS.projects);
  if (!raw) return [];
  try {
    const parsed: unknown = JSON.parse(raw);
    return Array.isArray(parsed) ? (parsed as Project[]) : [];
  } catch {
    return [];
  }
}

export function saveProjects(projects: Project[]): void {
  write(KEYS.projects, JSON.stringify(projects));
}
