"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ENGINE_DEFAULT_URL, HOSTED_ENGINE, HOSTED_ENGINE_URL } from "@/lib/site";
import { createEngine, EngineError, isLoopbackUrl, normalizeEngineUrl } from "@/lib/studio/engine";
import { KEYS, loadProjects, read, saveProjects, write } from "@/lib/studio/storage";
import { newId, totals } from "@/lib/studio/storyboard-ops";
import type { Job, Project, Quality, Storyboard, StoryboardRequest } from "@/lib/studio/types";
import { EnginePanel, type EngineState } from "./engine-panel";
import { HeadlinePanel } from "./headline-panel";
import { KeyPanel } from "./key-panel";
import { ProjectsPanel } from "./projects-panel";
import { isTerminal, RenderPanel } from "./render-panel";
import { StoryboardEditor } from "./storyboard-editor";

const HEALTH_EVERY_MS = 4000;

function message(err: unknown, fallback: string): string {
  if (err instanceof EngineError) {
    if (err.status === 409) return "A render is already running on the engine. Wait for it, or cancel it.";
    if (err.status === 429) return err.message || "The shared demo engine has reached today's limit. Back tomorrow, or run your own engine.";
    if (err.status === 502) return `The script writer failed: ${err.message}. Try again in a moment.`;
    return err.message || fallback;
  }
  return fallback;
}

