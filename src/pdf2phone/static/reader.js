/* pdf2phone reader controls. Everything here is an enhancement: the page
 * reads fine without it (CSS defaults are already large + OpenDyslexic). */
(function () {
  "use strict";

  var root = document.documentElement;
  var doc = document.getElementById("doc");
  var docId = (document.querySelector('meta[name="pdf2phone-doc"]') || {}).content || location.pathname;
  var SETTINGS_KEY = "pdf2phone:settings";
  var POS_KEY = "pdf2phone:pos:" + docId;

  // name: [default, step, min, max, unit, label(value)]
  var NUM = {
    size:    [24, 2, 14, 60, "px", function (v) { return v + "px"; }],
    gutter:  [10, 2, 0, 40, "px", function (v) { return v + "px"; }],
    lh:      [1.5, 0.1, 1.1, 2.6, "", function (v) { return v.toFixed(1); }],
    ws:      [0, 0.05, 0, 0.8, "em", function (v) { return v === 0 ? "Normal" : "+" + Math.round(v * 100) + "%"; }],
    ls:      [0, 0.01, 0, 0.2, "em", function (v) { return v === 0 ? "Normal" : "+" + Math.round(v * 100) + "%"; }],
    measure: [900, 100, 500, 2000, "px", function (v) { return v >= 2000 ? "Full" : v + "px"; }],
  };
  var CHOICE = { font: "dyslexic", theme: "cream" };

  // ---------------------------------------------------------- storage
  function load(key) {
    try { return JSON.parse(localStorage.getItem(key) || "null"); } catch (e) { return null; }
  }
  function save(key, value) {
    try { localStorage.setItem(key, JSON.stringify(value)); } catch (e) { /* private mode etc. */ }
  }

  var settings = {};
  function defaults() {
    var s = {};
    Object.keys(NUM).forEach(function (k) { s[k] = NUM[k][0]; });
    Object.keys(CHOICE).forEach(function (k) { s[k] = CHOICE[k]; });
    return s;
  }
  settings = Object.assign(defaults(), load(SETTINGS_KEY) || {});

  // ---------------------------------------------------------- keep your place
  // Changing text size reflows everything; remember which block was at the
  // top of the screen and put it back there afterwards.
  var blocks = doc ? doc.querySelectorAll("p, h1, h2, h3, h4, h5, h6, li, table, img, pre, blockquote, .math") : [];

  function topBlock() {
    for (var i = 0; i < blocks.length; i++) {
      var r = blocks[i].getBoundingClientRect();
      if (r.bottom > 8) {
        return { i: i, frac: r.height > 0 ? Math.max(0, -r.top) / r.height : 0 };
      }
    }
    return null;
  }
  function restoreBlock(pos) {
    if (!pos || !blocks[pos.i]) return;
    var el = blocks[pos.i];
    var r = el.getBoundingClientRect();
    window.scrollTo(0, window.scrollY + r.top + r.height * (pos.frac || 0));
  }

  // ---------------------------------------------------------- apply
  function apply(keepPlace) {
    var pos = keepPlace ? topBlock() : null;
    Object.keys(NUM).forEach(function (k) {
      var v = settings[k];
      var css = k === "measure" && v >= 2000 ? "none" : v + NUM[k][4];
      root.style.setProperty("--" + k, css);
    });
    root.setAttribute("data-font", settings.font);
    root.setAttribute("data-theme", settings.theme);

    document.querySelectorAll("[data-show]").forEach(function (o) {
      var k = o.getAttribute("data-show");
      o.textContent = NUM[k][5](settings[k]);
    });
    document.querySelectorAll("[data-choice]").forEach(function (group) {
      var k = group.getAttribute("data-choice");
      group.querySelectorAll("button").forEach(function (b) {
        b.setAttribute("aria-pressed", String(b.getAttribute("data-value") === settings[k]));
      });
    });
    if (pos) restoreBlock(pos);
  }

  function round(v) { return Math.round(v * 1000) / 1000; }

  document.addEventListener("click", function (ev) {
    var t = ev.target.closest("button, a");
    if (!t) return;

    var step = t.getAttribute("data-step");
    if (step) {
      var parts = step.split(":"), k = parts[0], dir = Number(parts[1]), spec = NUM[k];
      settings[k] = round(Math.min(spec[3], Math.max(spec[2], settings[k] + dir * spec[1])));
      save(SETTINGS_KEY, settings);
      apply(true);
      return;
    }
    var group = t.closest("[data-choice]");
    if (group && t.hasAttribute("data-value")) {
      settings[group.getAttribute("data-choice")] = t.getAttribute("data-value");
      save(SETTINGS_KEY, settings);
      apply(true);
      return;
    }
    if (t.hasAttribute("data-reset")) {
      settings = defaults();
      save(SETTINGS_KEY, settings);
      apply(true);
      return;
    }
    if (t.hasAttribute("data-open")) { openPanel(t.getAttribute("data-open")); return; }
    if (t.hasAttribute("data-close")) { closePanels(); return; }
    if (t.closest("#toc-panel") && t.tagName === "A") { closePanels(); }
  });

  // ---------------------------------------------------------- panels
  function closePanels() {
    document.querySelectorAll(".panel").forEach(function (p) { p.hidden = true; });
  }
  function openPanel(id) {
    var p = document.getElementById(id);
    if (!p) return;
    var wasOpen = !p.hidden;
    closePanels();
    if (wasOpen) return;
    p.hidden = false;
    if (id === "toc-panel") markCurrentSection(true);
  }
  document.addEventListener("keydown", function (e) { if (e.key === "Escape") closePanels(); });
  // Tap on the text closes an open panel.
  if (doc) doc.addEventListener("click", closePanels);

  // Build the drawer copy of the contents; hide the inline <details> version.
  var tocList = document.querySelector("#toc nav");
  var tocBody = document.getElementById("toc-panel-body");
  if (tocList && tocBody) {
    tocBody.appendChild(tocList.cloneNode(true));
    document.getElementById("toc").hidden = true;
  }
  var headings = doc ? doc.querySelectorAll("h1[id], h2[id], h3[id], h4[id], h5[id], h6[id]") : [];

  function markCurrentSection(scroll) {
    var current = null;
    for (var i = 0; i < headings.length; i++) {
      if (headings[i].getBoundingClientRect().top < 80) current = headings[i].id; else break;
    }
    if (!tocBody) return;
    tocBody.querySelectorAll("a").forEach(function (a) {
      var here = a.getAttribute("href") === "#" + current;
      a.classList.toggle("here", here);
      if (here && scroll) a.scrollIntoView({ block: "center" });
    });
  }

  // ---------------------------------------------------------- progress + position
  var bar = document.querySelector("#progress > div");
  var saveTimer = null;
  function onScroll() {
    var max = document.documentElement.scrollHeight - window.innerHeight;
    if (bar) bar.style.width = (max > 0 ? Math.min(100, (window.scrollY / max) * 100) : 100) + "%";
    clearTimeout(saveTimer);
    saveTimer = setTimeout(function () {
      var pos = topBlock();
      if (pos) save(POS_KEY, { i: pos.i, frac: pos.frac, pct: max > 0 ? window.scrollY / max : 1, t: Date.now() });
    }, 400);
  }
  window.addEventListener("scroll", onScroll, { passive: true });
  window.addEventListener("resize", onScroll);

  // ---------------------------------------------------------- start
  document.getElementById("fab").hidden = false;
  apply(false);
  // Restore where you were, unless the URL points at a section.
  if (!location.hash) {
    var saved = load(POS_KEY);
    if (saved) {
      var go = function () { restoreBlock(saved); onScroll(); };
      if (document.fonts && document.fonts.ready) document.fonts.ready.then(go); else go();
    }
  }
  onScroll();
})();
