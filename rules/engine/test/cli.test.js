import test from "node:test";
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import path from "node:path";

const here = path.dirname(fileURLToPath(import.meta.url));
const cliPath = path.join(here, "..", "cli.js");
const rulesPath = path.join(here, "..", "..", "rules.json");

function runCli(args, input) {
  return spawnSync(process.execPath, [cliPath, ...args], {
    input,
    encoding: "utf8",
    maxBuffer: 64 * 1024 * 1024,
  });
}

test("cli evaluates lakes from stdin and writes a JSON array to stdout", () => {
  const input = JSON.stringify({
    lakes: [
      { id: 1, name: "Clear Lake", chord_ft: 3000, area_acres: 50, public_access: true, federal_unit: null },
      { id: 2, name: null, chord_ft: 3000, area_acres: 5, public_access: true, federal_unit: null },
    ],
    restrictions: {
      1: [{ restriction_id: "x1", rule_id: null, restriction_type: "no_motorboats", scope: "lakewide", scope_description: null, hours: null, season: null, speed_mph: null, needs_review: false }],
    },
  });

  const result = runCli(["--rules", rulesPath], input);
  assert.equal(result.status, 0, result.stderr);
  const out = JSON.parse(result.stdout);
  assert.equal(out.length, 2);
  assert.equal(out[0].id, 1);
  assert.equal(out[0].verdict, "restricted");
  assert.equal(out[1].id, 2);
  assert.equal(out[1].verdict, "unknown");
});

test("cli respects --mac-loaded false and sets mac_pending", () => {
  const input = JSON.stringify({
    lakes: [{ id: 1, name: "Walloon Lake", chord_ft: 3000, area_acres: 50, public_access: true, federal_unit: null }],
    restrictions: {},
  });
  const result = runCli(["--rules", rulesPath, "--mac-loaded", "false"], input);
  assert.equal(result.status, 0, result.stderr);
  const out = JSON.parse(result.stdout);
  assert.deepEqual(out[0].flags, ["mac_pending"]);
});

test("cli exits non-zero with a clear stderr message on invalid JSON stdin", () => {
  const result = runCli(["--rules", rulesPath], "{not json");
  assert.notEqual(result.status, 0);
  assert.match(result.stderr, /not valid JSON/);
});

test("cli exits non-zero with a clear stderr message when 'lakes' is missing", () => {
  const result = runCli(["--rules", rulesPath], JSON.stringify({ restrictions: {} }));
  assert.notEqual(result.status, 0);
  assert.match(result.stderr, /"lakes" must be an array/);
});

test("cli exits non-zero when --rules is missing", () => {
  const result = runCli([], JSON.stringify({ lakes: [], restrictions: {} }));
  assert.notEqual(result.status, 0);
  assert.match(result.stderr, /--rules/);
});

test("cli exits non-zero with a clear stderr message on a bad --rules path", () => {
  const result = runCli(["--rules", path.join(here, "does-not-exist.json")], JSON.stringify({ lakes: [], restrictions: {} }));
  assert.notEqual(result.status, 0);
  assert.match(result.stderr, /could not read rules file/);
});

test("cli handles a large batch of lakes quickly", () => {
  const lakes = [];
  const restrictions = {};
  for (let i = 0; i < 20000; i++) {
    lakes.push({ id: i, name: `Lake ${i}`, chord_ft: 2500, area_acres: 10, public_access: true, federal_unit: null });
  }
  const input = JSON.stringify({ lakes, restrictions });
  const start = Date.now();
  const result = runCli(["--rules", rulesPath], input);
  const elapsedMs = Date.now() - start;
  assert.equal(result.status, 0, result.stderr);
  const out = JSON.parse(result.stdout);
  assert.equal(out.length, 20000);
  assert.ok(elapsedMs < 10000, `expected under 10s for 20000 lakes, took ${elapsedMs}ms`);
});
