import { cellOf, labelCentroids } from "@rules/waves/index.js";
import type { RegionWind, WavePoint } from "@rules/waves/index.js";
import type { ForecastWind as ForecastWindResponse } from "../briefing/types";
import { hourIndex, type HourIso } from "../state/clock";

/**
 * One water body's forecast wind, laid out so scrubbing the time bar costs nothing but the
 * wave math: every label is resolved to its forecast cell once, and the wind each point uses
 * at each hour is built once per hour and kept.
 *
 * Which cell a region uses is the briefing's rule (contract "Wind per region" and "Map time
 * bar"): the cell of the region's centroid, the plain mean of its points' lat and lon. A
 * point with no region of its own takes its own cell. Waves are computed at the gust.
 */

/** One hour's wind at one cell, as the api sends it. */
export interface HourWind {
  dir: number;
  kt: number;
  gust: number | null;
}

interface Cell {
  dir: (number | null)[];
  kt: (number | null)[];
  gust: (number | null)[];
}

export class LakeForecast {
  readonly lakeId: number;
  readonly times: readonly HourIso[];
  /** How many leading hours are past. */
  readonly past: number;
  readonly fetchedAt: string;
  /** The water body's centroid, for the readout when no region is picked and for night shading. */
  readonly centroid: { lat: number; lon: number };

  private readonly cells: Cell[];
  /** Label index -> cell slot. */
  private readonly labelCell = new Map<number, number>();
  /** Record index -> cell slot, only for points whose label names no region. */
  private readonly pointCell = new Map<number, number>();
  private readonly centroidCell: number;
  /** Per hour: label index -> wave wind (at the gust); built on first use. */
  private readonly byHour: (Map<number, RegionWind | null> | undefined)[];
  private readonly byHourPoint: (Map<number, RegionWind | null> | undefined)[];

  constructor(data: ForecastWindResponse, points: readonly WavePoint[], labels: readonly string[]) {
    this.lakeId = data.lake_id;
    this.times = data.times;
    this.past = data.past;
    this.fetchedAt = data.fetched_at;
    this.cells = data.cells.map((c) => ({ dir: c.dir, kt: c.kt, gust: c.gust }));
    this.byHour = new Array(data.times.length);
    this.byHourPoint = new Array(data.times.length);

    const cellDeg = data.cell_deg > 0 ? data.cell_deg : 0.1;
    const slotOf = new Map<string, number>();
    data.cells.forEach((c, i) => slotOf.set(cellOf(c.lat, c.lon, cellDeg).join(","), i));
    // The api lists the cells its points fall in; a region's centroid can sit in a cell no
    // point does (a crescent bay). Then the nearest listed cell stands in for it.
    const find = (lat: number, lon: number): number => {
      const exact = slotOf.get(cellOf(lat, lon, cellDeg).join(","));
      if (exact != null) return exact;
      let best = 0;
      let bestD = Infinity;
      const k = Math.cos((lat * Math.PI) / 180);
      data.cells.forEach((c, i) => {
        const d = (c.lat - lat) ** 2 + ((c.lon - lon) * k) ** 2;
        if (d < bestD) {
          bestD = d;
          best = i;
        }
      });
      return best;
    };

    let sumLat = 0;
    let sumLon = 0;
    for (const p of points) {
      sumLat += p.lat;
      sumLon += p.lon;
    }
    this.centroid =
      points.length > 0 ? { lat: sumLat / points.length, lon: sumLon / points.length } : { lat: 0, lon: 0 };
    this.centroidCell = this.cells.length > 0 ? find(this.centroid.lat, this.centroid.lon) : -1;

    if (this.cells.length === 0) return;
    for (const [label, c] of labelCentroids(points as WavePoint[])) {
      if (labels[label] != null) this.labelCell.set(label, find(c.lat, c.lon));
    }
    points.forEach((p, i) => {
      if (labels[p.label] == null) this.pointCell.set(i, find(p.lat, p.lon));
    });
  }

  get length(): number {
    return this.times.length;
  }

  /** Axis index for a clock hour; null (live) is the hour containing now. */
  index(hour: HourIso | null, now: Date = new Date()): number {
    return hourIndex(this.times, hour, now);
  }

  /** The hour containing now, clamped to the axis. */
  liveIndex(now: Date = new Date()): number {
    return this.index(null, now);
  }

  /** True when the axis no longer covers now (an old cached copy): not worth showing. */
  isStale(now: Date = new Date()): boolean {
    const last = this.times[this.times.length - 1];
    return last == null || Date.parse(last) + 3_600_000 <= now.getTime();
  }

  /** The forecast wind for a region (label index), or null when its cell has nothing that hour. */
  labelWind(h: number, label: number): HourWind | null {
    const slot = this.labelCell.get(label);
    return slot == null ? null : this.cellWind(slot, h);
  }

  /** The water body's centroid cell. */
  centroidWind(h: number): HourWind | null {
    return this.centroidCell < 0 ? null : this.cellWind(this.centroidCell, h);
  }

  /**
   * What each point's waves are computed with at hour `h`: its region's cell wind at the
   * gust. Feed it to `regionsForWinds`; null leaves the point out, as the api does.
   */
  waveWindOf(h: number): (label: number, index: number) => RegionWind | null {
    let byLabel = this.byHour[h];
    if (!byLabel) {
      byLabel = new Map();
      for (const [label, slot] of this.labelCell) byLabel.set(label, atGust(this.cellWind(slot, h)));
      this.byHour[h] = byLabel;
    }
    let byPoint = this.byHourPoint[h];
    if (!byPoint) {
      byPoint = new Map();
      for (const [i, slot] of this.pointCell) byPoint.set(i, atGust(this.cellWind(slot, h)));
      this.byHourPoint[h] = byPoint;
    }
    const labels = byLabel;
    const pts = byPoint;
    return (label, i) => labels.get(label) ?? pts.get(i) ?? null;
  }

  private cellWind(slot: number, h: number): HourWind | null {
    const c = this.cells[slot];
    if (!c) return null;
    const dir = c.dir[h];
    const kt = c.kt[h];
    if (dir == null || kt == null || !Number.isFinite(dir) || !Number.isFinite(kt)) return null;
    const gust = c.gust[h];
    return { dir, kt, gust: gust != null && Number.isFinite(gust) ? gust : null };
  }
}

/** Waves are scored at the gust (contract), so the wave wind takes the gust when it is higher. */
export function atGust(w: HourWind | null): RegionWind | null {
  if (!w) return null;
  return { dir: w.dir, kt: w.gust != null && w.gust > w.kt ? w.gust : w.kt };
}
