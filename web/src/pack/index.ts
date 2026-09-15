import { DATA_FILES } from "../config";
import type { Lake, Pack, RestrictionIndex, RulesFile } from "../types";

export interface LoadedData {
  pack: Pack | null;
  lakes: Lake[];
  restrictions: RestrictionIndex;
  rules: RulesFile | null;
  /** Names of /data files that returned 404 so the UI can degrade instead of crashing. */
  missing: string[];
}

async function getJson<T>(url: string, label: string, missing: string[]): Promise<T | null> {
  try {
    const res = await fetch(url, { cache: "no-cache" });
    if (!res.ok) {
      console.warn(`[pack] ${label} unavailable (HTTP ${res.status}) at ${url}`);
      missing.push(label);
      return null;
    }
    return (await res.json()) as T;
  } catch (err) {
    console.warn(`[pack] ${label} failed to load from ${url}`, err);
    missing.push(label);
    return null;
  }
}

/**
 * A HEAD probe so a pmtiles archive the pipeline has not produced yet is skipped with a
 * warning instead of leaving MapLibre retrying a 404 source forever.
 */
export async function pmtilesExists(url: string): Promise<boolean> {
  try {
    const res = await fetch(url, { method: "HEAD" });
    if (res.ok) return true;
    console.warn(`[pack] tiles missing, layer skipped: ${url} (HTTP ${res.status})`);
    return false;
  } catch (err) {
    console.warn(`[pack] tiles unreachable, layer skipped: ${url}`, err);
    return false;
  }
}

/** index.json is the only hard requirement; everything else degrades. */
export async function loadData(): Promise<LoadedData> {
  const missing: string[] = [];
  const [pack, lakes, restrictions, rules] = await Promise.all([
    getJson<Pack>(DATA_FILES.pack, "pack.json", missing),
    getJson<Lake[]>(DATA_FILES.index, "index.json", missing),
    getJson<RestrictionIndex>(DATA_FILES.restrictions, "restrictions.json", missing),
    getJson<RulesFile>(DATA_FILES.rules, "rules.json", missing),
  ]);

  return {
    pack,
    lakes: Array.isArray(lakes) ? lakes : [],
    restrictions: restrictions ?? {},
    rules,
    missing,
  };
}
