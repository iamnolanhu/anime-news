"use client";

import { useState } from "react";
import { PosterPlayer } from "@/components/poster-player";
import { durationText, type WeeklyEpisode } from "@/lib/weekly";

/** One weekly briefing: poster, the five headlines with topic tags, 16:9 / 9:16 player and a download. */
export function WeeklyCard({ episode }: { episode: WeeklyEpisode }) {
  const [vertical, setVertical] = useState(false);
  const src = vertical && episode.vertical ? episode.vertical : episode.video;
  const file = src.split("/").pop() ?? "episode.mp4";

  return (
    <article id={episode.week} className="grid scroll-mt-24 grid-cols-1 gap-6 border-t border-white/10 pt-8 lg:grid-cols-12 lg:gap-10">
      <div className="min-w-0 lg:col-span-5">
        <p className="kicker">{episode.heading}</p>
        <h3 className="display mt-3 text-[clamp(2rem,4vw,3rem)] text-gold">{episode.title}</h3>
        <ul className="mt-5 space-y-3">
          {episode.headlines.map((h) => (
            <li key={`${h.topic}-${h.headline}`} className="flex min-w-0 items-start gap-3">
              <span className="mt-0.5 w-[4.6rem] shrink-0 border border-cyan/40 px-1.5 py-0.5 text-center font-mono text-[10px] uppercase tracking-[0.14em] text-cyan">
                {h.topic}
              </span>
              <span className="min-w-0 break-words text-[15px] leading-snug text-white/85">
                {h.headline}
                {h.sources.slice(0, 2).map((s) => (
                  <a key={s.url} href={s.url} target="_blank" rel="noreferrer" className="link-cyan ml-2 whitespace-nowrap font-mono text-[11px]">
                    {s.name}
                  </a>
                ))}
              </span>
            </li>
          ))}
        </ul>
        <p className="meta mt-5">
          {[
            durationText(episode.duration),
            episode.shots ? `${episode.shots} shots` : "",
            episode.cutsPerMinute ? `${episode.cutsPerMinute.toFixed(1)} cuts/min` : "",
            episode.status === "partial" ? "some shots still rendering" : "",
          ]
            .filter(Boolean)
            .join(" / ")}
        </p>
        <div className="mt-5 flex flex-wrap gap-2">
          <div role="group" aria-label="Aspect ratio" className="flex">
            <button type="button" className="btn-ghost" aria-pressed={!vertical} onClick={() => setVertical(false)}>
              16:9
            </button>
            <button type="button" className="btn-ghost -ml-px" aria-pressed={vertical} disabled={!episode.vertical} onClick={() => setVertical(true)}>
              9:16
            </button>
          </div>
          <a href={src} download={file} className="btn-ghost">
            Download {vertical ? "9:16" : "16:9"}
          </a>
        </div>
      </div>
      <div className="min-w-0 lg:col-span-7">
        <div className={`panel brackets mx-auto p-2 ${vertical ? "max-w-[360px]" : ""}`}>
          <PosterPlayer src={src} poster={episode.poster} vertical={vertical} label={episode.heading} />
        </div>
      </div>
    </article>
  );
}

export function WeeklySkeleton() {
  return (
    <div aria-hidden className="space-y-14">
      {[0, 1].map((i) => (
        <div key={i} className="grid animate-pulse grid-cols-1 gap-6 border-t border-white/10 pt-8 lg:grid-cols-12 lg:gap-10">
          <div className="space-y-3 lg:col-span-5">
            <div className="h-3 w-40 bg-white/10" />
            <div className="h-10 w-3/4 bg-white/10" />
            {[0, 1, 2, 3, 4].map((j) => (
              <div key={j} className="h-4 w-full bg-white/5" />
            ))}
          </div>
          <div className="aspect-video bg-white/5 lg:col-span-7" />
        </div>
      ))}
    </div>
  );
}
