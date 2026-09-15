// Shared rules engine for the Michigan seaplane lake map.
//
// Zero dependencies, ESM, runs unmodified in Node (pipeline + CLI) and in
// modern browsers (client). See docs/data-contract.md for the authoritative
// schema this file implements: sections "restrictions", "rules/rules.json",
// "Rules engine API", and the name normalization section.

// Worst-to-best. Used both for lake-verdict aggregation and as the
// canonical list of valid verdict values.
export const VERDICT_ORDER = ["restricted", "conditional", "unknown", "clear"];

const VALID_VERDICTS = new Set(VERDICT_ORDER);

// -- name normalization -----------------------------------------------------
//
// Mirrors docs/data-contract.md "Name normalization (name_norm)". Kept here
// so both the pipeline (via the CLI, or a future Python port tested against
// rules/fixtures/names.json) and the client (which imports this module
// directly) share one implementation.

const GENERIC_WORDS = new Set(["lake", "pond", "reservoir", "impoundment", "flowage", "basin"]);

const ABBREVIATIONS = Object.freeze({
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
});

/**
 * Normalize a raw lake name per docs/data-contract.md.
 *
 * Decision (documented in the data contract): a parenthetical qualifier is
 * treated as a separate segment. The parentheses are stripped, the main
 * name and the parenthetical content are each run through the full
 * normalization pipeline independently, and the results are joined with a
 * space. Example: "Crooked Lake (Big)" -> "crooked" + "big" -> "crooked big".
 *
 * @param {string|null|undefined} raw
 * @returns {string}
 */
export function normalizeName(raw) {
  if (raw == null) return "";
  const parenSegments = [];
  const main = String(raw).replace(/\(([^)]*)\)/g, (_match, inner) => {
    parenSegments.push(inner);
    return " ";
  });
  const segments = [main, ...parenSegments]
    .map(normalizeSegment)
    .filter((segment) => segment.length > 0);
  return segments.join(" ");
}

function normalizeSegment(text) {
  let s = String(text).toLowerCase();
  // Strip diacritics: decompose then drop combining marks.
  s = s.normalize("NFD").replace(/[̀-ͯ]/g, "");
  // Strip punctuation, keep letters/digits/whitespace.
  s = s.replace(/[^a-z0-9\s]/g, " ");

  let tokens = s.split(/\s+/).filter((t) => t.length > 0);
  if (tokens.length === 0) return "";

  // Expand abbreviations as whole words.
  tokens = tokens.map((t) => ABBREVIATIONS[t] || t);

  // Remove leading/trailing generic words, but never strip the last
  // remaining token (so a bare "Lake" or "Pond" survives as itself).
  while (tokens.length > 1 && GENERIC_WORDS.has(tokens[0])) tokens.shift();
  while (tokens.length > 1 && GENERIC_WORDS.has(tokens[tokens.length - 1])) tokens.pop();

  return tokens.join(" ");
}

// -- rule loading -------------------------------------------------------------

const NUMERIC_OP_SUFFIXES = ["_lt", "_gte"];

/**
 * Validate and compile a parsed rules.json document.
 *
 * @param {unknown} rulesJson
 * @returns {object} a frozen, compiled rules object suitable for
 *   evaluateRestriction / evaluateLake / evaluateAll.
 * @throws {Error} if the shape is invalid.
 */
