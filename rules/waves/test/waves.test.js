// The shared wave fixtures are the contract between this module and the api
// service's Python port. Both must pass rules/fixtures/waves.json and
// rules/fixtures/wave_points.sample.bin|json unchanged.
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import {
  DEPTH_UNKNOWN,
  RECORD_BYTES,
  bestRegion,
  decodeWavePoints,
  encodeWavePoints,
  metresToInches,
  openWaterInches,
  regionsForWind,
  waveHeight,
  windBin,
} from "../index.js";

const here = path.dirname(fileURLToPath(import.meta.url));
const fixtures = path.resolve(here, "../../fixtures");

const waves = JSON.parse(readFileSync(path.join(fixtures, "waves.json"), "utf8"));
const sampleIndex = JSON.parse(readFileSync(path.join(fixtures, "wave_points.sample.json"), "utf8"));
const sampleBytes = readFileSync(path.join(fixtures, "wave_points.sample.bin"));
const sampleBuffer = sampleBytes.buffer.slice(
  sampleBytes.byteOffset,
  sampleBytes.byteOffset + sampleBytes.byteLength,
);

/** The fixture rounds to 4 decimals (metres) and 3 (seconds); stay inside half a digit. */
const HS_TOL = 6e-5;
const TP_TOL = 6e-4;

test("waveHeight matches every shared fixture case", () => {
  for (const c of waves.wave) {
    const { hsM, tpS } = waveHeight(c.wind_kt, c.fetch_m, c.depth_m);
    assert.ok(
      Math.abs(hsM - c.hs_m) < HS_TOL,
      `Hs for ${c.wind_kt} kt / ${c.fetch_m} m / depth ${c.depth_m}: ${hsM} vs ${c.hs_m}`,
    );
    assert.ok(
      Math.abs(tpS - c.tp_s) < TP_TOL,
      `Tp for ${c.wind_kt} kt / ${c.fetch_m} m / depth ${c.depth_m}: ${tpS} vs ${c.tp_s}`,
    );
  }
});

test("waveHeight is zero for calm or no fetch, and never negative", () => {
  assert.deepEqual(waveHeight(0, 5000, null), { hsM: 0, tpS: 0 });
  assert.deepEqual(waveHeight(-3, 5000, null), { hsM: 0, tpS: 0 });
  assert.deepEqual(waveHeight(12, 0, null), { hsM: 0, tpS: 0 });
  assert.deepEqual(waveHeight(12, -1, 2), { hsM: 0, tpS: 0 });
});

test("waveHeight rises with wind and with fetch, and shallow water is calmer", () => {
  const a = waveHeight(10, 20000, null).hsM;
  const b = waveHeight(20, 20000, null).hsM;
  const c = waveHeight(10, 40000, null).hsM;
  assert.ok(b > a, "more wind, more wave");
  assert.ok(c > a, "more fetch, more wave");
  const deep = waveHeight(15, 25000, null).hsM;
  const shallow = waveHeight(15, 25000, 1.0).hsM;
  assert.ok(shallow < deep, "unknown depth must be the conservative, deep-water answer");
});

test("waveHeight saturates at the fully developed cap", () => {
  // The cap bites at about a 1,000 km fetch for 30 kt; past it the ocean is as
  // developed as the formula allows and more fetch adds nothing.
  const huge = waveHeight(30, 50_000_000, null);
  const big = waveHeight(30, 5_000_000, null);
  assert.ok(Math.abs(huge.hsM - big.hsM) < 1e-9, "capped, so a longer fetch adds nothing");
  assert.ok(waveHeight(30, 500_000, null).hsM < big.hsM, "below the cap it still grows");
});

test("windBin matches the shared fixture", () => {
  for (const b of waves.bins) assert.equal(windBin(b.deg), b.bin, `wind ${b.deg}`);
});

test("windBin wraps in both directions", () => {
  assert.equal(windBin(360), 0);
  assert.equal(windBin(720.1), 0);
  assert.equal(windBin(-22.5), 15);
  assert.equal(windBin(-360), 0);
  for (let i = 0; i < 16; i++) assert.equal(windBin(i * 22.5), i);
});

