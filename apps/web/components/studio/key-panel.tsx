"use client";

import { useState } from "react";
import { Pill, Step } from "./step";

type CheckState = { kind: "idle" } | { kind: "checking" } | { kind: "ok"; models: number } | { kind: "bad"; message: string };

/**
 * The Comfy API key. Saved to this browser's localStorage and pushed to the local engine only.
 * The optional check calls api.comfy.org straight from the browser.
 */
export function KeyPanel({
  savedKey,
  engineHasKey,
  canPush,
  onSave,
}: {
  savedKey: string;
  engineHasKey: boolean | null;
  canPush: boolean;
  onSave: (key: string) => Promise<string | null>;
}) {
  const [draft, setDraft] = useState(savedKey);
  const [show, setShow] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const [check, setCheck] = useState<CheckState>({ kind: "idle" });

  async function save(value: string) {
    setNote(null);
    const err = await onSave(value.trim());
    setNote(err ?? (value.trim() ? "Saved on this computer." : "Key removed."));
  }

  async function runCheck() {
    const key = draft.trim();
    if (!key) return;
    setCheck({ kind: "checking" });
    try {
      const res = await fetch("https://api.comfy.org/v2/models", {
        headers: { "X-API-Key": key, Accept: "application/json" },
        signal: AbortSignal.timeout(10_000),
        cache: "no-store",
      });
      if (!res.ok) {
        setCheck({ kind: "bad", message: res.status === 401 || res.status === 403 ? "Comfy did not accept this key." : `Comfy answered ${res.status}.` });
        return;
      }
      const body: unknown = await res.json().catch(() => null);
      const list = Array.isArray(body) ? body : body && typeof body === "object" ? Object.values(body).find(Array.isArray) : undefined;
      setCheck({ kind: "ok", models: Array.isArray(list) ? list.length : 0 });
    } catch {
      setCheck({ kind: "bad", message: "Could not reach api.comfy.org." });
    }
  }

  const status =
    engineHasKey === null ? null : engineHasKey ? <Pill tone="ok">Engine has a key</Pill> : <Pill tone="warn">Engine has no key</Pill>;

  return (
    <Step n="02" title="Comfy API key" id="key" aside={status}>
      <form
        className="space-y-3"
        onSubmit={(e) => {
          e.preventDefault();
          void save(draft);
        }}
      >
        <label htmlFor="comfy-key" className="label">
          Comfy API key (from platform.comfy.org, API Keys)
        </label>
        <div className="flex flex-wrap gap-2">
          <input
            id="comfy-key"
            className="field min-w-0 flex-1 basis-64 font-mono"
            type={show ? "text" : "password"}
            value={draft}
            onChange={(e) => {
              setDraft(e.target.value);
              setCheck({ kind: "idle" });
            }}
            autoComplete="off"
            spellCheck={false}
            placeholder="comfyui-..."
            data-ph-no-capture
          />
          <button type="button" className="btn-ghost" onClick={() => setShow((s) => !s)} aria-pressed={show}>
            {show ? "Hide" : "Show"}
          </button>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <button type="submit" className="btn-gold" disabled={!draft.trim()}>
            Save key
          </button>
          <button type="button" className="btn-ghost px-3 py-2.5" onClick={runCheck} disabled={!draft.trim() || check.kind === "checking"}>
            {check.kind === "checking" ? "Checking" : "Check with Comfy"}
          </button>
          {savedKey ? (
            <button
              type="button"
              className="btn-ghost px-3 py-2.5"
              onClick={() => {
                setDraft("");
                void save("");
              }}
            >
              Remove
            </button>
          ) : null}
          <span role="status" className="meta">
            {check.kind === "ok" ? <span className="text-cyan">Comfy accepted the key{check.models ? ` (${check.models} models)` : ""}.</span> : null}
            {check.kind === "bad" ? <span className="text-error">{check.message}</span> : null}
            {check.kind === "idle" || check.kind === "checking" ? note : null}
          </span>
        </div>
        <p className="meta leading-relaxed">
          Stored on your machine, never on our servers: in this browser and, once the engine runs, in{" "}
          <span className="text-gold-pale">~/.anime-news/config.json</span>.{" "}
          {canPush ? null : <span className="text-gold">The key is only sent to an engine on 127.0.0.1 or localhost.</span>}
        </p>
      </form>
    </Step>
  );
}
