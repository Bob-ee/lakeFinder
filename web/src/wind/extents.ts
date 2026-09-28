import { DATA_FILES } from "../config";

/**
 * `lake_extents.json` (contract): per lake, the longest straight run of water along each of
 * 16 bearing bins, in feet. Fetched once, on the first selection that has a wind to look up;
 * null when the file is missing or offline, and callers fall back to the longest chord.
 */
type Extents = Record<string, number[]>;

let loading: Promise<Extents | null> | null = null;

function load(): Promise<Extents | null> {
  loading ??= (async () => {
    try {
      const res = await fetch(DATA_FILES.lakeExtents);
      if (!res.ok) return null;
      return (await res.json()) as Extents;
    } catch {
      return null;
    }
  })().then((v) => {
    // A failed load is retried on the next selection rather than remembered.
    if (v == null) loading = null;
    return v;
  });
  return loading;
}

/** Bin for a bearing: `round(bearing / 22.5) % 16`, as the api's `aero.extent_bin`. */
export function extentBin(bearing: number): number {
  return Math.round((((bearing % 360) + 360) % 360) / 22.5) % 16;
}

/** Feet of water along this bearing through the lake, or null when there is no entry. */
export async function runAlong(lakeId: number, bearing: number): Promise<number | null> {
  const all = await load();
  const bins = all?.[String(lakeId)];
  if (!Array.isArray(bins) || bins.length !== 16) return null;
  const v = bins[extentBin(bearing)];
  return typeof v === "number" && Number.isFinite(v) ? v : null;
}
