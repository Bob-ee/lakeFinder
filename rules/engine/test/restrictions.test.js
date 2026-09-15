import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

import { loadRules, evaluateRestriction } from "../index.js";

const here = path.dirname(fileURLToPath(import.meta.url));
const rulesPath = path.join(here, "..", "..", "rules.json");
const fixturesPath = path.join(here, "..", "..", "fixtures", "restrictions.json");

const rules = loadRules(JSON.parse(readFileSync(rulesPath, "utf8")));
const cases = JSON.parse(readFileSync(fixturesPath, "utf8"));

test("restriction fixtures load and cover at least 25 cases", () => {
  assert.ok(Array.isArray(cases));
  assert.ok(cases.length >= 25, `expected at least 25 fixtures, got ${cases.length}`);
});

test("restriction fixtures cover every restriction_type enum value", () => {
  const enumValues = [
    "no_vessels",
    "no_motorboats",
    "slow_no_wake",
    "no_high_speed",
    "high_speed_hours",
    "speed_limit",
    "no_towing",
    "no_pwc",
    "no_wake_zone_marked",
    "shore_buffer",
    "not_applicable",
    "mac_ordinance",
    "mac_conditional",
    "federal_no_landing",
    "other",
  ];
  const covered = new Set(cases.map((c) => c.restriction.restriction_type));
  for (const value of enumValues) {
    assert.ok(covered.has(value), `no fixture exercises restriction_type "${value}"`);
  }
});

for (const { name, restriction, expect } of cases) {
  test(`restriction fixture: ${name}`, () => {
    const result = evaluateRestriction(restriction, rules);
    assert.equal(result.matched_rule, expect.matched_rule);
    assert.equal(result.verdict, expect.verdict);
  });
}
