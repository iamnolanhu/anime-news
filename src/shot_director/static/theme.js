/* Anime News theme motion: particle field, header ident + anime.js micro-animations.
   Uses the vendored anime.js v3 (window.anime). If anime.js is missing every call
   below is a silent no-op; the particle field is plain canvas and needs no library.
   Public API: window.theme = { burst(el), scan(el, on), pulse(el), land(el), ident(), enabled } */
(function () {
  "use strict";

  var A = typeof window.anime === "function" ? window.anime : null;
  var mq = window.matchMedia ? window.matchMedia("(prefers-reduced-motion: reduce)") : { matches: false };
  function reduced() { return !!mq.matches; }
  function css(name, fallback) {
    var v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
    return v || fallback;
  }
  function safe(fn) {
    return function () {
      try { return fn.apply(null, arguments); } catch (e) { /* theme must never break the app */ }
    };
  }

  /* ---------- (a) particle field: gold motes drift up, rare cyan sparks ---------- */
  var MAX_PARTICLES = 80;      // hard cap
  var CYAN_CHANCE = 0.06;      // share of particles that are cyan sparks
  var particles = [], canvas = null, ctx = null, raf = 0, w = 0, h = 0, dpr = 1, gold, cyan;

  function spawn(p, anywhere) {
    p.x = Math.random() * w;
    p.y = anywhere ? Math.random() * h : h + 10;
    p.spark = Math.random() < CYAN_CHANCE;
    p.r = p.spark ? 0.8 + Math.random() * 0.8 : 0.6 + Math.random() * 1.6;
    p.vy = -(0.12 + Math.random() * 0.38) * (p.spark ? 2.2 : 1);
    p.vx = (Math.random() - 0.5) * 0.15;
    p.life = 0;
    p.ttl = 500 + Math.random() * 900;
    p.phase = Math.random() * Math.PI * 2;
    return p;
  }
  function resize() {
    dpr = Math.min(window.devicePixelRatio || 1, 2);
    w = window.innerWidth; h = window.innerHeight;
    canvas.width = Math.round(w * dpr); canvas.height = Math.round(h * dpr);
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  }
  function frame() {
    ctx.clearRect(0, 0, w, h);
    for (var i = 0; i < particles.length; i++) {
      var p = particles[i];
      p.life++; p.x += p.vx + Math.sin((p.life + p.phase * 40) / 60) * 0.12; p.y += p.vy;
      if (p.y < -10 || p.life > p.ttl) spawn(p, false);
      var t = p.life / p.ttl;
      var a = Math.min(1, t * 6) * (1 - t);                  // fade in, fade out
      if (p.spark) a *= 0.5 + 0.5 * Math.abs(Math.sin(p.life / 7)); // flicker
      ctx.globalAlpha = a * (p.spark ? 0.9 : 0.55);
      ctx.fillStyle = p.spark ? cyan : gold;
      ctx.beginPath(); ctx.arc(p.x, p.y, p.r, 0, Math.PI * 2); ctx.fill();
    }
    ctx.globalAlpha = 1;
    raf = requestAnimationFrame(frame);
  }
  function startParticles() {
    if (raf || reduced() || document.hidden || !canvas) return;
    raf = requestAnimationFrame(frame);
  }
  function stopParticles() { if (raf) { cancelAnimationFrame(raf); raf = 0; } }
  var initParticles = safe(function () {
    canvas = document.getElementById("fx-particles");
    if (!canvas || !canvas.getContext) return;
    ctx = canvas.getContext("2d");
    if (!ctx) { canvas = null; return; }
    gold = css("--sd-gold", "#F5C518"); cyan = css("--sd-cyan", "#19E6FF");
    resize();
    var n = Math.min(MAX_PARTICLES, Math.round((w * h) / 16000)); // fewer on small screens
    for (var i = 0; i < n; i++) particles.push(spawn({}, true));
    window.addEventListener("resize", resize);
    document.addEventListener("visibilitychange", function () { document.hidden ? stopParticles() : startParticles(); });
    var onMotionPref = function () { if (reduced()) { stopParticles(); ctx.clearRect(0, 0, w, h); } else startParticles(); };
    if (mq.addEventListener) mq.addEventListener("change", onMotionPref);
    else if (mq.addListener) mq.addListener(onMotionPref);
    startParticles();
  });

  /* ---------- (b) panel entrance stagger on load ---------- */
  var enter = safe(function () {
    if (!A || reduced()) return;
    var targets = document.querySelectorAll("header, main section");
    if (!targets.length) return;
    document.documentElement.classList.add("fx-enter");
    A({
      targets: targets,
      opacity: [0, 1],
      translateY: [14, 0],
      duration: 520,
      easing: "easeOutQuint",
      delay: A.stagger(60),
      complete: function () {
        document.documentElement.classList.remove("fx-enter");
        for (var i = 0; i < targets.length; i++) { targets[i].style.opacity = ""; targets[i].style.transform = ""; }
      }
    });
  });

  /* ---------- (c) chip select pulse ---------- */
  var pulse = safe(function (el) {
    if (!A || !el || reduced()) return;
    A.remove(el);
    A({ targets: el, scale: [0.94, 1], duration: 380, easing: "easeOutBack" });
    var ring = document.createElement("span");
    ring.setAttribute("aria-hidden", "true");
    ring.style.cssText = "position:absolute;inset:0;pointer-events:none;box-shadow:inset 0 0 0 2px " + css("--sd-gold-pale", "#FFE566");
    el.appendChild(ring);
    A({ targets: ring, opacity: [1, 0], duration: 460, easing: "easeOutQuad", complete: function () { ring.remove(); } });
  });

  /* ---------- Generate button: speed-line burst from the click point ---------- */
  var burst = safe(function (el, evt) {
    if (!A || !el || reduced()) return;
    var r = el.getBoundingClientRect();
    var cx = evt && evt.clientX ? evt.clientX : r.left + r.width / 2;
    var cy = evt && evt.clientY ? evt.clientY : r.top + r.height / 2;
    var lines = [], N = 14;
    for (var i = 0; i < N; i++) {
      var s = document.createElement("span");
      s.className = "fx-speedline";
      s.setAttribute("aria-hidden", "true");
      var angle = (360 / N) * i + (Math.random() * 14 - 7);
      s.dataset.angle = angle;
      s.style.transform = "translate(" + cx + "px," + cy + "px) rotate(" + angle + "deg) translateX(8px) scaleX(0.2)";
      document.body.appendChild(s);
      lines.push(s);
    }
    A({
      targets: lines,
      duration: 420,
      easing: "easeOutExpo",
      opacity: [1, 0],
      delay: A.stagger(8),
      update: function (anim) {
        var p = anim.progress / 100;
        for (var j = 0; j < lines.length; j++) {
          var dist = 8 + p * (60 + (j % 3) * 18);
          lines[j].style.transform = "translate(" + cx + "px," + cy + "px) rotate(" + lines[j].dataset.angle + "deg) translateX(" + dist + "px) scaleX(" + (0.2 + p * 0.8) + ")";
        }
      },
      complete: cleanup
    });
    // belt and braces: anime.js pauses in hidden tabs, so never leave lines behind
    setTimeout(cleanup, 900);
    function cleanup() { for (var k = 0; k < lines.length; k++) lines[k].remove(); }
  });

  /* ---------- (e) scan sweep across a shot card while it renders ---------- */
  var scan = safe(function (el, on) {
    if (!el) return;
    if (!on) {
      el.classList.remove("fx-scanning");
      var old = el.querySelector(":scope > .fx-sweep");
      if (old) { if (A) A.remove(old); old.remove(); }
      return;
    }
    el.classList.add("fx-scanning"); // CSS progress bar; works without anime.js
    if (!A || reduced() || el.querySelector(":scope > .fx-sweep")) return;
    var band = document.createElement("div");
    band.className = "fx-sweep";
    band.setAttribute("aria-hidden", "true");
    el.appendChild(band);
    A({ targets: band, translateX: ["-100%", "250%"], duration: 1600, easing: "easeInOutSine", loop: true });
  });

  /* ---------- (d) queue slot slide-in when a shot lands ---------- */
  var land = safe(function (el) {
    if (!A || !el || reduced()) return;
    A({ targets: el, opacity: [0, 1], translateX: [-24, 0], scale: [0.96, 1], duration: 560, easing: "easeOutQuint" });
  });

  /* ---------- (f) Anime News ident: 1.2 s station ident on the header mark ----------
     0-800 ms strokes draw (per face, 90 ms apart) · 450-900 ms glow blooms and settles ·
     860 ms one horizontal glitch slice (two clipped copies, 3 px apart) held 120 ms · then static.
     Hover breathing is CSS (theme.css). Reduced motion or no anime.js = the static mark. */
  var IDENT = { draw: 620, faceStagger: 90, bloomAt: 450, bloom: 450, glitchAt: 860, glitch: 120, slicePx: 3 };
  var FACES = ["#an-face-left", "#an-face-top", "#an-face-base"];
  var ident = safe(function () {
    var wrap = document.querySelector(".an-mark-wrap");
    var svg = wrap && wrap.querySelector("svg");
    if (!A || !svg || reduced()) return;
    var word = document.querySelector(".an-lockup .wordmark");
    var draws = svg.querySelectorAll("use.an-draw");
    var fill = svg.querySelector(".an-scanfill");
    for (var i = 0; i < draws.length; i++) { draws[i].style.strokeDasharray = "1 1"; draws[i].style.strokeDashoffset = "1"; }
    if (fill) fill.style.opacity = "0";
    if (word) word.style.opacity = "0";
    var faceOf = function (el) { return Math.max(0, FACES.indexOf(el.getAttribute("href"))); };
    var tl = A.timeline({ easing: "easeOutCubic" });
    tl.add({ targets: draws, strokeDashoffset: [1, 0], duration: IDENT.draw,
             delay: function (el) { return faceOf(el) * IDENT.faceStagger; } }, 0)
      .add({ targets: word, opacity: [0, 1], translateX: [-6, 0], duration: 500 }, 300)
      .add({ targets: fill, opacity: [0, 0.16], duration: 300 }, 500)
      .add({ targets: { k: 0 }, k: 1, duration: IDENT.bloom, easing: "linear",
             update: function (a) {
               var k = Math.sin(a.progress / 100 * Math.PI);          // up, then back down
               svg.style.filter = "drop-shadow(0 0 " + (3 + 10 * k).toFixed(1) + "px rgba(255,229,102," + (0.5 + 0.45 * k).toFixed(2) + "))";
             } }, IDENT.bloomAt)
      .add({ targets: { g: 0 }, g: 1, duration: IDENT.glitch, begin: glitch, complete: unglitch }, IDENT.glitchAt);
    tl.finished.then(settle);
    setTimeout(settle, 2500);   // anime.js pauses in hidden tabs; never leave the mark half drawn
    var copies = [];

    function glitch() {
      if (!svg.isConnected || copies.length) return;
      var cuts = [["inset(0 0 54% 0)", IDENT.slicePx], ["inset(46% 0 0 0)", -IDENT.slicePx]];
      copies = cuts.map(function (c) {
        var s = svg.cloneNode(true);
        s.classList.add("an-glitch-slice");
        s.setAttribute("aria-hidden", "true");
        s.style.clipPath = c[0];
        s.style.transform = "translateX(" + c[1] + "px)";
        wrap.appendChild(s);
        return s;
      });
      svg.style.visibility = "hidden";
    }
    function unglitch() {
      copies.forEach(function (s) { s.remove(); });
      copies = [];
      svg.style.visibility = "";
    }
    function settle() {
      unglitch();
      for (var j = 0; j < draws.length; j++) { draws[j].style.strokeDasharray = ""; draws[j].style.strokeDashoffset = ""; }
      if (fill) fill.style.opacity = "";
      if (word) { word.style.opacity = ""; word.style.transform = ""; }
      svg.style.filter = "";
    }
  });

  window.theme = { burst: burst, scan: scan, pulse: pulse, land: land, ident: ident, enabled: !!A && !reduced() };

  function boot() { initParticles(); enter(); ident(); }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot);
  else boot();
})();
