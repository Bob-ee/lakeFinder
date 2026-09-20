/**
 * Name normalization, per docs/data-contract.md "Name normalization (`name_norm`)".
 *
 * TODO: delete `localNormalizeName` and import from the shared engine once
 * rules/engine/index.js exports `normalizeName`:
 *
 *     import { normalizeName } from "@rules/engine/index.js";
 *
 * Until then `normalizeName` below prefers the shared engine at runtime (through the
 * `virtual:rules-engine` shim, which resolves through that same @rules alias) and falls
 * back to this local copy. Shared fixtures live in rules/fixtures/names.json.
 */
import * as engine from "virtual:rules-engine";

/** Step 2 of the contract: whole-word abbreviation expansion. */
const ABBREV: Record<string, string> = {
  lk: "lake",
  mt: "mount",
  st: "saint",
  n: "north",
  s: "south",
  e: "east",
  w: "west",
  upr: "upper",
  lwr: "lower",
  twp: "township",
};

/** Step 3: generic words stripped from the front and back, but never if they are the only word. */
const GENERIC = new Set(["lake", "pond", "reservoir", "impoundment", "flowage", "basin"]);

/**
 * Runs steps 1-4 (and implicitly 5) of the contract on one segment: the main name, or one
 * parenthetical qualifier. See the contract's "Implementation decision" for why a parenthetical
 * is handled as its own segment rather than inline text (e.g. "Crooked Lake (Big)" -> "crooked"
 * + "big" -> "crooked big", not "crooked lake big").
 */
function normalizeSegment(raw: string): string {
  // 1. lowercase, strip diacritics.
  let s = raw
    .normalize("NFD")
    .replace(/[̀-ͯ]/g, "")
    .toLowerCase();
  // 4. strip punctuation except spaces ("St. Clair" -> "st clair"), then collapse whitespace.
  s = s
    .replace(/[^a-z0-9\s]/g, " ")
    .replace(/\s+/g, " ")
    .trim();
  if (s === "") return "";
  // 2. expand abbreviations as whole words.
  const words = s.split(" ").map((w) => ABBREV[w] ?? w);
  // 3. remove leading/trailing generics. Qualifiers (big, little, north, ...) stay, per step 5.
  while (words.length > 1 && GENERIC.has(words[0]!)) words.shift();
  while (words.length > 1 && GENERIC.has(words[words.length - 1]!)) words.pop();
  return words.join(" ");
}

export function localNormalizeName(raw: string | null | undefined): string {
  if (raw == null) return "";
  const parenSegments: string[] = [];
  const main = String(raw).replace(/\(([^)]*)\)/g, (_match, inner) => {
    parenSegments.push(inner);
    return " ";
  });
  return [main, ...parenSegments]
    .map(normalizeSegment)
    .filter((segment) => segment.length > 0)
    .join(" ");
}

/** Prefers the shared rules engine's implementation when it is present. */
export const normalizeName: (raw: string | null | undefined) => string =
  engine.normalizeName ?? localNormalizeName;

/** True when the shared engine (rather than the local copy) is doing the normalizing. */
export const usingSharedNormalize = engine.normalizeName != null;
