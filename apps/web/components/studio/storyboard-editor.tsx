"use client";

import { formatDuration } from "@/lib/episodes";
import { blankCast, blankScene, blankShot, move, totals, updateScene, updateShot } from "@/lib/studio/storyboard-ops";
import { ACTS, SHOT_KINDS, type CastMember, type Scene, type Shot, type Storyboard } from "@/lib/studio/types";
import { Step } from "./step";

const MOVES = ["static", "slow push in", "dolly out", "orbit", "crane up", "handheld", "whip pan", "crash zoom", "tilt up", "tracking"];

type Change = (next: Storyboard) => void;

function IconButton({ label, onClick, disabled, children }: { label: string; onClick: () => void; disabled?: boolean; children: React.ReactNode }) {
  return (
    <button type="button" className="btn-ghost h-8 w-8 p-0 text-[13px]" aria-label={label} title={label} onClick={onClick} disabled={disabled}>
      {children}
    </button>
  );
}

function CastSheet({ sb, onChange }: { sb: Storyboard; onChange: Change }) {
  const set = (id: string, patch: Partial<CastMember>) => onChange({ ...sb, cast: sb.cast.map((c) => (c.id === id ? { ...c, ...patch } : c)) });
  return (
    <div>
      <div className="flex items-center justify-between gap-3">
        <h3 className="kicker">Cast sheet</h3>
        <button type="button" className="btn-ghost" onClick={() => onChange({ ...sb, cast: [...sb.cast, blankCast()] })}>
          + Cast member
        </button>
      </div>
      <div className="mt-3 grid gap-3 md:grid-cols-2 xl:grid-cols-3">
        {sb.cast.map((c) => (
          <div key={c.id} className="space-y-2 border border-white/10 bg-black p-3">
            <div className="flex gap-2">
              <input className="field field-sm min-w-0 flex-1 font-semibold" value={c.name} placeholder="Name" aria-label="Cast name" onChange={(e) => set(c.id, { name: e.target.value })} />
              <IconButton
                label={`Remove ${c.name || "cast member"}`}
                onClick={() =>
                  onChange({
                    ...sb,
                    cast: sb.cast.filter((x) => x.id !== c.id),
                    scenes: sb.scenes.map((s) => ({ ...s, shots: s.shots.map((t) => ({ ...t, cast: t.cast.filter((id) => id !== c.id) })) })),
                  })
                }
              >
                ×
              </IconButton>
            </div>
            <input className="field field-sm" value={c.role} placeholder="Role" aria-label="Role" onChange={(e) => set(c.id, { role: e.target.value })} />
            <input className="field field-sm" value={c.look} placeholder="Look" aria-label="Look" onChange={(e) => set(c.id, { look: e.target.value })} />
            <input className="field field-sm" value={c.voice} placeholder="Voice" aria-label="Voice" onChange={(e) => set(c.id, { voice: e.target.value })} />
          </div>
        ))}
      </div>
    </div>
  );
}

function ShotCard({
  sb,
  scene,
  shot,
  index,
  number,
  onChange,
}: {
  sb: Storyboard;
  scene: Scene;
  shot: Shot;
  index: number;
  number: number;
  onChange: Change;
}) {
  const set = (patch: Partial<Shot>) => onChange(updateShot(sb, scene.id, shot.id, patch));
  const setShots = (shots: Shot[]) => onChange(updateScene(sb, scene.id, { shots }));
  return (
    <li className="flex min-w-0 flex-col gap-2 border border-white/10 bg-black p-3" data-testid="shot-card">
      <div className="flex items-center gap-2">
        <span className="display text-[1.3rem] text-gold">{String(number).padStart(2, "0")}</span>
        <select className="field field-sm w-auto flex-1 font-mono uppercase" value={shot.kind} aria-label="Shot kind" onChange={(e) => set({ kind: e.target.value as Shot["kind"] })}>
          {SHOT_KINDS.map((k) => (
            <option key={k} value={k}>
              {k}
            </option>
          ))}
        </select>
        <IconButton label="Move shot earlier" onClick={() => setShots(move(scene.shots, index, index - 1))} disabled={index === 0}>
          ↑
        </IconButton>
        <IconButton label="Move shot later" onClick={() => setShots(move(scene.shots, index, index + 1))} disabled={index === scene.shots.length - 1}>
          ↓
        </IconButton>
        <IconButton label="Delete shot" onClick={() => setShots(scene.shots.filter((t) => t.id !== shot.id))}>
          ×
        </IconButton>
      </div>
      {sb.cast.length ? (
        <div className="flex flex-wrap gap-1" role="group" aria-label="Cast in this shot">
          {sb.cast.map((c) => {
            const on = shot.cast.includes(c.id);
            return (
              <button
                key={c.id}
                type="button"
                aria-pressed={on}
                className="btn-ghost px-2 py-1 text-[10px]"
                onClick={() => set({ cast: on ? shot.cast.filter((id) => id !== c.id) : [...shot.cast, c.id] })}
              >
                {c.name || c.id}
              </button>
            );
          })}
        </div>
      ) : null}
      <textarea className="field field-sm min-h-[84px] resize-y" value={shot.prompt} placeholder="What the frame shows" aria-label="Prompt" onChange={(e) => set({ prompt: e.target.value })} />
      <input className="field field-sm" value={shot.line ?? ""} placeholder="Spoken line (optional)" aria-label="Spoken line" onChange={(e) => set({ line: e.target.value || undefined })} />
      <div className="flex gap-2">
        <input className="field field-sm min-w-0 flex-1" list="an-moves" value={shot.move} placeholder="Camera move" aria-label="Camera move" onChange={(e) => set({ move: e.target.value })} />
        <label className="flex items-center gap-1">
          <input
            className="field field-sm w-16 text-right"
            type="number"
            min={1}
            max={20}
            value={shot.seconds}
            aria-label="Seconds"
            onChange={(e) => set({ seconds: Math.max(1, Number(e.target.value) || 1) })}
          />
          <span className="meta">s</span>
        </label>
      </div>
    </li>
  );
}