test("metresToInches rounds half up, per the contract", () => {
  assert.equal(metresToInches(0), 0);
  assert.equal(metresToInches(0.0254), 1);
  assert.equal(metresToInches(0.0254 * 2.5), 3);
  assert.equal(metresToInches(0.0254 * 2.49), 2);
});

// -- wave_points.bin --------------------------------------------------------

test("decodeWavePoints reads the sample file", () => {
  const all = decodeWavePoints(sampleBuffer, sampleIndex);
  assert.equal(all.length, sampleBytes.length / RECORD_BYTES);
  assert.equal(all.length, 8);

  const first = all[0];
  assert.ok(Math.abs(first.lon - -82.7166) < 1e-4);
  assert.ok(Math.abs(first.lat - 42.65) < 1e-4);
  assert.equal(first.depth_dm, 30);
  assert.equal(first.label, 0);
  assert.equal(first.fetch.length, 16);
  assert.equal(first.run.length, 8);
  // fetch[8] is 39.2 km, stored in units of 10 m.
  assert.equal(first.fetch[8], 3920);
  assert.equal(first.run[0], 900);

  // The shallow fixture lake carries the unknown-depth sentinel.
  assert.equal(all[6].depth_dm, DEPTH_UNKNOWN);
});

test("decodeWavePoints slices one water body out of the whole file", () => {
  const lake222 = decodeWavePoints(sampleBuffer, sampleIndex, 222);
  assert.equal(lake222.length, 2);
  assert.equal(lake222[0].label, 3);
  assert.equal(lake222[1].label, 2);
  // Numbers and strings both address the same entry.
  assert.deepEqual(decodeWavePoints(sampleBuffer, sampleIndex, "222"), lake222);
});

test("decodeWavePoints accepts a Range response holding only that water body", () => {
  const [first, count] = sampleIndex.lakes["222"];
  const slice = sampleBuffer.slice(first * RECORD_BYTES, (first + count) * RECORD_BYTES);
  const fromSlice = decodeWavePoints(slice, sampleIndex, 222);
  const fromWhole = decodeWavePoints(sampleBuffer, sampleIndex, 222);
  assert.equal(fromSlice.length, fromWhole.length);
  for (let i = 0; i < fromSlice.length; i++) {
    assert.equal(fromSlice[i].lon, fromWhole[i].lon);
    assert.equal(fromSlice[i].label, fromWhole[i].label);
    assert.deepEqual([...fromSlice[i].fetch], [...fromWhole[i].fetch]);
  }
});

test("decodeWavePoints returns nothing for a water body with no entry", () => {
  assert.deepEqual(decodeWavePoints(sampleBuffer, sampleIndex, 999), []);
});

test("decodeWavePoints accepts a typed-array view as well as an ArrayBuffer", () => {
  const view = new Uint8Array(sampleBuffer);
  assert.equal(decodeWavePoints(view, sampleIndex).length, 8);
});

test("encodeWavePoints round-trips the sample file byte for byte", () => {
  const decoded = decodeWavePoints(sampleBuffer, sampleIndex);
  const encoded = encodeWavePoints(decoded);
  assert.equal(encoded.length, sampleBytes.length);
  assert.deepEqual(Buffer.from(encoded), Buffer.from(sampleBytes));
});

test("encodeWavePoints clamps out-of-range integers instead of wrapping", () => {
  const bytes = encodeWavePoints([
    {
      lon: -83,
      lat: 42,
      depth_dm: -5,
      label: 1,
      fetch: new Array(16).fill(100000),
      run: new Array(8).fill(-1),
    },
  ]);
  const [p] = decodeWavePoints(bytes.buffer, { record_bytes: RECORD_BYTES, lakes: {} });
  assert.equal(p.depth_dm, 0);
  assert.equal(p.fetch[0], 0xffff);
  assert.equal(p.run[0], 0);
});

