// Wave field maths for the Michigan seaplane lake map.
//
// Zero dependencies, ESM, runs unmodified in Node (tests, the dev-fixture
// generator) and in modern browsers (the client imports this module directly).
// See docs/data-contract.md section "Wave field" for the authoritative
// definitions this file implements: the SPM 1984 fetch-limited formula, the
// 16-point wind binning, the `wave_points.bin` record layout and the region
// aggregation rule.
//
// The api service (`api/`, Python) implements the same three things. Both sides
// are pinned to the shared fixtures in rules/fixtures/waves.json and
// rules/fixtures/wave_points.sample.bin|json; if you change anything here,
// change the contract first and make both sides pass those fixtures again.

const G = 9.80665;
const KT_TO_MS = 0.514444;

/** Fully developed caps on the dimensionless height and period (SPM 1984). */
const H_CAP = 0.2433;
const T_CAP = 8.134;

/** `depth_dm` sentinel: no bathymetry covered this point. */
export const DEPTH_UNKNOWN = 0xffff;

/** Record layout of `wave_points.bin`: struct "<ffHH16H8H". */
export const RECORD_BYTES = 60;
/** `fetch[16]` is stored in units of 10 m, `run[8]` in units of 10 ft. */
export const FETCH_UNIT_M = 10;
export const RUN_UNIT_FT = 10;

const INCH_M = 0.0254;

/**
 * A decoded sample point. The field names match the JSON the Python reference
 * implementation writes (rules/fixtures/make_wave_fixtures.py), so a fixture
 * row and a decoded record are the same shape and the tests can use either.
 *
 * @typedef {Object} WavePoint
 * @property {number} lon
 * @property {number} lat
 * @property {number} depth_dm depth in decimetres, {@link DEPTH_UNKNOWN} when unknown
 * @property {number} label index into the index file's `labels`
 * @property {ArrayLike<number>} fetch 16 bearing bins, units of {@link FETCH_UNIT_M}
 * @property {ArrayLike<number>} run 8 bearing bins, units of {@link RUN_UNIT_FT}
 */

/**
 * `wave_points.json`, the index that says where each water body's records live.
 *
 * @typedef {Object} WavePointsIndex
 * @property {number} version
 * @property {number} record_bytes
 * @property {number} fetch_unit_m
 * @property {number} run_unit_ft
 * @property {string[]} labels
 * @property {Record<string, [number, number]>} lakes `id` -> [first_record, count]
 */

/**
 * One region: every point of a water body that shares a label.
 *
 * @typedef {Object} WaveRegion
 * @property {string} label
 * @property {number} n_points
 * @property {number} n_usable points whose run into this wind meets `min_run_ft`
 * @property {number} hs_all_in 75th percentile over ALL the label's points, whole inches
 * @property {number|null} hs_in the same percentile over the usable points; null when none are usable
 * @property {number|null} run_ft lower median of the usable runs
 * @property {number|null} point index into the `points` array of the calmest usable point
 */

// -- wave height ------------------------------------------------------------

/**
 * SPM 1984 fetch-limited significant wave height and peak period.
 *
 * With no depth this is the deep-water form, which overstates waves in shallow
 * water: the conservative direction, and the one most of the country is in
 * because there is no bathymetry for it.
 *
 * @param {number} windKt wind speed in knots
 * @param {number} fetchM fetch in metres
 * @param {number|null|undefined} [depthM] depth in metres, or null/undefined when unknown
 * @returns {{hsM: number, tpS: number}} significant height in metres, peak period in seconds
 */
export function waveHeight(windKt, fetchM, depthM) {
  if (!(windKt > 0) || !(fetchM > 0)) return { hsM: 0, tpS: 0 };

  const ua = 0.71 * Math.pow(windKt * KT_TO_MS, 1.23);
  const ua2 = ua * ua;
  const f = (G * fetchM) / ua2;

  let h;
  let t;
  if (depthM == null) {
    h = 1.6e-3 * Math.sqrt(f);
    t = 0.2857 * Math.cbrt(f);
  } else {
    const d = (G * Math.max(depthM, 0.1)) / ua2;
    const th = Math.tanh(0.53 * Math.pow(d, 0.75));
    h = 0.283 * th * Math.tanh((0.00565 * Math.sqrt(f)) / th);
    const tt = Math.tanh(0.833 * Math.pow(d, 0.375));
    t = 7.54 * tt * Math.tanh((0.0379 * Math.cbrt(f)) / tt);
  }

  h = Math.min(h, H_CAP);
  t = Math.min(t, T_CAP);
  return { hsM: (h * ua2) / G, tpS: (t * ua) / G };
}

