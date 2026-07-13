/*
 * Interlude save/resume bridge — shared by every vendored game.
 *
 * Include it as the FIRST <script> in a game's <head>, before the game's own
 * scripts run:  <script src="/games/_bridge.js"></script>
 *
 * What it does
 *  - Infers the game id from the path (/games/<id>/...).
 *  - BEFORE the game boots, synchronously hydrates localStorage from the
 *    Interlude server (state.arcade[<id>].store) so games that persist to
 *    localStorage (2048, snake, …) resume mid-play with zero extra code.
 *  - Mirrors every localStorage write back to the server (debounced), and on
 *    tab-hide / page-hide / parent "save" message. State survives window
 *    close, server restart, and app relaunch.
 *  - Applies the Interlude light/dark theme passed as ?theme= and via
 *    postMessage from the parent app.
 *  - Exposes window.Interlude for games that keep state outside localStorage.
 *
 * The per-game store is written as a single JSON *string* leaf so the server's
 * deep-merge replaces it wholesale — deleted keys (e.g. after "New Game")
 * really disappear instead of lingering.
 */
(function () {
  "use strict";

  var PREFIX = "/games/";
  var p = location.pathname;
  var GAME = "unknown";
  if (p.indexOf(PREFIX) === 0) {
    GAME = p.slice(PREFIX.length).split("/")[0] || "unknown";
  }
  var params = new URLSearchParams(location.search);
  var THEME = params.get("theme") || "light";

  // --- theme: set an attribute the game's CSS / _theme.css can key off ---
  function applyTheme(t) {
    THEME = t;
    var r = document.documentElement;
    r.setAttribute("data-il-theme", t);
    r.classList.toggle("il-dark", t === "dark");
    r.classList.toggle("il-light", t !== "dark");
  }
  applyTheme(THEME);

  // --- 1) hydrate localStorage from the server (synchronous, pre-boot) ---
  var hydrated = null;
  try {
    var xhr = new XMLHttpRequest();
    xhr.open("GET", "/api/state", false); // sync: must finish before game boots
    xhr.send();
    if (xhr.status === 200) {
      var st = JSON.parse(xhr.responseText || "{}");
      var rec = st.arcade && st.arcade[GAME];
      // Isolate games from one another: wipe, then lay down only this game's keys.
      try { localStorage.clear(); } catch (e) {}
      if (rec && typeof rec.store === "string") {
        try {
          var store = JSON.parse(rec.store);
          hydrated = store;
          Object.keys(store).forEach(function (k) {
            try { localStorage.setItem(k, store[k]); } catch (e) {}
          });
        } catch (e) {}
      }
    }
  } catch (e) {}

  // --- 2) mirror localStorage back to the server ---
  function snapshot() {
    var store = {};
    for (var i = 0; i < localStorage.length; i++) {
      var k = localStorage.key(i);
      if (k && k.indexOf("__il") !== 0) store[k] = localStorage.getItem(k);
    }
    return store;
  }
  function payload(extra) {
    var rec = { store: JSON.stringify(snapshot()), updatedAt: Date.now() };
    if (extra) for (var k in extra) rec[k] = extra[k];
    var body = { arcade: {} };
    body.arcade[GAME] = rec;
    return JSON.stringify(body);
  }
  function save(extra) {
    try {
      var x = new XMLHttpRequest();
      x.open("POST", "/api/state", true);
      x.setRequestHeader("Content-Type", "application/json");
      x.send(payload(extra));
    } catch (e) {}
  }
  function saveBeacon() {
    try {
      if (navigator.sendBeacon) {
        navigator.sendBeacon("/api/state", new Blob([payload()], { type: "application/json" }));
      } else {
        var x = new XMLHttpRequest();
        x.open("POST", "/api/state", false);
        x.setRequestHeader("Content-Type", "application/json");
        x.send(payload());
      }
    } catch (e) {}
  }
  var t = null;
  function saveSoon() { clearTimeout(t); t = setTimeout(function () { save(); }, 300); }

  // patch writes so localStorage-backed games auto-save with no extra code
  try {
    var _set = localStorage.setItem.bind(localStorage);
    localStorage.setItem = function (k, v) { _set(k, v); if (String(k).indexOf("__il") !== 0) saveSoon(); };
    var _rm = localStorage.removeItem.bind(localStorage);
    localStorage.removeItem = function (k) { _rm(k); saveSoon(); };
    var _clear = localStorage.clear.bind(localStorage);
    localStorage.clear = function () { _clear(); saveSoon(); };
  } catch (e) {}

  document.addEventListener("visibilitychange", function () { if (document.hidden) saveBeacon(); });
  window.addEventListener("pagehide", saveBeacon);
  window.addEventListener("beforeunload", saveBeacon);
  window.addEventListener("message", function (e) {
    var d = e && e.data;
    if (!d || typeof d !== "object") return;
    if (d.type === "interlude:save") save();
    else if (d.type === "interlude:theme" && d.theme) applyTheme(d.theme);
  });

  // --- 3) API for games that keep state outside localStorage ---
  window.Interlude = {
    gameId: GAME,
    get theme() { return THEME; },
    isDark: function () { return THEME === "dark"; },
    // hydrated localStorage snapshot captured at boot (or null on first play)
    hydrated: hydrated,
    // read a value stored by save(); returns the parsed snapshot or null
    load: function () {
      try {
        var x = new XMLHttpRequest();
        x.open("GET", "/api/state", false);
        x.send();
        if (x.status !== 200) return null;
        var st = JSON.parse(x.responseText || "{}");
        var rec = st.arcade && st.arcade[GAME];
        return rec && rec.snapshot != null ? rec.snapshot : null;
      } catch (e) { return null; }
    },
    // save an arbitrary JSON-able snapshot (+ optional {best: N})
    save: function (snap, meta) {
      var extra = { snapshot: snap };
      if (meta && meta.best != null) extra.best = meta.best;
      save(extra);
    },
    // record a best score, keeping the max
    setBest: function (n) { save({ best: n }); },
    flush: save,
  };
})();
