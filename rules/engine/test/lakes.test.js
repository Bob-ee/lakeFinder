import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

import { loadRules, evaluateLake, evaluateAll } from "../index.js";

const here = path.dirname(fileURLToPath(import.meta.url));
const rulesPath = path.join(here, "..", "..", "rules.json");
const fixturesPath = path.join(here, "..", "..", "fixtures", "lakes.json");

const rules = loadRules(JSON.parse(readFileSync(rulesPath, "utf8")));
const cases = JSON.parse(readFileSync(fixturesPath, "utf8"));

test("lake fixtures load and cover at least 12 cases", () => {
  assert.ok(Array.isArray(cases));
  assert.ok(cases.length >= 12, `expected at least 12 fixtures, got ${cases.length}`);
});

for (const { name, lake, restrictions, options, expect } of cases) {
  test(`lake fixture: ${name}`, () => {
    const result = evaluateLake(lake, restrictions, rules, options || {});
    assert.equal(result.verdict, expect.verdict);
    assert.deepEqual(result.flags, expect.flags);
    assert.equal(result.id, lake.id);
    assert.equal(result.reasons.length, restrictions.length);
  });
}

test("evaluateAll runs every fixture lake through evaluateLake and returns matching results", () => {
  const lakes = cases.map((c) => c.lake);
  const restrictionsByLakeId = {};
  for (const c of cases) restrictionsByLakeId[c.lake.id] = c.restrictions;

  // evaluateAll only takes one shared options object; group fixtures by
  // their options so each group's expectations still hold.
  const byOptionsKey = new Map();
  for (const c of cases) {
    const key = JSON.stringify(c.options || {});
    if (!byOptionsKey.has(key)) byOptionsKey.set(key, []);
    byOptionsKey.get(key).push(c);
  }

  for (const [key, group] of byOptionsKey) {
    const options = JSON.parse(key);
    const groupLakes = group.map((c) => c.lake);
    const results = evaluateAll(groupLakes, restrictionsByLakeId, rules, options);
    assert.equal(results.length, group.length);
    for (let i = 0; i < group.length; i++) {
      assert.equal(results[i].verdict, group[i].expect.verdict, group[i].name);
      assert.deepEqual(results[i].flags, group[i].expect.flags, group[i].name);
    }
  }

  // Sanity: evaluateAll with every lake at once still matches evaluateLake
  // for lakes whose fixture used the default options ({}).
  const defaultGroup = cases.filter((c) => !c.options || Object.keys(c.options).length === 0);
  const allResults = evaluateAll(lakes, restrictionsByLakeId, rules, {});
  const byId = new Map(allResults.map((r) => [r.id, r]));
  for (const c of defaultGroup) {
    assert.equal(byId.get(c.lake.id).verdict, c.expect.verdict, c.name);
  }
});
