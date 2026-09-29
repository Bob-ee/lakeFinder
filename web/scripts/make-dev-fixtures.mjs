#!/usr/bin/env node
/**
 * Regenerates web/dev-fixtures/ so the client runs without the Python pipeline.
 *
 *   node web/scripts/make-dev-fixtures.mjs
 *
 * Writes (all conforming to docs/data-contract.md):
 *   index.json  restrictions.json  rules.json  pack.json      <- committed
 *   wave_points.json  wave_points.bin                          <- committed
 *   geojson/*.geojson                                          <- intermediate, gitignored
 *   lakes.pmtiles  usable_water.pmtiles  overlays.pmtiles      <- gitignored binaries
 *
 * This script only writes the files it lists; it never clears the directory. Two fixtures
 * are hand-maintained and must survive a regeneration: basemap.pmtiles and briefing.json
 * (the api service writes the real briefing, so the pipeline has nothing to derive it
 * from). If you ever add a clean step here, keep both.
 *
 * briefing.json is the one exception to "hand-maintained means untouched": its wave-field
 * fields (`kind`, `region`, `hs_in`, `run_ft`, `hs_open_in`, `regions`, and the whole
 * `home_water` block) are recomputed here from the wave points this script just generated,
 * so the card's region names always match the field the map is drawing. Everything else in
 * that file is left exactly as it was.
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
// The binary layout is written by the same module the client reads it with, so the fixture
// can never drift from the decoder. Hand-written bytes would only be a second guess at it.
import {
  DEPTH_UNKNOWN,
  encodeWavePoints,
  openWaterInches,
  regionsForWind,
} from "../../rules/waves/index.js";

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
// wave field (docs/data-contract.md, "Wave field")
// ---------------------------------------------------------------------------

/** Position descriptors, in the contract's order: index 0 is north, then clockwise. */
const SECTOR_LABELS = [
  "north end",
  "northeast side",
  "east end",
  "southeast side",
  "south end",
  "southwest side",
  "west end",
  "northwest side",
];

/** A ring in metres relative to `origin`, so every ray cast is plain plane geometry. */
function localRing(ring, origin, mLon) {
  const out = new Array(ring.length);
  for (let i = 0; i < ring.length; i++) {
    out[i] = [
      (ring[i][0] - origin[0]) * mLon,
      (ring[i][1] - origin[1]) * M_PER_DEG_LAT,
    ];
  }
  return out;
}

/**
 * Distance from the origin to the first crossing of `ring` along `bearingDeg` (0 = north,
 * clockwise). The ring is closed, so a ray from inside it always hits.
 */
function rayToRing(ring, bearingDeg, capM = 100_000) {
  const t = (bearingDeg * Math.PI) / 180;
  const dx = Math.sin(t);
  const dy = Math.cos(t);
  let best = capM;
  for (let i = 0; i < ring.length - 1; i++) {
    const [ax, ay] = ring[i];
    const [bx, by] = ring[i + 1];
    const ex = bx - ax;
    const ey = by - ay;
    const det = ex * dy - dx * ey;
    if (Math.abs(det) < 1e-9) continue;
    const hit = (ex * ay - ax * ey) / det;
    const along = (dx * ay - ax * dy) / det;
    if (hit > 0 && hit < best && along >= 0 && along <= 1) best = hit;
  }
  return best;
}

/** Ray crossing count; `ring` is closed (last point repeats the first). */
function insideRing(ring, lon, lat) {
  let inside = false;
  for (let i = 0, j = ring.length - 2; i < ring.length - 1; j = i++) {
    const [xi, yi] = ring[i];
    const [xj, yj] = ring[j];
    if (yi > lat !== yj > lat && lon < ((xj - xi) * (lat - yi)) / (yj - yi) + xi) {
      inside = !inside;
    }
  }
  return inside;
}

function metresBetween(aLon, aLat, bLon, bLat, mLon) {
  return Math.hypot((aLon - bLon) * mLon, (aLat - bLat) * M_PER_DEG_LAT);
}