export function StoryboardEditor({
  sb,
  onChange,
  onRegenerate,
  regenerating,
}: {
  sb: Storyboard;
  onChange: Change;
  onRegenerate: () => void;
  regenerating: boolean;
}) {
  const t = totals(sb);
  let shotNumber = 0;
  const setScenes = (scenes: Scene[]) => onChange({ ...sb, scenes });

  return (
    <Step
      n="04"
      title="Storyboard"
      id="storyboard"
      aside={
        <span className="meta" data-testid="storyboard-totals">
          {t.scenes} scenes / {t.shots} shots / {formatDuration(t.seconds)} of footage
        </span>
      }
    >
      <datalist id="an-moves">
        {MOVES.map((m) => (
          <option key={m} value={m} />
        ))}
      </datalist>
      <div className="space-y-6">
        <div className="grid gap-3 md:grid-cols-[1fr_16rem]">
          <div>
            <label className="label" htmlFor="logline">Logline</label>
            <textarea id="logline" className="field field-sm min-h-[60px] resize-y" value={sb.logline} onChange={(e) => onChange({ ...sb, logline: e.target.value })} />
          </div>
          <div>
            <label className="label" htmlFor="series">Series title</label>
            <input id="series" className="field field-sm" value={sb.series} onChange={(e) => onChange({ ...sb, series: e.target.value })} />
            <button type="button" className="btn-ghost mt-3 w-full py-2" onClick={onRegenerate} disabled={regenerating}>
              {regenerating ? "Rewriting" : "Regenerate storyboard"}
            </button>
          </div>
        </div>

        <CastSheet sb={sb} onChange={onChange} />

        <ol className="space-y-5">
          {sb.scenes.map((scene, si) => (
            <li key={scene.id} className="border-l-2 border-gold/70 pl-3 sm:pl-4" data-testid="scene">
              <div className="flex flex-wrap items-center gap-2">
                <span className="display text-[1.6rem]">Scene {String(si + 1).padStart(2, "0")}</span>
                <select
                  className="field field-sm w-auto font-mono uppercase"
                  value={scene.act}
                  aria-label="Act"
                  onChange={(e) => onChange(updateScene(sb, scene.id, { act: e.target.value }))}
                >
                  {(ACTS as readonly string[]).includes(scene.act) ? null : <option value={scene.act}>{scene.act}</option>}
                  {ACTS.map((a) => (
                    <option key={a} value={a}>
                      {a.replace("_", " ")}
                    </option>
                  ))}
                </select>
                <span className="meta">{scene.shots.length} shots</span>
                <span className="ml-auto flex gap-1">
                  <IconButton label="Move scene earlier" onClick={() => setScenes(move(sb.scenes, si, si - 1))} disabled={si === 0}>↑</IconButton>
                  <IconButton label="Move scene later" onClick={() => setScenes(move(sb.scenes, si, si + 1))} disabled={si === sb.scenes.length - 1}>↓</IconButton>
                  <IconButton label="Delete scene" onClick={() => setScenes(sb.scenes.filter((s) => s.id !== scene.id))}>×</IconButton>
                </span>
              </div>
              <div className="mt-2 grid gap-2 md:grid-cols-3">
                <input className="field field-sm" value={scene.setting} placeholder="Setting" aria-label="Setting" onChange={(e) => onChange(updateScene(sb, scene.id, { setting: e.target.value }))} />
                <input className="field field-sm" value={scene.caption} placeholder="On-screen caption" aria-label="Caption" onChange={(e) => onChange(updateScene(sb, scene.id, { caption: e.target.value }))} />
                <input className="field field-sm" value={scene.narration} placeholder="Narration" aria-label="Narration" onChange={(e) => onChange(updateScene(sb, scene.id, { narration: e.target.value }))} />
              </div>
              <ol className="mt-3 grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
                {scene.shots.map((shot, i) => {
                  shotNumber += 1;
                  return <ShotCard key={shot.id} sb={sb} scene={scene} shot={shot} index={i} number={shotNumber} onChange={onChange} />;
                })}
              </ol>
              <button
                type="button"
                className="btn-ghost mt-3"
                onClick={() => onChange(updateScene(sb, scene.id, { shots: [...scene.shots, blankShot()] }))}
                data-testid="add-shot"
              >
                + Shot
              </button>
            </li>
          ))}
        </ol>
        <button type="button" className="btn-ghost px-4 py-2.5" onClick={() => setScenes([...sb.scenes, blankScene()])}>
          + Scene
        </button>
      </div>
    </Step>
  );
}
