/**
 * DEV ONLY. `?fakeforecast=1` answers `/api/forecast/wind` in the browser with the shape the
 * contract describes, so the time bar can be built and screenshotted before (or without) the
 * api. Imported only from an `import.meta.env.DEV` branch, so a production build has none of it.
 *
 * The wind veers through a full circle over the axis while it builds from 4 to 22 kt, and it
 * varies across the cells (a few knots and up to ~30° over Lake St. Clair) so regions visibly
 * disagree. `?fakehour=N` (also dev only) sets the clock N hours from now once it has loaded.
 */
import { cellOf, labelCentroids } from "@rules/waves/index.js";
import type { WavePoint } from "@rules/waves/index.js";
import type { ForecastWithRegions } from "./lake";
import { localIso } from "./time";

const PAST = 6;
const AHEAD = 72;
const MAX_CELLS = 120;

export function fakeForecastWind(
  lakeId: number,
  points: readonly WavePoint[],
  labels: readonly string[],
): ForecastWithRegions {
  // Cells exactly as the contract picks them: 0.1°, doubled until 120 or fewer.
  let cellDeg = 0.1;
  let keys = new Map<string, [number, number]>();
  for (;;) {
    keys = new Map();
    for (const p of points) {
      const c = cellOf(p.lat, p.lon, cellDeg);
      keys.set(c.join(","), c);
    }
    if (keys.size <= MAX_CELLS || cellDeg > 10) break;
    cellDeg *= 2;
  }

  const now = new Date();
  now.setMinutes(0, 0, 0);
  const times: string[] = [];
  for (let h = -PAST; h <= AHEAD; h++) times.push(localIso(new Date(now.getTime() + h * 3_600_000)));

  const lat0 = points.reduce((s, p) => s + p.lat, 0) / Math.max(1, points.length);
  const lon0 = points.reduce((s, p) => s + p.lon, 0) / Math.max(1, points.length);
  const n = times.length - 1;
  const round = (x: number) => Math.round(x * 1e4) / 1e4;

  const series = (lat: number, lon: number) => {
    const dir: number[] = [];
    const kt: number[] = [];
    const gust: number[] = [];
    for (let h = 0; h <= n; h++) {
      const f = h / n;
      const spatialDir = (lon - lon0) * 70 + (lat - lat0) * 50;
      const spatialKt = 3 * Math.sin((lat - lat0) * 25 + f * 6) + (lon - lon0) * 8;
      const d = (((180 + 360 * f + spatialDir) % 360) + 360) % 360;
      const k = Math.max(1, Math.round(4 + 18 * f + spatialKt));
      dir.push(Math.round(d));
      kt.push(k);
      gust.push(Math.round(k * 1.35 + 2));
    }
    return { lat, lon, dir, kt, gust };
  };
  const cells = [...keys.values()].map(([i, j]) => series(round(i * cellDeg), round(j * cellDeg)));
  // Per region: the centroid's cell, always 0.1°, as the api does.
  const regions = [...labelCentroids(points as WavePoint[])]
    .filter(([label]) => labels[label] != null)
    .map(([label, c]) => {
      const [i, j] = cellOf(c.lat, c.lon, 0.1);
      return { label: labels[label]!, ...series(round(i * 0.1), round(j * 0.1)) };
    });

  return {
    lake_id: lakeId,
    cell_deg: cellDeg,
    times,
    past: PAST,
    models: times.map(() => "fake"),
    cells,
    regions,
    fetched_at: new Date().toISOString(),
    errors: [],
  };
}