/**
 * Sample points for one water body, following the contract: a regular grid inside the
 * usable water, `fetch[16]` as the mean of five rays across the whole polygon, `run[8]`
 * through the usable water in both directions, a label per point, and depth where the
 * fixture pretends to have a survey.
 *
 * The fetch rays run against the *full* ring, not the eroded one, because fetch is how far
 * the wind has had to work on the water; the run rays use the eroded ring, because that is
 * where you can actually put an aircraft.
 */
function waveFieldFor(lake, ring, eroded, metrics) {
  if (!eroded || metrics.areaAcres < 100) return [];
  const areaM2 = metrics.areaAcres * ACRE_M2;
  const target = metrics.areaAcres >= 10_000 ? 400 : 60;
  const spacing = Math.min(2000, Math.max(150, Math.sqrt(areaM2 / target)));
  const mLon = mPerDegLon(lake.lat);
  const dLon = spacing / mLon;
  const dLat = spacing / M_PER_DEG_LAT;

  const lons = eroded.map((p) => p[0]);
  const lats = eroded.map((p) => p[1]);
  // Anchored on a whole number of cells from the origin, so regenerating moves nothing.
  const startLon = Math.ceil(Math.min(...lons) / dLon) * dLon;
  const startLat = Math.ceil(Math.min(...lats) / dLat) * dLat;
  const maxLon = Math.max(...lons);
  const maxLat = Math.max(...lats);

  const points = [];
  for (let lat = startLat; lat <= maxLat; lat += dLat) {
    for (let lon = startLon; lon <= maxLon; lon += dLon) {
      if (!insideRing(eroded, lon, lat)) continue;
      const origin = [lon, lat];
      const full = localRing(ring, origin, mLon);
      const usable = localRing(eroded, origin, mLon);

      const fetch = [];
      for (let bin = 0; bin < 16; bin++) {
        let sum = 0;
        for (const offset of [-12, -6, 0, 6, 12]) sum += rayToRing(full, bin * 22.5 + offset);
        fetch.push(Math.min(0xffff, Math.round(sum / 5 / 10)));
      }
      const run = [];
      for (let bin = 0; bin < 8; bin++) {
        const length =
          rayToRing(usable, bin * 22.5) + rayToRing(usable, bin * 22.5 + 180);
        run.push(Math.min(0xffff, Math.round((length * FT_PER_M) / 10)));
      }

      points.push({
        lon: Number(lon.toFixed(6)),
        lat: Number(lat.toFixed(6)),
        depth_dm: depthAt(lake, ring, origin, mLon, metrics),
        label: labelAt(lake, ring, origin, mLon, metrics),
        fetch,
        run,
      });
    }
  }
  return points;
}

/**
 * A plausible bowl: deepest in the middle, shallowing to the shore. Only the water bodies
 * that declare `maxDepthM` get one, so the client exercises both the known-depth and the
 * unknown-depth branch of the formula.
 */
function depthAt(lake, ring, origin, mLon, metrics) {
  if (!lake.maxDepthM) return DEPTH_UNKNOWN;
  const bearing =
    (Math.atan2(
      (origin[0] - metrics.centroid[0]) * mLon,
      (origin[1] - metrics.centroid[1]) * M_PER_DEG_LAT,
    ) *
      180) /
    Math.PI;
  const fromCentre = metresBetween(
    origin[0],
    origin[1],
    metrics.centroid[0],
    metrics.centroid[1],
    mLon,
  );
  const toShore = rayToRing(localRing(ring, metrics.centroid, mLon), bearing);
  const frac = toShore > 0 ? Math.min(1, fromCentre / toShore) : 1;
  const depth = Math.max(0.6, lake.maxDepthM * Math.sqrt(Math.max(0, 1 - frac * frac)));
  return Math.min(0xfffe, Math.round(depth * 10));
}

