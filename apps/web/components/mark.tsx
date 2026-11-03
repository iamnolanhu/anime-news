import { useId } from "react";

/** The Anime News cube mark, inlined so the strokes can draw on (THEME.md, "Brand: Anime News"). */
export function Mark({ size = 34, className = "" }: { size?: number; className?: string }) {
  const uid = useId().replace(/[^a-zA-Z0-9]/g, "");
  const id = (n: string) => `${n}-${uid}`;
  const faces = ["left", "top", "base"].map((f) => `#${id(`an-face-${f}`)}`);
  const uses = (cls?: string) => faces.map((href) => <use key={href} className={cls} href={href} />);
  return (
    <svg viewBox="110 110 1280 1280" width={size} height={size} className={className} aria-hidden="true" focusable="false">
      <defs>
        <path id={id("an-face-left")} pathLength={1} d="M232 574 L545 390 L863 572 L557 754 L557 1136 L232 951" />
        <path id={id("an-face-top")} pathLength={1} d="M614 347 L921 165 L1252 352 L1253 1082 L934 887 L933 527 Z" />
        <path id={id("an-face-base")} pathLength={1} d="M617 792 L1238 1159 L923 1346 L617 1173 Z" />
        <linearGradient id={id("an-gold")} gradientUnits="userSpaceOnUse" x1="0" y1="165" x2="0" y2="1346">
          <stop offset="0" stopColor="#FFE566" />
          <stop offset=".45" stopColor="#F5C518" />
          <stop offset="1" stopColor="#C99700" />
        </linearGradient>
        <pattern id={id("an-scan")} patternUnits="userSpaceOnUse" width="24" height="24">
          <rect width="24" height="8" fill="#F5C518" />
        </pattern>
        <filter id={id("an-blur")} x="-25%" y="-25%" width="150%" height="150%">
          <feGaussianBlur stdDeviation="22" />
        </filter>
      </defs>
      <g fill={`url(#${id("an-scan")})`} stroke="none" opacity=".16">
        {uses()}
      </g>
      <g className="an-glow" fill="none" stroke="#FFE566" strokeWidth="52" filter={`url(#${id("an-blur")})`} opacity=".55">
        {uses("an-draw")}
      </g>
      <g className="an-chroma" fill="none" strokeWidth="26" opacity=".25">
        <g transform="translate(-10 0)" stroke="#19E6FF">
          {uses("an-draw")}
        </g>
        <g transform="translate(10 0)" stroke="#FF2BD6">
          {uses("an-draw")}
        </g>
      </g>
      <g className="an-stroke" fill="none" stroke={`url(#${id("an-gold")})`} strokeWidth="26">
        {uses("an-draw")}
      </g>
    </svg>
  );
}

export function Lockup({ ident = false }: { ident?: boolean }) {
  return (
    <span className={`an-lockup inline-flex items-center gap-2.5 ${ident ? "an-ident" : ""}`}>
      <Mark size={34} />
      <span
        className="an-word font-[family-name:var(--font-wordmark)] text-[19px] font-bold uppercase tracking-[0.2em] text-gold"
        style={{ textShadow: "0 0 10px rgba(245,197,24,.35)" }}
      >
        Anime News
      </span>
    </span>
  );
}
