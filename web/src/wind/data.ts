import { API_BASE, IDB, WIND } from "../config";
import { db } from "../state/db";
import { distanceNm } from "./format";
import type { PointWind, Station, StationsResponse } from "./types";

/**
 * Wind observations and model wind, as the client holds them.
 *
 * Everything is kept per **1-degree tile**, the unit the api caches in, and every request
 * asks for whole tiles, so a tile's station list is complete and can be cached on its own
 * (IndexedDB store `wind`, key `tile:<x>,<y>`; model wind under `point:<lat>,<lon>`). The
 * last good answer is what the map and the sheet draw, with its age; a failed or offline
 * fetch changes nothing and says nothing.
 */

export type Bbox = [number, number, number, number];

interface TileEntry {
  fetchedAt: number;
  stations: Station[];
}

interface PointEntry {
  fetchedAt: number;
  wind: PointWind;
}

type Purpose = "view" | "lake";

const TIMEOUT_MS = 10_000;

/** Tile range `[x0, y0, x1, y1]` (inclusive) of a bbox, the api's floor(lon/lat) tiling. */
export function tileRange(b: Bbox): [number, number, number, number] {
  return [Math.floor(b[0]), Math.floor(b[1]), Math.floor(b[2]), Math.floor(b[3])];
}

/**
 * Shrinks a tile range to at most `WIND.maxTiles`, keeping the tiles nearest the center.
 * The map at zoom 8 on a wide iPad can see more than 16 tiles; the far edges wait.
 */
export function clampRange(
  r: [number, number, number, number],
  cx: number,
  cy: number,
): [number, number, number, number] {
  let [x0, y0, x1, y1] = r;
  const tx = Math.floor(cx);
  const ty = Math.floor(cy);
  while ((x1 - x0 + 1) * (y1 - y0 + 1) > WIND.maxTiles) {
    if (x1 - x0 >= y1 - y0) {
      if (tx - x0 > x1 - tx) x0++;
      else x1--;
    } else if (ty - y0 > y1 - ty) y0++;
    else y1--;
  }
  return [x0, y0, x1, y1];
}

function tileKey(x: number, y: number): string {
  return `tile:${x},${y}`;
}

/** 0.1 degree cell, the api's snap for `/api/wind/point`. */
export function pointKey(lat: number, lon: number): string {
  return `point:${(Math.round(lat * 10) / 10).toFixed(1)},${(Math.round(lon * 10) / 10).toFixed(1)}`;
}

function parseTime(iso: string | null | undefined): number | null {
  if (!iso) return null;
  const t = Date.parse(iso);
  return Number.isNaN(t) ? null : t;
}

async function getJson<T>(path: string): Promise<T | null> {
  const ctrl = new AbortController();
  const timer = window.setTimeout(() => ctrl.abort(), TIMEOUT_MS);
  try {
    const res = await fetch(`${API_BASE}${path}`, {
      signal: ctrl.signal,
      cache: "no-store",
      headers: { Accept: "application/json" },
    });
    if (!res.ok) return null;
    return (await res.json()) as T;
  } catch {
    return null;
  } finally {
    clearTimeout(timer);
  }
}

export interface NearStation {
  station: Station;
  nm: number;
  ageMin: number;
}

export class WindData {
  private tiles = new Map<string, TileEntry>();
  private points = new Map<string, PointEntry>();
  private pointInflight = new Map<string, Promise<PointWind | null>>();
  private listeners = new Set<() => void>();
  private lastRequest = 0;
  private inflight = false;
  private timer: number | null = null;
  /** Latest wanted range per purpose; a later ask of the same purpose replaces it. */
  private wanted = new Map<Purpose, [number, number, number, number]>();
  private hydrated: Promise<void>;

  constructor() {
    this.hydrated = this.hydrate();
  }

  onChange(fn: () => void): () => void {
    this.listeners.add(fn);
    return () => this.listeners.delete(fn);
  }

  private emit(): void {
    for (const fn of this.listeners) {
      try {
        fn();
      } catch (err) {
        console.warn("[wind] listener failed", err);
      }
    }
  }

