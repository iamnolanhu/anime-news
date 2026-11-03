import { createMockEngine } from "./mock-engine";
import type { Health, Job, RenderRequest, Storyboard, StoryboardRequest } from "./types";

/** What the Studio needs from the local engine. The real client and the in-browser mock both implement it. */
export interface Engine {
  readonly mock: boolean;
  health(): Promise<Health>;
  /** Sends the Comfy API key to the engine (empty string clears it). */
  setKey(key: string): Promise<{ has_credential: boolean }>;
  storyboard(req: StoryboardRequest): Promise<Storyboard>;
  /** Starts a render and returns its job id. */
  render(req: RenderRequest): Promise<string>;
  job(id: string): Promise<Job>;
  cancel(id: string): Promise<void>;
  /** Turns an engine media path into a URL the browser can load. */
  mediaUrl(path: string): string;
}

export class EngineError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
  }
}

const LOOPBACK = new Set(["127.0.0.1", "localhost", "[::1]", "::1"]);

/** The key is only ever sent to an engine on this computer. */
export function isLoopbackUrl(url: string): boolean {
  try {
    return LOOPBACK.has(new URL(url).hostname);
  } catch {
    return false;
  }
}

export function normalizeEngineUrl(url: string): string {
  return url.trim().replace(/\/+$/, "");
}

async function readError(res: Response): Promise<string> {
  try {
    const body: unknown = await res.json();
    if (body && typeof body === "object") {
      const detail = (body as { detail?: unknown; error?: unknown }).detail ?? (body as { error?: unknown }).error;
      if (typeof detail === "string") return detail;
      if (detail) return JSON.stringify(detail);
    }
  } catch {
    /* not JSON */
  }
  return `${res.status} ${res.statusText}`.trim();
}

function createHttpEngine(baseUrl: string): Engine {
  const base = normalizeEngineUrl(baseUrl);

  async function call<T>(method: string, path: string, body?: unknown, timeoutMs = 30_000): Promise<T> {
    let res: Response;
    try {
      res = await fetch(`${base}${path}`, {
        method,
        headers: body === undefined ? undefined : { "content-type": "application/json" },
        body: body === undefined ? undefined : JSON.stringify(body),
        signal: AbortSignal.timeout(timeoutMs),
        cache: "no-store",
      });
    } catch {
      throw new EngineError(`Cannot reach the engine at ${base}`, 0);
    }
    if (!res.ok) throw new EngineError(await readError(res), res.status);
    return (await res.json()) as T;
  }

  return {
    mock: false,
    health: () => call<Health>("GET", "/api/health", undefined, 2500),
    async setKey(key) {
      if (!isLoopbackUrl(base)) throw new EngineError("The key is only sent to an engine on 127.0.0.1 or localhost.", 0);
      return call<{ has_credential: boolean }>("PUT", "/api/config", { comfy_api_key: key });
    },
    async storyboard(req) {
      const out = await call<{ storyboard: Storyboard }>("POST", "/api/storyboard", req, 180_000);
      return out.storyboard;
    },
    async render(req) {
      const out = await call<{ job_id: string }>("POST", "/api/render", req);
      return out.job_id;
    },
    job: (id) => call<Job>("GET", `/api/jobs/${encodeURIComponent(id)}`),
    async cancel(id) {
      await call<unknown>("DELETE", `/api/jobs/${encodeURIComponent(id)}`);
    },
    mediaUrl: (path) => (/^https?:\/\//.test(path) ? path : `${base}${path.startsWith("/") ? "" : "/"}${path}`),
  };
}

export function createEngine(baseUrl: string, mock: boolean): Engine {
  return mock ? createMockEngine() : createHttpEngine(baseUrl);
}
