/** A numbered Studio step: gold number, title, optional right-side slot, then the content. */
export function Step({
  n,
  title,
  aside,
  children,
  id,
}: {
  n: string;
  title: string;
  aside?: React.ReactNode;
  children: React.ReactNode;
  id?: string;
}) {
  return (
    <section id={id} aria-labelledby={`${id ?? n}-title`} className="panel brackets p-4 sm:p-6">
      <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-2">
        <h2 id={`${id ?? n}-title`} className="flex items-baseline gap-3">
          <span className="display text-[1.9rem] text-gold">{n}</span>
          <span className="font-mono text-[12px] uppercase tracking-[0.18em] text-white">{title}</span>
        </h2>
        {aside}
      </div>
      <div className="mt-4">{children}</div>
    </section>
  );
}

export function Pill({ tone, children }: { tone: "ok" | "warn" | "down" | "idle"; children: React.ReactNode }) {
  const tones = {
    ok: "border-cyan/50 text-cyan",
    warn: "border-gold/60 text-gold",
    down: "border-error/60 text-error",
    idle: "border-white/20 text-dim",
  } as const;
  const dot = { ok: "bg-cyan shadow-[0_0_8px_#19e6ff]", warn: "bg-gold", down: "bg-error", idle: "bg-dim" } as const;
  return (
    <span className={`inline-flex items-center gap-2 border px-2 py-1 font-mono text-[11px] uppercase tracking-[0.14em] ${tones[tone]}`}>
      <span className={`h-1.5 w-1.5 rounded-full ${dot[tone]}`} aria-hidden="true" />
      {children}
    </span>
  );
}