  /** Last responses from IndexedDB, so the map and sheet have something offline. */
  private async hydrate(): Promise<void> {
    try {
      const database = await db();
      if (!database) return;
      const rows = await database.getAll(IDB.stores.wind);
      for (const row of rows) {
        const payload = row.payload as { stations?: Station[] } | PointWind | null;
        if (!payload) continue;
        if (row.key.startsWith("tile:") && "stations" in payload && Array.isArray(payload.stations)) {
          this.tiles.set(row.key, { fetchedAt: row.fetched_at, stations: payload.stations });
        } else if (row.key.startsWith("point:")) {
          this.points.set(row.key, { fetchedAt: row.fetched_at, wind: payload as PointWind });
        }
      }
      if (rows.length > 0) this.emit();
    } catch {
      // No IndexedDB (private mode, quota): nothing cached, nothing to say.
    }
  }

  private async persist(key: string, fetchedAt: number, payload: unknown): Promise<void> {
    try {
      const database = await db();
      if (!database) return;
      await database.put(IDB.stores.wind, { key, fetched_at: fetchedAt, payload });
    } catch {
      // Best effort.
    }
  }

  get hasEverFetched(): boolean {
    return this.tiles.size > 0 || this.points.size > 0;
  }

  /** Every cached station, one per source + id, newest observation wins. */
  stations(): Station[] {
    const byId = new Map<string, Station>();
    for (const tile of this.tiles.values()) {
      for (const s of tile.stations) {
        const k = `${s.source}:${s.id}`;
        const prev = byId.get(k);
        if (!prev || (parseTime(s.obs_time) ?? 0) > (parseTime(prev.obs_time) ?? 0)) byId.set(k, s);
      }
    }
    return [...byId.values()];
  }

  /**
   * When the stations covering this bbox were fetched: the oldest of the cached tiles in
   * it, or null when none of them has ever been fetched.
   */
  fetchedAt(b: Bbox): number | null {
    const [x0, y0, x1, y1] = tileRange(b);
    let oldest: number | null = null;
    for (let x = x0; x <= x1; x++) {
      for (let y = y0; y <= y1; y++) {
        const t = this.tiles.get(tileKey(x, y));
        if (t && (oldest == null || t.fetchedAt < oldest)) oldest = t.fetchedAt;
      }
    }
    return oldest;
  }

  /** Nearest stations to a point, observed within the display limit. */
  nearest(lat: number, lon: number, n: number, now = Date.now()): NearStation[] {
    const out: NearStation[] = [];
    for (const s of this.stations()) {
      const t = parseTime(s.obs_time);
      if (t == null) continue;
      const ageMin = (now - t) / 60_000;
      if (ageMin > WIND.hideAfterMin) continue;
      if (s.speed_kt == null) continue;
      out.push({ station: s, nm: distanceNm(lat, lon, s.lat, s.lon), ageMin: Math.max(0, ageMin) });
    }
    out.sort((a, b) => a.nm - b.nm);
    return out.slice(0, n);
  }

  /** Asks for the stations in the visible map bbox (throttled; see `schedule`). */
  requestView(b: Bbox): void {
    const cx = (b[0] + b[2]) / 2;
    const cy = (b[1] + b[3]) / 2;
    this.want("view", clampRange(tileRange(b), cx, cy));
  }

  /** Asks for the stations around a selected water body. */
  requestAround(lat: number, lon: number): void {
    const dLat = WIND.lakeRadiusDeg;
    const dLon = dLat / Math.max(0.2, Math.cos((lat * Math.PI) / 180));
    const b: Bbox = [lon - dLon, lat - dLat, lon + dLon, lat + dLat];
    this.want("lake", clampRange(tileRange(b), lon, lat));
  }

  private want(purpose: Purpose, range: [number, number, number, number]): void {
    if (this.allFresh(range)) {
      this.wanted.delete(purpose);
      return;
    }
    this.wanted.set(purpose, range);
    this.schedule();
  }

  private allFresh(r: [number, number, number, number], now = Date.now()): boolean {
    for (let x = r[0]; x <= r[2]; x++) {
      for (let y = r[1]; y <= r[3]; y++) {
        const t = this.tiles.get(tileKey(x, y));
        if (!t || now - t.fetchedAt > WIND.tileFreshMs) return false;
      }
    }
    return true;
  }

