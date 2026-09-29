// Per-region wind: `regionsForWinds` and the forecast-cell helpers the map time bar uses.
// The single-wind case must be `regionsForWind` value for value; the per-label case must
// be what the api's Python `regions(..., winds=...)` computes (each label on its own wind,
// a label with no wind left out).
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import {
  cellOf,
  decodeWavePoints,
  labelCentroids,
  metresToInches,
  pointWave,
  regionsForWind,
  regionsForWinds,
  roundHalfEven,
} from "../index.js";

const here = path.dirname(fileURLToPath(import.meta.url));
const fixtures = path.resolve(here, "../../fixtures");
const waves = JSON.parse(readFileSync(path.join(fixtures, "waves.json"), "utf8"));
const sampleIndex = JSON.parse(readFileSync(path.join(fixtures, "wave_points.sample.json"), "utf8"));
const sampleBytes = readFileSync(path.join(fixtures, "wave_points.sample.bin"));

function lakePoints(lake) {
  return decodeWavePoints(sampleBytes, sampleIndex, lake);
}

test("one constant wind gives exactly regionsForWind, on every shared fixture", () => {
  for (const c of waves.regions) {
    const points = lakePoints(c.lake);
    const wind = { dir: c.wind_dir, kt: c.wind_kt };
    const got = regionsForWinds(points, sampleIndex.labels, () => wind, c.min_run_ft);
    assert.deepEqual(got, regionsForWind(points, sampleIndex.labels, c.wind_dir, c.wind_kt, c.min_run_ft));
    assert.deepEqual(got, c.regions);
  }
});

test("each label takes its own wind: a label's row is its row under that wind alone", () => {
  const winds = [
    { dir: 225, kt: 12 },
    { dir: 340, kt: 18 },
    { dir: 90, kt: 6 },
    { dir: 180, kt: 24 },
  ];
  for (const lake of Object.keys(sampleIndex.lakes)) {
    const points = lakePoints(lake);
    const labelsHere = [...new Set(points.map((p) => p.label))];
    const windFor = new Map(labelsHere.map((l, k) => [l, winds[k % winds.length]]));
    const mixed = regionsForWinds(points, sampleIndex.labels, (label) => windFor.get(label), 2000);
    assert.equal(mixed.length, labelsHere.length);
    for (const label of labelsHere) {
      const w = windFor.get(label);
      const name = sampleIndex.labels[label] ?? String(label);
      const alone = regionsForWind(points, sampleIndex.labels, w.dir, w.kt, 2000).find((r) => r.label === name);
      const row = mixed.find((r) => r.label === name);
      assert.deepEqual(row, alone, `lake ${lake} label ${name}`);
    }
    // Still calm to rough with the unusable last.
    for (let k = 1; k < mixed.length; k++) {
      const a = mixed[k - 1];
      const b = mixed[k];
      if (a.hs_in != null && b.hs_in != null) assert.ok(a.hs_in <= b.hs_in);
      if (a.hs_in == null) assert.equal(b.hs_in, null);
    }
  }
});

test("a label with no wind for the hour is left out, as the Python side does", () => {
  const lake = Object.keys(sampleIndex.lakes).find(
    (id) => new Set(lakePoints(id).map((p) => p.label)).size > 1,
  );
  const points = lakePoints(lake);
  const dropped = points[0].label;
  const got = regionsForWinds(
    points,
    sampleIndex.labels,
    (label) => (label === dropped ? null : { dir: 270, kt: 12 }),
    2000,
  );
  assert.ok(!got.some((r) => r.label === sampleIndex.labels[dropped]));
  assert.ok(got.length > 0);
  // `point` still indexes the full points array.
  for (const r of got) if (r.point != null) assert.notEqual(points[r.point].label, dropped);
  assert.deepEqual(regionsForWinds(points, sampleIndex.labels, () => null, 2000), []);
});

test("windOf is asked with the record index, so an unlabelled point can take its own cell", () => {
  const points = lakePoints(Object.keys(sampleIndex.lakes)[0]);
  const seen = [];
  regionsForWinds(
    points,
    sampleIndex.labels,
    (label, i) => {
      seen.push([label, i]);
      return { dir: 0, kt: 10 };
    },
    2000,
  );
  assert.deepEqual(
    seen,
    points.map((p, i) => [p.label, i]),
  );
});

test("pointWave is the per-point number the regions aggregate", () => {
  for (const c of waves.regions) {
    const points = lakePoints(c.lake);
    // A single-point region's hs is that point's own number.
    for (const r of c.regions) {
      if (r.point == null || r.n_usable !== 1) continue;
      const pw = pointWave(points[r.point], c.wind_dir, c.wind_kt, c.min_run_ft);
      assert.ok(pw.usable);
      assert.equal(metresToInches(pw.hsM), r.hs_in);
      assert.equal(pw.runFt, r.run_ft);
    }
  }
  const w = pointWave(lakePoints(Object.keys(sampleIndex.lakes)[0])[0], 270, 0, 0);
  assert.equal(w.hsM, 0);
  assert.equal(w.usable, true);
});

test("roundHalfEven matches Python's round()", () => {
  const cases = [
    [0.5, 0],
    [1.5, 2],
    [2.5, 2],
    [-0.5, 0],
    [-1.5, -2],
    [-2.5, -2],
    [2.4, 2],
    [2.6, 3],
    [-2.6, -3],
    [3, 3],
  ];
  for (const [x, want] of cases) assert.equal(roundHalfEven(x) + 0, want, `round(${x})`);
});

test("cellOf snaps to the contract's cell indices", () => {
  assert.deepEqual(cellOf(42.44, -82.76, 0.1), [424, -828]);
  assert.deepEqual(cellOf(42.46, -82.74, 0.1), [425, -827]);
  assert.deepEqual(cellOf(42.46, -82.74, 0.2), [212, -414]);
  assert.deepEqual(cellOf(43.9, -87.1, 0.4), [110, -218]);
});

test("labelCentroids is the plain mean of each label's points", () => {
  const pts = [
    { lat: 42, lon: -82, label: 1 },
    { lat: 43, lon: -83, label: 1 },
    { lat: 10, lon: 20, label: 4 },
  ];
  const c = labelCentroids(pts);
  assert.deepEqual(c.get(1), { lat: 42.5, lon: -82.5, n: 2 });
  assert.deepEqual(c.get(4), { lat: 10, lon: 20, n: 1 });
  assert.equal(c.size, 2);
});