// -- regions ----------------------------------------------------------------

test("regionsForWind matches every shared aggregation fixture", () => {
  const byLake = {
    111: decodeWavePoints(sampleBuffer, sampleIndex, 111),
    222: decodeWavePoints(sampleBuffer, sampleIndex, 222),
  };
  for (const c of waves.regions) {
    const got = regionsForWind(
      byLake[c.lake],
      sampleIndex.labels,
      c.wind_dir,
      c.wind_kt,
      c.min_run_ft,
    );
    assert.equal(windBin(c.wind_dir), c.bin, `bin for ${c.wind_dir}`);
    assert.deepEqual(
      got,
      c.regions,
      `lake ${c.lake} wind ${c.wind_dir}/${c.wind_kt} min run ${c.min_run_ft}`,
    );
  }
});

test("regionsForWind is driven by the fixture JSON points as well as the binary", () => {
  // Decoded records and the Python reference's JSON rows are the same shape, so
  // a hand-written point works as an input. Two labels, one of them unusable.
  const points = [
    { lon: 0, lat: 0, depth_dm: DEPTH_UNKNOWN, label: 0, fetch: new Array(16).fill(500), run: new Array(8).fill(300) },
    { lon: 0, lat: 0, depth_dm: DEPTH_UNKNOWN, label: 1, fetch: new Array(16).fill(50), run: new Array(8).fill(100) },
  ];
  const regions = regionsForWind(points, ["open", "cove"], 270, 15, 2500);
  assert.deepEqual(
    regions.map((r) => [r.label, r.hs_in, r.run_ft, r.n_usable]),
    [
      ["open", regions[0].hs_in, 3000, 1],
      ["cove", null, null, 0],
    ],
  );
  assert.ok(regions[1].hs_all_in >= 0, "an unusable region still reports its open-water figure");
});

test("the calmest region moves when the wind changes", () => {
  const points = decodeWavePoints(sampleBuffer, sampleIndex, 111);
  const fromSouthwest = regionsForWind(points, sampleIndex.labels, 225, 12, 2000);
  const fromNorthwest = regionsForWind(points, sampleIndex.labels, 340, 18, 2000);
  assert.equal(bestRegion(fromSouthwest).label, "Big Muscamoot Bay");
  assert.equal(bestRegion(fromNorthwest).label, "Big Muscamoot Bay");
  // The open middle is the roughest end of the field either way.
  assert.equal(fromSouthwest.at(-1).label, "middle");
  assert.ok(openWaterInches(fromNorthwest) > openWaterInches(fromSouthwest));
});

test("bestRegion is null when nothing has a usable run", () => {
  const points = decodeWavePoints(sampleBuffer, sampleIndex, 222);
  const regions = regionsForWind(points, sampleIndex.labels, 270, 14, 5000);
  assert.equal(bestRegion(regions), null);
  assert.equal(openWaterInches(regions), 5);
});

test("regionsForWind with no points has no regions", () => {
  assert.deepEqual(regionsForWind([], sampleIndex.labels, 270, 10, 2000), []);
  assert.equal(openWaterInches([]), null);
  assert.equal(bestRegion([]), null);
});

test("a label index with no entry in labels falls back to its number", () => {
  const points = [
    { lon: 0, lat: 0, depth_dm: DEPTH_UNKNOWN, label: 7, fetch: new Array(16).fill(200), run: new Array(8).fill(400) },
  ];
  assert.equal(regionsForWind(points, ["only"], 90, 10, 2000)[0].label, "7");
});

test("calm wind makes every region flat but keeps them sorted by label", () => {
  const points = decodeWavePoints(sampleBuffer, sampleIndex, 111);
  const regions = regionsForWind(points, sampleIndex.labels, 225, 0, 2000);
  assert.deepEqual(
    regions.map((r) => r.label),
    ["Anchor Bay", "Big Muscamoot Bay", "middle"],
  );
  for (const r of regions) assert.equal(r.hs_in, 0);
});
