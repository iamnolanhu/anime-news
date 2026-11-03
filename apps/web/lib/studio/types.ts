/** Types for the local engine HTTP contract (README, "API contract"). */

export const ACTS = ["cold_open", "setup", "escalation", "turn", "climax", "sign_off"] as const;
export type Act = (typeof ACTS)[number];

export const SHOT_KINDS = ["talk", "action", "still", "insert"] as const;
export type ShotKind = (typeof SHOT_KINDS)[number];

export type CastMember = { id: string; name: string; role: string; look: string; voice: string };

export type Shot = {
  id: string;
  kind: ShotKind;
  cast: string[];
  line?: string;
  prompt: string;
  move: string;
  seconds: number;
};

export type Scene = {
  id: string;
  act: Act | string;
  setting: string;
  caption: string;
  narration: string;
  shots: Shot[];
};

export type Storyboard = {
  id: string;
  headline: string;
  source_text_excerpt?: string;
  series: string;
  logline: string;
  minutes: number;
  cast: CastMember[];
  scenes: Scene[];
};

export type HostedLimits = {
  daily_budget_usd: number;
  renders_per_ip_per_day: number;
  storyboards_per_ip_per_day: number;
  max_minutes: number;
  quality: Quality;
  max_parallel_jobs: number;
};

export type Health = {
  ok: boolean;
  version?: string;
  mode?: "live" | "mock" | string;
  has_credential?: boolean;
  /** Set by the shared demo engine (hosted mode) only. */
  hosted?: boolean;
  limits?: HostedLimits;
  remaining_for_you?: number;
  storyboards_left_for_you?: number;
  budget_remaining_usd?: number;
};

export type StoryboardRequest = { headline: string; text?: string; url?: string; minutes?: number; style?: string };

export type Quality = "480p" | "720p";
export type RenderRequest = { storyboard: Storyboard; quality: Quality; vertical: boolean };

export type JobStatus = "queued" | "rendering" | "cutting" | "done" | "failed";
export type JobEpisode = {
  id: string;
  mp4_url: string;
  vertical_url?: string;
  duration: number;
  shots: number;
  cuts_per_minute: number;
};
export type Job = {
  job_id: string;
  status: JobStatus;
  stage: string;
  done: number;
  total: number;
  started?: string | number;
  /** Place in the shared engine's queue while status is "queued". */
  position?: number;
  error?: string;
  episode?: JobEpisode;
};

export type Project = {
  id: string;
  headline: string;
  request: StoryboardRequest;
  createdAt: number;
  updatedAt: number;
  storyboard: Storyboard;
  jobs: Job[];
};