/**
 * A named bay when the point is in one, otherwise a position descriptor: "middle" for the
 * open water in the centre, and a sector name for everything closer to one shore than the
 * other. The share of the way to the shore, rather than a flat distance, is what makes the
 * same rule work on a round pond and on a long channel.
 */
function labelAt(lake, ring, origin, mLon, metrics) {
  for (const bay of lake.bays ?? []) {
    if (metresBetween(origin[0], origin[1], bay.lon, bay.lat, mLon) <= bay.radiusM) {
      return bay.name;
    }
  }
  const dx = (origin[0] - metrics.centroid[0]) * mLon;
  const dy = (origin[1] - metrics.centroid[1]) * M_PER_DEG_LAT;
  const bearing = (Math.atan2(dx, dy) * 180) / Math.PI;
  const toShore = rayToRing(localRing(ring, metrics.centroid, mLon), bearing);
  const frac = toShore > 0 ? Math.hypot(dx, dy) / toShore : 1;
  if (frac < 0.45) return "middle";
  return SECTOR_LABELS[Math.round(((bearing % 360) + 360) % 360 / 45) % 8];
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
  // Two `kind: "river"` polygons sharing one name, so fixture mode exercises the river label,
  // the repeated-name search rows and the reach_unresolved notice.
  {
    key: "MIGF-HYDRO-0100011",
    name: "Huron River",
    kind: "river",
    county: "Oakland",
    township: "Milford Township",
    lon: -83.6008,
    lat: 42.5773,
    majorM: 1400,
    minorM: 110,
    rot: 115,
    access: null,
  },
  {
    key: "MIGF-HYDRO-0100012",
    name: "Huron River",
    kind: "river",
    county: "Oakland",
    township: "Commerce Township",
    lon: -83.5120,
    lat: 42.5622,
    majorM: 900,
    minorM: 90,
    rot: 80,
    access: null,
  },
  // Big water. Deliberately fictional names in a fictional place: the real Great Lakes
  // polygons come from a different source than this county hydrography, and a fixture that
  // said "Lake St. Clair" while drawing a blob in Oakland County would be a lie the first
  // time somebody looked at it. What matters is that the two kinds exist, that `county` is
  // null on them the way the contract says it may be, and that they are big enough to carry
  // a real wave field.
  {
    key: "MIGF-BIGWATER-0200001",
    name: "Big Fixture Lake",
    kind: "great_lake",
    county: null,
    counties: ["Oakland", "Wayne", "Macomb"],
    township: null,
    lon: -83.14,
    lat: 42.47,
    majorM: 17000,
    minorM: 11000,
    rot: 30,
    access: "Big Fixture Lake Metropark BAS",
    // Shallow, like the water this feature was built for: depth is what keeps a big fetch
    // from turning into a big wave, and the sheet has to show that branch.
    maxDepthM: 3.6,
    bays: [
      { name: "Anchor Bay", lon: -83.10, lat: 42.515, radiusM: 3400 },
      { name: "Muscamoot Bay", lon: -83.205, lat: 42.437, radiusM: 2800 },
    ],
  },
  {
    key: "MIGF-BIGWATER-0200002",
    name: "Fixture Channel",
    kind: "connecting_water",
    county: null,
    counties: ["Oakland", "Wayne"],
    township: null,
    lon: -83.30,
    lat: 42.43,
    majorM: 9000,
    minorM: 900,
    rot: 200,
    access: null,
    maxDepthM: 8.0,
    // index.json carries `federal_unit` on the ~170 entries inside one; the client names it
    // on the chip and in the Federal fact row instead of saying "a federal unit".
    federalUnit: "Fixture National Wildlife Refuge",
  },
];