/**
 * Bearing bin for a wind direction. `wind_dir` is the direction the wind blows
 * FROM, in degrees true, and bin `i` is centred on `i * 22.5`.
 *
 * @param {number} deg
 * @returns {number} 0-15
 */
export function windBin(deg) {
  const raw = Math.floor(deg / 22.5 + 0.5);
  return ((raw % 16) + 16) % 16;
}

/** Metres to whole inches, contract rounding: `floor(x / 0.0254 + 0.5)`. */
export function metresToInches(m) {
  return Math.floor(m / INCH_M + 0.5);
}

// -- wave_points.bin --------------------------------------------------------

/** Little-endian host? The fast path below reads u16 arrays directly. */
const LITTLE_ENDIAN = (() => {
  const probe = new Uint8Array(2);
  new Uint16Array(probe.buffer)[0] = 1;
  return probe[0] === 1;
})();

function asBufferView(source) {
  if (ArrayBuffer.isView(source)) {
    return { buffer: source.buffer, byteOffset: source.byteOffset, byteLength: source.byteLength };
  }
  return { buffer: source, byteOffset: 0, byteLength: source.byteLength };
}

/**
 * Decodes `wave_points.bin` records.
 *
 * `buffer` may be the whole file or the slice one water body's HTTP Range
 * request returned. With `lakeId` given, the index says which records belong to
 * it; if the buffer is long enough to be the whole file the records are read at
 * `first * record_bytes`, and otherwise the buffer is taken to start at that
 * water body's first record, which is what a 206 response contains.
 *
 * @param {ArrayBuffer|ArrayBufferView} buffer
 * @param {WavePointsIndex} index parsed `wave_points.json`
 * @param {number|string|null} [lakeId] decode only this water body's records
 * @returns {WavePoint[]} empty when the index has no entry for `lakeId`
 */
export function decodeWavePoints(buffer, index, lakeId) {
  const recordBytes = index?.record_bytes ?? RECORD_BYTES;
  if (recordBytes !== RECORD_BYTES) {
    throw new Error(`wave_points record_bytes ${recordBytes} is not the contract's ${RECORD_BYTES}`);
  }
  const view = asBufferView(buffer);
  const available = Math.floor(view.byteLength / recordBytes);

  let first = 0;
  let count = available;
  if (lakeId != null) {
    const entry = index?.lakes?.[String(lakeId)];
    if (!entry) return [];
    const [entryFirst, entryCount] = entry;
    // A Range response holds only this water body; the whole file holds everything.
    first = available >= entryFirst + entryCount ? entryFirst : 0;
    count = Math.max(0, Math.min(entryCount, available - first));
  }

  const dv = new DataView(view.buffer, view.byteOffset, view.byteLength);
  /** @type {WavePoint[]} */
  const out = new Array(count);
  for (let i = 0; i < count; i++) {
    const base = (first + i) * recordBytes;
    let fetch;
    let run;
    if (LITTLE_ENDIAN) {
      // Records are 60 bytes from an even offset, so the u16 block is aligned.
      const u16 = new Uint16Array(view.buffer, view.byteOffset + base + 8, 26);
      fetch = u16.subarray(2, 18);
      run = u16.subarray(18, 26);
    } else {
      fetch = new Uint16Array(16);
      run = new Uint16Array(8);
      for (let k = 0; k < 16; k++) fetch[k] = dv.getUint16(base + 12 + k * 2, true);
      for (let k = 0; k < 8; k++) run[k] = dv.getUint16(base + 44 + k * 2, true);
    }
    out[i] = {
      lon: dv.getFloat32(base, true),
      lat: dv.getFloat32(base + 4, true),
      depth_dm: dv.getUint16(base + 8, true),
      label: dv.getUint16(base + 10, true),
      fetch,
      run,
    };
  }
  return out;
}

function u16(value) {
  const n = Math.round(Number(value) || 0);
  return n < 0 ? 0 : n > 0xffff ? 0xffff : n;
}

/**
 * Encodes sample points back into the contract's binary layout. The pipeline
 * writes the real file in Python; this exists so the dev fixtures are generated
 * by the same code that reads them instead of by hand-written bytes, and so the
 * tests can prove the two directions agree.
 *
 * @param {WavePoint[]} points
 * @returns {Uint8Array}
 */
