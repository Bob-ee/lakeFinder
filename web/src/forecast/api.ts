import type { WavePoint } from "@rules/waves/index.js";
import type { ForecastWind as ForecastWindResponse } from "../briefing/types";
import { API_BASE, IDB } from "../config";
import { db } from "../state/db";

/**
 * `GET /api/forecast/wind?lake=<id>`: the forecast wind for every cell a water body's wave
 * points fall in, over the forecast axis. Kept in memory for the api's own cache time and in
 * IndexedDB (the `wind` store, key `forecast:<id>`) so a pilot who looked at it on the ground
 * still has it on the water. Every failure is "no forecast", and the Water section then keeps
 * its wind dial exactly as before.
 */

const FRESH_MS = 30 * 60_000;
const key = (lakeId: number) => `forecast:${lakeId}`;

interface Entry {
  at: number;
  data: ForecastWindResponse;
}

export class ForecastWindClient {
  private mem = new Map<number, Entry>();
  private inflight = new Map<number, Promise<ForecastWindResponse | null>>();

  /** `points` feeds the dev fake only (`?fakeforecast=1`). */
  get(lakeId: number, points: readonly WavePoint[], labels: readonly string[]): Promise<ForecastWindResponse | null> {
    const hit = this.mem.get(lakeId);
    if (hit && Date.now() - hit.at < FRESH_MS) return Promise.resolve(hit.data);
    let pending = this.inflight.get(lakeId);
    if (!pending) {
      pending = this.load(lakeId, points, labels).finally(() => this.inflight.delete(lakeId));
      this.inflight.set(lakeId, pending);
    }
    return pending;
  }

  private async load(
    lakeId: number,
    points: readonly WavePoint[],
    labels: readonly string[],
  ): Promise<ForecastWindResponse | null> {
    if (import.meta.env.DEV && fakeRequested()) {
      const { fakeForecastWind } = await import("./fake");
      const data = fakeForecastWind(lakeId, points, labels);
      this.mem.set(lakeId, { at: Date.now(), data });
      return data;
    }

    const fresh = await fetchForecast(lakeId);
    if (fresh) {
      this.mem.set(lakeId, { at: Date.now(), data: fresh });
      void persist(lakeId, fresh);
      return fresh;
    }
    // Offline or the api is down: the last copy, if its axis still reaches now.
    const cached = this.mem.get(lakeId)?.data ?? (await restore(lakeId));
    if (cached && axisCoversNow(cached)) return cached;
    return null;
  }
}

async function fetchForecast(lakeId: number): Promise<ForecastWindResponse | null> {
  try {
    const res = await fetch(`${API_BASE}/forecast/wind?lake=${encodeURIComponent(lakeId)}`, {
      headers: { Accept: "application/json" },
    });
    if (!res.ok) return null;
    const body = (await res.json()) as ForecastWindResponse;
    return isForecast(body) ? body : null;
  } catch {
    return null;
  }
}

function isForecast(x: unknown): x is ForecastWindResponse {
  const b = x as ForecastWindResponse | null;
  return (
    !!b &&
    typeof b === "object" &&
    Array.isArray(b.times) &&
    b.times.length > 0 &&
    Array.isArray(b.cells) &&
    b.cells.length > 0 &&
    b.cells.every((c) => Array.isArray(c.dir) && Array.isArray(c.kt) && c.kt.length === b.times.length)
  );
}

function axisCoversNow(data: ForecastWindResponse): boolean {
  const last = data.times[data.times.length - 1];
  return last != null && Date.parse(last) + 3_600_000 > Date.now();
}

async function persist(lakeId: number, data: ForecastWindResponse): Promise<void> {
  try {
    const database = await db();
    if (!database) return;
    await database.put(IDB.stores.wind, { key: key(lakeId), fetched_at: Date.now(), payload: data });
  } catch {
    // Best effort.
  }
}

async function restore(lakeId: number): Promise<ForecastWindResponse | null> {
  try {
    const database = await db();
    if (!database) return null;
    const row = await database.get(IDB.stores.wind, key(lakeId));
    return row && isForecast(row.payload) ? row.payload : null;
  } catch {
    return null;
  }
}

function fakeRequested(): boolean {
  return new URL(window.location.href).searchParams.get("fakeforecast") === "1";
}

/** DEV ONLY: `?fakehour=N`, hours from now to put the clock on once a forecast loads. */
export function devHourOffset(): number | null {
  if (!import.meta.env.DEV) return null;
  const raw = new URL(window.location.href).searchParams.get("fakehour");
  if (raw == null || raw === "") return null;
  const n = Number(raw);
  return Number.isFinite(n) ? Math.round(n) : null;
}
