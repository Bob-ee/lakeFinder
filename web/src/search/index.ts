import { SEARCH } from "../config";
import type { Lake } from "../types";
import { normalizeName } from "./normalize";
import { trigramScore, trigramSet } from "./trigram";

export type MatchKind = "prefix" | "substring" | "fuzzy";

export interface SearchHit {
  lake: Lake;
  kind: MatchKind;
  score: number;
}

/** Below this a trigram match is noise rather than a near miss. */
const FUZZY_FLOOR = 0.42;

/**
 * In-memory typeahead over index.json. Three passes, in order: prefix on `name_norm`,
 * then substring, then a trigram scorer. Within a tier, alphabetical.
 * (Distance sort arrives with location in phase 2.)
 */
export class SearchIndex {
  private lakes: Lake[] = [];
  private byId = new Map<number, Lake>();
  /** Parallel arrays, sorted by name_norm, so the three passes are plain scans. */
  private norms: string[] = [];

  load(lakes: Lake[]): void {
    this.lakes = [...lakes].sort((a, b) => compareLakes(a, b));
    this.norms = this.lakes.map((l) => l.name_norm || normalizeName(l.name) || "");
    this.byId.clear();
    for (const l of this.lakes) this.byId.set(l.id, l);
  }

  get size(): number {
    return this.lakes.length;
  }

  all(): readonly Lake[] {
    return this.lakes;
  }

  get(id: number): Lake | undefined {
    return this.byId.get(id);
  }

  search(rawQuery: string, limit = SEARCH.maxRows): SearchHit[] {
    const q = normalizeName(rawQuery);
    if (q === "") return [];

    const prefix: SearchHit[] = [];
    const substring: SearchHit[] = [];
    const seen = new Set<number>();

    for (let i = 0; i < this.lakes.length; i++) {
      const norm = this.norms[i]!;
      if (norm === "") continue;
      if (norm.startsWith(q)) {
        prefix.push({ lake: this.lakes[i]!, kind: "prefix", score: 1 - norm.length / 1000 });
        seen.add(this.lakes[i]!.id);
      }
    }
    if (prefix.length >= limit) return prefix.slice(0, limit);

    for (let i = 0; i < this.lakes.length; i++) {
      const norm = this.norms[i]!;
      if (norm === "" || seen.has(this.lakes[i]!.id)) continue;
      const at = norm.indexOf(q);
      if (at > 0) {
        // Word-boundary hits ("school" in "big school lot") beat mid-word ones.
        const boundary = norm[at - 1] === " ";
        substring.push({
          lake: this.lakes[i]!,
          kind: "substring",
          score: (boundary ? 0.9 : 0.7) - at / 1000,
        });
        seen.add(this.lakes[i]!.id);
      }
    }
    const tiered = [...prefix, ...substring];
    if (tiered.length >= limit) return tiered.slice(0, limit);

    const grams = trigramSet(q);
    const fuzzy: SearchHit[] = [];
    for (let i = 0; i < this.lakes.length; i++) {
      const norm = this.norms[i]!;
      if (norm === "" || seen.has(this.lakes[i]!.id)) continue;
      const score = trigramScore(grams, norm);
      if (score >= FUZZY_FLOOR) fuzzy.push({ lake: this.lakes[i]!, kind: "fuzzy", score });
    }
    fuzzy.sort((a, b) => b.score - a.score || compareLakes(a.lake, b.lake));

    return [...tiered, ...fuzzy].slice(0, limit);
  }
}

/** Alphabetical by display name; unnamed polygons sort last. */
export function compareLakes(a: Lake, b: Lake): number {
  const an = a.name ?? "";
  const bn = b.name ?? "";
  if (an === "" && bn !== "") return 1;
  if (bn === "" && an !== "") return -1;
  return an.localeCompare(bn) || a.id - b.id;
}
