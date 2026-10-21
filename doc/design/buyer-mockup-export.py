"""Read-only export of real data for the buyer mockup. Prices recomputed with the fixed pricing rules."""
import json, sqlite3, re, sys
sys.path.insert(0, "src")
from carshop.domain import pricing

DB = "file:data/app.db?mode=ro"
RUN = "20261020T052908Z-manual_date-a78f54"
c = sqlite3.connect(DB, uri=True); c.row_factory = sqlite3.Row

def money(cents): return f"${cents/100:,.2f}"

def recompute(oid):
    rows = c.execute("select label, cents, kind from price_components where observation_id=? order by price_component_id", (oid,)).fetchall()
    pairs = [(r["label"], money(r["cents"])) for r in rows if r["cents"] is not None]
    if not pairs: return None, None, None, "No price shown"
    ps = pricing.build_price_set(pairs)
    cmp_ = ps.comparison_price_cents(5_500_000, 12_000_000)
    flags = {f.value for f in ps.flags}
    why = None
    if cmp_ is None:
        why = ("Lease/finance payment only" if "PRICE_IS_PAYMENT" in flags else
               "Price only after conditional rebates" if "CONDITIONAL_ONLY" in flags else
               "Call for price" if "CALL_FOR_PRICE" in flags else
               "Only MSRP shown" if ps.msrp_cents else "No cash price shown")
    return cmp_, ps.msrp_cents, getattr(ps, "price_basis", None), why

def canon_ext(raw):
    if not raw: return "Not shown"
    s = re.sub(r"\bmetallic\b", "", raw, flags=re.I).replace("Gray", "Grey").replace("gray", "grey")
    s = " ".join(s.split())
    known = ["Black Sapphire","Mineral White","Skyscraper Grey","Carbon Black","Tanzanite Blue II","Alpine White",
             "Brooklyn Grey","Dravit Grey","Manhattan Green","Frozen Pure Grey","Blue Ridge Mountain","Grigio Telesto"]
    for k in known:
        if k.lower() in s.lower(): return k
    return s
def ext_family(name):
    n = name.lower()
    for fam in ["grey","white","black","blue","green"]:
        if fam in n: return fam.capitalize()
    return "Other"
def canon_int(raw):
    if not raw: return "Not shown", None
    s = raw.lower()
    color = next((k for k in ["ivory white","silverstone","cognac","coffee","tartufo","black"] if k in s), None)
    mat = "Merino leather" if "merino" in s else "Sensafin" if "sensafin" in s else None
    return (color.title() if color else raw), mat

loc = {}
try:
    import glob
    snap = sorted(glob.glob("data/registry/locator-*Z.json"))[-1]
    for d in json.load(open(snap))["dealers"]:
        loc[str(d["center_id"])] = (d["latitude"], d["longitude"])
except Exception as e:
    print("locator read failed", e, file=sys.stderr)

dealers = {}
for d in c.execute("select dealer_id, name, city, center_number, hostname from dealers where active=1"):
    dealers[d["dealer_id"]] = dict(id=d["dealer_id"], name=d["name"], city=d["city"], site=d["hostname"] or None,
                                   lat=loc.get(str(d["center_number"]), (None, None))[0], lon=loc.get(str(d["center_number"]), (None, None))[1])
# latest status per dealer: statewide run, then any later single-dealer run overrides
st = {r["dealer_id"]: dict(status=r["status"], reason=r["reason_code"], cars=r["eligible_count"]) for r in c.execute(
    "select dealer_id, status, reason_code, eligible_count from dealer_runs where run_id=?", (RUN,))}
for r in c.execute("""select dr.dealer_id, dr.status, dr.reason_code, dr.eligible_count from dealer_runs dr join runs r using(run_id)
                      where r.status='PUBLISHED' and r.started_at > (select started_at from runs where run_id=?) order by r.started_at""", (RUN,)):
    st[r["dealer_id"]] = dict(status=r["status"], reason=r["reason_code"], cars=r["eligible_count"])
for k, v in dealers.items(): v.update(st.get(k, dict(status="NOT_RUN", reason=None, cars=0)))

feat_names = ["DAP","M_SPORT","MASSAGE","PANORAMIC_ROOF","SKY_LOUNGE","EXECUTIVE","CLIMATE_COMFORT","AIR_SUSPENSION"]
cars = []
for o in c.execute("""select o.*, u.first_seen_at from observations o left join units u using(vin)
                      where o.run_id=? and o.rejected=0 order by o.vin""", (RUN,)).fetchall():
    stg = c.execute("select status from inventory_staging where run_id=? and dealer_id=? and vin=?", (RUN, o["dealer_id"], o["vin"])).fetchone()
    if stg and stg[0] != "VALIDATED": continue
    feats = {r["feature"]: r["status"] for r in c.execute("select feature, status from vehicle_features where observation_id=?", (o["observation_id"],))}
    cmp_, msrp, basis, why = recompute(o["observation_id"])
    ext = canon_ext(o["exterior_raw"]); icol, imat = canon_int(o["interior_raw"])
    cars.append(dict(vin=o["vin"], dealer=o["dealer_id"], price=cmp_, msrp=msrp or o["msrp_cents"], noPriceWhy=why,
        ext=ext, extFamily=ext_family(ext), extRaw=o["exterior_raw"], int=icol, intMat=imat, intRaw=o["interior_raw"],
        inTransit=bool(o["in_transit"]), stock=o["stock"], url=o["detail_url"] or o["source_url"],
        firstSeen=(o["first_seen_at"] or "")[:10], feats={k: feats.get(k, "UNKNOWN") for k in feat_names}))

# per-VIN history across published runs (recomputed)
hist = {}
for r in c.execute("""select o.vin, o.observation_id, r.snapshot_date from observations o join runs r using(run_id)
                      where r.status='PUBLISHED' and o.rejected=0 order by r.snapshot_date, r.started_at"""):
    p, _, _, _ = recompute(r["observation_id"])
    hist.setdefault(r["vin"], {})[r["snapshot_date"]] = p
for car in cars:
    h = hist.get(car["vin"], {})
    car["history"] = [[d, p] for d, p in sorted(h.items())]

days = [r[0] for r in c.execute("select distinct snapshot_date from runs where status='PUBLISHED' order by 1")]
out = dict(asOf="2026-10-20", runId=RUN, days=days, dealers=list(dealers.values()), cars=cars)
json.dump(out, open(sys.argv[1], "w"), separators=(",", ":"))
priced = [x for x in cars if x["price"]]
print(len(cars), "cars,", len(priced), "priced,", len(dealers), "dealers, days", days)
print("price range", min(x["price"] for x in priced)/100, max(x["price"] for x in priced)/100)
print("dealers w/o coords", [d["id"] for d in dealers.values() if d["lat"] is None])
print("ext", sorted({x["ext"] for x in cars})); print("int", sorted({x["int"] for x in cars}))
print("history >1 day:", sum(1 for x in cars if len(x["history"])>1), "changed:", sum(1 for x in cars if len({p for _,p in x["history"]})>1))
