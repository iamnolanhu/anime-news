"use client";

import { useState } from "react";
import { formatDuration } from "@/lib/episodes";
import type { Job, Quality } from "@/lib/studio/types";
import { Pill, Step } from "./step";

const TERMINAL = new Set(["done", "failed"]);

export function isTerminal(job: Job | null | undefined): boolean {
  return Boolean(job && TERMINAL.has(job.status));
}

export function RenderPanel({
  job,
  error,
  disabled,
  shots,
  mediaUrl,
  onRender,
  onCancel,
}: {
  job: Job | null;
  error: string | null;
  disabled: boolean;
  shots: number;
  mediaUrl: (path: string) => string;
  onRender: (quality: Quality, vertical: boolean) => void;
  onCancel: () => void;
}) {
  const [quality, setQuality] = useState<Quality>("480p");
  const [vertical, setVertical] = useState(false);
  const running = Boolean(job && !isTerminal(job));
  const pct = job ? (job.status === "done" ? 100 : job.total ? Math.round((job.done / job.total) * 100) : 0) : 0;
  const episode = job?.status === "done" ? job.episode : undefined;
  const [showVertical, setShowVertical] = useState(false);
  const videoPath = episode ? (showVertical && episode.vertical_url ? episode.vertical_url : episode.mp4_url) : "";

  return (
    <Step
      n="05"
      title="Render"
      id="render"
      aside={job ? <Pill tone={job.status === "done" ? "ok" : job.status === "failed" ? "down" : "warn"}>{job.status}</Pill> : null}
    >
      <div className="flex flex-wrap items-end gap-x-6 gap-y-4">
        <div>
          <span className="label">Quality</span>
          <div role="group" aria-label="Quality" className="flex">
            {(["480p", "720p"] as const).map((q, i) => (
              <button key={q} type="button" className={`btn-ghost px-4 py-2 ${i ? "-ml-px" : ""}`} aria-pressed={quality === q} onClick={() => setQuality(q)}>
                {q}
              </button>
            ))}
          </div>
        </div>
        <div>
          <span className="label">Also export</span>
          <button type="button" className="btn-ghost px-4 py-2" aria-pressed={vertical} onClick={() => setVertical((v) => !v)}>
            9:16 vertical
          </button>
        </div>
        <div className="flex gap-2">
          <button type="button" className="btn-gold" disabled={disabled || running || shots === 0} onClick={() => onRender(quality, vertical)}>
            Render {shots} shots
          </button>
          {running ? (
            <button type="button" className="btn-ghost px-4" onClick={onCancel}>
              Cancel
            </button>
          ) : null}
        </div>
      </div>
      <p className="meta mt-3">480p is cheaper and faster for a first look; 720p is the finished cut. Every shot is rendered fresh; none is reused.</p>

      {job ? (
        <div className="mt-5 space-y-2" aria-live="polite">
          <div className="flex justify-between gap-3 font-mono text-[12px]">
            <span className="truncate text-white/85" data-testid="job-stage">
              {job.stage || job.status}
            </span>
            <span className="shrink-0 text-gold">
              {job.done}/{job.total} shots / {pct}%
            </span>
          </div>
          <div
            className={`h-2 border border-[var(--sd-gold-line)] bg-black ${running ? "scanbar" : ""}`}
            role="progressbar"
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={pct}
            aria-label="Render progress"
          >
            <div className="h-full bg-gold transition-[width] duration-300" style={{ width: `${pct}%` }} />
          </div>
          {job.status === "failed" ? <p className="text-error">{job.error || "The render failed."}</p> : null}
        </div>
      ) : null}
      {error ? (
        <p role="alert" className="mt-3 text-error">
          {error}
        </p>
      ) : null}

      {episode ? (
        <div className="mt-6 space-y-3" data-testid="episode-result">
          <div className={`mx-auto border border-white/10 bg-black ${showVertical ? "max-w-[340px]" : ""}`}>
            <video key={videoPath} src={mediaUrl(videoPath)} controls playsInline className={`block w-full ${showVertical ? "aspect-[9/16]" : "aspect-video"}`} />
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <span className="meta mr-2">
              {formatDuration(episode.duration)} / {episode.shots} shots / {episode.cuts_per_minute} cuts per min
            </span>
            {episode.vertical_url ? (
              <div role="group" aria-label="Aspect ratio" className="flex">
                <button type="button" className="btn-ghost" aria-pressed={!showVertical} onClick={() => setShowVertical(false)}>16:9</button>
                <button type="button" className="btn-ghost -ml-px" aria-pressed={showVertical} onClick={() => setShowVertical(true)}>9:16</button>
              </div>
            ) : null}
            <a className="btn-ghost" href={mediaUrl(episode.mp4_url)} download target="_blank" rel="noreferrer">
              Download 16:9
            </a>
            {episode.vertical_url ? (
              <a className="btn-ghost" href={mediaUrl(episode.vertical_url)} download target="_blank" rel="noreferrer">
                Download 9:16
              </a>
            ) : null}
          </div>
        </div>
      ) : null}
    </Step>
  );
}