export function loadRules(rulesJson) {
  if (rulesJson == null || typeof rulesJson !== "object" || Array.isArray(rulesJson)) {
    throw new Error("rules.json must be an object");
  }
  const { version, aircraft, aggregate, rules } = rulesJson;

  if (typeof version !== "string" || version.length === 0) {
    throw new Error('rules.json: "version" must be a non-empty string');
  }
  if (aggregate !== "worst_of") {
    throw new Error('rules.json: "aggregate" must be "worst_of"');
  }
  if (aircraft == null || typeof aircraft !== "object" || Array.isArray(aircraft)) {
    throw new Error('rules.json: "aircraft" must be an object');
  }
  if (typeof aircraft.name !== "string" || aircraft.name.length === 0) {
    throw new Error('rules.json: "aircraft.name" must be a non-empty string');
  }
  if (typeof aircraft.min_chord_ft !== "number" || !Number.isFinite(aircraft.min_chord_ft)) {
    throw new Error('rules.json: "aircraft.min_chord_ft" must be a finite number');
  }
  if (typeof aircraft.min_takeoff_mph !== "number" || !Number.isFinite(aircraft.min_takeoff_mph)) {
    throw new Error('rules.json: "aircraft.min_takeoff_mph" must be a finite number');
  }
  if (!Array.isArray(rules) || rules.length === 0) {
    throw new Error('rules.json: "rules" must be a non-empty array');
  }

  const seenIds = new Set();
  const compiledRules = rules.map((rule, index) => {
    if (rule == null || typeof rule !== "object" || Array.isArray(rule)) {
      throw new Error(`rules.json: rules[${index}] must be an object`);
    }
    const { id, when, verdict, note } = rule;
    if (typeof id !== "string" || id.length === 0) {
      throw new Error(`rules.json: rules[${index}].id must be a non-empty string`);
    }
    if (seenIds.has(id)) {
      throw new Error(`rules.json: duplicate rule id "${id}"`);
    }
    seenIds.add(id);
    if (when == null || typeof when !== "object" || Array.isArray(when)) {
      throw new Error(`rules.json: rules[${index}] ("${id}").when must be an object`);
    }
    if (!VALID_VERDICTS.has(verdict)) {
      throw new Error(
        `rules.json: rules[${index}] ("${id}").verdict must be one of ${VERDICT_ORDER.join(", ")}`,
      );
    }
    if (typeof note !== "string" || note.length === 0) {
      throw new Error(`rules.json: rules[${index}] ("${id}").note must be a non-empty string`);
    }
    return Object.freeze({ id, when: Object.freeze({ ...when }), verdict, note });
  });

  return Object.freeze({
    __compiled: true,
    version,
    aircraft: Object.freeze({ ...aircraft }),
    aggregate,
    rules: Object.freeze(compiledRules),
  });
}

function assertCompiled(rules) {
  if (!rules || rules.__compiled !== true) {
    throw new Error("rules must be produced by loadRules() before evaluating");
  }
}

function getPath(obj, path) {
  const parts = path.split(".");
  let cur = obj;
  for (const part of parts) {
    if (cur == null) return undefined;
    cur = cur[part];
  }
  return cur;
}

function resolveWhenValue(value, aircraft) {
  if (typeof value === "string" && value.startsWith("$aircraft.")) {
    const path = value.slice("$aircraft.".length);
    const resolved = getPath(aircraft, path);
    if (resolved === undefined) {
      throw new Error(`rules.json: unknown aircraft substitution "${value}"`);
    }
    return resolved;
  }
  return value;
}

function matchesWhen(restriction, when, aircraft) {
  for (const key of Object.keys(when)) {
    const rawValue = when[key];
    const opSuffix = NUMERIC_OP_SUFFIXES.find((suffix) => key.endsWith(suffix));

    if (opSuffix) {
      const field = key.slice(0, -opSuffix.length);
      const actual = restriction[field];
      const expected = resolveWhenValue(rawValue, aircraft);
      if (typeof actual !== "number" || typeof expected !== "number") return false;
      if (opSuffix === "_lt" && !(actual < expected)) return false;
      if (opSuffix === "_gte" && !(actual >= expected)) return false;
      continue;
    }

    const actual = restriction[key];
    if (rawValue === null) {
      if (actual !== null && actual !== undefined) return false;
    } else if (rawValue === "*") {
      if (actual === null || actual === undefined) return false;
    } else if (Array.isArray(rawValue)) {
      if (!rawValue.includes(actual)) return false;
    } else {
      const expected = resolveWhenValue(rawValue, aircraft);
      if (actual !== expected) return false;
    }
  }
  return true;
}

/**
 * Render a note template, substituting {field} / {nested.path} tokens with
 * values pulled from the restriction record. Missing/null values render as
 * empty string.
 *
 * @param {string} template
 * @param {object} restriction
 * @returns {string}
 */
export function renderNote(template, restriction) {
  const rendered = template.replace(/\{([a-zA-Z0-9_.]+)\}/g, (_match, path) => {
    const value = getPath(restriction, path);
    return value === null || value === undefined ? "" : String(value);
  });
  return rendered.replace(/[ \t]{2,}/g, " ").trim();
}

