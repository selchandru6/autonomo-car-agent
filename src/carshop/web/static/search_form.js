// Saved-search form (requirements-shortlist.md §5.2): the preview is a dry run of the same
// matcher the daily run uses, so its counts equal what saving writes.
(function () {
  const form = document.querySelector("[data-search-form]");
  if (!form) return;
  const base = JSON.parse(document.getElementById("searchBase").textContent);
  const FEATS = ["DAP", "M_SPORT", "MASSAGE", "PANORAMIC_ROOF", "SKY_LOUNGE", "EXECUTIVE", "CLIMATE_COMFORT", "AIR_SUSPENSION"];
  const esc = s => String(s ?? "").replace(/[&<>"']/g, ch => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]));
  const usd = c => c == null ? "" : "$" + Math.round(c / 100).toLocaleString("en-US");
  const status = form.querySelector("[data-sf-status]");
  const pv = k => form.querySelector('[data-pv="' + k + '"]');
  const checked = name => [...form.querySelectorAll('input[name="' + name + '"]:checked')].map(i => i.value);
  const KIND = { met: "c-yes", unk: "c-partial", diff: "c-info" };

  function criteria() {
    const f = form.elements;
    const features = {};
    for (const k of FEATS) features[k] = (form.querySelector('input[name="feat-' + k + '"]:checked') || {}).value || "ignore";
    const max = f.max_price.value.trim();
    const city = f.city.value || null;
    return {
      version: 1, base, features,
      exterior: { families: checked("exterior-family"), names: checked("exterior-name") },
      interior: { families: checked("interior-family"), names: checked("interior-name") },
      colors_only: f.colors_only.checked,
      max_price_cents: max === "" ? null : Math.round(+max) * 100,
      band_cents: f.band.disabled || !f.band.value ? null : +f.band.value * 100,
      no_price: (form.querySelector('input[name="no_price"]:checked') || {}).value || "include",
      location: { city, radius_mi: city && f.radius.value ? +f.radius.value : null },
      in_transit: f.in_transit.checked ? "include" : "exclude",
      sort: "match",
    };
  }
  function row(r) {
    const tier = r.tier === "MATCH" ? '<span class="tier-match">Match</span>' : '<span class="tier-possible">Possible match</span>';
    return '<li><div class="flex justify-between gap-2"><a class="font-mono text-xs link" href="/vin/' + esc(r.vin) + '">' + esc(r.vin) + '</a>' + tier + '</div>' +
      '<div class="text-xs muted">' + esc(r.dealer_name) + ' · ' + esc(r.ext_name) + ' / ' + esc(r.int_name) + ' · ' + (r.price == null ? "no cash price" : esc(usd(r.price))) + '</div>' +
      '<div class="reasons mt-1">' + r.reasons.map(x => '<span class="chip ' + KIND[x.k] + '">' + esc(x.t) + '</span>').join("") + '</div></li>';
  }
  let timer = null, seq = 0;
  async function preview() {
    const mine = ++seq;
    try {
      const r = await window.Shortlist.call("POST", "/api/shortlist/searches/preview", { criteria: criteria() });
      if (mine !== seq) return;
      pv("match").textContent = r.match; pv("possible").textContent = r.possible;
      pv("top").innerHTML = r.top.length ? r.top.map(row).join("") : '<li class="muted">No cars in the latest data fit these limits.</li>';
      status.textContent = "";
    } catch (e) { if (mine === seq) status.textContent = e.message; }
  }
  const later = () => { clearTimeout(timer); timer = setTimeout(preview, 250); };
  form.addEventListener("change", later);
  form.addEventListener("input", later);
  form.addEventListener("submit", async e => {
    e.preventDefault();
    const id = form.dataset.searchId;
    const body = { name: form.elements.name.value, criteria: criteria(), alerts: form.elements.alerts.checked };
    const btn = form.querySelector('button[type="submit"]');
    if (btn) btn.disabled = true;
    status.textContent = "Saving…";
    try {
      const r = await window.Shortlist.call(id ? "PATCH" : "POST", "/api/shortlist/searches" + (id ? "/" + id : ""), body);
      location.href = r.url;
    } catch (err) {
      status.textContent = err.message;
      if (btn) btn.disabled = false;
    }
  });
  if (window.Shortlist && window.Shortlist.writes) preview();
  else status.textContent = "The preview needs the app running on this computer.";
})();
