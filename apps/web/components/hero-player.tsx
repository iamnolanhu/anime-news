"use client";

import { useRef, useState } from "react";

/** The latest episode, playing muted on loop. A click (or Enter/Space) turns the sound on and restarts it. */
export function HeroPlayer({ src, poster, title }: { src: string; poster: string; title: string }) {
  const ref = useRef<HTMLVideoElement>(null);
  const [muted, setMuted] = useState(true);

  function toggle() {
    const v = ref.current;
    if (!v) return;
    if (v.muted) {
      v.muted = false;
      v.currentTime = 0;
      void v.play();
      setMuted(false);
    } else {
      v.muted = true;
      setMuted(true);
    }
  }

  return (
    <button
      type="button"
      onClick={toggle}
      aria-label={muted ? `Play ${title} with sound` : "Mute"}
      className="group relative block aspect-video w-full overflow-hidden bg-black text-left"
    >
      <video
        ref={ref}
        src={src}
        poster={poster}
        autoPlay
        muted
        loop
        playsInline
        preload="metadata"
        className="h-full w-full object-cover"
      />
      <span className="pointer-events-none absolute inset-x-0 bottom-0 h-24 bg-gradient-to-t from-black/80 to-transparent" />
      <span className="absolute left-3 top-3 flex items-center gap-2 bg-black/70 px-2 py-1 font-mono text-[10px] uppercase tracking-[0.18em] text-cyan">
        <span className="h-1.5 w-1.5 rounded-full bg-cyan shadow-[0_0_8px_#19e6ff]" />
        On air
      </span>
      <span className="absolute bottom-3 right-3 bg-gold px-2.5 py-1.5 font-mono text-[11px] font-bold uppercase tracking-[0.14em] text-black transition-transform duration-150 group-active:scale-[0.97]">
        {muted ? "Click for sound" : "Sound on / click to mute"}
      </span>
    </button>
  );
}