/**
 * Evaluate a single restriction record against a compiled rules object.
 * First matching rule (in rules.json order) wins. No match -> unknown.
 *
 * @param {object} restriction
 * @param {object} rules compiled rules object from loadRules()
 * @returns {{matched_rule: string|null, verdict: string, note: string}}
 */
export function evaluateRestriction(restriction, rules) {
  assertCompiled(rules);
  if (restriction == null || typeof restriction !== "object") {
    throw new Error("evaluateRestriction: restriction must be an object");
  }
  for (const rule of rules.rules) {
    if (matchesWhen(restriction, rule.when, rules.aircraft)) {
      return {
        matched_rule: rule.id,
        verdict: rule.verdict,
        note: renderNote(rule.note, restriction),
      };
    }
  }
  return { matched_rule: null, verdict: "unknown", note: "Unclassified restriction." };
}

function worstOf(verdicts) {
  for (const v of VERDICT_ORDER) {
    if (verdicts.includes(v)) return v;
  }
  return "clear";
}

/**
 * Evaluate a lake against its restrictions.
 *
 * Aggregation (worst_of): restricted > conditional > unknown > clear.
 * A lake with no restrictions and a name is clear; a lake with no name and
 * no restrictions is unknown. For a lake with no name and restrictions, the
 * aggregate verdict is floored at "unknown" (an unnamed match is never
 * reported better than unknown, but a real restricted/conditional finding
 * still wins, since that is worse than unknown).
 *
 * @param {{id: number, name: string|null, chord_ft?: number|null,
 *   area_acres?: number, public_access?: boolean, federal_unit?: string|null}} lake
 * @param {object[]} restrictions
 * @param {object} rules compiled rules object from loadRules()
 * @param {{mac_record_loaded?: boolean}} [options]
 * @returns {{id: number, verdict: string, reasons: object[], flags: string[]}}
 */
export function evaluateLake(lake, restrictions, rules, options = {}) {
  assertCompiled(rules);
  if (lake == null || typeof lake !== "object") {
    throw new Error("evaluateLake: lake must be an object");
  }
  const list = Array.isArray(restrictions) ? restrictions : [];
  const macRecordLoaded = options.mac_record_loaded !== false;

  const reasons = list.map((restriction) => {
    const { matched_rule, verdict, note } = evaluateRestriction(restriction, rules);
    return {
      restriction_id: restriction.restriction_id ?? null,
      rule_id: restriction.rule_id ?? null,
      matched_rule,
      verdict,
      note,
    };
  });

  let verdict;
  if (reasons.length === 0) {
    verdict = lake.name == null ? "unknown" : "clear";
  } else {
    verdict = worstOf(reasons.map((r) => r.verdict));
    if (lake.name == null) {
      verdict = worstOf([verdict, "unknown"]);
    }
  }

  const flags = [];
  if (lake.public_access === false) flags.push("no_public_access");
  if (typeof lake.chord_ft === "number" && lake.chord_ft < rules.aircraft.min_chord_ft) {
    flags.push("chord_below_minimum");
  }
  if (lake.federal_unit != null) flags.push("federal_overlay");
  if (list.some((r) => r && r.needs_review === true)) flags.push("needs_review");
  if (!macRecordLoaded) flags.push("mac_pending");

  return { id: lake.id ?? null, verdict, reasons, flags };
}

/**
 * Evaluate every lake in `lakes` against its restrictions.
 *
 * @param {object[]} lakes
 * @param {Record<string, object[]>} restrictionsByLakeId keyed by lake id
 *   (string or number keys both work, since JS object keys stringify).
 * @param {object} rules compiled rules object from loadRules()
 * @param {{mac_record_loaded?: boolean}} [options]
 * @returns {object[]}
 */
export function evaluateAll(lakes, restrictionsByLakeId, rules, options = {}) {
  assertCompiled(rules);
  if (!Array.isArray(lakes)) {
    throw new Error("evaluateAll: lakes must be an array");
  }
  const byId = restrictionsByLakeId || {};
  return lakes.map((lake) => evaluateLake(lake, byId[lake.id] || [], rules, options));
}
