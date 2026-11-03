"use client";

import { useState } from "react";

/** A shell command on one line with a copy button. The command scrolls sideways instead of overflowing the page. */
export function CopyCommand({ command, label }: { command: string; label?: string }) {
  const [copied, setCopied] = useState(false);
  async function copy() {
    try {
      await navigator.clipboard.writeText(command);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1400);
    } catch {
      setCopied(false);
    }
  }
  return (
    <div className="flex min-w-0 items-stretch border border-white/10 bg-black">
      {label ? <span className="hidden shrink-0 items-center border-r border-white/10 px-3 font-mono text-[11px] text-gold sm:flex">{label}</span> : null}
      <code className="min-w-0 flex-1 overflow-x-auto whitespace-nowrap px-3 py-2.5 font-mono text-[13px] text-white/90">
        <span className="select-none text-gold">$ </span>
        {command}
      </code>
      <button type="button" onClick={copy} className="btn-ghost shrink-0 border-0 border-l" aria-live="polite">
        {copied ? "Copied" : "Copy"}
      </button>
    </div>
  );
}
