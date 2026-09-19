"""Reference implementation used once to write the shared wave fixtures (rules/fixtures/waves.json and
wave_points.sample.*). The contract text in docs/data-contract.md is authoritative; this mirrors it."""
import json, math, struct, pathlib

G = 9.80665
KT = 0.514444

def wave(wind_kt, fetch_m, depth_m=None):
    """SPM 1984 fetch-limited Hs (m) and Tp (s); shallow-water form when depth is known."""
    if wind_kt <= 0 or fetch_m <= 0:
        return 0.0, 0.0
    ua = 0.71 * (wind_kt * KT) ** 1.23
    f = G * fetch_m / ua**2
    if depth_m is None:
        h = 1.6e-3 * math.sqrt(f)
        t = 0.2857 * f ** (1 / 3)
    else:
        d = G * max(depth_m, 0.1) / ua**2
        th = math.tanh(0.530 * d**0.75)
        h = 0.283 * th * math.tanh(0.00565 * math.sqrt(f) / th)
        tt = math.tanh(0.833 * d**0.375)
        t = 7.54 * tt * math.tanh(0.0379 * f ** (1 / 3) / tt)
    h = min(h, 0.2433)          # fully developed
    t = min(t, 8.134)
    return h * ua**2 / G, t * ua / G

def wind_bin(deg):
    return int(math.floor(deg / 22.5 + 0.5)) % 16

def pct75(vals):
    s = sorted(vals); return s[max(0, math.ceil(0.75 * len(s)) - 1)]

def median_low(vals):
    s = sorted(vals); return s[(len(s) - 1) // 2]

def regions(points, labels, wind_dir, wind_kt, min_run_ft):
    b = wind_bin(wind_dir)
    rows = {}
    for i, p in enumerate(points):
        depth = None if p["depth_dm"] == 0xFFFF else p["depth_dm"] / 10
        hs_m, _ = wave(wind_kt, p["fetch"][b] * 10, depth)
        run_ft = p["run"][b % 8] * 10
        rows.setdefault(p["label"], []).append((hs_m, run_ft, i))
    out = []
    for lab, pts in rows.items():
        usable = [x for x in pts if x[1] >= min_run_ft]
        r = {"label": labels[lab], "n_points": len(pts), "n_usable": len(usable),
             "hs_all_in": round(pct75([x[0] for x in pts]) / 0.0254)}
        if usable:
            best = min(usable, key=lambda x: (x[0], x[2]))
            r.update(hs_in=round(pct75([x[0] for x in usable]) / 0.0254),
                     run_ft=median_low([x[1] for x in usable]), point=best[2])
        else:
            r.update(hs_in=None, run_ft=None, point=None)
        out.append(r)
    out.sort(key=lambda r: (r["hs_in"] is None, r["hs_in"] if r["hs_in"] is not None else 0, r["label"]))
    return out

cases = []
for wind_kt, fetch_m, depth_m in [(20, 5000, None), (20, 40000, None), (10, 1000, None), (15, 25000, 3.4), (15, 25000, 1.0),
                                  (25, 39000, 3.4), (12, 1200, 1.0), (30, 500000, None), (8, 300, None), (0, 5000, None)]:
    h, t = wave(wind_kt, fetch_m, depth_m)
    cases.append({"wind_kt": wind_kt, "fetch_m": fetch_m, "depth_m": depth_m, "hs_m": round(h, 4), "tp_s": round(t, 3)})

# sample points: lake 111 "bay lake" with three labels, lake 222 shallow with one label
labels = ["Anchor Bay", "Big Muscamoot Bay", "middle", "north end"]
def pt(lon, lat, depth_dm, label, fetch_km, run_kft):
    return {"lon": lon, "lat": lat, "depth_dm": depth_dm, "label": label,
            "fetch": [round(k * 100) for k in fetch_km], "run": [round(k * 100) for k in run_kft]}
anchor = [3.8, 4.9, 5.5, 7.5, 7.4, 4.8, 3.5, 5.6, 39.2, 38.3, 7.5, 9.0, 6.9, 3.8, 3.5, 3.5]
musc   = [1.2, 1.1, 1.4, 3.8, 1.1, 1.0, 0.9, 0.9, 1.2, 2.4, 24.8, 19.5, 1.6, 1.3, 1.3, 1.4]
mid    = [10.4, 12.1, 10.7, 15.0, 21.9, 22.8, 17.9, 15.1, 14.7, 15.9, 18.1, 21.5, 16.0, 18.4, 19.8, 26.7]
pts111 = [pt(-82.7166, 42.6500, 30, 0, anchor, [9, 8, 7, 9, 12, 10, 8, 9]),
          pt(-82.7000, 42.6400, 34, 0, [x * 1.1 for x in anchor], [10, 9, 8, 9, 12, 10, 8, 9]),
          pt(-82.6607, 42.5578, 9, 1, musc, [3, 3, 4, 6, 3, 1.5, 2.5, 2.5]),
          pt(-82.6550, 42.5600, 8, 1, [x * 0.9 for x in musc], [3, 3, 4, 5, 3, 1.2, 2.2, 2.4]),
          pt(-82.6800, 42.4300, 58, 2, mid, [40, 45, 40, 50, 60, 60, 55, 50]),
          pt(-82.6500, 42.4500, 55, 2, [x * 0.95 for x in mid], [40, 45, 40, 50, 60, 60, 55, 50])]
pts222 = [pt(-83.4200, 42.6100, 0xFFFF, 3, [0.4] * 16, [2.2] * 8), pt(-83.4180, 42.6080, 0xFFFF, 2, [0.9] * 16, [4.1] * 8)]
allpts = pts111 + pts222
index = {"version": 1, "record_bytes": 60, "fetch_unit_m": 10, "run_unit_ft": 10, "labels": labels,
         "lakes": {"111": [0, len(pts111)], "222": [len(pts111), len(pts222)]}}
buf = b"".join(struct.pack("<ffHH16H8H", p["lon"], p["lat"], p["depth_dm"], p["label"], *p["fetch"], *p["run"]) for p in allpts)
assert len(buf) == 60 * len(allpts)

agg = []
for lake, pts, wd, wk, mr in [("111", pts111, 225, 12, 2000), ("111", pts111, 180, 15, 2000), ("111", pts111, 340, 18, 2000),
                              ("111", pts111, 100, 10, 2600), ("222", pts222, 270, 14, 2000), ("222", pts222, 270, 14, 5000)]:
    agg.append({"lake": lake, "wind_dir": wd, "wind_kt": wk, "min_run_ft": mr, "bin": wind_bin(wd),
                "regions": regions(pts, labels, wd, wk, mr)})

fx = pathlib.Path("rules/fixtures")
(fx / "waves.json").write_text(json.dumps({"note": "Shared by api (Python) and rules/waves (JS). Formula and aggregation: docs/data-contract.md, Wave field.",
    "wave": cases, "bins": [{"deg": d, "bin": wind_bin(d)} for d in (0, 11, 11.25, 12, 180, 348.74, 348.75, 359.9)],
    "regions": agg}, indent=1) + "\n")
(fx / "wave_points.sample.bin").write_bytes(buf)
(fx / "wave_points.sample.json").write_text(json.dumps(index, indent=1) + "\n")
print(json.dumps(cases))
for a in agg: print(a["lake"], a["wind_dir"], a["wind_kt"], [(r["label"], r["hs_in"], r["run_ft"], r["hs_all_in"]) for r in a["regions"]])
