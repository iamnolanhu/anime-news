"use client";

import { totals } from "@/lib/studio/storyboard-ops";
import type { Project } from "@/lib/studio/types";
import { Step } from "./step";

export function ProjectsPanel({
  projects,
  currentId,
  onOpen,
  onDelete,
  onNew,
}: {
  projects: Project[];
  currentId: string | null;
  onOpen: (id: string) => void;
  onDelete: (id: string) => void;
  onNew: () => void;
}) {
  return (
    <Step
      n="06"
      title="Projects"
      id="projects"
      aside={
        <button type="button" className="btn-ghost" onClick={onNew}>
          + New
        </button>
      }
    >
      {projects.length === 0 ? (
        <p className="meta">Storyboards and renders you make are kept here, in this browser only.</p>
      ) : (
        <ul className="divide-y divide-white/10 border-y border-white/10">
          {projects.map((p) => {
            const t = totals(p.storyboard);
            const last = p.jobs[p.jobs.length - 1];
            return (
              <li key={p.id} className="flex flex-wrap items-center gap-x-4 gap-y-1 py-3">
                <button type="button" className="min-w-0 flex-1 text-left" onClick={() => onOpen(p.id)}>
                  <span className={`block truncate ${p.id === currentId ? "text-gold" : "text-white"}`}>{p.headline}</span>
                  <span className="meta">
                    {new Date(p.updatedAt).toLocaleString()} / {t.scenes} scenes / {t.shots} shots
                    {last ? ` / last render ${last.status}` : ""}
                  </span>
                </button>
                <button type="button" className="btn-ghost" onClick={() => onDelete(p.id)} aria-label={`Delete ${p.headline}`}>
                  Delete
                </button>
              </li>
            );
          })}
        </ul>
      )}
    </Step>
  );
}
