/**
 * A small trigram scorer, used only as the last fallback after prefix and substring
 * matching. Written here rather than pulling in fuse.js: the whole matcher is 40 lines
 * and the bundle budget matters more than the generality.
 */

/** "mud" -> ["  m", " mu", "mud", "ud ", "d  "] (padded so short names still produce grams). */
export function trigrams(s: string): string[] {
  const padded = `  ${s} `;
  const out: string[] = [];
  for (let i = 0; i + 3 <= padded.length; i++) out.push(padded.slice(i, i + 3));
  return out;
}

export function trigramSet(s: string): Set<string> {
  return new Set(trigrams(s));
}

/**
 * Dice coefficient over trigram sets, biased toward the query: a short query that is
 * fully contained in a long name should still score well, so the denominator leans on
 * the query size. Returns 0..1.
 */
export function trigramScore(queryGrams: Set<string>, target: string): number {
  if (queryGrams.size === 0) return 0;
  const targetGrams = trigramSet(target);
  if (targetGrams.size === 0) return 0;
  let shared = 0;
  for (const g of queryGrams) if (targetGrams.has(g)) shared++;
  if (shared === 0) return 0;
  const coverage = shared / queryGrams.size;
  const dice = (2 * shared) / (queryGrams.size + targetGrams.size);
  // Weighted so "cass" against "cass lake" ranks above "cass" against a long unrelated name.
  return 0.65 * coverage + 0.35 * dice;
}
