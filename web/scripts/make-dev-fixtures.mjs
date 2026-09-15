#!/usr/bin/env node
/**
 * Regenerates web/dev-fixtures/ so the client runs without the Python pipeline.
 *
 *   node web/scripts/make-dev-fixtures.mjs
 *
 * Writes (all conforming to docs/data-contract.md):
 *   index.json  restrictions.json  rules.json  pack.json      <- committed
 *   geojson/*.geojson                                          <- intermediate, gitignored
 *   lakes.pmtiles  usable_water.pmtiles  overlays.pmtiles      <- gitignored binaries
 *
 * basemap.pmtiles is NOT produced here; it is a one-off Protomaps extract, see
 * `npm run fixtures -- --print-basemap-cmd` or the README.
 *
 * Requires `tippecanoe` on PATH (brew install tippecanoe). Without it the JSON files are
 * still written and the tile build is skipped with a warning.
 */
import { createHash } from "node:crypto";
import { execFileSync } from "node:child_process";
import { existsSync, mkdirSync, readFileSync, statSync, writeFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const webDir = path.resolve(here, "..");
const repoRoot = path.resolve(webDir, "..");
const outDir = path.join(webDir, "dev-fixtures");
const geoDir = path.join(outDir, "geojson");

const BUILD_DATE = "2026-09-15";
const BUILT_AT = "2026-09-15T18:30:00Z";
const BASEMAP_BBOX = "-83.7,42.4,-83.0,42.9";
const SOURCE_URL =
  "https://www.michigan.gov/dnr/managing-resources/laws/controls/localcontrols/oakland/local-watercraft-controls";

// ---------------------------------------------------------------------------
// contract helpers
// ---------------------------------------------------------------------------

const ABBREV = {
  lk: "lake",
  mt: "mount",
  st: "saint",
  n: "north",
  s: "south",
  e: "east",
  w: "west",
  upr: "upper",
  lwr: "lower",
  twp: "township",
};
const GENERIC = new Set(["lake", "pond", "reservoir", "impoundment", "flowage", "basin"]);

/** Mirror of docs/data-contract.md "Name normalization". Kept in sync with web/src/search/normalize.ts. */
function normalizeName(raw) {
  if (raw == null) return "";
  let s = String(raw).normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase();
  s = s.replace(/[^a-z0-9\s]/g, " ").replace(/\s+/g, " ").trim();
  let words = s.split(" ").filter(Boolean).map((w) => ABBREV[w] ?? w);
  while (words.length > 1 && GENERIC.has(words[0])) words.shift();
  while (words.length > 1 && GENERIC.has(words[words.length - 1])) words.pop();
  return words.join(" ");
}

/** Contract: id = int(sha1(source_key)[:8], 16) & 0x7fffffff */
function lakeId(sourceKey) {
  const hex = createHash("sha1").update(sourceKey).digest("hex").slice(0, 8);
  return parseInt(hex, 16) & 0x7fffffff;
}

/** Contract: sha1(f"{county}|{lake_name_raw}|{township}|{raw_text}")[:12] */
function restrictionId(county, lakeNameRaw, township, rawText) {
  return createHash("sha1")
    .update(`${county}|${lakeNameRaw}|${township}|${rawText}`)
    .digest("hex")
    .slice(0, 12);
}

// ---------------------------------------------------------------------------
// geometry
// ---------------------------------------------------------------------------

const M_PER_DEG_LAT = 111_320;
const ACRE_M2 = 4046.8564224;
const FT_PER_M = 3.280839895;
const SHORE_BUFFER_M = 100 / FT_PER_M; // statewide 100 ft slow-no-wake shore buffer

function mPerDegLon(lat) {
  return M_PER_DEG_LAT * Math.cos((lat * Math.PI) / 180);
}

/** Deterministic [0,1) PRNG so regenerating the fixtures does not churn the tiles. */
function mulberry32(seed) {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/** A plausible lake blob: a rotated ellipse with smooth radial wobble. */
function lakeRing(lon, lat, majorM, minorM, rotDeg, seed, points = 64) {
  const rnd = mulberry32(seed);
  const harmonics = [2, 3, 5].map((k) => ({
    k,
    amp: 0.06 + rnd() * 0.1,
    phase: rnd() * Math.PI * 2,
  }));
  const rot = (rotDeg * Math.PI) / 180;
  const mLon = mPerDegLon(lat);
  const ring = [];
  for (let i = 0; i < points; i++) {
    const t = (i / points) * Math.PI * 2;
    let wobble = 1;
    for (const h of harmonics) wobble += h.amp * Math.sin(h.k * t + h.phase);
    const ex = Math.cos(t) * majorM * 0.5 * wobble;
    const ey = Math.sin(t) * minorM * 0.5 * wobble;
    const x = ex * Math.cos(rot) - ey * Math.sin(rot);
    const y = ex * Math.sin(rot) + ey * Math.cos(rot);
    ring.push([
      Number((lon + x / mLon).toFixed(6)),
      Number((lat + y / M_PER_DEG_LAT).toFixed(6)),
    ]);
  }
  ring.push([...ring[0]]);
  return ring;
}

/** Shrink every vertex toward the centroid by 100 ft: an approximate erosion, good enough for fixtures. */
function erodeRing(ring, centroid) {
  const [cLon, cLat] = centroid;
  const mLon = mPerDegLon(cLat);
  const out = [];
  for (let i = 0; i < ring.length - 1; i++) {
    const dx = (ring[i][0] - cLon) * mLon;
    const dy = (ring[i][1] - cLat) * M_PER_DEG_LAT;
    const d = Math.hypot(dx, dy);
    if (d <= SHORE_BUFFER_M * 1.15) return null; // lake too small to leave usable water
    const f = (d - SHORE_BUFFER_M) / d;
    out.push([
      Number((cLon + (dx * f) / mLon).toFixed(6)),
      Number((cLat + (dy * f) / M_PER_DEG_LAT).toFixed(6)),
    ]);
  }
  out.push([...out[0]]);
  return out;
}

function ringMetrics(ring, lat0) {
  const mLon = mPerDegLon(lat0);
  const pts = ring.slice(0, -1).map(([lon, lat]) => [lon * mLon, lat * M_PER_DEG_LAT]);
  let twiceArea = 0;
  let cx = 0;
  let cy = 0;
  for (let i = 0; i < pts.length; i++) {
    const [x1, y1] = pts[i];
    const [x2, y2] = pts[(i + 1) % pts.length];
    const cross = x1 * y2 - x2 * y1;
    twiceArea += cross;
    cx += (x1 + x2) * cross;
    cy += (y1 + y2) * cross;
  }
  const areaM2 = Math.abs(twiceArea) / 2;
  const centroid = [cx / (3 * twiceArea) / mLon, cy / (3 * twiceArea) / M_PER_DEG_LAT];

  // Longest chord across the (near-convex) blob: widest vertex pair.
  let best = 0;
  let bestPair = [pts[0], pts[0]];
  for (let i = 0; i < pts.length; i++) {
    for (let j = i + 1; j < pts.length; j++) {
      const d = Math.hypot(pts[i][0] - pts[j][0], pts[i][1] - pts[j][1]);
      if (d > best) {
        best = d;
        bestPair = [pts[i], pts[j]];
      }
    }
  }
  const dx = bestPair[1][0] - bestPair[0][0];
  const dy = bestPair[1][1] - bestPair[0][1];
  let bearing = (Math.atan2(dx, dy) * 180) / Math.PI;
  bearing = ((bearing % 180) + 180) % 180; // contract: chord_bearing_deg is 0-179

  const lons = ring.map((p) => p[0]);
  const lats = ring.map((p) => p[1]);
  return {
    areaAcres: Number((areaM2 / ACRE_M2).toFixed(1)),
    chordFt: Math.round(best * FT_PER_M),
    chordBearingDeg: Math.round(bearing),
    centroid: [Number(centroid[0].toFixed(4)), Number(centroid[1].toFixed(4))],
    bbox: [
      Number(Math.min(...lons).toFixed(4)),
      Number(Math.min(...lats).toFixed(4)),
      Number(Math.max(...lons).toFixed(4)),
      Number(Math.max(...lats).toFixed(4)),
    ],
  };
}

// ---------------------------------------------------------------------------
// the fixture dataset: plausible Oakland County, Michigan lakes
// ---------------------------------------------------------------------------

/** `name: null` marks an unnamed polygon, which the contract says the engine verdicts `unknown`. */
const LAKES = [
  {
    key: "MIGF-HYDRO-0100001",
    name: "Big School Lot Lake",
    county: "Oakland",
    township: "Rose Township",
    lon: -83.5901,
    lat: 42.7712,
    majorM: 900,
    minorM: 520,
    rot: 47,
    access: null,
  },
  {
    key: "MIGF-HYDRO-0100002",
    name: "Little School Lot Lake",
    county: "Oakland",
    township: "Rose Township",
    lon: -83.5738,
    lat: 42.7791,
    majorM: 520,
    minorM: 330,
    rot: 20,
    access: null,
  },
  {
    key: "MIGF-HYDRO-0100003",
    name: "Pontiac Lake",
    county: "Oakland",
    township: "Waterford Township",
    lon: -83.4489,
    lat: 42.6841,
    majorM: 2600,
    minorM: 1450,
    rot: 78,
    access: "Pontiac Lake State Recreation Area BAS",
  },
  {
    key: "MIGF-HYDRO-0100004",
    name: "Union Lake",
    county: "Oakland",
    township: "Commerce Township",
    lon: -83.4192,
    lat: 42.6123,
    majorM: 1900,
    minorM: 1250,
    rot: 12,
    access: "Union Lake BAS",
  },
  {
    key: "MIGF-HYDRO-0100005",
    name: "Lake Angelus",
    county: "Oakland",
    township: "City of Lake Angelus",
    lon: -83.3281,
    lat: 42.7095,
    majorM: 1650,
    minorM: 1020,
    rot: 131,
    access: null,
  },
  {
    key: "MIGF-HYDRO-0100006",
    name: "Cass Lake",
    county: "Oakland",
    township: "West Bloomfield Township",
    lon: -83.3418,
    lat: 42.6089,
    majorM: 3400,
    minorM: 1900,
    rot: 24,
    access: "Dodge Brothers #4 BAS",
  },
  {
    key: "MIGF-HYDRO-0100007",
    name: "Mud Lake",
    county: "Oakland",
    township: "Rose Township",
    lon: -83.6122,
    lat: 42.7398,
    majorM: 430,
    minorM: 300,
    rot: 95,
    access: null,
  },
  {
    key: "MIGF-HYDRO-0100008",
    name: "Loon Lake",
    county: "Oakland",
    township: "Wixom",
    lon: -83.5334,
    lat: 42.5451,
    majorM: 1150,
    minorM: 780,
    rot: 160,
    access: "Loon Lake BAS",
  },
  {
    key: "MIGF-HYDRO-0100009",
    name: "Round Lake",
    county: "Oakland",
    township: "Springfield Township",
    lon: -83.4995,
    lat: 42.7541,
    majorM: 980,
    minorM: 900,
    rot: 5,
    access: "Round Lake BAS",
  },
  {
    key: "MIGF-HYDRO-0100010",
    name: null, // unnamed polygon over 20 acres, kept per the index.json contract
    county: "Oakland",
    township: "Highland Township",
    lon: -83.6205,
    lat: 42.6512,
    majorM: 700,
    minorM: 460,
    rot: 60,
    access: null,
  },
];

/** `lake` is the fixture key above; the pipeline would fill lake_ids via the match stage. */
const RESTRICTIONS = [
  {
    lake: "MIGF-HYDRO-0100001",
    rule_id: "R 281.763.3",
    restriction_type: "no_high_speed",
    scope: "lakewide",
    scope_description: null,
    hours: null,
    season: null,
    speed_mph: null,
    plss: [{ township: "4N", range: "7E", sections: [16, 21] }],
    raw_text:
      "It is unlawful for the operator of a vessel to exceed a slow, no wake speed or to operate at a high speed or on plane upon the waters of Big School Lot Lake, section 16 and 21, T4N, R7E, Rose Township, Oakland County.",
  },
  {
    lake: "MIGF-HYDRO-0100002",
    rule_id: "R 281.763.3",
    restriction_type: "no_high_speed",
    scope: "lakewide",
    scope_description: null,
    hours: null,
    season: null,
    speed_mph: null,
    plss: [{ township: "4N", range: "7E", sections: [16] }],
    related_rule_ids: ["R 281.747.1"],
    raw_text:
      "It is unlawful for the operator of a vessel to exceed a slow, no wake speed or to operate at a high speed or on plane upon the waters of Little School Lot Lake, section 16, T4N, R7E, Rose Township, Oakland County. (See R 281.747.1 for the Livingston county portion.)",
  },
  {
    lake: "MIGF-HYDRO-0100003",
    rule_id: "R 281.763.8",
    restriction_type: "slow_no_wake",
    scope: "zone",
    scope_description: "within 200 feet of the north shore and the marked swimming area",
    hours: { text: "6:30 p.m. to 10:00 a.m.", start: "18:30", end: "10:00", days: null },
    season: null,
    speed_mph: null,
    clause: "(a)",
    signage_required: true,
    plss: [{ township: "3N", range: "8E", sections: [11, 12] }],
    raw_text:
      "It is unlawful for the operator of a vessel to exceed a slow, no wake speed upon the waters of Pontiac Lake within 200 feet of the north shore and the marked swimming area, between the hours of 6:30 p.m. and 10:00 a.m., sections 11 and 12, T3N, R8E, Waterford Township, Oakland County.",
  },
  {
    lake: "MIGF-HYDRO-0100004",
    rule_id: "R 281.763.11",
    restriction_type: "no_towing",
    scope: "lakewide",
    scope_description: null,
    hours: {
      text: "sunset to 11:00 a.m.",
      start: "20:30",
      end: "11:00",
      days: "Saturdays, Sundays and holidays",
    },
    season: null,
    speed_mph: null,
    clause: "(b)",
    plss: [{ township: "2N", range: "8E", sections: [4, 5] }],
    raw_text:
      "It is unlawful for a person to water ski or to tow a person on water skis, an aquaplane, or a similar contrivance upon the waters of Union Lake between the hours of sunset and 11:00 a.m., sections 4 and 5, T2N, R8E, Commerce Township, Oakland County.",
  },
  {
    lake: "MIGF-HYDRO-0100005",
    rule_id: "R 281.763.5",
    restriction_type: "high_speed_hours",
    scope: "lakewide",
    scope_description: null,
    hours: { text: "11:00 a.m. to 6:30 p.m.", start: "11:00", end: "18:30" },
    season: { text: "Memorial Day through Labor Day", start: "05-25", end: "09-07" },
    speed_mph: null,
    plss: [{ township: "4N", range: "10E", sections: [30] }],
    raw_text:
      "High speed boating is permitted upon the waters of Lake Angelus only between the hours of 11:00 a.m. and 6:30 p.m. from Memorial Day through Labor Day, section 30, T4N, R10E, City of Lake Angelus, Oakland County.",
  },
  {
    lake: "MIGF-HYDRO-0100007",
    rule_id: "R 281.763.14",
    restriction_type: "no_motorboats",
    scope: "lakewide",
    scope_description: null,
    hours: null,
    season: null,
    speed_mph: null,
    plss: [{ township: "4N", range: "7E", sections: [28] }],
    raw_text:
      "It is unlawful for a person to operate a motorboat propelled by an internal combustion engine upon the waters of Mud Lake, section 28, T4N, R7E, Rose Township, Oakland County.",
  },
];

/** Minimal rule set matching docs/data-contract.md, used when ../rules/rules.json is absent. */
const FALLBACK_RULES = {
  version: BUILD_DATE,
  aircraft: { name: "SeaRey", min_chord_ft: 2000, min_takeoff_mph: 40 },
  aggregate: "worst_of",
  rules: [
    {
      id: "no-vessels",
      when: { restriction_type: ["no_vessels", "no_motorboats"] },
      verdict: "restricted",
      note: "Motorboats prohibited lakewide ({rule_id}).",
    },
    {
      id: "federal-no-landing",
      when: { restriction_type: "federal_no_landing" },
      verdict: "restricted",
      note: "Federal unit with no designated seaplane area.",
    },
    {
      id: "mac-ordinance",
      when: { restriction_type: "mac_ordinance" },
      verdict: "restricted",
      note: "MAC-approved seaplane ordinance on file.",
    },
    {
      id: "mac-conditional",
      when: { restriction_type: "mac_conditional" },
      verdict: "conditional",
      note: "MAC record entry with conditions.",
    },
    {
      id: "no-planing-lakewide",
      when: {
        restriction_type: ["no_high_speed", "slow_no_wake"],
        scope: "lakewide",
        hours: null,
        season: null,
      },
      verdict: "restricted",
      note: "High-speed/planing prohibited lakewide. Takeoff and landing require planing.",
    },
    {
      id: "no-planing-timed",
      when: { restriction_type: ["no_high_speed", "slow_no_wake"], scope: "lakewide" },
      verdict: "conditional",
      note: "Slow-no-wake in force {hours.text}. Landing is only possible outside that window.",
    },
    {
      id: "no-planing-zone",
      when: { restriction_type: ["no_high_speed", "slow_no_wake"], scope: "zone" },
      verdict: "conditional",
      note: "Planing prohibited in {scope_description}. Use the rest of the lake.",
    },
    {
      id: "hours-window",
      when: { restriction_type: "high_speed_hours" },
      verdict: "conditional",
      note: "High speed allowed only {hours.text}.",
    },
    {
      id: "speed-limit-low",
      when: {
        restriction_type: "speed_limit",
        scope: "lakewide",
        speed_mph_lt: "$aircraft.min_takeoff_mph",
      },
      verdict: "restricted",
      note: "{speed_mph} mph limit lakewide is below takeoff speed.",
    },
    {
      id: "speed-limit-zone",
      when: { restriction_type: "speed_limit", scope: "zone" },
      verdict: "conditional",
      note: "{speed_mph} mph limit in {scope_description}.",
    },
    {
      id: "speed-limit-ok",
      when: { restriction_type: "speed_limit" },
      verdict: "clear",
      note: "Speed limit is above takeoff speed.",
    },
    {
      id: "no-wake-zone-marked",
      when: { restriction_type: "no_wake_zone_marked" },
      verdict: "conditional",
      note: "Buoyed no-wake zone; stay clear of it on landing rollout.",
    },
    {
      id: "ski-only",
      when: { restriction_type: "no_towing" },
      verdict: "clear",
      note: "Towing ban only; does not affect landing.",
    },
    {
      id: "pwc-only",
      when: { restriction_type: "no_pwc" },
      verdict: "clear",
      note: "PWC ban only; does not affect landing.",
    },
  ],
};

// ---------------------------------------------------------------------------
// build
// ---------------------------------------------------------------------------

const VERDICT_RANK = { restricted: 4, conditional: 3, unknown: 2, clear: 1 };

/**
 * A deliberately small stand-in for rules/engine so the fixtures carry a prebaked verdict.
 * The real client re-runs the shared engine; this only has to agree on these six records.
 */
function fixtureVerdict(restrictions, hasName) {
  if (!hasName) return "unknown";
  let worst = "clear";
  for (const r of restrictions) {
    let v = "unknown";
    if (r.restriction_type === "no_motorboats" || r.restriction_type === "no_vessels") {
      v = "restricted";
    } else if (r.restriction_type === "no_high_speed" || r.restriction_type === "slow_no_wake") {
      v = r.scope === "lakewide" && !r.hours && !r.season ? "restricted" : "conditional";
    } else if (r.restriction_type === "high_speed_hours") {
      v = "conditional";
    } else if (r.restriction_type === "no_towing" || r.restriction_type === "no_pwc") {
      v = "clear";
    }
    if (VERDICT_RANK[v] > VERDICT_RANK[worst]) worst = v;
  }
  return worst;
}

function feature(id, geometry, properties) {
  return { type: "Feature", id, geometry, properties };
}

function writeGeojson(name, features) {
  writeFileSync(
    path.join(geoDir, `${name}.geojson`),
    features.map((f) => JSON.stringify(f)).join("\n") + "\n",
  );
}

function writeJson(name, value) {
  writeFileSync(path.join(outDir, name), JSON.stringify(value, null, 2) + "\n");
}

function main() {
  mkdirSync(geoDir, { recursive: true });

  // --- restrictions -------------------------------------------------------
  const byLakeKey = new Map();
  const restrictionsOut = {};
  for (const r of RESTRICTIONS) {
    const lake = LAKES.find((l) => l.key === r.lake);
    if (!lake) throw new Error(`restriction references unknown lake ${r.lake}`);
    const id = restrictionId(lake.county, lake.name, lake.township, r.raw_text);
    const record = {
      restriction_id: id,
      rule_id: r.rule_id,
      county: lake.county,
      township: lake.township,
      lake_name_raw: lake.name,
      lake_name_norm: normalizeName(lake.name),
      plss: r.plss,
      restriction_type: r.restriction_type,
      scope: r.scope,
      scope_description: r.scope_description,
      hours: r.hours,
      season: r.season,
      speed_mph: r.speed_mph,
      status: r.status ?? "active",
      clause: r.clause ?? null,
      signage_required: r.signage_required ?? false,
      related_rule_ids: r.related_rule_ids ?? [],
      raw_text: r.raw_text,
      source_url: SOURCE_URL,
      fetched_at: BUILT_AT,
      parser: "regex",
      needs_review: false,
      lake_ids: [lakeId(lake.key)],
    };
    restrictionsOut[id] = record;
    const list = byLakeKey.get(lake.key) ?? [];
    list.push(record);
    byLakeKey.set(lake.key, list);
  }

  // --- lakes --------------------------------------------------------------
  const index = [];
  const lakeFeatures = [];
  const usableFeatures = [];
  for (const lake of LAKES) {
    const id = lakeId(lake.key);
    const ring = lakeRing(lake.lon, lake.lat, lake.majorM, lake.minorM, lake.rot, id);
    const m = ringMetrics(ring, lake.lat);
    const restrictions = byLakeKey.get(lake.key) ?? [];
    const verdict = fixtureVerdict(restrictions, Boolean(lake.name));

    const flags = [];
    if (!lake.access) flags.push("no_public_access");
    if (m.chordFt < FALLBACK_RULES.aircraft.min_chord_ft) flags.push("chord_below_minimum");
    if (!lake.name) flags.push("needs_review");

    index.push({
      id,
      name: lake.name,
      name_norm: normalizeName(lake.name),
      county: lake.county,
      township: lake.township,
      lat: m.centroid[1],
      lon: m.centroid[0],
      bbox: m.bbox,
      area_acres: m.areaAcres,
      chord_ft: m.chordFt,
      chord_bearing_deg: m.chordBearingDeg,
      verdict,
      flags,
      restriction_ids: restrictions.map((r) => r.restriction_id),
      access: lake.access,
    });

    lakeFeatures.push(
      feature(id, { type: "Polygon", coordinates: [ring] }, {
        id,
        name: lake.name,
        verdict,
        flags: flags.join(","),
        county: lake.county,
      }),
    );

    const eroded = erodeRing(ring, m.centroid);
    if (eroded) {
      usableFeatures.push(
        feature(id, { type: "Polygon", coordinates: [eroded] }, { id }),
      );
    }
  }
  index.sort((a, b) => (a.name_norm || "￿").localeCompare(b.name_norm || "￿"));

  // --- overlays -----------------------------------------------------------
  const bas = index
    .filter((l) => l.access)
    .map((l, i) =>
      feature(9000 + i, { type: "Point", coordinates: [l.lon + 0.004, l.lat + 0.003] }, {
        name: l.access,
        kind: "boating_access_site",
        lake_id: l.id,
      }),
    );
  const airports = [
    { name: "Oakland County International", kind: "airport", id: "KPTK", lon: -83.4161, lat: 42.6655 },
    { name: "Oakland Southwest", kind: "airport", id: "Y47", lon: -83.5983, lat: 42.5306 },
    { name: "Oakland/Troy", kind: "airport", id: "KVLL", lon: -83.1781, lat: 42.5428 },
  ].map((a, i) =>
    feature(9100 + i, { type: "Point", coordinates: [a.lon, a.lat] }, {
      name: a.name,
      kind: a.kind,
      ident: a.id,
    }),
  );
  const airspace = [
    feature(
      9200,
      { type: "Polygon", coordinates: [circle(-83.4161, 42.6655, 7400)] },
      { name: "KPTK Class D", kind: "class_d", ceiling_ft: 3400 },
    ),
  ];
  const federal = [
    feature(
      9300,
      { type: "Polygon", coordinates: [circle(-83.6600, 42.4700, 4200)] },
      { name: "Fixture National Wildlife Refuge", kind: "usfws_refuge" },
    ),
  ];

  writeGeojson("lakes", lakeFeatures);
  writeGeojson("usable_water", usableFeatures);
  writeGeojson("bas", bas);
  writeGeojson("airports", airports);
  writeGeojson("airspace", airspace);
  writeGeojson("federal", federal);

  // --- json files ---------------------------------------------------------
  writeJson("index.json", index);
  writeJson("restrictions.json", restrictionsOut);

  const sharedRules = path.join(repoRoot, "rules/rules.json");
  if (existsSync(sharedRules)) {
    writeFileSync(path.join(outDir, "rules.json"), readFileSync(sharedRules));
    console.log("rules.json copied from ../rules/rules.json");
  } else {
    writeJson("rules.json", FALLBACK_RULES);
    console.log("rules.json written from the built-in fallback set (../rules/rules.json absent)");
  }

  // --- tiles --------------------------------------------------------------
  let tiled = true;
  try {
    execFileSync("tippecanoe", ["--version"], { stdio: "ignore" });
  } catch {
    tiled = false;
    console.warn("WARNING: tippecanoe not on PATH, skipping tile build (JSON fixtures written)");
  }
  if (tiled) {
    run("tippecanoe", [
      "-o", path.join(outDir, "lakes.pmtiles"), "-f", "-q",
      "-z14", "-Z6",
      "--no-feature-limit", "--no-tile-size-limit",
      "--detect-shared-borders", "--coalesce-densest-as-needed",
      "--extend-zooms-if-still-dropping",
      "-l", "lakes",
      path.join(geoDir, "lakes.geojson"),
    ]);
    run("tippecanoe", [
      "-o", path.join(outDir, "usable_water.pmtiles"), "-f", "-q",
      "-z14", "-Z12",
      "--no-feature-limit", "--no-tile-size-limit",
      "--extend-zooms-if-still-dropping",
      "-l", "usable_water",
      path.join(geoDir, "usable_water.geojson"),
    ]);
    run("tippecanoe", [
      "-o", path.join(outDir, "overlays.pmtiles"), "-f", "-q",
      "-z14", "-Z6",
      "--no-feature-limit", "--no-tile-size-limit",
      "-r1",
      "-L", `federal:${path.join(geoDir, "federal.geojson")}`,
      "-L", `airspace:${path.join(geoDir, "airspace.geojson")}`,
      "-L", `bas:${path.join(geoDir, "bas.geojson")}`,
      "-L", `airports:${path.join(geoDir, "airports.geojson")}`,
    ]);
  }

  // --- pack manifest ------------------------------------------------------
  const packFiles = [
    "index.json",
    "restrictions.json",
    "rules.json",
    "lakes.pmtiles",
    "usable_water.pmtiles",
    "overlays.pmtiles",
    "basemap.pmtiles",
  ];
  const files = [];
  for (const name of packFiles) {
    const p = path.join(outDir, name);
    if (!existsSync(p)) {
      console.warn(`note: ${name} missing, omitted from pack.json`);
      continue;
    }
    files.push({
      name,
      bytes: statSync(p).size,
      sha256: createHash("sha256").update(readFileSync(p)).digest("hex"),
    });
  }
  writeJson("pack.json", {
    version: BUILD_DATE,
    built_at: BUILT_AT,
    rules_version: BUILD_DATE,
    files,
    counts: {
      lakes: index.length,
      restrictions: Object.keys(restrictionsOut).length,
      needs_review: index.filter((l) => l.flags.includes("needs_review")).length,
    },
    mac_record_loaded: false,
  });

  console.log(`wrote ${index.length} lakes and ${Object.keys(restrictionsOut).length} restrictions to ${outDir}`);
  if (!existsSync(path.join(outDir, "basemap.pmtiles"))) {
    console.log(
      "\nbasemap.pmtiles is missing; the app runs without it. To fetch one (about 25 MB):\n" +
        `  cd ${outDir} && pmtiles extract https://build.protomaps.com/<YYYYMMDD>.pmtiles basemap.pmtiles --bbox=${BASEMAP_BBOX} --maxzoom=14`,
    );
  }
}

function circle(lon, lat, radiusM, points = 48) {
  const mLon = mPerDegLon(lat);
  const ring = [];
  for (let i = 0; i < points; i++) {
    const t = (i / points) * Math.PI * 2;
    ring.push([
      Number((lon + (Math.cos(t) * radiusM) / mLon).toFixed(6)),
      Number((lat + (Math.sin(t) * radiusM) / M_PER_DEG_LAT).toFixed(6)),
    ]);
  }
  ring.push([...ring[0]]);
  return ring;
}

function run(cmd, args) {
  console.log(`$ ${cmd} ${args.map((a) => (a.includes(" ") ? JSON.stringify(a) : a)).join(" ")}`);
  execFileSync(cmd, args, { stdio: ["ignore", "inherit", "inherit"] });
}

if (process.argv.includes("--print-basemap-cmd")) {
  console.log(
    `pmtiles extract https://build.protomaps.com/<YYYYMMDD>.pmtiles basemap.pmtiles --bbox=${BASEMAP_BBOX} --maxzoom=14`,
  );
} else {
  main();
}
