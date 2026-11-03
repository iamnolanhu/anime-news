/* Anime News local engine UI: key -> headline -> editable storyboard (no shot cap) -> render job -> episodes.
   Plain DOM, no framework; user text only ever goes through textContent / value. */
(function () {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const KINDS = ["talk", "action", "still", "insert"];
  const ACTS = ["cold_open", "setup", "escalation", "turn", "climax", "sign_off"];
  let MOVES = ["dolly_in", "dolly_out", "orbit", "crane_up", "handheld", "whip_pan", "crash_zoom", "static"];
  let sb = null;      // the storyboard being edited
  let jobTimer = null;

  const api = (path, opts) => (window.access ? window.access.fetch(path, opts || {}) : fetch(path, opts || {}));
  async function call(path, method, body) {
    const r = await api(path, { method: method || "GET", headers: body ? { "Content-Type": "application/json" } : {},
                                body: body ? JSON.stringify(body) : undefined });
    const j = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(j.detail || j.error || r.statusText);
    return j;
  }
  function status(msg, err) { $("status").textContent = msg || ""; $("status").className = err ? "err" : ""; }
  function el(tag, attrs, kids) {
    const n = document.createElement(tag);
    Object.entries(attrs || {}).forEach(([k, v]) => {
      if (k === "text") n.textContent = v; else if (k === "class") n.className = v;
      else if (k.startsWith("on")) n.addEventListener(k.slice(2), v); else n.setAttribute(k, v);
    });
    (kids || []).forEach((c) => c && n.appendChild(c));
    return n;
  }
  function select(options, value, onchange) {
    const s = el("select", { onchange: (e) => onchange(e.target.value) });
    options.forEach((o) => { const opt = el("option", { value: o, text: o }); if (o === value) opt.selected = true; s.appendChild(opt); });
    return s;
  }
  function field(label, node) { return el("div", {}, [el("label", { text: label }), node]); }
  function input(value, onchange, tag) {
    const n = el(tag || "input", tag ? {} : { type: "text" });
    n.value = value || "";
    n.addEventListener("input", (e) => onchange(e.target.value));
    return n;
  }
  const uid = () => Math.random().toString(36).slice(2, 8);

  // -- engine state --------------------------------------------------------------------------------
  async function refreshConfig() {
    const c = await call("/api/config");
    $("mode").textContent = c.mode === "live" ? "live" : "mock (offline placeholders)";
    $("mode").className = "badge " + c.mode;
    $("keyState").textContent = c.has_credential ? "A key is saved. Renders spend your Comfy credits." :
      "No key saved: storyboards come from a template and renders are offline placeholders.";
  }
  $("saveKey").onclick = async () => {
    const v = $("key").value.trim();
    if (!v) { status("Paste a key first.", true); return; }
    try { await call("/api/config", "PUT", { comfy_api_key: v }); $("key").value = ""; status("Key saved on this machine."); }
    catch (e) { status(e.message, true); }
    refreshConfig().catch(() => {});
  };
  $("clearKey").onclick = async () => {
    try { await call("/api/config", "PUT", { comfy_api_key: "" }); status("Key cleared."); } catch (e) { status(e.message, true); }
    refreshConfig().catch(() => {});
  };

  // -- storyboard ----------------------------------------------------------------------------------
  async function write() {
    const headline = $("headline").value.trim();
    if (!headline) { status("Type a headline first.", true); return; }
    const extra = $("text").value.trim();
    const body = { headline, minutes: parseFloat($("minutes").value) || 1, style: $("style").value.trim() || undefined };
    if (/^https?:\/\/\S+$/i.test(extra)) body.url = extra; else if (extra) body.text = extra;
    $("write").disabled = $("rewrite").disabled = true;
    status("Writing the storyboard (about a minute when live)...");
    try {
      const j = await call("/api/storyboard", "POST", body);
      sb = j.storyboard;
      draw();
      status("Storyboard ready. Edit it, then render.");
    } catch (e) { status(e.message, true); }
    finally { $("write").disabled = $("rewrite").disabled = false; }
  }
  $("write").onclick = write;
  $("rewrite").onclick = write;

  function stats() {
    const shots = sb.scenes.flatMap((s) => s.shots);
    const n = (k) => shots.filter((s) => s.kind === k).length;
    $("sbStats").textContent = `${sb.scenes.length} scenes · ${shots.length} shots (${n("talk")} talk, ${n("action")} action, ${n("still")} still, ${n("insert")} insert)`;
  }
  function move(list, i, d) { const j = i + d; if (j < 0 || j >= list.length) return; [list[i], list[j]] = [list[j], list[i]]; draw(); }

  function shotCard(scene, shot, i) {
    const castIds = (sb.cast || []).map((c) => c.id);
    const card = el("div", { class: "shot " + shot.kind });
    card.appendChild(el("div", { class: "row" }, [
      field("Kind", select(KINDS, shot.kind, (v) => { shot.kind = v; if (v === "talk" && !shot.line) shot.line = "..."; draw(); })),
      field("Move", select(MOVES, shot.move, (v) => { shot.move = v; })),
    ]));
    if (shot.kind === "talk") {
      card.appendChild(field("Speaker", select(castIds, (shot.cast || [])[0], (v) => { shot.cast = [v]; })));
      const line = input(shot.line, (v) => { shot.line = v; });
      line.maxLength = 60;
      card.appendChild(field("Spoken line (60 chars)", line));
    }
    card.appendChild(field("Prompt", input(shot.prompt, (v) => { shot.prompt = v; }, "textarea")));
    card.appendChild(el("div", { class: "tools" }, [
      el("button", { class: "ghost", text: "←", title: "earlier", onclick: () => move(scene.shots, i, -1) }),
      el("button", { class: "ghost", text: "→", title: "later", onclick: () => move(scene.shots, i, 1) }),
      el("button", { class: "ghost", text: "Duplicate", onclick: () => { scene.shots.splice(i + 1, 0, { ...shot, id: shot.id + "_" + uid() }); draw(); } }),
      el("button", { class: "ghost danger", text: "Delete", onclick: () => { scene.shots.splice(i, 1); draw(); } }),
    ]));
    return card;
  }

  function draw() {
    $("sbPanel").hidden = $("renderPanel").hidden = !sb;
    if (!sb) return;
    $("logline").value = sb.logline || "";
    $("logline").oninput = (e) => { sb.logline = e.target.value; };
    const root = $("scenes");
    root.replaceChildren();
    sb.scenes.forEach((scene, si) => {
      const box = el("div", { class: "scene" });
      box.appendChild(el("div", { class: "scene-head" }, [
        el("strong", { text: `Scene ${si + 1}` }),
        select(ACTS, scene.act, (v) => { scene.act = v; }),
        el("span", { style: "flex:1" }),
        el("button", { class: "ghost", text: "↑", onclick: () => move(sb.scenes, si, -1) }),
        el("button", { class: "ghost", text: "↓", onclick: () => move(sb.scenes, si, 1) }),
        el("button", { class: "ghost danger", text: "Delete scene", onclick: () => { sb.scenes.splice(si, 1); draw(); } }),
      ]));
      box.appendChild(el("div", { class: "row" }, [
        field("Setting", input(scene.setting, (v) => { scene.setting = v; })),
        field("Caption", input(scene.caption, (v) => { scene.caption = v; })),
      ]));
      box.appendChild(field("Narration", input(scene.narration, (v) => { scene.narration = v; }, "textarea")));
      const shots = el("div", { class: "shots" });
      scene.shots.forEach((shot, i) => shots.appendChild(shotCard(scene, shot, i)));
      box.appendChild(shots);
      const add = (kind) => () => {
        scene.shots.push({ id: `${scene.id}_${uid()}`, kind, cast: kind === "talk" ? [(sb.cast[0] || {}).id] : [],
                           line: kind === "talk" ? "Good evening." : undefined, prompt: "Medium shot of ...", move: "static", seconds: 5 });
        draw();
      };
      box.appendChild(el("div", { class: "tools" }, KINDS.map((k) => el("button", { class: "ghost", text: "+ " + k, onclick: add(k) }))));
      root.appendChild(box);
    });
    stats();
  }
  $("addScene").onclick = () => {
    sb.scenes.push({ id: "s" + uid(), act: "setup", setting: "", caption: "", narration: "",
                     shots: [{ id: "x" + uid(), kind: "still", cast: [], prompt: "Wide shot of ...", move: "static", seconds: 5 }] });
    draw();
  };

  // -- render job ----------------------------------------------------------------------------------
  function showJob(j) {
    const pct = j.total ? Math.round(100 * j.done / j.total) : 0;
    $("bar").style.width = (j.status === "done" ? 100 : pct) + "%";
    $("jobState").textContent = `${j.status} · ${j.stage} · ${j.done}/${j.total}` + (j.error ? ` · ${j.error}` : "");
    if (j.status === "done" && j.episode) {
      const ep = j.episode;
      const kids = [el("video", { src: ep.mp4_url, controls: "", playsinline: "" }),
        el("div", { class: "hint", text: `${Math.round(ep.duration || 0)} s · ${ep.shots} cuts · ${ep.cuts_per_minute} cuts/min` +
          (ep.reuse ? ` · ${ep.reuse.overlapping_windows + ep.reuse.still_repeats} reused moments` : "") }),
        el("a", { href: ep.mp4_url, download: "", text: "Download mp4" })];
      if (ep.vertical_url) kids.push(el("span", { text: " · " }), el("a", { href: ep.vertical_url, download: "", text: "Download 9:16" }));
      $("result").replaceChildren(...kids);
    }
  }
  async function poll(id) {
    try {
      const j = await call(`/api/jobs/${id}`);
      showJob(j);
      if (j.status === "done" || j.status === "failed") {
        clearInterval(jobTimer); jobTimer = null;
        $("render").disabled = false; $("cancel").hidden = true;
        status(j.status === "done" ? "Episode ready." : "Render failed: " + (j.error || ""), j.status !== "done");
        loadEpisodes().catch(() => {});
      }
    } catch (e) { status(e.message, true); }
  }
  $("render").onclick = async () => {
    if (!sb) return;
    $("render").disabled = true; $("result").replaceChildren();
    try {
      const j = await call("/api/render", "POST", { storyboard: sb, quality: $("quality").value, vertical: $("vertical").checked });
      status(`Rendering (${j.mode}${j.estimate_usd != null && j.mode === "live" ? `, estimated $${j.estimate_usd}` : ""})...`);
      $("cancel").hidden = false;
      $("cancel").onclick = () => call(`/api/jobs/${j.job_id}`, "DELETE").then(() => status("Cancelling after the shots in flight...")).catch((e) => status(e.message, true));
      jobTimer = setInterval(() => poll(j.job_id), 2500);
      poll(j.job_id);
    } catch (e) { status(e.message, true); $("render").disabled = false; }
  };

  // -- episodes ------------------------------------------------------------------------------------
  async function loadEpisodes() {
    const j = await call("/api/episodes");
    const done = (j.episodes || []).filter((e) => e.mp4_url).slice(0, 12);
    $("episodes").replaceChildren(...done.map((e) => el("div", { class: "ep" }, [
      el("video", { src: e.mp4_url, controls: "", preload: "metadata", playsinline: "" }),
      el("div", { text: e.headline || "" }),
      el("div", { class: "meta", text: [e.duration ? `${Math.round(e.duration)} s` : null, e.shots ? `${e.shots} cuts` : null,
        e.quality, e.cost_estimate != null ? `~$${e.cost_estimate}` : null].filter(Boolean).join(" · ") }),
    ])));
    $("noEps").hidden = done.length > 0;
  }

  fetch("/api/presets").then((r) => r.json()).then((p) => { if (Array.isArray(p.moves)) MOVES = p.moves.map((m) => m.id); }).catch(() => {});
  refreshConfig().catch((e) => status(e.message, true));
  loadEpisodes().catch(() => {});
})();
