"use client";

import { useEffect, useRef, useState } from "react";

/**
 * Poster first, video on tap. iOS Safari paints nothing for a <video> until it is tapped, so the box shows the poster image
 * with a play button; the <video> mounts on tap and play() is called inside that gesture. Switching src (16:9 / 9:16) resets it.
 */
export function PosterPlayer({ src, poster, vertical, label }: { src: string; poster?: string | null; vertical: boolean; label: string }) {
  const [active, setActive] = useState(false);
  const ref = useRef<HTMLVideoElement>(null);
  const ratio = vertical ? "aspect-[9/16]" : "aspect-video";

  useEffect(() => setActive(false), [src]);

  function start() {
    setActive(true);
    // The element mounts synchronously inside the tap; play() on the next frame still counts as the same user activation.
    requestAnimationFrame(() => void ref.current?.play().catch(() => {}));
  }

  if (active) {
    return (
      <video
        ref={ref}
        key={src}
        src={src}
        poster={poster ?? undefined}
        controls
        autoPlay
        playsInline
        preload="metadata"
        className={`block w-full bg-black ${ratio}`}
      />
    );
  }
  return (
    <button type="button" onClick={start} aria-label={`Play ${label}`} className={`group relative block w-full overflow-hidden bg-black ${ratio}`}>
      {poster ? (
        // eslint-disable-next-line @next/next/no-img-element
        <img src={poster} alt="" className="h-full w-full object-cover" />
      ) : null}
      <span className="pointer-events-none absolute inset-0 grid place-items-center bg-black/20">
        <span className="grid h-16 w-16 place-items-center rounded-full border border-gold bg-black/70 transition-transform duration-150 group-hover:scale-105 group-active:scale-95">
          <svg viewBox="0 0 24 24" className="ml-1 h-7 w-7 fill-gold" aria-hidden>
            <path d="M6 4l14 8-14 8z" />
          </svg>
        </span>
      </span>
    </button>
  );
}
