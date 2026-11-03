"use client";

import { useState } from "react";
import type { StoryboardRequest } from "@/lib/studio/types";
import { Step } from "./step";

const URL_ONLY = /^https?:\/\/\S+$/i;

/** One box: the first line is the headline; anything after it is the article text, or a link to it. */
export function parseInput(raw: string, minutes: number): StoryboardRequest | null {
  const lines = raw.split(/\r?\n/);
  const first = lines.findIndex((l) => l.trim());
  if (first < 0) return null;
  const headline = lines[first].trim();
  if (URL_ONLY.test(headline)) return null;
  const rest = lines.slice(first + 1).join("\n").trim();
  const req: StoryboardRequest = { headline, minutes };
  if (URL_ONLY.test(rest)) req.url = rest;
  else if (rest) req.text = rest;
  return req;
}

function minutesLabel(m: number): string {
  return m < 1 ? `${Math.round(m * 60)} s` : `${m} min`;
}

export function HeadlinePanel({
  busy,
  error,
  hasStoryboard,
  disabled,
  maxMinutes = 5,
  onSubmit,
}: {
  busy: boolean;
  error: string | null;
  hasStoryboard: boolean;
  disabled: boolean;
  /** The shared demo engine's cap; 5 minutes otherwise. */
  maxMinutes?: number;
  onSubmit: (req: StoryboardRequest) => void;
}) {
  const [raw, setRaw] = useState("");
  const [chosenMinutes, setMinutes] = useState(1);
  const max = Math.max(0.5, maxMinutes);
  const minutes = Math.min(chosenMinutes, max);
  const req = parseInput(raw, minutes);
  const words = req?.text ? req.text.split(/\s+/).filter(Boolean).length : 0;

  return (
    <Step n="03" title="Headline" id="headline">
      <form
        className="space-y-4"
        onSubmit={(e) => {
          e.preventDefault();
          if (req) onSubmit(req);
        }}
      >
        <label htmlFor="headline-input" className="label">
          Headline on the first line, or a week's worth of headlines. Optional: paste the article text or its link below it.
        </label>
        <textarea
          id="headline-input"
          className="field min-h-[132px] resize-y leading-relaxed"
          value={raw}
          onChange={(e) => setRaw(e.target.value)}
          placeholder={"Mayor bans Mondays, citing \"morale emergency\"\nhttps://example.com/the-article"}
        />
        <p className="meta" aria-live="polite">
          {req ? (
            <>
              <span className="text-gold">Headline</span> {req.headline}
              {req.url ? <span> / + link</span> : null}
              {req.text ? <span> / + article text, {words} words</span> : null}
            </>
          ) : raw.trim() ? (
            <span className="text-error">Start with the headline, then the link on the next line.</span>
          ) : (
            "Nothing typed yet."
          )}
        </p>
        <div className="flex flex-wrap items-end gap-x-6 gap-y-4">
          <div className="min-w-[220px] flex-1">
            <label htmlFor="minutes" className="label">
              Length <span className="text-gold">{minutesLabel(minutes)}</span>
            </label>
            <input
              id="minutes"
              type="range"
              min={0.5}
              max={max}
              step={0.5}
              value={minutes}
              onChange={(e) => setMinutes(Number(e.target.value))}
              className="w-full accent-[#f5c518]"
            />
            <div className="meta flex justify-between text-[10px]">
              <span>30 s</span>
              <span>{minutesLabel(max)}</span>
            </div>
          </div>
          <button type="submit" className="btn-gold" disabled={!req || busy || disabled}>
            {busy ? "Writing storyboard" : hasStoryboard ? "Write a new storyboard" : "Write storyboard"}
          </button>
        </div>
        {busy ? <div className="scanbar h-[3px] bg-gold/20" aria-hidden="true" /> : null}
        {error ? (
          <p role="alert" className="text-error">
            {error}
          </p>
        ) : null}
      </form>
    </Step>
  );
}