export function Studio() {
  const [ready, setReady] = useState(false);
  const [mock, setMock] = useState(false);
  const [localUrl, setLocalUrl] = useState(ENGINE_DEFAULT_URL);
  // The shared demo engine is the default when this deployment has one; the visitor can switch to their own.
  const [useLocal, setUseLocal] = useState(!HOSTED_ENGINE);
  const [savedKey, setSavedKey] = useState("");
  const [projects, setProjects] = useState<Project[]>([]);
  const [currentId, setCurrentId] = useState<string | null>(null);
  const [engineState, setEngineState] = useState<EngineState>({ kind: "checking" });
  const [writing, setWriting] = useState(false);
  const [writeError, setWriteError] = useState<string | null>(null);
  const [renderError, setRenderError] = useState<string | null>(null);
  const [activeJobId, setActiveJobId] = useState<string | null>(null);

  const hosted = HOSTED_ENGINE && !useLocal && !mock;
  const engineUrl = hosted ? HOSTED_ENGINE_URL : localUrl;
  const engine = useMemo(() => createEngine(engineUrl, mock), [engineUrl, mock]);
  const canPushKey = !hosted && (mock || isLoopbackUrl(engineUrl));
  const savedKeyRef = useRef(savedKey);
  savedKeyRef.current = savedKey;

  // Load everything this browser kept.
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    setMock(params.get("mock") === "1");
    setLocalUrl(normalizeEngineUrl(read(KEYS.engineUrl) || ENGINE_DEFAULT_URL));
    if (HOSTED_ENGINE) setUseLocal(read(KEYS.engineChoice) === "local");
    setSavedKey(read(KEYS.comfyKey) ?? "");
    const stored = loadProjects();
    setProjects(stored);
    const cur = read(KEYS.currentProject);
    if (cur && stored.some((p) => p.id === cur)) {
      setCurrentId(cur);
      const last = stored.find((p) => p.id === cur)?.jobs.at(-1);
      if (last && !isTerminal(last)) setActiveJobId(last.job_id);
    }
    setReady(true);
  }, []);

  useEffect(() => {
    if (ready) saveProjects(projects);
  }, [projects, ready]);
  useEffect(() => {
    if (ready) write(KEYS.currentProject, currentId);
  }, [currentId, ready]);

  // Engine health, plus handing the saved key to an engine that has none.
  const checkHealth = useCallback(async () => {
    try {
      let health = await engine.health();
      if (health.ok && health.has_credential === false && savedKeyRef.current && canPushKey) {
        await engine.setKey(savedKeyRef.current);
        health = await engine.health();
      }
      setEngineState({ kind: "up", health });
    } catch {
      setEngineState({ kind: "down" });
    }
  }, [engine, canPushKey]);

  useEffect(() => {
    if (!ready) return;
    setEngineState({ kind: "checking" });
    void checkHealth();
    const t = window.setInterval(() => void checkHealth(), HEALTH_EVERY_MS);
    return () => window.clearInterval(t);
  }, [ready, checkHealth]);

  const current = projects.find((p) => p.id === currentId) ?? null;
  const currentJob = current?.jobs.at(-1) ?? null;
  const engineUp = engineState.kind === "up";
  const health = engineState.kind === "up" ? engineState.health : null;
  const limits = hosted ? health?.limits : undefined;

  const patchProject = useCallback((id: string, fn: (p: Project) => Project) => {
    setProjects((ps) => ps.map((p) => (p.id === id ? { ...fn(p), updatedAt: Date.now() } : p)));
  }, []);

  const patchJob = useCallback((jobId: string, job: Job) => {
    setProjects((ps) =>
      ps.map((p) => (p.jobs.some((j) => j.job_id === jobId) ? { ...p, jobs: p.jobs.map((j) => (j.job_id === jobId ? job : j)) } : p)),
    );
  }, []);

  // Poll the running job until it is done or failed.
  useEffect(() => {
    if (!activeJobId) return;
    let stopped = false;
    const tick = async () => {
      try {
        const job = await engine.job(activeJobId);
        if (stopped) return;
        setRenderError(null);
        patchJob(activeJobId, job);
        if (isTerminal(job)) setActiveJobId(null);
      } catch (err) {
        if (stopped) return;
        if (err instanceof EngineError && err.status === 404) {
          patchJob(activeJobId, { job_id: activeJobId, status: "failed", stage: "lost", done: 0, total: 0, error: "The engine no longer knows this render (it was restarted)." });
          setActiveJobId(null);
        } else {
          setRenderError("Lost contact with the engine. Still trying.");
        }
      }
    };
    void tick();
    const t = window.setInterval(() => void tick(), engine.mock ? 700 : 2000);
    return () => {
      stopped = true;
      window.clearInterval(t);
    };
  }, [activeJobId, engine, patchJob]);

  async function saveKey(key: string): Promise<string | null> {
    write(KEYS.comfyKey, key);
    setSavedKey(key);
    if (!canPushKey) return "Saved in this browser. Point the engine URL at 127.0.0.1 to use it.";
    if (!engineUp) return key ? "Saved in this browser. It goes to the engine as soon as the engine is running." : null;
    try {
      await engine.setKey(key);
      await checkHealth();
      return null;
    } catch (err) {
      return message(err, "Saved in this browser, but the engine did not take it.");
    }
  }

  async function writeStoryboard(req: StoryboardRequest) {
    setWriting(true);
    setWriteError(null);
    try {
      const storyboard = await engine.storyboard(req);
      void checkHealth();
      const now = Date.now();
      const project: Project = { id: newId("proj"), headline: req.headline, request: req, createdAt: now, updatedAt: now, storyboard, jobs: [] };
      setProjects((ps) => [project, ...ps]);
      setCurrentId(project.id);
    } catch (err) {
      setWriteError(message(err, "The engine could not write a storyboard."));
    } finally {
      setWriting(false);
    }
  }

  async function regenerate() {
    if (!current) return;
    if (!window.confirm("Write a fresh storyboard for this headline? Your edits to this one are replaced.")) return;
    setWriting(true);
    setWriteError(null);
    try {
      const storyboard = await engine.storyboard(current.request);
      patchProject(current.id, (p) => ({ ...p, storyboard }));
    } catch (err) {
      setWriteError(message(err, "The engine could not write a storyboard."));
    } finally {
      setWriting(false);
    }
  }

  function chooseEngine(local: boolean) {
    write(KEYS.engineChoice, local ? "local" : null);
    setUseLocal(local);
    setActiveJobId(null);
    setWriteError(null);
    setRenderError(null);
  }

  async function render(quality: Quality, vertical: boolean) {
    if (!current) return;
    setRenderError(null);
    try {
      const jobId = await engine.render({ storyboard: current.storyboard, quality, vertical });
      void checkHealth();
      const job: Job = { job_id: jobId, status: "queued", stage: "queued", done: 0, total: totals(current.storyboard).shots };
      patchProject(current.id, (p) => ({ ...p, jobs: [...p.jobs, job] }));
      setActiveJobId(jobId);
    } catch (err) {
      setRenderError(message(err, "The engine could not start the render."));
    }
  }

  if (!ready) {
    return <div className="meta mx-auto max-w-[1100px] px-4 py-16 sm:px-6">Loading the Studio</div>;
  }

  return (
    <div className="mx-auto max-w-[1100px] space-y-6 px-4 pt-10 sm:px-6 lg:pt-14">
      <header className="mb-10">
        <p className="kicker">Studio {mock ? "/ demo mode" : ""}</p>
        <h1 className="display mt-3 text-[clamp(3rem,8vw,5.5rem)]">Direct an episode</h1>
        <p className="mt-4 max-w-2xl text-white/75">
          {hosted
            ? "The shared demo engine does the work; this page is the remote. Write the storyboard, change anything in it, then render the cut."
            : "Your engine runs on your computer and does the work; this page is the remote. Write the storyboard, change anything in it, then render the cut."}
        </p>
      </header>

      <EnginePanel
        url={engineUrl}
        mock={mock}
        hosted={hosted}
        hostedAvailable={HOSTED_ENGINE && !mock}
        state={engineState}
        onUrlChange={(url) => {
          const next = normalizeEngineUrl(url);
          write(KEYS.engineUrl, next === ENGINE_DEFAULT_URL ? null : next);
          setLocalUrl(next);
        }}
        onChooseLocal={chooseEngine}
      />
      {hosted ? null : (
        <KeyPanel savedKey={savedKey} engineHasKey={engineUp ? Boolean(engineState.health.has_credential) : null} canPush={canPushKey} onSave={saveKey} />
      )}
      <HeadlinePanel
        busy={writing && !current}
        error={current ? null : writeError}
        hasStoryboard={Boolean(current)}
        disabled={!engineUp}
        maxMinutes={limits?.max_minutes}
        onSubmit={writeStoryboard}
      />

      {current ? (
        <>
          <StoryboardEditor
            sb={current.storyboard}
            onChange={(sb: Storyboard) => patchProject(current.id, (p) => ({ ...p, storyboard: sb }))}
            onRegenerate={regenerate}
            regenerating={writing}
          />
          {writeError ? (
            <p role="alert" className="text-error">
              {writeError}
            </p>
          ) : null}
          <RenderPanel
            job={currentJob}
            error={renderError}
            disabled={!engineUp}
            fixedQuality={limits?.quality}
            shots={totals(current.storyboard).shots}
            mediaUrl={engine.mediaUrl}
            onRender={render}
            onCancel={() => {
              if (activeJobId) void engine.cancel(activeJobId).catch(() => setRenderError("The engine did not confirm the cancel."));
            }}
          />
        </>
      ) : null}

      <ProjectsPanel
        projects={projects}
        currentId={currentId}
        onOpen={(id) => {
          setCurrentId(id);
          setWriteError(null);
          setRenderError(null);
          const last = projects.find((p) => p.id === id)?.jobs.at(-1);
          setActiveJobId(last && !isTerminal(last) ? last.job_id : null);
          document.getElementById("storyboard")?.scrollIntoView({ block: "start" });
        }}
        onDelete={(id) => {
          if (!window.confirm("Delete this project from this browser?")) return;
          setProjects((ps) => ps.filter((p) => p.id !== id));
          if (id === currentId) setCurrentId(null);
        }}
        onNew={() => {
          setCurrentId(null);
          setActiveJobId(null);
          document.getElementById("headline")?.scrollIntoView({ block: "start" });
        }}
      />
    </div>
  );
}
