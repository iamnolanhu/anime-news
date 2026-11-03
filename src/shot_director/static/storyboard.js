/* Studio storyboard: groups the shot queue into scenes (title, set, characters), keeps the queue in
   scene order for the stitch, adds continuity helpers and a printable one-page storyboard.
   Client-side only: the storyboard lives in localStorage; the server queue stays the source of truth
   for which shots exist. Public API:
   window.storyboard = { init(el, opts), reconcile(shots), order(), ordered(), current(), prepare(req),
                         addShot(shot, beat), runtime(), exportPage() } */
(function () {
  "use strict";
  var KEY = "shotDirectorStoryboard";
  var el = null, opts = {};
  var sb = load();
  var pending = {};  // scene id -> shots rendering for it right now (not persisted)

  function uid() { return "sc-" + Math.random().toString(36).slice(2, 9); }
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  function blankScene(n) { return { id: uid(), title: "Scene " + n, set: "", characters: "", lockFrame: false, shots: [] }; }
  function load() {
    try {
      var v = JSON.parse(localStorage.getItem(KEY) || "null");
      if (v && Array.isArray(v.scenes) && v.scenes.length) return v;
    } catch (e) {}
    var s = blankScene(1);
    return { scenes: [s], current: s.id };
  }
  function save() { try { localStorage.setItem(KEY, JSON.stringify(sb)); } catch (e) {} }
  function current() {
    return sb.scenes.find(function (s) { return s.id === sb.current; }) || sb.scenes[sb.scenes.length - 1];
  }
  function ordered() {
    var out = [];
    sb.scenes.forEach(function (sc) { sc.shots.forEach(function (sh) { out.push(sh); }); });
    return out;
  }
  function order() { return ordered().map(function (s) { return s.id; }); }
  function sceneFrame(sc) {
    for (var i = 0; i < sc.shots.length; i++) if (sc.shots[i].frame) return sc.shots[i].frame;
    return null;
  }
  function runtime() {
    return ordered().reduce(function (t, s) { return t + (Number(s.duration) || 0); }, 0);
  }
  function fmtTime(sec) {
    sec = Math.round(sec * 10) / 10;
    if (sec < 60) return sec.toFixed(1) + " s";
    var m = Math.floor(sec / 60), r = Math.round(sec - m * 60);
    return m + ":" + String(r).padStart(2, "0");
  }
  function moveLabel(id) {
    var p = opts.presets && opts.presets();
    var m = p && (p.moves || []).find(function (x) { return x.id === id; });
    return m ? m.label : String(id || "").replace(/_/g, " ");
  }
  function engineLabel(id) {
    var p = opts.presets && opts.presets();
    var e = p && (p.engines || []).find(function (x) { return x.id === id; });
    return e ? e.label : String(id || "");
  }
  // a shot is "connected" to the one before it when it continues it (FLUX v2v) or shares its start frame
  function connected(prev, next) {
    if (!prev || !next) return null;
    if (next.continued_from && next.continued_from === prev.id) return "continues the previous shot (FLUX 3 Video)";
    if (next.frame && next.frame === prev.frame) return "same start frame";
    return null;
  }

  /* keep the storyboard in step with the server queue: drop shots the server no longer has, refresh
     the records, put any unassigned queue shots into the current scene */
  function reconcile(shots) {
    var byId = {};
    (shots || []).forEach(function (s) { byId[s.id] = s; });
    var seen = {};
    sb.scenes.forEach(function (sc) {
      sc.shots = sc.shots.filter(function (sh) { return byId[sh.id] && !seen[sh.id]; }).map(function (sh) {
        seen[sh.id] = true;
        var fresh = byId[sh.id];
        return Object.assign({}, sh, fresh, { duration: fresh.duration || sh.duration, beat: sh.beat, caption: sh.caption });
      });
    });
    var waiting = Object.keys(pending).filter(function (k) { return pending[k] > 0; });
    var cur = sceneById(waiting[0]) || current();  // a just-finished shot belongs to the scene that asked for it
    (shots || []).forEach(function (s) { if (!seen[s.id]) cur.shots.push(Object.assign({}, s)); });
    save();
    render();
    return ordered();
  }

  /* shot request -> request with the scene's continuity applied: set + characters lead the prompt,
     and the scene's first start frame is reused when the scene's toggle is on */
  function sceneById(id) { return id ? sb.scenes.find(function (s) { return s.id === id; }) : null; }

  /* a shot request starts: remember which scene asked, so the shot lands there even if the user
     switches scenes while it renders; returns that scene id */
  function begin() {
    var id = current().id;
    pending[id] = (pending[id] || 0) + 1;
    render();
    return id;
  }
  function finish(id) {
    if (pending[id]) pending[id] -= 1;
    render();
  }

  function prepare(req, sceneId) {
    var sc = sceneById(sceneId) || current(), out = Object.assign({}, req);
    var lead = [sc.set, sc.characters ? "Characters: " + sc.characters : ""].filter(Boolean).join(". ");
    if (lead) out.scene = lead + (req.scene ? ". " + req.scene : "");
    var f = sceneFrame(sc);
    if (sc.lockFrame && f) out.frame = f;
    return out;
  }

  function addShot(shot, beat, sceneId) {
    var sc = sceneById(sceneId) || current();
    sc.shots = sc.shots.filter(function (s) { return s.id !== shot.id; });
    sc.shots.push(Object.assign({}, shot, { beat: beat || "" }));
    save();
    render();
  }

  function moveShot(id, sceneId) {
    var shot = null;
    sb.scenes.forEach(function (sc) {
      sc.shots = sc.shots.filter(function (s) { if (s.id === id) { shot = s; return false; } return true; });
    });
    var dest = sb.scenes.find(function (s) { return s.id === sceneId; });
    if (shot && dest) dest.shots.push(shot);
    save(); render(); if (opts.onChange) opts.onChange();
  }

  function newSceneForm() {
    var f = el.querySelector(".sb-new");
    f.hidden = !f.hidden;
    if (!f.hidden) f.querySelector("[name=title]").focus();
  }

  function addScene(form) {
    var data = new FormData(form);
    var sc = blankScene(sb.scenes.length + 1);
    sc.title = (data.get("title") || "").trim() || sc.title;
    sc.set = (data.get("set") || "").trim();
    sc.characters = (data.get("characters") || "").trim();
    sc.lockFrame = !!data.get("lockFrame");
    sb.scenes.push(sc);
    sb.current = sc.id;
    form.reset();
    save(); render(); if (opts.onChange) opts.onChange();
  }

  var LINK_SVG = '<svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M10 13a5 5 0 0 0 7.07 0l3-3a5 5 0 0 0-7.07-7.07l-1.5 1.5"/><path d="M14 11a5 5 0 0 0-7.07 0l-3 3a5 5 0 0 0 7.07 7.07l1.5-1.5"/></svg>';

  function cardHtml(sh, n, sceneId) {
    var thumb = sh.frame
      ? '<img src="/media/' + esc(sh.frame) + '" alt="" loading="lazy">'
      : '<video src="' + esc(sh.url) + '#t=0.4" muted playsinline preload="metadata"></video>';
    var opts2 = sb.scenes.map(function (sc, i) {
      return '<option value="' + sc.id + '"' + (sc.id === sceneId ? " selected" : "") + ">" + (i + 1) + ". " + esc(sc.title) + "</option>";
    }).join("");
    return '<div class="sb-card" data-id="' + esc(sh.id) + '" title="' + esc(sh.prompt) + '">' +
      '<div class="sb-thumb">' + thumb + '<span class="sb-n">' + n + "</span></div>" +
      '<div class="sb-meta"><b>' + esc(moveLabel(sh.move)) + "</b> · " + esc(sh.body) + "<br>" +
      esc(engineLabel(sh.engine)) + ' · <span class="sb-dur">' + (sh.duration ? Number(sh.duration).toFixed(1) + " s" : "…") + "</span></div>" +
      (sh.beat ? '<div class="sb-beat">' + esc(sh.beat) + "</div>" : "") +
      '<label class="sb-move">Scene <select data-move="' + esc(sh.id) + '">' + opts2 + "</select></label></div>";
  }

  function render() {
    if (!el) return;
    var n = 0, max = (opts.max && opts.max()) || 12, total = ordered().length;
    var head = '<div class="sb-bar"><span class="sb-stats"><b>' + sb.scenes.length + "</b> scene" + (sb.scenes.length === 1 ? "" : "s") +
      " · <b>" + total + "</b>/" + max + " shots · runtime <b>" + fmtTime(runtime()) + "</b></span>" +
      '<span style="flex:1"></span><button class="ghost" data-act="new">New scene</button>' +
      '<button class="ghost" data-act="export">Storyboard page</button></div>' +
      '<form class="sb-new" hidden autocomplete="off">' +
      '<input type="text" name="title" placeholder="Scene title (e.g. Rooftop standoff)">' +
      '<input type="text" name="set" placeholder="Set: place, time, light (e.g. neon rooftop, night, rain, magenta key light)">' +
      '<input type="text" name="characters" placeholder="Characters, comma separated (e.g. Aiko, the courier)">' +
      '<label class="sb-check"><input type="checkbox" name="lockFrame"> Use scene start frame for every shot</label>' +
      '<div class="row"><button class="primary" type="submit" style="margin-top:0">Add scene</button><button class="ghost" type="button" data-act="cancel">Cancel</button></div></form>';
    var body = sb.scenes.map(function (sc, si) {
      var isCur = sc.id === current().id;
      var cards = "", prev = null;
      sc.shots.forEach(function (sh) {
        n += 1;
        var link = connected(prev, sh);
        if (prev) cards += link
          ? '<span class="sb-link on" title="Connected: ' + esc(link) + '">' + LINK_SVG + "</span>"
          : '<span class="sb-link" title="Not connected: new start frame">' + "</span>";
        cards += cardHtml(sh, n, sc.id);
        prev = sh;
      });
      for (var w = 0; w < (pending[sc.id] || 0); w++) {
        cards += (sc.shots.length || w ? '<span class="sb-link"></span>' : "") +
          '<div class="sb-card sb-pending fx-scanning"><div class="sb-thumb"></div><div class="sb-meta">Rendering for this scene…</div></div>';
      }
      if (!sc.shots.length && !pending[sc.id]) cards = '<div class="sb-empty">' + (isCur ? "Next shot lands here." : "No shots yet.") + "</div>";
      var secs = sc.shots.reduce(function (t, s) { return t + (Number(s.duration) || 0); }, 0);
      return '<div class="sb-scene' + (isCur ? " current" : "") + '" data-scene="' + sc.id + '">' +
        '<div class="sb-shead"><span class="sb-sn">' + String(si + 1).padStart(2, "0") + "</span>" +
        '<div class="sb-st"><b>' + esc(sc.title) + "</b>" +
        '<div class="sb-set">' + (sc.set ? esc(sc.set) : "<i>no set line</i>") + (sc.characters ? " · " + esc(sc.characters) : "") + "</div></div>" +
        '<span class="sb-time">' + sc.shots.length + " shot" + (sc.shots.length === 1 ? "" : "s") + " · " + fmtTime(secs) + "</span>" +
        '<label class="sb-check" title="Reuse this scene\'s first start frame as the start frame of every later shot in it">' +
        '<input type="checkbox" data-lock="' + sc.id + '"' + (sc.lockFrame ? " checked" : "") + "> Use scene start frame for every shot</label>" +
        (isCur ? '<span class="sb-cur">current</span>' : '<button class="ghost" data-pick="' + sc.id + '">Shoot here</button>') +
        (!sc.shots.length && !pending[sc.id] && sb.scenes.length > 1 ? '<button class="ghost" data-del="' + sc.id + '" title="Delete empty scene">×</button>' : "") +
        '</div><div class="sb-row">' + cards + "</div></div>";
    }).join("");
    el.innerHTML = head + '<div class="sb-scenes">' + body + "</div>";
    // fill unknown durations (older records) from the clip metadata
    el.querySelectorAll(".sb-card video").forEach(function (v) {
      v.addEventListener("loadedmetadata", function () {
        var id = v.closest(".sb-card").dataset.id;
        var sh = ordered().find(function (s) { return s.id === id; });
        if (sh && !sh.duration && isFinite(v.duration)) { sh.duration = v.duration; save(); render(); }
      }, { once: true });
    });
  }

  function bind() {
    el.addEventListener("click", function (e) {
      var t = e.target.closest("button");
      if (!t) return;
      if (t.dataset.act === "new") { e.preventDefault(); newSceneForm(); }
      else if (t.dataset.act === "cancel") { el.querySelector(".sb-new").hidden = true; }
      else if (t.dataset.act === "export") { exportPage(); }
      else if (t.dataset.pick) { sb.current = t.dataset.pick; save(); render(); }
      else if (t.dataset.del) {
        sb.scenes = sb.scenes.filter(function (s) { return s.id !== t.dataset.del || s.shots.length; });
        if (!sb.scenes.some(function (s) { return s.id === sb.current; })) sb.current = sb.scenes[sb.scenes.length - 1].id;
        save(); render();
      }
    });
    el.addEventListener("change", function (e) {
      var t = e.target;
      if (t.dataset.move) moveShot(t.dataset.move, t.value);
      else if (t.dataset.lock) {
        var sc = sb.scenes.find(function (s) { return s.id === t.dataset.lock; });
        if (sc) { sc.lockFrame = t.checked; save(); render(); }
      }
    });
    el.addEventListener("submit", function (e) {
      e.preventDefault();
      addScene(e.target);
    });
  }

  /* ---------- printable storyboard page (standalone HTML in a blob) ---------- */
  function videoThumb(url) {
    return new Promise(function (resolve) {
      var v = document.createElement("video"), done = false;
      function finish(x) { if (!done) { done = true; resolve(x); } }
      v.muted = true; v.preload = "auto"; v.crossOrigin = "anonymous"; v.src = url;
      v.addEventListener("loadeddata", function () { v.currentTime = Math.min(0.4, (v.duration || 1) / 2); });
      v.addEventListener("seeked", function () {
        try {
          var c = document.createElement("canvas");
          c.width = 480; c.height = Math.round(480 * (v.videoHeight || 9) / (v.videoWidth || 16));
          c.getContext("2d").drawImage(v, 0, 0, c.width, c.height);
          finish(c.toDataURL("image/jpeg", 0.8));
        } catch (e) { finish(null); }
      });
      v.addEventListener("error", function () { finish(null); });
      setTimeout(function () { finish(null); }, 5000);
    });
  }

  function exportHtml(thumbs) {
    var origin = location.origin, n = 0;
    var rows = sb.scenes.filter(function (sc) { return sc.shots.length; }).map(function (sc, si) {
      var prev = null;
      var cards = sc.shots.map(function (sh) {
        n += 1;
        var link = connected(prev, sh);
        prev = sh;
        var img = thumbs[sh.id] ? '<img src="' + esc(thumbs[sh.id]) + '" alt="">' : '<div class="noimg">no frame</div>';
        return (link ? '<div class="lk" title="' + esc(link) + '">' + LINK_SVG + "</div>" : (n > 1 && sc.shots[0] !== sh ? '<div class="lk off"></div>' : "")) +
          '<figure><div class="th">' + img + "<span>" + n + "</span></div>" +
          '<figcaption><div class="m"><b>' + esc(moveLabel(sh.move)) + "</b> · " + esc(sh.body) + " · " + esc(engineLabel(sh.engine)) +
          " · " + (sh.duration ? Number(sh.duration).toFixed(1) + " s" : "?") + "</div>" +
          '<div class="cap" contenteditable="true" spellcheck="true">' + esc(sh.caption || sh.beat || "") + "</div>" +
          '<a class="no-print" href="' + origin + esc(sh.url) + '" target="_blank" rel="noopener">clip</a></figcaption></figure>';
      }).join("");
      var secs = sc.shots.reduce(function (t, s) { return t + (Number(s.duration) || 0); }, 0);
      return '<section><header><span class="sn">' + String(si + 1).padStart(2, "0") + '</span><div><h2>' + esc(sc.title) + "</h2>" +
        "<p>" + (sc.set ? esc(sc.set) : "") + (sc.characters ? " · <em>" + esc(sc.characters) + "</em>" : "") + "</p></div>" +
        '<span class="t">' + sc.shots.length + " shots · " + fmtTime(secs) + "</span></header>" +
        '<div class="row">' + cards + "</div></section>";
    }).join("");
    var scenesWithShots = sb.scenes.filter(function (sc) { return sc.shots.length; }).length;
    var css = [
      "@page{size:A4 landscape;margin:12mm}",
      "*{box-sizing:border-box}body{margin:0;background:#000;color:#fff;font:14px/1.45 Poppins,system-ui,-apple-system,'Segoe UI',Roboto,Arial,sans-serif;padding:28px 34px}",
      ".top{display:flex;align-items:baseline;gap:16px;border-bottom:1px solid rgba(245,197,24,.4);padding-bottom:12px;margin-bottom:18px}",
      ".top h1{margin:0;font:700 22px/1 'Avenir Next Condensed','Roboto Condensed','Arial Narrow',Arial,sans-serif;letter-spacing:.12em;text-transform:uppercase;color:#F5C518}",
      ".top .s{font:12px ui-monospace,Menlo,monospace;color:#a3a3a3;letter-spacing:.06em;text-transform:uppercase}",
      ".top button{margin-left:auto;background:#F5C518;color:#000;border:0;padding:8px 14px;font:700 12px ui-monospace,Menlo,monospace;letter-spacing:.12em;text-transform:uppercase;cursor:pointer}",
      "section{border:1px solid rgba(245,197,24,.28);border-left:3px solid #F5C518;padding:12px 14px 14px;margin-bottom:14px;break-inside:avoid;background:#0d0d0d}",
      "section header{display:flex;gap:12px;align-items:flex-start;margin-bottom:10px}",
      ".sn{font:700 20px ui-monospace,Menlo,monospace;color:#F5C518;min-width:30px}",
      "h2{margin:0;font-size:16px;letter-spacing:.02em}section p{margin:2px 0 0;color:#a3a3a3;font-size:12.5px}em{color:#FFE566;font-style:normal}",
      ".t{margin-left:auto;font:11px ui-monospace,Menlo,monospace;color:#a3a3a3;text-transform:uppercase;white-space:nowrap}",
      ".row{display:flex;flex-wrap:wrap;align-items:center;gap:6px}",
      "figure{margin:0;width:208px;border:1px solid rgba(255,255,255,.12);background:#000}",
      ".th{position:relative;aspect-ratio:16/9;background:#111}.th img{width:100%;height:100%;object-fit:cover;display:block}",
      ".th span{position:absolute;left:0;top:0;background:#F5C518;color:#000;font:700 11px ui-monospace,Menlo,monospace;padding:2px 6px}",
      ".noimg{height:100%;display:flex;align-items:center;justify-content:center;color:#555;font:11px ui-monospace,monospace}",
      "figcaption{padding:6px 8px 8px}.m{font:10.5px ui-monospace,Menlo,monospace;color:#a3a3a3;text-transform:uppercase;letter-spacing:.03em}.m b{color:#F5C518}",
      ".cap{min-height:2.6em;margin-top:5px;font-size:12px;color:#fff;border-bottom:1px dashed rgba(255,255,255,.18);outline:none}",
      ".cap:empty::before{content:'caption…';color:#555}",
      "figcaption a{font:10px ui-monospace,monospace;color:#19E6FF}",
      ".lk{width:22px;display:flex;justify-content:center;color:#19E6FF}.lk.off{color:transparent}",
      "@media print{body{background:#fff;color:#000;padding:0}section{background:#fff;border-color:#C99700}.top h1,.sn,.m b{color:#8a6a00}.no-print,.top button{display:none}.cap{color:#000}.cap:empty::before{content:''}figure{border-color:#ccc}.lk{color:#0a8fa0}section p,.t,.m{color:#444}em{color:#8a6a00}}"
    ].join("\n");
    return '<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Anime News storyboard</title><style>' + css + "</style></head><body>" +
      '<div class="top"><h1>Anime News · Storyboard</h1><span class="s">' + scenesWithShots + " scenes · " + n + " shots · runtime " + fmtTime(runtime()) +
      " · " + new Date().toLocaleDateString("en-CA") + '</span><button onclick="print()">Print</button></div>' +
      (rows || "<p>No shots yet.</p>") + "</body></html>";
  }

  function exportPage() {
    var win = window.open("", "_blank");  // open synchronously so pop-up blockers allow it
    var shots = ordered(), thumbs = {};
    return Promise.all(shots.map(function (sh) {
      if (sh.frame) { thumbs[sh.id] = location.origin + "/media/" + sh.frame; return null; }
      return videoThumb(sh.url).then(function (d) { if (d) thumbs[sh.id] = d; });
    })).then(function () {
      var url = URL.createObjectURL(new Blob([exportHtml(thumbs)], { type: "text/html" }));
      if (win) win.location.href = url; else location.href = url;
      try { if (window.posthog && window.posthog.capture) window.posthog.capture("storyboard_export", { scenes: sb.scenes.length, shots: shots.length }); } catch (e) {}
      return url;
    });
  }

  function init(target, o) {
    el = target; opts = o || {};
    bind(); render();
  }

  window.storyboard = { init: init, reconcile: reconcile, order: order, ordered: ordered, current: current,
    prepare: prepare, begin: begin, finish: finish, addShot: addShot, runtime: runtime, exportPage: exportPage, exportHtml: exportHtml, render: render };
})();
