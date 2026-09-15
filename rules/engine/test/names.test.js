import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

import { normalizeName } from "../index.js";

const here = path.dirname(fileURLToPath(import.meta.url));
const fixturesPath = path.join(here, "..", "..", "fixtures", "names.json");
const cases = JSON.parse(readFileSync(fixturesPath, "utf8"));

test("names fixtures load and cover at least 30 cases", () => {
  assert.ok(Array.isArray(cases));
  assert.ok(cases.length >= 30, `expected at least 30 fixtures, got ${cases.length}`);
});

for (const { raw, norm } of cases) {
  test(`normalizeName(${JSON.stringify(raw)}) === ${JSON.stringify(norm)}`, () => {
    assert.equal(normalizeName(raw), norm);
  });
}

test("normalizeName is idempotent on already-normalized input", () => {
  for (const { norm } of cases) {
    assert.equal(normalizeName(norm), norm, `normalizeName(${JSON.stringify(norm)}) should be a fixed point`);
  }
});

test("normalizeName(null) and normalizeName(undefined) return empty string", () => {
  assert.equal(normalizeName(null), "");
  assert.equal(normalizeName(undefined), "");
});
