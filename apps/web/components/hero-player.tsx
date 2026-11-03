"use client";

import { useRef, useState } from "react";

/**
 * This week's episode, playing muted on loop. A tap (or Enter/Space) turns the sound on; a second tap mutes.
 * iOS Safari only allows audio from inside the user gesture, so unmute + play happen synchronously in the click
 * handler, play() is awaited, and a rejection falls back to muted with a "Tap again" prompt.
 */
export function HeroPlayer({ src, poster, title }: { src: string; poster: string; title: string }) {
  const ref = useRef<HTMLVideoElement>(null);
  const [muted, setMuted] = useState(true);
  const [retry, setRetry] = useState(false);

  async function toggle() {
    const v = ref.current;
    if (!v) return;
    if (!v.muted) {
      v.muted = true;
      setMuted(true);
      return;
    }
    setRetry(false);
    v.muted = false;
    v.volume = 1;
    try {
      await v.play();
      setMuted(false);
    } catch {
      v.muted = true;
      void v.play().catch(() => {});
      setMuted(true);
      setRetry(true);
    }
  }

  return (
    <button
      type="button"
      onClick={() => void toggle()}
      aria-label={muted ? `Play ${title} with sound` : "Mute"}
      className="group relative block aspect-video min-h-[44px] w-full cursor-pointer touch-manipulation overflow-hidden bg-black text-left [-webkit-tap-highlight-color:transparent]"
    >
      <video
        ref={ref}
        src={src}
        poster={poster}
        autoPlay
        muted
        loop
        playsInline
        preload="auto"
        disablePictureInPicture
        className="pointer-events-none h-full w-full object-cover"
      />
      <span className="pointer-events-none absolute inset-x-0 bottom-0 h-24 bg-gradient-to-t from-black/80 to-transparent" />
      <span className="absolute left-3 top-3 flex items-center gap-2 bg-black/70 px-2 py-1 font-mono text-[10px] uppercase tracking-[0.18em] text-cyan">
        <span className="h-1.5 w-1.5 rounded-full bg-cyan shadow-[0_0_8px_#19e6ff]" />
        This week
      </span>
      <span className="absolute bottom-3 right-3 flex min-h-[44px] items-center bg-gold px-4 py-1.5 font-mono text-[11px] font-bold uppercase tracking-[0.14em] text-black transition-transform duration-150 group-active:scale-[0.97]">
        {retry ? "Tap again for sound" : muted ? "Tap for sound" : "Sound on / tap to mute"}
      </span>
    </button>
  );
}
