/* Access token for the public deploy (SHOT_DIRECTOR_ACCESS_TOKEN on the server).
   Browsing is public; anything that spends Router credits needs the token. The first call that comes
   back 401 opens a small modal, the token is kept in localStorage and sent as X-Access-Token.
   Local dev (no token on the server): the chip stays hidden and nothing here changes behaviour.
   Public API: window.access = { fetch(path, opts), prompt(), get(), headers(h), refresh() } */
(function () {
  "use strict";
  var KEY = "shotDirectorAccessToken";
  var required = false;
  var pending = null;

  function get() { try { return localStorage.getItem(KEY) || ""; } catch (e) { return ""; } }
  function set(t) { try { t ? localStorage.setItem(KEY, t) : localStorage.removeItem(KEY); } catch (e) {} refresh(); }
  function headers(h) {
    var out = Object.assign({}, h || {});
    var t = get();
    if (t) out["X-Access-Token"] = t;
    return out;
  }
  function track(name, props) { try { if (window.posthog && window.posthog.capture) window.posthog.capture(name, props || {}); } catch (e) {} }

  function refresh() {
    var chip = document.getElementById("access");
    if (!chip) return;
    chip.hidden = !required;
    var open = !!get();
    chip.textContent = open ? "Access: unlocked" : "Access: locked";
    chip.className = "badge access " + (open ? "live" : "mock");
    chip.title = open ? "Token saved in this browser. Click to change or forget it." : "Browsing is open. Rendering needs the access token. Click to enter it.";
  }

  function modal() {
    var box = document.getElementById("access-modal");
    if (box) return box;
    box = document.createElement("div");
    box.id = "access-modal";
    box.hidden = true;
    box.setAttribute("role", "dialog");
    box.setAttribute("aria-modal", "true");
    box.setAttribute("aria-labelledby", "access-title");
    box.innerHTML =
      '<form class="access-card">' +
      '<h2 id="access-title">Access token</h2>' +
      '<p>Watching episodes is open to everyone. Rendering spends shared Comfy Router credits, so it needs the access token.</p>' +
      '<input type="password" id="access-input" class="no-record" autocomplete="off" spellcheck="false" placeholder="paste the token">' +
      '<div class="row"><button type="submit" class="primary" style="margin-top:0">Unlock</button>' +
      '<button type="button" class="ghost" data-act="forget">Forget</button>' +
      '<button type="button" class="ghost" data-act="cancel">Cancel</button></div>' +
      '</form>';
    document.body.appendChild(box);
    return box;
  }

  function prompt() {
    if (pending) return pending;
    var box = modal(), form = box.querySelector("form"), input = box.querySelector("#access-input");
    pending = new Promise(function (resolve) {
      function done(ok) {
        box.hidden = true;
        form.onsubmit = null;
        box.onclick = null;
        document.removeEventListener("keydown", onKey);
        pending = null;
        resolve(ok);
      }
      function onKey(e) { if (e.key === "Escape") done(false); }
      form.onsubmit = function (e) {
        e.preventDefault();
        var t = input.value.trim();
        if (!t) return;
        set(t);
        done(true);
      };
      box.onclick = function (e) {
        var act = e.target && e.target.getAttribute && e.target.getAttribute("data-act");
        if (act === "forget") { set(""); done(false); }
        else if (act === "cancel" || e.target === box) done(false);
      };
      document.addEventListener("keydown", onKey);
      input.value = "";
      box.hidden = false;
      input.focus();
    });
    return pending;
  }

  /* fetch with the token; on 401, ask once and retry once */
  function accessFetch(path, opts) {
    opts = opts || {};
    var send = function () { return fetch(path, Object.assign({}, opts, { headers: headers(opts.headers) })); };
    return send().then(function (r) {
      if (r.status !== 401) return r;
      required = true;
      set("");
      return prompt().then(function (ok) {
        if (!ok) return r;
        return send().then(function (r2) {
          if (r2.status === 401) set("");
          else track("token_unlocked", { path: path });
          return r2;
        });
      });
    });
  }

  window.access = { fetch: accessFetch, prompt: prompt, get: get, headers: headers, refresh: refresh, track: track };

  document.addEventListener("DOMContentLoaded", function () {
    var chip = document.getElementById("access");
    if (chip) chip.onclick = function () { prompt(); };
    fetch("/api/presets").then(function (r) { return r.json(); }).then(function (p) {
      required = !!p.access_required;
      refresh();
    }).catch(function () {});
  });
})();
