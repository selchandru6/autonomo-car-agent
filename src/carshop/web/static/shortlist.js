// ☆ Save / ★ Saved (requirements-shortlist.md §4.1). One click saves; a second click asks
// "Remove from shortlist?" in place, and a third removes. Every [data-save] button on the page
// for the same VIN changes together.
(function () {
  const S = JSON.parse(document.getElementById("shortlistState").textContent);
  const saved = new Set(S.saved);
  const esc = s => String(s ?? "").replace(/[&<>"']/g, ch => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]));
  const token = () => (document.querySelector('meta[name="csrf-token"]') || {}).content || "";
  let toastTimer = null;

  function toast(msg) {
    const el = document.getElementById("slToast");
    if (!el) return;
    el.textContent = msg; el.hidden = false;
    clearTimeout(toastTimer); toastTimer = setTimeout(() => { el.hidden = true; }, 5000);
  }
  async function call(method, url, body) {
    const res = await fetch(url, {
      method, credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-CSRF-Token": token() },
      body: body ? JSON.stringify(body) : undefined,
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || data.detail || "Could not save. Try again.");
    return data;
  }
  function paint(btn, state) {
    btn.dataset.state = state;
    btn.classList.toggle("on", state !== "off");
    btn.classList.toggle("confirm", state === "confirm");
    btn.setAttribute("aria-pressed", state === "off" ? "false" : "true");
    btn.textContent = state === "off" ? "☆ Save" : state === "confirm" ? "Remove from shortlist?" : "★ Saved";
  }
  function sync(vin) {
    const st = saved.has(vin) ? "on" : "off";
    document.querySelectorAll('[data-save="' + vin + '"]').forEach(b => paint(b, st));
    document.dispatchEvent(new CustomEvent("shortlist-change", { detail: { vin, saved: saved.has(vin) } }));
  }
  async function click(btn) {
    const vin = btn.dataset.save;
    if (btn.disabled || btn.dataset.busy) return;
    if (!saved.has(vin)) {
      btn.dataset.busy = "1";
      try { await call("POST", "/api/shortlist/cars", { vin }); saved.add(vin); sync(vin); toast("Saved to your shortlist."); }
      catch (e) { toast(e.message); }
      finally { delete btn.dataset.busy; }
    } else if (btn.dataset.state !== "confirm") {
      paint(btn, "confirm");
      setTimeout(() => { if (btn.dataset.state === "confirm") paint(btn, "on"); }, 4000);
    } else {
      btn.dataset.busy = "1";
      try { await call("DELETE", "/api/shortlist/cars/" + encodeURIComponent(vin)); saved.delete(vin); sync(vin); toast("Removed from your shortlist."); }
      catch (e) { toast(e.message); paint(btn, "on"); }
      finally { delete btn.dataset.busy; }
    }
  }
  // Capture phase, so a click on the control never also opens the row it sits in.
  document.addEventListener("click", e => {
    const btn = e.target.closest && e.target.closest("[data-save]");
    if (!btn) return;
    e.preventDefault(); e.stopPropagation();
    click(btn);
  }, true);
  document.addEventListener("keydown", e => {
    if (e.key !== "Escape") return;
    document.querySelectorAll('[data-save][data-state="confirm"]').forEach(b => paint(b, "on"));
  });
  document.addEventListener("submit", async e => {
    const form = e.target.closest && e.target.closest("form[data-note]");
    if (!form) return;
    e.preventDefault();
    const out = form.querySelector("[data-note-status]");
    try {
      const r = await call("PATCH", "/api/shortlist/cars/" + encodeURIComponent(form.dataset.note), { note: form.note.value });
      if (out) out.textContent = r.note ? "Note saved." : "Note cleared.";
    } catch (err) { if (out) out.textContent = err.message; }
  });

  // Saved-search controls: pause, resume, digest alerts, and delete (asks once in place).
  const ACTS = {
    pause: { paused: true }, resume: { paused: false },
    "alerts-on": { alerts: true }, "alerts-off": { alerts: false },
  };
  document.addEventListener("click", async e => {
    const btn = e.target.closest && e.target.closest("[data-search-act]");
    if (!btn || btn.disabled) return;
    const id = btn.dataset.searchId, act = btn.dataset.searchAct;
    if (act === "delete" && !btn.dataset.confirm) {
      btn.dataset.confirm = "1"; btn.textContent = "Delete this search?";
      setTimeout(() => { delete btn.dataset.confirm; btn.textContent = "Delete"; }, 4000);
      return;
    }
    btn.disabled = true;
    try {
      if (act === "delete") await call("DELETE", "/api/shortlist/searches/" + id);
      else await call("PATCH", "/api/shortlist/searches/" + id, ACTS[act]);
      if (btn.dataset.after) location.href = btn.dataset.after; else location.reload();
    } catch (err) { toast(err.message); btn.disabled = false; }
  });

  window.Shortlist = {
    writes: !!S.writes,
    call,
    toast,
    has: vin => saved.has(vin),
    button(vin) {
      if (!S.writes) return "";
      const on = saved.has(vin);
      return '<button type="button" class="save-btn' + (on ? " on" : "") + '" data-save="' + esc(vin) + '" aria-pressed="' + on + '">' + (on ? "★ Saved" : "☆ Save") + "</button>";
    },
  };
})();