  /** One request per `WIND.throttleMs`; asks that arrive in between wait for the next slot. */
  private schedule(): void {
    if (this.inflight || this.timer != null) return;
    const wait = Math.max(0, this.lastRequest + WIND.throttleMs - Date.now());
    this.timer = window.setTimeout(() => {
      this.timer = null;
      void this.fire();
    }, wait);
  }

  /**
   * Picks what to send: the union of the view and the lake when that stays within the tile
   * limit, otherwise the lake first (the sheet is what the pilot asked about) and the view
   * in the next slot.
   */
  private async fire(): Promise<void> {
    const lake = this.wanted.get("lake");
    const view = this.wanted.get("view");
    let range: [number, number, number, number] | undefined;
    if (lake && view) {
      const u: [number, number, number, number] = [
        Math.min(lake[0], view[0]),
        Math.min(lake[1], view[1]),
        Math.max(lake[2], view[2]),
        Math.max(lake[3], view[3]),
      ];
      if ((u[2] - u[0] + 1) * (u[3] - u[1] + 1) <= WIND.maxTiles) {
        range = u;
        this.wanted.clear();
      } else {
        range = lake;
        this.wanted.delete("lake");
      }
    } else if (lake) {
      range = lake;
      this.wanted.delete("lake");
    } else if (view) {
      range = view;
      this.wanted.delete("view");
    }
    if (!range) return;
    if (this.allFresh(range)) {
      if (this.wanted.size > 0) this.schedule();
      return;
    }

    this.inflight = true;
    this.lastRequest = Date.now();
    const [x0, y0, x1, y1] = range;
    // Whole tiles, with the east and north edges pulled in a hair: the api tiles by
    // floor(), so an edge sitting exactly on x1 + 1 would count one more column.
    const e = 1e-6;
    const bbox = [x0, y0, x1 + 1 - e, y1 + 1 - e].map((v) => v.toFixed(6)).join(",");
    const res = await getJson<StationsResponse>(`/wind/stations?bbox=${bbox}`);
    this.inflight = false;

    if (res && Array.isArray(res.stations)) {
      const fetchedAt = parseTime(res.fetched_at) ?? Date.now();
      const perTile = new Map<string, Station[]>();
      for (let x = x0; x <= x1; x++) for (let y = y0; y <= y1; y++) perTile.set(tileKey(x, y), []);
      for (const s of res.stations) {
        if (!Number.isFinite(s.lat) || !Number.isFinite(s.lon)) continue;
        perTile.get(tileKey(Math.floor(s.lon), Math.floor(s.lat)))?.push(s);
      }
      for (const [key, stations] of perTile) {
        this.tiles.set(key, { fetchedAt, stations });
        void this.persist(key, fetchedAt, { stations });
      }
      this.emit();
    }
    if (this.wanted.size > 0) this.schedule();
  }

  /** Cached model wind for the cell of this point, whatever its age. */
  cachedPoint(lat: number, lon: number): { wind: PointWind; fetchedAt: number } | null {
    const hit = this.points.get(pointKey(lat, lon));
    return hit ? { wind: hit.wind, fetchedAt: hit.fetchedAt } : null;
  }

  /** Model wind at a point; refetched past the api's 15 min cache, else the cached one. */
  async point(lat: number, lon: number): Promise<PointWind | null> {
    await this.hydrated;
    const key = pointKey(lat, lon);
    const hit = this.points.get(key);
    if (hit && Date.now() - hit.fetchedAt < WIND.pointFreshMs) return hit.wind;
    const running = this.pointInflight.get(key);
    if (running) return running;
    const job = (async () => {
      const res = await getJson<PointWind>(
        `/wind/point?lat=${lat.toFixed(4)}&lon=${lon.toFixed(4)}`,
      );
      this.pointInflight.delete(key);
      if (!res || res.speed_kt === undefined) return hit?.wind ?? null;
      const fetchedAt = Date.now();
      this.points.set(key, { fetchedAt, wind: res });
      void this.persist(key, fetchedAt, res);
      this.emit();
      return res;
    })();
    this.pointInflight.set(key, job);
    return job;
  }
}
