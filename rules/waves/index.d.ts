/**
 * Types for the shared wave-field module. Hand written rather than generated so
 * `web/` can `import ... from "@rules/waves/index.js"` and get real types, the
 * same way `rules/engine` is typed through the `virtual:rules-engine` module
 * declaration in web/src/vite-env.d.ts.
 *
 * Shapes come from docs/data-contract.md, section "Wave field".
 */

/** `depth_dm` sentinel: no bathymetry covered this point. */
export declare const DEPTH_UNKNOWN: 65535;
export declare const RECORD_BYTES: 60;
export declare const FETCH_UNIT_M: 10;
export declare const RUN_UNIT_FT: 10;

export interface WavePoint {
  lon: number;
  lat: number;
  /** Decimetres; `DEPTH_UNKNOWN` when no bathymetry covered the point. */
  depth_dm: number;
  /** Index into the index file's `labels`. */
  label: number;
  /** 16 bearing bins, units of `FETCH_UNIT_M`. */
  fetch: ArrayLike<number>;
  /** 8 bearing bins, units of `RUN_UNIT_FT`. */
  run: ArrayLike<number>;
}

export interface WavePointsIndex {
  version: number;
  record_bytes: number;
  fetch_unit_m: number;
  run_unit_ft: number;
  labels: string[];
  /** `"<lake_id>": [first_record, count]`. */
  lakes: Record<string, [number, number]>;
}

export interface WaveRegion {
  label: string;
  n_points: number;
  n_usable: number;
  /** 75th percentile over every point of the label, whole inches. */
  hs_all_in: number;
  /** The same percentile over the usable points; null when none are usable. */
  hs_in: number | null;
  /** Lower median of the usable runs, feet. */
  run_ft: number | null;
  /** Index into the `points` array of the calmest usable point. */
  point: number | null;
}

export declare function waveHeight(
  windKt: number,
  fetchM: number,
  depthM?: number | null,
): { hsM: number; tpS: number };

export declare function windBin(deg: number): number;

export declare function metresToInches(m: number): number;

export declare function decodeWavePoints(
  buffer: ArrayBuffer | ArrayBufferView,
  index: WavePointsIndex,
  lakeId?: number | string | null,
): WavePoint[];

export declare function encodeWavePoints(points: WavePoint[]): Uint8Array;

export declare function regionsForWind(
  points: WavePoint[],
  labels: string[],
  windDir: number,
  windKt: number,
  minRunFt: number,
): WaveRegion[];

export declare function openWaterInches(regions: WaveRegion[]): number | null;

export declare function bestRegion(regions: WaveRegion[]): WaveRegion | null;
