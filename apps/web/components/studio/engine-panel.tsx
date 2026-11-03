"use client";

import { useState } from "react";
import { CopyCommand } from "@/components/copy-command";
import { ENGINE_DEFAULT_URL, UVX_SERVE } from "@/lib/site";
import type { Health } from "@/lib/studio/types";
import { Pill, Step } from "./step";

export type EngineState = { kind: "checking" } | { kind: "up"; health: Health } | { kind: "down" };

export function EnginePanel({
  url,
  mock,
  state,
  onUrlChange,
}: {
  url: string;
  mock: boolean;
  state: EngineState;
  onUrlChange: (url: string) => void;
}) {
  const [draft, setDraft] = useState(url);
  const [editing, setEditing] = useState(false);

  const pill =
    state.kind === "up" ? (
      <Pill tone={state.health.mode === "live" ? "ok" : "warn"}>
        Engine {mock ? "mock (in browser)" : state.health.mode === "live" ? "live" : (state.health.mode ?? "up")}
        {state.health.version && !mock ? ` / v${state.health.version}` : ""}
      </Pill>
    ) : state.kind === "down" ? (
      <Pill tone="down">Engine not found</Pill>
    ) : (
      <Pill tone="idle">Checking engine</Pill>
    );

  return (
    <Step n="01" title="Engine" id="engine" aside={<span role="status">{pill}</span>}>
      {mock ? (
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
        </div>
      )}
    </Step>
  );
}