/** `lake` is the fixture key above; the pipeline would fill lake_ids via the match stage. */
const RESTRICTIONS = [
  {
    // Big water: a rule that covers part of it, which the shared engine caps at conditional.
    lake: "MIGF-BIGWATER-0200002",
    reach_unresolved: true,
    big_water_partial: true,
    rule_id: "R 281.799.4",
    restriction_type: "slow_no_wake",
    scope: "lakewide",
    scope_description: null,
    hours: null,
    season: null,
    speed_mph: null,
    plss: [],
    raw_text:
      "It is unlawful for the operator of a vessel to exceed a slow, no wake speed upon the waters of the Fixture Channel within the marked channel between buoy 12 and the highway bridge.",
  },
  {
    lake: "MIGF-HYDRO-0100011",
    also: ["MIGF-HYDRO-0100012"],
    reach_unresolved: true,
    rule_id: "R 281.763.21",
    restriction_type: "slow_no_wake",
    scope: "lakewide",
    scope_description: null,
    hours: null,
    season: null,
    speed_mph: null,
    plss: [],
    raw_text:
      "It is unlawful for the operator of a vessel to exceed a slow, no wake speed upon the waters of the Huron River from the Commerce Road bridge downstream to the Oakland County line, Oakland County.",
  },
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
    // Same cap the shared engine applies: a rule that covers part of a Great Lake or a
    // connecting water cannot turn the whole thing red.
    if (r.big_water_partial && v === "restricted") v = "conditional";
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
    // `also` is the waterway case: one river rule covering several same-name polygons.
    const keys = [r.lake, ...(r.also ?? [])];
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
      lake_ids: keys.map(lakeId),
      ...(r.reach_unresolved ? { reach_unresolved: true } : {}),
      ...(r.big_water_partial ? { big_water_partial: true } : {}),
    };
    restrictionsOut[id] = record;
    for (const key of keys) {
      const list = byLakeKey.get(key) ?? [];
      list.push(record);
      byLakeKey.set(key, list);
    }
  }

  // --- lakes --------------------------------------------------------------
  const index = [];
  const lakeFeatures = [];
  const usableFeatures = [];
  /** id -> sample points, in the order they go into wave_points.bin. */
  const waveFields = new Map();
  /** id -> name_norm, kept out of the written index.json (data-contract.md) but needed to sort it. */
  const norms = new Map();
  for (const lake of LAKES) {
    const id = lakeId(lake.key);
    const ring = lakeRing(lake.lon, lake.lat, lake.majorM, lake.minorM, lake.rot, id);
    const m = ringMetrics(ring, lake.lat);
    const restrictions = byLakeKey.get(lake.key) ?? [];
    const verdict = fixtureVerdict(restrictions, Boolean(lake.name));

    const flags = [];
    if (restrictions.some((r) => r.reach_unresolved)) flags.push("reach_unresolved");
    if (lake.federalUnit) flags.push("federal_overlay");
    if (!lake.access) flags.push("no_public_access");
    if (m.chordFt < FALLBACK_RULES.aircraft.min_chord_ft) flags.push("chord_below_minimum");
    if (!lake.name) flags.push("needs_review");

    norms.set(id, normalizeName(lake.name));
    index.push({
      id,
      name: lake.name,
      kind: lake.kind ?? "lake",
      county: lake.county ?? null,
      ...(lake.counties ? { counties: lake.counties } : {}),
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
      ...(lake.federalUnit ? { federal_unit: lake.federalUnit } : {}),
    });

    lakeFeatures.push(
      feature(id, { type: "Polygon", coordinates: [ring] }, {
        id,
        name: lake.name,
        kind: lake.kind ?? "lake",
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

    const points = waveFieldFor(lake, ring, eroded, m);
    if (points.length > 0) waveFields.set(id, points);
  }
  index.sort((a, b) => (norms.get(a.id) || "￿").localeCompare(norms.get(b.id) || "￿"));

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

  // --- wave field ---------------------------------------------------------
  const waveIndex = writeWaveField(waveFields);
  updateBriefingFixture(index, waveFields, waveIndex);

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
    "wave_points.json",
    "wave_points.bin",
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

/**
 * Writes `wave_points.bin` and its index. Labels are pooled across every water body, which
 * is what the contract's shared `labels` array is for, and records of one water body stay
 * contiguous so the client can fetch them with a single Range request.
 */
function writeWaveField(waveFields) {
  const labels = [];
  const labelIndex = new Map();
  const records = [];
  const lakes = {};

  for (const [id, points] of waveFields) {
    lakes[String(id)] = [records.length, points.length];
    for (const p of points) {
      let at = labelIndex.get(p.label);
      if (at == null) {
        at = labels.length;
        labels.push(p.label);
        labelIndex.set(p.label, at);
      }
      records.push({ ...p, label: at });
    }
  }

  const index = {
    version: 1,
    record_bytes: 60,
    fetch_unit_m: 10,
    run_unit_ft: 10,
    labels,
    lakes,
  };
  writeJson("wave_points.json", index);
  writeFileSync(path.join(outDir, "wave_points.bin"), encodeWavePoints(records));
  console.log(
    `wave field: ${records.length} points over ${waveFields.size} water bodies, ` +
      `${labels.length} labels, ${records.length * 60} bytes`,
  );
  return { index, records };
}

/**
 * Refreshes the wave-field parts of the hand-maintained briefing fixture from the field
 * just generated, so the card's regions, the sheet's list and the map overlay all name the
 * same places. Rows whose water body has no wave field keep their lake-level numbers, which
 * is exactly what the api does.
 */
function updateBriefingFixture(index, waveFields, wave) {
  const file = path.join(outDir, "briefing.json");
  if (!existsSync(file)) {
    console.warn("note: briefing.json absent, wave-field fields not written");
    return;
  }
  const briefing = JSON.parse(readFileSync(file, "utf8"));
  const byId = new Map(index.map((l) => [l.id, l]));

  const pointsFor = (id) => {
    const entry = wave.index.lakes[String(id)];
    return entry ? wave.records.slice(entry[0], entry[0] + entry[1]) : null;
  };

  /** Rank stands in for the api's scoring: the fixture only exercises the colour bar. */
  const shapeRegion = (region, pts, rank) => {
    const point = pts[region.point ?? 0];
    return {
      label: region.label,
      hs_in: region.hs_in,
      run_ft: region.run_ft,
      lat: Number(point.lat.toFixed(5)),
      lon: Number(point.lon.toFixed(5)),
      score:
        region.hs_in == null ? "unfavorable" : rank === 0 ? "favorable" : rank <= 2 ? "marginal" : "unfavorable",
    };
  };

  /** Fills one ranked row from its own wind; `maxRegions` is 4 per the contract. */
  const fillRow = (row, maxRegions) => {
    const lake = byId.get(row.id);
    if (lake) row.kind = lake.kind;
    const pts = pointsFor(row.id);
    if (!pts) return;
    const regions = regionsForWind(pts, wave.index.labels, row.wind.dir, row.wind.kt, 2000);
    const best = regions.find((r) => r.hs_in != null);
    row.region = best ? best.label : null;
    row.hs_open_in = openWaterInches(regions);
    if (best) {
      row.hs_in = best.hs_in;
      row.run_ft = best.run_ft;
    }
    row.regions = regions.slice(0, maxRegions).map((r, i) => shapeRegion(r, pts, i));
  };

  for (const row of briefing.lakes ?? []) fillRow(row, 4);
  for (const row of briefing.outlook?.lakes ?? []) fillRow(row, 4);

  /** Home water lists every region, and the live one carries the second opinions. */
  const homeWaterRow = (lake, wind, withObserved) => {
    const pts = pointsFor(lake.id) ?? [];
    const regions = regionsForWind(pts, wave.index.labels, wind.dir, wind.kt, 2000);
    const best = regions.find((r) => r.hs_in != null);
    const calm = best != null && best.hs_in <= 8;
    const row = {
      id: lake.id,
      name: lake.name,
      kind: lake.kind,
      score: calm ? "favorable" : "marginal",
      limiting: calm ? null : "waves",
      hs_in: best ? best.hs_in : 0,
      run_ft: best ? best.run_ft : 0,
      region: best ? best.label : null,
      hs_open_in: openWaterInches(regions),
      regions: regions.map((r, i) => shapeRegion(r, pts, i)),
      wind,
      distance_nm: 18.4,
      bearing_deg: 122,
      verdict: lake.verdict,
      frozen: false,
    };
    if (withObserved) {
      row.observed = [
        {
          station: "45147",
          name: "Big Fixture Lake buoy",
          kind: "buoy",
          at: "2026-09-19T13:00:00Z",
          wind: { dir: 245, kt: 12, gust: 16 },
          wave_ft: 1.0,
          distance_nm: 6.2,
        },
        {
          station: "KFXR",
          name: "Fixture Shore AWOS",
          kind: "metar",
          at: "2026-09-19T12:53:00Z",
          wind: { dir: 260, kt: 9, gust: null },
          wave_ft: null,
          distance_nm: 11.7,
        },
      ];
      row.marine_hs_in = 12;
    }
    return row;
  };

  // The biggest field is the great_lake fixture, which is the one worth briefing whole.
  const homeId = [...waveFields.keys()].sort(
    (a, b) => (waveFields.get(b)?.length ?? 0) - (waveFields.get(a)?.length ?? 0),
  )[0];
  const home = homeId == null ? null : byId.get(homeId);
  if (home) {
    briefing.home_water = homeWaterRow(home, { dir: 250, kt: 11, gust: 16 }, true);
    if (briefing.outlook) {
      briefing.outlook.home_water = homeWaterRow(home, { dir: 230, kt: 8, gust: 12 }, false);
    }
  }

  // The timeline hangs off the real clock (its axis is "6 h before now to 72 h after"), so
  // the run it belongs to is dated now too, with the day and outlook dates following it.
  const genMs = Date.now();
  briefing.generated_at = new Date(genMs - 10 * 60_000).toISOString().replace(/\.\d+Z$/, "Z");
  const today = localIso(genMs, briefing.timezone).slice(0, 10);
  const plusDays = (iso, n) => new Date(Date.parse(iso) + n * 86_400_000).toISOString().slice(0, 10);
  (briefing.days ?? []).forEach((d, i) => (d.date = plusDays(today, i)));
  if (briefing.outlook) briefing.outlook.target_date = plusDays(today, 1);
  briefing.timeline = home
    ? syntheticTimeline(briefing, home, pointsFor(home.id) ?? [], wave.index.labels)
    : null;

  writeFileSync(file, JSON.stringify(briefing, null, 1) + "\n");
  console.log(
    `briefing.json refreshed with wave-field fields${home ? ` and home water "${home.name}"` : ""}` +
      `${briefing.timeline ? `, timeline of ${briefing.timeline.hours.length} h` : ""}`,
  );
}

// ---------------------------------------------------------------------------
// the forecast timeline (contract: "Forecast timeline and waves over time")
// ---------------------------------------------------------------------------

/**
 * Local ISO 8601 with offset for an instant in `tz`: "2026-09-29T06:00:00-04:00".
 * Intl's `longOffset` gives "GMT-04:00", which is all the offset arithmetic needed.
 */
function localIso(ms, tz) {
  const parts = {};
  for (const p of new Intl.DateTimeFormat("en-US", {
    timeZone: tz,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hourCycle: "h23",
    timeZoneName: "longOffset",
  }).formatToParts(new Date(ms))) {
    parts[p.type] = p.value;
  }
  const off = parts.timeZoneName === "GMT" ? "+00:00" : parts.timeZoneName.slice(3);
  return `${parts.year}-${parts.month}-${parts.day}T${parts.hour}:${parts.minute}:${parts.second}${off}`;
}

/**
 * Wind keyframes `[day, hour, dir, kt, gust]`, day 0 being the date of the generation hour,
 * interpolated hour by hour. The story it tells: a breezy today, a foggy calm tomorrow
 * morning that turns into a favorable late morning and a gusty westerly afternoon, a
 * light northerly morning the day after, then a windy last day.
 */
const WIND_KEYS = [
  [-1, 12, 250, 12, 18],
  [0, 0, 240, 7, 10],
  [0, 9, 250, 10, 15],
  [0, 15, 260, 13, 21],
  [0, 21, 230, 6, 9],
  [1, 3, 200, 3, 4],
  [1, 8, 190, 4, 6],
  [1, 11, 210, 7, 10],
  [1, 13, 240, 9, 13],
  [1, 15, 270, 14, 23],
  [1, 18, 280, 13, 21],
  [1, 22, 300, 7, 10],
  [2, 6, 340, 5, 7],
  [2, 11, 10, 7, 10],
  [2, 15, 30, 12, 18],
  [2, 21, 40, 8, 12],
  [3, 6, 60, 13, 19],
  [3, 14, 70, 18, 27],
  [4, 6, 70, 14, 22],
];

function windAt(dayHour) {
  const keys = WIND_KEYS.map(([d, h, dir, kt, gust]) => ({ x: d * 24 + h, dir, kt, gust }));
  if (dayHour <= keys[0].x) return keys[0];
  for (let k = 1; k < keys.length; k++) {
    const a = keys[k - 1];
    const b = keys[k];
    if (dayHour > b.x) continue;
    const f = (dayHour - a.x) / (b.x - a.x);
    const turn = ((b.dir - a.dir + 540) % 360) - 180;
    return {
      dir: Math.round((a.dir + turn * f + 360) % 360),
      kt: Math.round(a.kt + (b.kt - a.kt) * f),
      gust: Math.round(a.gust + (b.gust - a.gust) * f),
    };
  }
  return keys[keys.length - 1];
}

/** Airport score for one synthetic hour, in the spirit of the briefing's table. */
function airportScore(wind, fog, vis) {
  const spread = wind.gust - wind.kt;
  if (fog && vis < 2) return ["unfavorable", "fog"];
  if (wind.kt > 18 || spread >= 10) return ["unfavorable", spread >= 10 ? "gusts" : "wind"];
  if (fog) return ["marginal", "fog"];
  if (wind.kt > 12 || spread >= 7) return ["marginal", spread >= 7 ? "gusts" : "wind"];
  return ["favorable", null];
}

function syntheticTimeline(briefing, home, points, allLabels) {
  const tz = briefing.timezone;
  const HOUR = 3_600_000;
  const nowHour = Math.floor(Date.now() / HOUR) * HOUR;
  const start = nowHour - 6 * HOUR;
  const todayDate = localIso(nowHour, tz).slice(0, 10);
  const dayOf = (iso) => Math.round((Date.parse(iso.slice(0, 10)) - Date.parse(todayDate)) / 86_400_000);

  // Each region gets its own wind, as its forecast cell would: a knot or two either way.
  const regionLabels = [...new Set(regionsForWind(points, allLabels, 270, 10, 2000).map((r) => r.label))].sort();
  const okIn = 8;
  const maxIn = 12;
  const minWindow = 2;

  const hours = [];
  const hw = {
    id: home.id,
    name: home.name,
    labels: regionLabels,
    hs_in: [],
    wind: [],
    best: [],
    open_in: [],
    score: [],
    limiting: [],
    observed: [],
    marine_in: [],
  };

  for (let i = 0; i < 79; i++) {
    const ms = start + i * HOUR;
    const t = localIso(ms, tz);
    const hh = Number(t.slice(11, 13));
    const day = dayOf(t);
    const wind = windAt(day * 24 + hh);
    const daylight = hh >= 7 && hh < 20;
    const fog = day === 1 && hh >= 4 && hh <= 9;
    const vis = fog ? (hh <= 7 ? 0.5 : 2) : 10;
    const [score, limiting] = airportScore(wind, fog, vis);
    hours.push({
      t,
      past: i < 6,
      daylight,
      score,
      limiting,
      wind,
      model: day <= 2 ? "ncep_nbm_conus" : "best_match",
      ceiling_ft: fog ? (hh <= 7 ? 200 : 700) : day === 3 ? 3500 : null,
      ceiling_known: i < 8,
      vis_sm: vis,
      fog_risk: fog,
      precip_prob: day === 3 ? 60 : fog ? 10 : 5,
      temp_f: Math.round(52 + 12 * Math.sin(((hh - 9) / 24) * 2 * Math.PI)),
    });

    // Waves at the gust, each region from its own wind.
    const perLabel = regionLabels.map((label, k) => {
      const rw = { dir: (wind.dir + (k % 3) * 5) % 360, kt: wind.kt + (k % 2), gust: wind.gust + (k % 3) };
      const region = regionsForWind(points, allLabels, rw.dir, rw.gust, 2000).find((r) => r.label === label);
      return { rw, region };
    });
    const hsRow = perLabel.map(({ region }) => (region ? region.hs_in : null));
    hw.hs_in.push(hsRow);
    hw.wind.push(perLabel.map(({ rw }) => rw));
    let best = null;
    hsRow.forEach((v, k) => {
      if (v != null && (best == null || v < best.hs_in)) best = { label: regionLabels[k], hs_in: v };
    });
    hw.best.push(best);
    const open = Math.max(...perLabel.map(({ region }) => region?.hs_all_in ?? 0));
    hw.open_in.push(open);
    const ws = best == null ? "unfavorable" : best.hs_in <= okIn ? "favorable" : best.hs_in <= maxIn ? "marginal" : "unfavorable";
    hw.score.push(ws);
    hw.limiting.push(ws === "favorable" ? null : best == null ? "run" : "waves");
    hw.marine_in.push(Math.round(open * 0.8));
    // A few buoy reports in the past hours, one missing, reading a little under the model.
    if (i < 6 && i !== 2) {
      hw.observed.push({
        t,
        station: "45147",
        wave_in: Math.max(1, Math.round(open * 0.7)),
        wind: { dir: (wind.dir + 10) % 360, kt: Math.max(0, wind.kt - 1), gust: i % 2 ? null : wind.gust },
      });
    }
  }

  // Windows: runs of daylight, non-past hours whose combined score is favorable (or, on a
  // day with none, marginal), at least `minWindow` long, never across dusk.
  const RANK = { favorable: 0, marginal: 1, unfavorable: 2 };
  const combinedAt = (i) => {
    const a = hours[i];
    const w = hw.score[i];
    return RANK[w] > RANK[a.score] ? { score: w, limiting: hw.limiting[i] } : { score: a.score, limiting: a.limiting };
  };
  const windows = [];
  const days = [...new Set(hours.map((h) => h.t.slice(0, 10)))];
  for (const date of days) {
    const idx = hours.map((h, i) => i).filter((i) => hours[i].t.startsWith(date) && hours[i].daylight && !hours[i].past);
    const found = (target) => {
      const out = [];
      let run = [];
      const close = () => {
        if (run.length >= minWindow) out.push([run[0], run[run.length - 1] + 1]);
        run = [];
      };
      for (const i of idx) {
        if (combinedAt(i).score !== target) {
          close();
          continue;
        }
        if (run.length > 0 && i !== run[run.length - 1] + 1) close();
        run.push(i);
      }
      close();
      return out;
    };
    let runs = found("favorable");
    let score = "favorable";
    if (runs.length === 0) {
      runs = found("marginal");
      score = "marginal";
    }
    for (const [a, b] of runs) {
      const after = b < hours.length && hours[b].daylight ? combinedAt(b).limiting : null;
      windows.push({
        start: hours[a].t,
        end: b < hours.length ? hours[b].t : localIso(start + b * HOUR, tz),
        score,
        limiting_after: after,
      });
    }
  }

  return { hours, windows, home_water: hw };
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
