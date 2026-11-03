"use client";

import { useState } from "react";
import { CopyCommand } from "@/components/copy-command";
import { ENGINE_DEFAULT_URL, UVX_SERVE } from "@/lib/site";
import type { Health } from "@/lib/studio/types";
import { Pill, Step } from "./step";

export type EngineState = { kind: "checking" } | { kind: "up"; health: Health } | { kind: "down" };

function minutesText(m: number): string {
  return m < 1 ? `${Math.round(m * 60)} seconds` : m === 1 ? "1 minute" : `${m} minutes`;
}

/** "Running on the shared demo engine (480p, up to 1 minute, 2 renders left today)". */
export function hostedLine(health: Health): string {
  const lim = health.limits;
  const parts = [lim?.quality ?? "480p", `up to ${minutesText(lim?.max_minutes ?? 1)}`];
  if (typeof health.remaining_for_you === "number") {
    parts.push(`${health.remaining_for_you} render${health.remaining_for_you === 1 ? "" : "s"} left today`);
  }
  return `Running on the shared demo engine (${parts.join(", ")})`;
}

export function EnginePanel({
  url,
  mock,
  hosted,
  hostedAvailable,
  state,
  onUrlChange,
  onChooseLocal,
}: {
  url: string;
  mock: boolean;
  /** The Studio is on the shared demo engine. */
  hosted: boolean;
  /** This deployment has a shared demo engine to switch back to. */
  hostedAvailable: boolean;
  state: EngineState;
  onUrlChange: (url: string) => void;
  onChooseLocal: (local: boolean) => void;
}) {
  const [draft, setDraft] = useState(url);
  const [editing, setEditing] = useState(false);

  const pill =
    state.kind === "up" ? (
      <Pill tone={state.health.mode === "live" ? "ok" : "warn"}>
        {hosted ? "Shared engine" : "Engine"} {mock ? "mock (in browser)" : state.health.mode === "live" ? "live" : (state.health.mode ?? "up")}
        {state.health.version && !mock ? ` / v${state.health.version}` : ""}
      </Pill>
    ) : state.kind === "down" ? (
      <Pill tone="down">{hosted ? "Shared engine offline" : "Engine not found"}</Pill>
    ) : (
      <Pill tone="idle">Checking engine</Pill>
    );

  return (
    <Step n="01" title="Engine" id="engine" aside={<span role="status">{pill}</span>}>
      {hosted ? (
        <div className="space-y-3">
          {state.kind === "up" ? (
            <p className="text-white/85" data-testid="hosted-line">
              {hostedLine(state.health)}. No key needed: renders here use the demo&apos;s shared credits, so they are capped per visitor
              and per day.
            </p>
          ) : state.kind === "down" ? (
            <p className="text-white/85">The shared demo engine is not answering right now. Try again in a minute, or use your own engine.</p>
          ) : (
            <p className="meta">Connecting to the shared demo engine</p>
          )}
          <button type="button" className="btn-ghost" onClick={() => onChooseLocal(true)}>
            Use my own local engine
          </button>
        </div>
      ) : mock ? (
        <p className="text-white/75">
          Demo mode: a stand-in engine runs inside this page, so you can try the whole flow without installing anything. Renders play a
          sample episode. <a href="/studio" className="link-cyan">Use your real engine</a>.
        </p>
      ) : (
        <div className="space-y-4">
          <div className="flex flex-wrap items-center gap-2">
            <span className="meta">Engine URL</span>
            {editing ? (
              <form
                className="flex min-w-0 flex-1 flex-wrap gap-2"
                onSubmit={(e) => {
                  e.preventDefault();
                  onUrlChange(draft.trim() || ENGINE_DEFAULT_URL);
                  setEditing(false);
                }}
              >
                <input
                  className="field field-sm min-w-0 flex-1 font-mono"
                  value={draft}
                  onChange={(e) => setDraft(e.target.value)}
                  aria-label="Engine URL"
                  inputMode="url"
                  spellCheck={false}
                />
                <button type="submit" className="btn-ghost">Save</button>
                <button
                  type="button"
                  className="btn-ghost"
                  onClick={() => {
                    setDraft(ENGINE_DEFAULT_URL);
                    onUrlChange(ENGINE_DEFAULT_URL);
                    setEditing(false);
                  }}
                >
                  Reset
                </button>
              </form>
            ) : (
              <>
                <code className="min-w-0 truncate font-mono text-[13px] text-gold-pale">{url}</code>
                <button
                  type="button"
                  className="btn-ghost"
                  onClick={() => {
                    setDraft(url);
                    setEditing(true);
                  }}
                >
                  Edit
                </button>
              </>
            )}
          </div>
          {state.kind === "down" ? (
            <div className="space-y-3 border-l-2 border-gold pl-4">
              <p className="text-white/85">
                Start the engine on this computer (needs <span className="font-mono text-[13px]">uv</span> and{" "}
                <span className="font-mono text-[13px]">ffmpeg</span>). This page finds it within a few seconds.
              </p>
              <CopyCommand command={UVX_SERVE} />
              <p className="meta">
                No engine yet? <a href="/studio?mock=1" className="link-cyan">Try the Studio in demo mode</a>.
              </p>
            </div>
          ) : null}
          {hostedAvailable ? (
            <button type="button" className="btn-ghost" onClick={() => onChooseLocal(false)}>
              Use the shared demo engine
            </button>
          ) : null}
        </div>
      )}
    </Step>
  );
}
