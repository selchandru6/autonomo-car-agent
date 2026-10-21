// App shell: theme, sidebar states, drawer, run status polling, and the search palette.
(function () {
  var root = document.documentElement, app = document.getElementById("app");
  var reduced = window.matchMedia("(prefers-reduced-motion: reduce)");

  // ---------------------------------------------------------------- theme
  var themeBtn = document.getElementById("themeBtn"), themeSystem = document.getElementById("themeSystem");
  var dark = window.matchMedia("(prefers-color-scheme: dark)");
  function darkNow() { return root.getAttribute("data-theme") === "dark" || (!root.hasAttribute("data-theme") && dark.matches); }
  function paintTheme() { themeBtn.setAttribute("aria-label", darkNow() ? "Switch to light mode" : "Switch to dark mode"); }
  function themeChanged() {
    root.classList.add("theme-switching");
    setTimeout(function () { root.classList.remove("theme-switching"); }, 450);
    paintTheme();
    window.dispatchEvent(new Event("x5finder-theme"));
  }
  themeBtn.addEventListener("click", function () {
    var next = darkNow() ? "light" : "dark";
    root.setAttribute("data-theme", next);
    try { localStorage.setItem("x5finder.theme", next); } catch (e) {}
    var icon = themeBtn.querySelector(next === "dark" ? ".ico-sun" : ".ico-moon");
    if (icon && icon.animate && !reduced.matches)
      icon.animate([{ transform: "rotate(-90deg) scale(.6)", opacity: 0 }, { transform: "none", opacity: 1 }], { duration: 500, easing: "cubic-bezier(0.34, 1.56, 0.64, 1)" });
    themeChanged();
  });
  themeSystem.addEventListener("click", function () {
    root.removeAttribute("data-theme");
    try { localStorage.removeItem("x5finder.theme"); } catch (e) {}
    themeChanged();
  });
  dark.addEventListener("change", function () { if (!root.hasAttribute("data-theme")) themeChanged(); });
  paintTheme();

  // ---------------------------------------------------------------- sidebar
  var collapseBtn = document.getElementById("collapseBtn");
  function savedSide() { try { return localStorage.getItem("x5finder.sidebar"); } catch (e) { return null; } }
  function paintCollapse() {
    var on = root.classList.contains("sb-collapsed");
    collapseBtn.setAttribute("aria-label", on ? "Expand menu" : "Collapse menu");
    collapseBtn.setAttribute("aria-expanded", on ? "false" : "true");
  }
  collapseBtn.addEventListener("click", function () {
    var on = !root.classList.contains("sb-collapsed");
    root.classList.toggle("sb-collapsed", on);
    try { localStorage.setItem("x5finder.sidebar", on ? "collapsed" : "expanded"); } catch (e) {}
    paintCollapse();
  });
  window.addEventListener("resize", function () {
    if (!savedSide()) { root.classList.toggle("sb-collapsed", window.x5AutoCollapsed()); paintCollapse(); }
  });
  paintCollapse();

  var menuBtn = document.getElementById("menuBtn"), scrim = document.getElementById("scrim"), sidebar = document.getElementById("sidebar");
  function drawer(open) {
    app.classList.toggle("drawer", open);
    menuBtn.setAttribute("aria-expanded", open ? "true" : "false");
    if (open) { var first = sidebar.querySelector("a"); if (first) first.focus(); } else menuBtn.focus();
  }
  menuBtn.addEventListener("click", function () { drawer(!app.classList.contains("drawer")); });
  scrim.addEventListener("click", function () { drawer(false); });
  sidebar.addEventListener("click", function (e) { if (e.target.closest("a") && app.classList.contains("drawer")) app.classList.remove("drawer"); });

  // ---------------------------------------------------------------- run status
  var card = document.getElementById("runcard"), liveNote = document.getElementById("liveNote");
  var running = card.dataset.running === "1";
  function field(name) { return card.querySelector('[data-s="' + name + '"]'); }
  function apply(st) {
    var was = running;
    running = !!st.running;
    var tone = running ? "running" : (st.run ? st.run.status.toLowerCase() : "queued");
    if (st.run && field("date")) {
      field("date").textContent = st.run.date;
      field("trigger").textContent = st.run.trigger.replace(/_/g, " ");
      field("asof").textContent = st.run.as_of;
      var chip = field("chip");
      chip.className = "chip c-" + tone;
      chip.innerHTML = (running ? '<span class="pulse-dot"></span>' : "") + st.run.status;
    }
    field("cov").textContent = st.coverage.reported + " / " + st.coverage.active;
    field("nosrc").textContent = st.coverage.no_source;
    field("model").textContent = st.model;
    field("watch").hidden = !running;
    card.dataset.running = running ? "1" : "0";
    document.querySelectorAll("[data-live]").forEach(function (el) { el.hidden = !running; });
    if (running !== was) liveNote.textContent = running ? "A run started. Watch it on Live pipeline." : "The run finished: " + (st.run ? st.run.status : "");
  }
  function poll() {
    fetch("/api/status", { headers: { Accept: "application/json" } })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (st) { if (st) apply(st); })
      .catch(function () {})
      .finally(function () { setTimeout(poll, running ? 5000 : 30000); });
  }
  setTimeout(poll, running ? 5000 : 30000);

  // ---------------------------------------------------------------- search palette
  var pal = document.getElementById("palette"), palQ = document.getElementById("palQ"), palRes = document.getElementById("palRes");
  var palAll = document.getElementById("palAll"), searchForm = document.getElementById("searchForm"), searchInput = document.getElementById("searchInput");
  var mac = /Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent);
  document.getElementById("kbdHint").textContent = mac ? "⌘K" : "Ctrl K";
  var GROUPS = [["pages", "Pages"], ["cars", "Cars"], ["dealers", "Dealers"], ["runs", "Runs"]];
  var items = [], active = -1, lastFocus = null, timer = null, ctrl = null;
  var esc = function (s) { return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]; }); };

  function recent() { try { return JSON.parse(localStorage.getItem("x5finder.recent")) || []; } catch (e) { return []; } }
  function remember(it) {
    try {
      var list = recent().filter(function (r) { return r.href !== it.href; });
      list.unshift({ label: it.label, sublabel: it.sublabel, href: it.href });
      localStorage.setItem("x5finder.recent", JSON.stringify(list.slice(0, 5)));
    } catch (e) {}
  }
  function draw(groups, q) {
    items = [];
    var html = "";
    groups.forEach(function (g) {
      if (!g[1].length) return;
      html += '<div class="gh" role="presentation">' + esc(g[0]) + "</div>";
      g[1].forEach(function (it) {
        var i = items.push(it) - 1;
        html += '<a class="it" role="option" id="pal-' + i + '" href="' + esc(it.href) + '" data-i="' + i + '" aria-selected="false">' +
          '<span class="pl">' + esc(it.label) + "</span><small>" + esc(it.sublabel || "") + "</small>" +
          (it.alt ? '<span class="alt" data-alt="' + esc(it.alt.href) + '">' + esc(it.alt.label) + "</span>" : "") + "</a>";
      });
    });
    if (!items.length) html = '<div class="empty">' + (q.length < 2 ? "Type at least two characters, or a full VIN." : "Nothing matches “" + esc(q) + "”.") + "</div>";
    palRes.innerHTML = html;
    palAll.href = "/search?q=" + encodeURIComponent(q);
    move(items.length ? 0 : -1);
  }
  function move(i) {
    var prev = palRes.querySelector('[aria-selected="true"]');
    if (prev) prev.setAttribute("aria-selected", "false");
    active = i;
    if (i < 0) { palQ.removeAttribute("aria-activedescendant"); return; }
    var el = document.getElementById("pal-" + i);
    el.setAttribute("aria-selected", "true");
    palQ.setAttribute("aria-activedescendant", el.id);
    el.scrollIntoView({ block: "nearest" });
  }
  function query() {
    var q = palQ.value.trim();
    if (ctrl) ctrl.abort();
    if (!q) {
      fetch("/api/search?q=").then(function (r) { return r.json(); }).then(function (res) {
        draw([["Recent", recent()], ["Pages", res.pages]], q);
      }).catch(function () {});
      return;
    }
    ctrl = window.AbortController ? new AbortController() : null;
    fetch("/api/search?limit=6&q=" + encodeURIComponent(q), ctrl ? { signal: ctrl.signal } : {})
      .then(function (r) { return r.json(); })
      .then(function (res) { if (palQ.value.trim() === q) draw(GROUPS.map(function (g) { return [g[1], res[g[0]] || []]; }), q); })
      .catch(function () {});
  }
  function open(seed) {
    if (!pal.hidden) return;
    lastFocus = document.activeElement;
    pal.hidden = false;
    palQ.value = seed || "";
    palQ.focus();
    query();
  }
  function close() {
    pal.hidden = true;
    if (lastFocus && lastFocus !== searchInput && lastFocus !== document.body && lastFocus.focus) lastFocus.focus();
    else document.getElementById("searchBtn").focus();
  }
  function go(i, newTab) {
    var it = items[i]; if (!it) return;
    remember(it);
    if (newTab) window.open(it.href, "_blank", "noopener"); else location.href = it.href;
  }
  palQ.addEventListener("input", function () { clearTimeout(timer); timer = setTimeout(query, 150); });
  palQ.addEventListener("keydown", function (e) {
    if (e.key === "ArrowDown") { e.preventDefault(); if (items.length) move((active + 1) % items.length); }
    else if (e.key === "ArrowUp") { e.preventDefault(); if (items.length) move((active - 1 + items.length) % items.length); }
    else if (e.key === "Enter") { e.preventDefault(); if (active >= 0) go(active, e.metaKey || e.ctrlKey); else location.href = palAll.href; }
  });
  palRes.addEventListener("click", function (e) {
    var alt = e.target.closest("[data-alt]");
    if (alt) { e.preventDefault(); location.href = alt.dataset.alt; return; }
    var a = e.target.closest("a.it"); if (!a) return;
    e.preventDefault();
    go(+a.dataset.i, e.metaKey || e.ctrlKey);
  });
  palRes.addEventListener("mousemove", function (e) { var a = e.target.closest("a.it"); if (a && +a.dataset.i !== active) move(+a.dataset.i); });
  pal.addEventListener("mousedown", function (e) { if (e.target === pal) close(); });
  pal.addEventListener("keydown", function (e) {
    if (e.key === "Escape") { e.preventDefault(); close(); return; }
    if (e.key !== "Tab") return;
    var f = pal.querySelectorAll("input, a[href]");
    var first = f[0], last = f[f.length - 1];
    if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
    else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
  });
  searchForm.addEventListener("submit", function (e) { e.preventDefault(); open(searchInput.value); });
  searchInput.addEventListener("focus", function () { var v = searchInput.value; searchInput.blur(); open(v); });
  document.getElementById("searchBtn").addEventListener("click", function (e) { e.preventDefault(); open(searchInput.value); });

  document.addEventListener("keydown", function (e) {
    var t = e.target, typing = t && (t.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName));
    if ((e.key === "k" || e.key === "K") && (e.metaKey || e.ctrlKey)) { e.preventDefault(); pal.hidden ? open("") : close(); }
    else if (e.key === "/" && !typing && pal.hidden) { e.preventDefault(); open(""); }
    else if (e.key === "Escape" && app.classList.contains("drawer")) drawer(false);
  });
})();
