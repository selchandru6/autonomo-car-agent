// Find a car (buyer-ui-spec.md). Wish-list filtering runs in the browser over the page payload.
(function () {
const D = JSON.parse(document.getElementById("finderData").textContent);
const FEATS = [
  { k: "DAP", label: "Driving Assistance Professional", short: "Driving Assist Pro" },
  { k: "PANORAMIC_ROOF", label: "Panoramic roof", short: "Panoramic roof" },
  { k: "SKY_LOUNGE", label: "Sky Lounge LED roof", short: "Sky Lounge" },
  { k: "MASSAGE", label: "Massage seats", short: "Massage seats" },
  { k: "M_SPORT", label: "M Sport package", short: "M Sport" },
  { k: "EXECUTIVE", label: "Executive package", short: "Executive" },
  { k: "CLIMATE_COMFORT", label: "Climate Comfort package", short: "Climate Comfort" },
  { k: "AIR_SUSPENSION", label: "Air suspension", short: "Air suspension" },
];
// Paint swatches are car colors, not theme colors.
const SW = {
  "Alpine White": "#f1f1ec", "Mineral White": "#e3e3dd", "Black Sapphire": "#1a1c22", "Carbon Black": "#1c2634",
  "Skyscraper Grey": "#8b9198", "Brooklyn Grey": "#9c9e99", "Dravit Grey": "#696d6c", "Tanzanite Blue II": "#22305f",
  "Manhattan Green": "#3c4b3d", "Grigio Telesto": "#8e9195", "Frozen Pure Grey": "#b9bcbd", "Blue Ridge Mountain": "#3e5570",
  "Black": "#1b1b1b", "Cognac": "#9a5a2e", "Coffee": "#4a3226", "Silverstone": "#cbc5b8", "Tartufo": "#6b4f3a", "Ivory White": "#ece6d7",
};
const DEFAULT_WISH = {
  budget: null, near: "any", radius: null, transit: true, colorsOnly: false, sort: "match",
  ext: ["Skyscraper Grey", "Brooklyn Grey", "Dravit Grey", "Frozen Pure Grey"],
  int: ["Cognac", "Coffee", "Tartufo", "Ivory White", "Silverstone"],
  feats: { DAP: "must", PANORAMIC_ROOF: "nice", MASSAGE: "nice" },
};
const KEY = "x5finder.wish.v1";
const dealerById = Object.fromEntries(D.dealers.map(d => [d.id, d]));
const usd = c => c == null ? "" : "$" + Math.round(c / 100).toLocaleString("en-US");
const fmtDay = iso => iso ? new Date(iso + "T12:00:00").toLocaleDateString("en-US", { month: "short", day: "numeric" }) : "";
const esc = s => String(s ?? "").replace(/[&<>"']/g, ch => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]));
const $ = id => document.getElementById(id);
const cities = {};
for (const d of D.dealers) if (d.lat != null) (cities[d.city] ||= []).push([d.lat, d.lon]);
const CITY = Object.fromEntries(Object.entries(cities).map(([c, pts]) =>
  [c, [pts.reduce((a, p) => a + p[0], 0) / pts.length, pts.reduce((a, p) => a + p[1], 0) / pts.length]]));
function miles(a, b) {
  const R = 3958.8, r = x => x * Math.PI / 180;
  const dLat = r(b[0] - a[0]), dLon = r(b[1] - a[1]);
  const h = Math.sin(dLat / 2) ** 2 + Math.cos(r(a[0])) * Math.cos(r(b[0])) * Math.sin(dLon / 2) ** 2;
  return 2 * R * Math.asin(Math.sqrt(h));
}
let wish = load();
let compare = [];
let showAll = { match: 6, maybe: 6 };
function load() {
  try { const s = JSON.parse(localStorage.getItem(KEY)); if (s && s.feats) return { ...structuredClone(DEFAULT_WISH), ...s }; } catch (e) {}
  return structuredClone(DEFAULT_WISH);
}
function save() { try { localStorage.setItem(KEY, JSON.stringify(wish)); } catch (e) {} }
function distanceTo(car) {
  if (wish.near === "any") return null;
  const d = dealerById[car.dealer];
  return d && d.lat != null ? miles(CITY[wish.near], [d.lat, d.lon]) : null;
}
function judge(car) {
  const out = [], wishes = [];
  const dist = distanceTo(car);
  if (wish.budget != null) {
    if (car.price == null) wishes.push({ label: "Within budget", s: "unk" });
    else if (car.price <= wish.budget * 100) wishes.push({ label: "Within budget", s: "yes" });
    else out.push("over budget");
  }
  if (wish.radius != null && dist != null && dist > wish.radius) out.push("too far");
  if (!wish.transit && car.inTransit) out.push("still in transit");
  if (wish.ext.length) {
    const ok = wish.ext.includes(car.ext);
    if (!ok && wish.colorsOnly) out.push("exterior color"); else wishes.push({ label: car.ext + " exterior", s: ok ? "yes" : "no" });
  }
  if (wish.int.length) {
    const ok = wish.int.includes(car.int);
    if (!ok && wish.colorsOnly) out.push("interior color"); else wishes.push({ label: car.int + " interior", s: ok ? "yes" : "no" });
  }
  const mustMissing = [];
  for (const f of FEATS) {
    const w = wish.feats[f.k]; if (!w) continue;
    const has = car.feats[f.k] === "yes";
    wishes.push({ label: f.short, s: has ? "yes" : "unk", must: w === "must" });
    if (w === "must" && !has) mustMissing.push(f.short);
  }
  const met = wishes.filter(w => w.s === "yes").length;
  const group = out.length ? "out" : mustMissing.length ? "maybe" : "match";
  return { group, out, wishes, met, total: wishes.length, mustMissing, dist };
}
function rank(list) {
  const by = {
    match: (a, b) => (b.j.met - a.j.met) || ((a.c.price ?? 1e12) - (b.c.price ?? 1e12)),
    price: (a, b) => ((a.c.price ?? 1e12) - (b.c.price ?? 1e12)) || (b.j.met - a.j.met),
    near: (a, b) => ((a.j.dist ?? 1e9) - (b.j.dist ?? 1e9)) || ((a.c.price ?? 1e12) - (b.c.price ?? 1e12)),
  }[wish.sort];
  return list.sort(by);
}
function median(xs) { const s = [...xs].sort((a, b) => a - b), m = s.length >> 1; return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2; }
function quart(xs, q) { const s = [...xs].sort((a, b) => a - b); const i = (s.length - 1) * q, lo = Math.floor(i); return s[lo] + (s[Math.ceil(i)] - s[lo]) * (i - lo); }
function render() {
  const all = D.cars.map(c => ({ c, j: judge(c) }));
  const match = rank(all.filter(x => x.j.group === "match"));
  const maybe = rank(all.filter(x => x.j.group === "maybe"));
  const out = all.filter(x => x.j.group === "out");
  const priced = D.cars.filter(c => c.price != null).map(c => c.price);
  $("aMatch").textContent = match.length;
  $("aMatchLbl").textContent = "cars fit your wish list" + (maybe.length ? " · " + maybe.length + " more might" : "");
  $("flowChoose").textContent = match.length + " cars fit";
  $("flowChooseNote").textContent = maybe.length ? maybe.length + " more might" : "from this check";
  const mp = match.filter(x => x.c.price != null).map(x => x.c.price);
  const lowSet = mp.length ? mp : priced;
  $("aLow").textContent = lowSet.length ? usd(Math.min(...lowSet)) : "—";
  $("aLowLbl").textContent = mp.length ? "lowest price among your matches" : "lowest price in Texas";
  $("aTypical").textContent = priced.length ? usd(median(priced)) : "—";
  $("aTypicalLbl").textContent = priced.length ? "typical price · most sell for " + usd(quart(priced, .25)) + "–" + usd(quart(priced, .75)) : "no cash prices yet";
  const t = trendStats();
  $("aTrend").textContent = !t.both ? "Needs more days" : t.changed ? (t.down >= t.up ? "▼ Going down" : "▲ Going up") : "● Steady";
  $("aTrendLbl").textContent = t.both
    ? (t.changed ? t.changed + " price changes" : "no price changes") + " across " + t.both + " cars since " + fmtDay(t.first)
    : "needs two days of checks";
  renderRange(all);
  drawBand(all);
  renderResults(match, maybe, out);
  renderTrend(t);
  renderTray();
}
function trendStats() {
  let both = 0, up = 0, down = 0, first = D.days[0], changes = [];
  for (const c of D.cars) {
    const h = (c.history || []).filter(p => p[1] != null);
    if (h.length < 2) continue;
    both++;
    const a = h[0][1], b = h[h.length - 1][1];
    if (Math.abs(b - a) >= 100) { b < a ? down++ : up++; changes.push({ c, from: a, to: b, d0: h[0][0], d1: h[h.length - 1][0] }); }
  }
  return { both, up, down, changed: up + down, first, changes };
}
function pickExtreme(cars, high) {
  return cars.reduce((best, c) => {
    if (!best) return c;
    if (c.price !== best.price) return (high ? c.price > best.price : c.price < best.price) ? c : best;
    if ((c.firstSeen || "") !== (best.firstSeen || "")) return (c.firstSeen || "") < (best.firstSeen || "") ? c : best;
    return c.vin < best.vin ? c : best;
  }, null);
}
function wishLine(j) {
  if (j.group === "match") return "Fits your wish list · meets " + j.met + " of " + j.total + " wishes";
  if (j.group === "maybe") return "Might fit: not listed: " + j.mustMissing.join(", ");
  return "Outside your limits: " + j.out.join(", ");
}
function renderRange(all) {
  const el = $("range");
  const priced = all.filter(x => x.c.price != null);
  if (!priced.length) { el.innerHTML = '<div class="empty">No dealer is showing a cash price right now.</div>'; return; }
  const low = pickExtreme(priced.map(x => x.c), false);
  const high = pickExtreme(priced.map(x => x.c), true);
  if (low.vin === high.vin) {
    el.innerHTML = '<h3 class="section-title">Only car with a cash price</h3>' + card(priced.find(x => x.c.vin === low.vin), 0, "range");
    return;
  }
  const lowJ = priced.find(x => x.c.vin === low.vin);
  const highJ = priced.find(x => x.c.vin === high.vin);
  el.innerHTML = '<div class="range"><div><h3 class="section-title">Lowest price in Texas</h3>' + card(lowJ, 0, "range") +
    '</div><div><h3 class="section-title">Highest price in Texas</h3>' + card(highJ, 0, "range") +
    '</div></div><p class="spread"><b class="num">' + usd(high.price - low.price) + ' difference</b> between the lowest and highest price · ' +
    priced.length + ' cars have a cash price.</p>';
}
function drawBand(all) {
  const pts = all.filter(x => x.c.price != null);
  const box = $("band");
  if (!pts.length) { box.innerHTML = ""; return; }
  const lo = Math.floor(Math.min(...pts.map(x => x.c.price)) / 100 / 2500) * 2500;
  const hi = Math.ceil(Math.max(...pts.map(x => x.c.price)) / 100 / 2500) * 2500;
  const span = Math.max(hi - lo, 1);
  const W = 900, padL = 20, padR = 20, top = 16, dotR = 5, bin = 500;
  const x = v => padL + (v - lo) / span * (W - padL - padR);
  const stacks = {};
  const placed = pts.slice().sort((a, b) => (a.j.group === "match") - (b.j.group === "match")).map(p => {
    const b = Math.round(p.c.price / 100 / bin) * bin; const n = (stacks[b] = (stacks[b] || 0) + 1) - 1; return { p, b, n };
  });
  const maxStack = Math.max(...Object.values(stacks));
  const H = top + maxStack * (dotR * 2 + 2) + 34;
  const base = H - 30;
  let s = '<svg viewBox="0 0 ' + W + ' ' + H + '" role="img" aria-label="Price of every car for sale">';
  for (let v = lo; v <= hi; v += 2500) {
    s += '<line x1="' + x(v) + '" x2="' + x(v) + '" y1="' + (top - 6) + '" y2="' + base + '" stroke="var(--line)" stroke-width="1"/>';
    s += '<text x="' + x(v) + '" y="' + (base + 18) + '" text-anchor="middle" font-size="12" font-family="var(--font-data)" fill="var(--muted)">' + esc(usd(v * 100).replace(",000", "k")) + '</text>';
  }
  if (wish.budget != null && wish.budget >= lo && wish.budget <= hi) {
    s += '<line x1="' + x(wish.budget) + '" x2="' + x(wish.budget) + '" y1="' + (top - 10) + '" y2="' + base + '" stroke="var(--gold)" stroke-width="1.5" stroke-dasharray="4 3"/>';
    s += '<text x="' + (x(wish.budget) + 5) + '" y="' + top + '" font-size="12" fill="var(--gold)">Your budget</text>';
  }
  const pop = !matchMedia("(prefers-reduced-motion: reduce)").matches && !drawBand.done;
  placed.forEach((item, i) => {
    const cy = base - dotR - 2 - item.n * (dotR * 2 + 2);
    const fit = item.p.j.group === "match";
    const dealer = dealerById[item.p.c.dealer];
    s += '<circle cx="' + x(item.b) + '" cy="' + cy + '" r="' + dotR + '" style="fill:' + (fit ? "var(--accent)" : "var(--dim)") + ';transition:fill .4s' +
      (pop ? ';transform-box:fill-box;transform-origin:center;animation:dot-pop .55s var(--spring) ' + (i * 7) + 'ms both' : '') + '"><title>' +
      esc(usd(item.p.c.price) + " · " + item.p.c.ext + " / " + item.p.c.int + " · " + (dealer ? dealer.name : "")) + '</title></circle>';
  });
  s += '<style>@keyframes dot-pop{from{transform:scale(0)}}</style></svg>';
  box.innerHTML = s;
  drawBand.done = true;
  $("legBudget").innerHTML = wish.budget != null ? '<i style="background:transparent;border:1.5px dashed var(--gold)"></i>Your budget ' + esc(usd(wish.budget * 100)) : "";
}
function featTags(c) {
  const wanted = new Set(FEATS.filter(f => wish.feats[f.k]).map(f => f.k));
  return FEATS.filter(f => c.feats[f.k] === "yes" || wanted.has(f.k)).map(f =>
    c.feats[f.k] === "yes"
      ? '<span class="feat yes">✓ ' + esc(f.short) + '</span>'
      : '<span class="feat unk" title="The dealer\'s page doesn\'t mention it. Ask the dealer.">? ' + esc(f.short) + '</span>').join("");
}
function card(x, i, group) {
  const c = x.c, j = x.j, d = dealerById[c.dealer] || { name: "Dealer", city: "" };
  const priceHtml = c.price != null
    ? '<div class="price num">' + esc(usd(c.price)) + '</div><div class="vsmsrp">' + (c.msrp ? esc(vsMsrp(c.price, c.msrp)) : "cash price") + '</div>'
    : '<div class="price none">' + esc(c.noPriceWhy) + '</div><div class="vsmsrp">' + (c.msrp ? "MSRP " + esc(usd(c.msrp)) + " · ask the dealer" : "ask the dealer") + '</div>';
  const tag = group === "range" ? '<div class="wishline">' + esc(wishLine(j)) + '</div>'
    : group === "match"
    ? '<span class="fit-tag good">' + (j.total ? "Meets " + j.met + " of " + j.total + " wishes" : "Fits") + '</span>'
    : '<span class="fit-tag maybe">Not listed: ' + esc(j.mustMissing.join(", ")) + '</span>';
  const dist = j.dist != null ? ' <span class="dist">· ' + Math.round(j.dist) + ' mi</span>' : "";
  const inCmp = compare.includes(c.vin);
  const link = c.url ? '<a href="' + esc(c.url) + '" target="_blank" rel="noopener">View at dealer <span class="ext">↗</span></a>' : '<span class="note">No dealer page listed</span>';
  return '<article class="car ' + (group === "match" && i === 0 && wish.sort === "match" ? "best" : "") + '" data-vin="' + esc(c.vin) + '">' +
    '<div>' + priceHtml + '</div>' + tag +
    '<div class="where"><a href="/dealers/' + esc(c.dealer) + '"><b>' + esc(d.name) + '</b></a> · ' + esc(d.city) + dist + '</div>' +
    '<div class="colors"><span><i class="sw" style="background:' + (SW[c.ext] || "var(--sunk)") + '"></i>' + esc(c.ext) + '</span><span><i class="sw" style="background:' + (SW[c.int] || "var(--sunk)") + '"></i>' + esc(c.int + (c.intMat ? " " + c.intMat : "") + " interior") + '</span></div>' +
    '<div class="feats">' + (featTags(c) || '<span class="note">No packages listed by the dealer</span>') + '</div>' +
    '<div class="car-foot"><span class="seen">' + (c.inTransit ? "On the way to the dealer" : "On the lot") + ' · first seen ' + esc(fmtDay(c.firstSeen)) + '</span>' +
    '<span style="display:flex;gap:12px;align-items:center"><a href="/vin/' + esc(c.vin) + '">Details</a><label class="cmp"><input type="checkbox" data-cmp="' + esc(c.vin) + '" ' + (inCmp ? "checked" : "") + ' ' + (!inCmp && compare.length >= 3 ? "disabled" : "") + '> Compare</label>' + link + '</span></div></article>';
}
function vsMsrp(p, m) {
  const d = Math.round((p - m) / 100);
  if (Math.abs(d) < 1) return "At MSRP " + usd(m);
  return d < 0 ? usd(-d * 100) + " below MSRP " + usd(m) : usd(d * 100) + " above MSRP " + usd(m);
}
function renderResults(match, maybe, out) {
  const box = $("results");
  const before = flipFirst(box);
  const sec = (key, title, list, help) => {
    if (!list.length) return "";
    const shown = list.slice(0, showAll[key]);
    return '<div class="section-title">' + title + ' <span class="count" data-group="' + key + '">' + list.length + '</span></div>' +
      (help ? '<p class="note">' + help + '</p>' : "") +
      '<div class="cards">' + shown.map((x, i) => card(x, i, key)).join("") + '</div>' +
      (list.length > shown.length ? '<button class="more" type="button" data-more="' + key + '">Show all ' + list.length + '</button>' : "");
  };
  let html = sec("match", "Fit your wish list", match, "");
  html += sec("maybe", "Might fit: the dealer doesn't list a must-have", maybe, "These cars may still have the package. Dealer pages often leave it out, so ask the dealer before ruling them out.");
  if (!match.length && !maybe.length) html = '<div class="empty">No cars fit right now. Try a higher budget, a wider distance, or turn a must-have into a nice-to-have.</div>';
  if (out.length) {
    const why = {};
    for (const x of out) for (const r of x.j.out) why[r] = (why[r] || 0) + 1;
    html += '<p class="note" id="hiddenLine">' + out.length + ' cars hidden by your limits: ' + Object.entries(why).map(([r, n]) => n + " " + r).join(", ") + '.</p>';
  }
  box.innerHTML = html;
  flipPlay(box, before);
}
// FLIP re-rank: cars that stay glide to their new place; newly shown cars fade up.
function flipFirst(box) {
  if (!render.ready) return null;
  const m = new Map();
  box.querySelectorAll(".car[data-vin]").forEach(el => m.set(el.dataset.vin, el.getBoundingClientRect()));
  return m;
}
function flipPlay(box, before) {
  if (!before || !box.animate || matchMedia("(prefers-reduced-motion: reduce)").matches) return;
  let fresh = 0;
  box.querySelectorAll(".car[data-vin]").forEach(el => {
    const was = before.get(el.dataset.vin);
    if (was) {
      const now = el.getBoundingClientRect(), dx = was.left - now.left, dy = was.top - now.top;
      if (dx || dy) el.animate([{ transform: "translate(" + dx + "px," + dy + "px)" }, { transform: "none" }], { duration: 520, easing: "cubic-bezier(0.16, 1, 0.3, 1)" });
    } else {
      el.animate([{ opacity: .35, transform: "translateY(12px)" }, { opacity: 1, transform: "none" }], { duration: 420, delay: 35 * fresh++, easing: "cubic-bezier(0.16, 1, 0.3, 1)", fill: "backwards" });
    }
  });
}
function renderTrend(t) {
  $("trendSub").textContent = t.both
    ? "Same cars priced on more than one day."
    : "We need at least two days of checks to show price changes.";
  const body = $("trendBody");
  if (!t.changed) {
    body.innerHTML = '<div class="empty">None of those ' + t.both + ' cars changed price. A clearer trend appears after about a week of daily checks.</div>';
    return;
  }
  body.innerHTML = '<div class="cards">' + t.changes.map(ch => '<article class="car"><div class="price num">' + esc(usd(ch.to)) + '</div><div class="' + (ch.to < ch.from ? "yes" : "unk") + '">' +
    (ch.to < ch.from ? "▼" : "▲") + " " + esc(usd(Math.abs(ch.to - ch.from))) + " since " + esc(fmtDay(ch.d0)) + '</div><div class="where">' +
    esc((dealerById[ch.c.dealer] || {}).name || "") + " · " + esc(ch.c.ext) + '</div></article>').join("") + '</div>';
}
function renderTray() {
  const tray = $("tray");
  tray.hidden = compare.length === 0;
  if (!compare.length) return;
  const cars = compare.map(v => D.cars.find(c => c.vin === v)).filter(Boolean);
  $("trayTitle").textContent = "Comparing " + cars.length + (cars.length === 1 ? " car" : " cars") + (cars.length < 3 ? " · add up to 3" : "");
  const row = (label, f) => '<tr><th>' + label + '</th>' + cars.map(c => '<td>' + f(c) + '</td>').join("") + '</tr>';
  $("trayTable").innerHTML = '<table>' +
    row("Price", c => c.price != null ? '<b class="num">' + esc(usd(c.price)) + '</b>' : '<span class="unk">' + esc(c.noPriceWhy) + '</span>') +
    row("MSRP", c => c.msrp ? esc(usd(c.msrp)) : "") +
    row("Dealer", c => esc((dealerById[c.dealer] || {}).name || "") + ", " + esc((dealerById[c.dealer] || {}).city || "")) +
    row("Exterior", c => esc(c.ext)) +
    row("Interior", c => esc(c.int + (c.intMat ? " " + c.intMat : ""))) +
    FEATS.map(f => row(f.label, c => c.feats[f.k] === "yes" ? '<span class="yes">✓ Listed</span>' : '<span class="unk">Not listed</span>')).join("") +
    row("Availability", c => c.inTransit ? "On the way" : "On the lot") +
    row("VIN", c => '<span class="num">' + esc(c.vin) + '</span>') +
    '</table>';
}
function chips(el, key) {
  const counts = {};
  for (const c of D.cars) counts[c[key]] = (counts[c[key]] || 0) + 1;
  el.innerHTML = Object.keys(counts).sort((a, b) => counts[b] - counts[a]).map(v =>
    '<button type="button" class="wchip" data-chip="' + key + '" data-v="' + esc(v) + '" aria-pressed="' + wish[key].includes(v) + '"><i class="sw" style="background:' + (SW[v] || "var(--sunk)") + '"></i>' + esc(v) + ' <span class="ct">' + counts[v] + '</span></button>').join("");
}
function buildControls() {
  $("asof").textContent = D.asOf ? new Date(D.asOf + "T12:00:00").toLocaleDateString("en-US", { month: "long", day: "numeric", year: "numeric" }) : "not yet";
  const b = $("budget");
  b.value = wish.budget ?? b.max;
  const near = $("near");
  near.innerHTML = '<option value="any">Anywhere in Texas</option>' + Object.keys(CITY).sort().map(c => '<option value="' + esc(c) + '">Near ' + esc(c) + '</option>').join("");
  near.value = CITY[wish.near] ? wish.near : "any";
  if (near.value === "any") wish.near = "any";
  $("radius").innerHTML = [50, 100, 200, null].map(r =>
    '<button type="button" class="wchip" data-radius="' + (r ?? "any") + '" aria-pressed="' + (wish.radius === r) + '" ' + (wish.near === "any" ? "disabled" : "") + '>' + (r ? "Within " + r + " mi" : "Any distance") + '</button>').join("");
  $("pkgs").innerHTML = FEATS.map(f => {
    const n = D.cars.filter(c => c.feats[f.k] === "yes").length;
    return '<span class="name">' + esc(f.label) + ' <span class="note">(' + n + ')</span></span><span class="seg" role="group" aria-label="' + esc(f.label) + '">' +
      [["must", "Must"], ["nice", "Nice"], ["", "Skip"]].map(([v, l]) => '<button type="button" data-feat="' + f.k + '" data-v="' + v + '" aria-pressed="' + ((wish.feats[f.k] || "") === v) + '">' + l + '</button>').join("") + '</span>';
  }).join("");
  chips($("extChips"), "ext");
  chips($("intChips"), "int");
  $("colorsOnly").checked = wish.colorsOnly;
  $("transit").checked = wish.transit;
  $("sort").value = wish.sort;
  budgetLabel();
}
function budgetLabel() {
  $("budgetOut").textContent = wish.budget == null ? "No limit" : "Up to " + usd(wish.budget * 100);
}
function update() { save(); buildControls(); render(); }
const finder = document.querySelector(".finder");
finder.addEventListener("click", e => {
  const t = e.target.closest("button"); if (!t) return;
  if (t.dataset.chip) { const k = t.dataset.chip, v = t.dataset.v; wish[k] = wish[k].includes(v) ? wish[k].filter(x => x !== v) : [...wish[k], v]; update(); }
  else if (t.dataset.radius) { wish.radius = t.dataset.radius === "any" ? null : +t.dataset.radius; update(); }
  else if (t.dataset.feat) { const v = t.dataset.v; if (v) wish.feats[t.dataset.feat] = v; else delete wish.feats[t.dataset.feat]; update(); }
  else if (t.dataset.more) { showAll[t.dataset.more] = 1e9; render(); }
  else if (t.id === "reset") { wish = structuredClone(DEFAULT_WISH); compare = []; update(); }
  else if (t.id === "trayClear") { compare = []; render(); }
  else if (t.id === "trayToggle") { const tb = $("trayTable"); tb.hidden = !tb.hidden; t.textContent = tb.hidden ? "Show" : "Hide"; }
});
finder.addEventListener("change", e => {
  const t = e.target;
  if (t.dataset.cmp) { const v = t.dataset.cmp; compare = t.checked ? [...compare, v].slice(0, 3) : compare.filter(x => x !== v); render(); }
  else if (t.id === "near") { wish.near = t.value; if (t.value === "any") wish.radius = null; else if (wish.radius == null) wish.radius = 200; update(); }
  else if (t.id === "colorsOnly") { wish.colorsOnly = t.checked; update(); }
  else if (t.id === "transit") { wish.transit = t.checked; update(); }
  else if (t.id === "sort") { wish.sort = t.value; save(); render(); }
});
$("budget").addEventListener("input", e => {
  const v = +e.target.value; wish.budget = v >= +e.target.max ? null : v; budgetLabel(); save(); render();
});
buildControls();
render();
render.ready = true;
})();
