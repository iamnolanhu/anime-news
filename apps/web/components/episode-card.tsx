"use client";

import { useState } from "react";
import { formatDuration, type Episode } from "@/lib/episodes";

/** One finished episode: player with a 16:9 / 9:16 toggle, the numbers, and a download of the format on screen. */
export function EpisodeCard({ episode, index }: { episode: Episode; index: number }) {
  const [vertical, setVertical] = useState(false);
  const canVertical = Boolean(episode.vertical);
  const src = vertical && episode.vertical ? episode.vertical : episode.video;
  const poster = vertical && episode.verticalPoster ? episode.verticalPoster : episode.poster;
  const file = src.split("/").pop() ?? "episode.mp4";

  return (
    <article id={episode.slug} className="grid scroll-mt-24 gap-6 border-t border-white/10 pt-8 lg:grid-cols-12 lg:gap-10">
      <div className="lg:col-span-4">
        <p className="kicker">
          Episode {String(index).padStart(2, "0")} / {episode.date}
        </p>
        <h2 className="display mt-3 text-[clamp(2.2rem,4.5vw,3.4rem)] text-gold">{episode.title}</h2>
        <p className="mt-3 text-[16px] leading-snug text-white/85">&ldquo;{episode.headline}&rdquo;</p>
        <dl className="mt-6 grid grid-cols-3 gap-px border border-white/10 bg-white/10">
          {[
            ["Length", formatDuration(episode.duration)],
            ["Shots", String(episode.shots)],
            ["Cuts/min", episode.cutsPerMinute.toFixed(1)],
          ].map(([k, v]) => (
            <div key={k} className="flex flex-col-reverse bg-black px-3 py-2.5">
              <dt className="meta text-[10px] uppercase tracking-[0.14em]">{k}</dt>
              <dd className="display text-[1.8rem]">{v}</dd>
            </div>
          ))}
        </dl>
        <p className="mt-3 inline-flex items-center gap-2 border border-cyan/40 px-2 py-1 font-mono text-[11px] uppercase tracking-[0.14em] text-cyan">
          {episode.reusedShots} reused shots
        </p>
        <div className="mt-6 flex flex-wrap gap-2">
          <div role="group" aria-label="Aspect ratio" className="flex">
            <button type="button" className="btn-ghost" aria-pressed={!vertical} onClick={() => setVertical(false)}>
              16:9
            </button>
            <button type="button" className="btn-ghost -ml-px" aria-pressed={vertical} disabled={!canVertical} onClick={() => setVertical(true)}>
              9:16
            </button>
          </div>
          <a href={src} download={file} className="btn-ghost">
            Download {vertical ? "9:16" : "16:9"}
          </a>
        </div>
      </div>
      <div className="lg:col-span-8">
        <div className={`panel brackets mx-auto p-2 ${vertical ? "max-w-[360px]" : ""}`}>
          <video
            key={src}
            src={src}
            poster={poster}
            controls
            playsInline
            preload="metadata"
            className={`block w-full bg-black ${vertical ? "aspect-[9/16]" : "aspect-video"}`}
          />
        </div>
      </div>
    </article>
  );
}
