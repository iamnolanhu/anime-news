import Link from "next/link";
import { Lockup } from "@/components/mark";
import { REPO_URL } from "@/lib/site";

export function SiteHeader() {
  return (
    <header className="sticky top-0 z-50 border-b border-[var(--sd-gold-line)] bg-black/85 backdrop-blur-md">
      <div className="mx-auto flex h-14 max-w-[1240px] items-center justify-between gap-3 px-4 sm:px-6">
        <Link href="/" aria-label="Anime News home" className="min-w-0">
          <Lockup ident />
        </Link>
        <nav className="flex items-center gap-1 font-mono text-[11px] uppercase tracking-[0.14em] sm:gap-5">
          <Link href="/episodes" className="px-2 py-2 text-white/80 hover:text-gold">
            Episodes
          </Link>
          <Link href="/studio" className="px-2 py-2 text-white/80 hover:text-gold">
            Studio
          </Link>
          <a href={REPO_URL} target="_blank" rel="noreferrer" className="hidden px-2 py-2 text-white/80 hover:text-gold sm:inline">
            GitHub
          </a>
        </nav>
      </div>
    </header>
  );
}

export function SiteFooter() {
  return (
    <footer className="mt-24 border-t border-[var(--sd-gold-line)]">
      <div className="mx-auto flex max-w-[1240px] flex-col gap-6 px-4 py-10 sm:flex-row sm:items-end sm:justify-between sm:px-6">
        <div className="space-y-2">
          <Lockup />
          <p className="meta max-w-md">
            Built for Open Source Higgsfield: ComfyUI Build Night, NYC, November 2026. Models through Comfy Router. MIT licensed.
          </p>
        </div>
        <nav className="flex flex-wrap gap-x-5 gap-y-2 font-mono text-[11px] uppercase tracking-[0.14em] text-white/70">
          <Link href="/weekly" className="hover:text-gold">Weekly</Link>
          <Link href="/episodes" className="hover:text-gold">Episodes</Link>
          <Link href="/studio" className="hover:text-gold">Studio</Link>
          <a href={REPO_URL} target="_blank" rel="noreferrer" className="hover:text-gold">Source</a>
          <a href={`${REPO_URL}/blob/main/LICENSE`} target="_blank" rel="noreferrer" className="hover:text-gold">MIT</a>
          <a
            href="https://sigmasynapses.com"
            target="_blank"
            rel="noopener"
            className="font-sans text-[10px] normal-case tracking-normal text-[var(--sd-muted)] hover:text-gold"
          >
            Built by Sigma Synapses
          </a>
        </nav>
      </div>
    </footer>
  );
}