export function encodeWavePoints(points) {
  const bytes = new Uint8Array(points.length * RECORD_BYTES);
  const dv = new DataView(bytes.buffer);
  points.forEach((p, i) => {
    const base = i * RECORD_BYTES;
    dv.setFloat32(base, p.lon, true);
    dv.setFloat32(base + 4, p.lat, true);
    dv.setUint16(base + 8, u16(p.depth_dm), true);
    dv.setUint16(base + 10, u16(p.label), true);
    for (let k = 0; k < 16; k++) dv.setUint16(base + 12 + k * 2, u16(p.fetch[k]), true);
    for (let k = 0; k < 8; k++) dv.setUint16(base + 44 + k * 2, u16(p.run[k]), true);
  });
  return bytes;
}

// -- regions ----------------------------------------------------------------

/** 75th percentile, nearest rank: sorted ascending, index `ceil(0.75 n) - 1`. */
function percentile75(sorted) {
  return sorted[Math.max(0, Math.ceil(0.75 * sorted.length) - 1)];
}

/** Lower median: sorted ascending, index `(n - 1) >> 1`. */
function medianLow(sorted) {
  return sorted[(sorted.length - 1) >> 1];
}

function ascending(a, b) {
  return a - b;
}

/**
 * Aggregates a water body's sample points into regions for one wind.
 *
 * Points sharing a label are one region. A point is `usable` when its run along
 * the wind bin meets `minRunFt`; a region with no usable point still reports its
 * open-water figure (`hs_all_in`) but has no `hs_in`, and sorts last.
 *
 * @param {WavePoint[]} points one water body's points, in record order
 * @param {string[]} labels the index file's `labels`
 * @param {number} windDir degrees true the wind is FROM
 * @param {number} windKt
 * @param {number} minRunFt
 * @returns {WaveRegion[]} calm to rough, regions with no usable run last
 */
export function regionsForWind(points, labels, windDir, windKt, minRunFt) {
  const bin = windBin(windDir);
  const runBin = bin % 8;

  /** @type {Map<number, {hs: number[], run: number[], idx: number[]}>} */
  const groups = new Map();
  for (let i = 0; i < points.length; i++) {
    const p = points[i];
    const depth = p.depth_dm === DEPTH_UNKNOWN ? null : p.depth_dm / 10;
    const { hsM } = waveHeight(windKt, p.fetch[bin] * FETCH_UNIT_M, depth);
    const runFt = p.run[runBin] * RUN_UNIT_FT;
    let group = groups.get(p.label);
    if (!group) {
      group = { hs: [], run: [], idx: [] };
      groups.set(p.label, group);
    }
    group.hs.push(hsM);
    group.run.push(runFt);
    group.idx.push(i);
  }

  /** @type {WaveRegion[]} */
  const out = [];
  for (const [labelIndex, group] of groups) {
    const label = labels?.[labelIndex] ?? String(labelIndex);
    const usableHs = [];
    const usableRun = [];
    let bestAt = -1;
    for (let k = 0; k < group.hs.length; k++) {
      if (group.run[k] < minRunFt) continue;
      usableHs.push(group.hs[k]);
      usableRun.push(group.run[k]);
      // Ties go to the lowest record index, and `idx` is already ascending.
      if (bestAt < 0 || group.hs[k] < group.hs[bestAt]) bestAt = k;
    }
    /** @type {WaveRegion} */
    const region = {
      label,
      n_points: group.hs.length,
      n_usable: usableHs.length,
      hs_all_in: metresToInches(percentile75([...group.hs].sort(ascending))),
      hs_in: null,
      run_ft: null,
      point: null,
    };
    if (usableHs.length > 0) {
      region.hs_in = metresToInches(percentile75(usableHs.slice().sort(ascending)));
      region.run_ft = medianLow(usableRun.slice().sort(ascending));
      region.point = group.idx[bestAt];
    }
    out.push(region);
  }

  out.sort((a, b) => {
    const aNull = a.hs_in == null;
    const bNull = b.hs_in == null;
    if (aNull !== bNull) return aNull ? 1 : -1;
    const diff = (a.hs_in ?? 0) - (b.hs_in ?? 0);
    if (diff !== 0) return diff;
    // Code-unit order, to match Python's plain string sort.
    return a.label < b.label ? -1 : a.label > b.label ? 1 : 0;
  });
  return out;
}

/**
 * The water body's open-water figure: the roughest region's `hs_all_in`.
 *
 * @param {WaveRegion[]} regions
 * @returns {number|null}
 */
export function openWaterInches(regions) {
  let worst = null;
  for (const r of regions) {
    if (worst == null || r.hs_all_in > worst) worst = r.hs_all_in;
  }
  return worst;
}

/** The calmest region with a usable run, or null when nothing is usable. */
export function bestRegion(regions) {
  for (const r of regions) if (r.hs_in != null) return r;
  return null;
}
