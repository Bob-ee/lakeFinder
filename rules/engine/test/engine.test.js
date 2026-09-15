import test from "node:test";
import assert from "node:assert/strict";

import {
  loadRules,
  evaluateRestriction,
  evaluateLake,
  renderNote,
  normalizeName,
  VERDICT_ORDER,
} from "../index.js";

const baseRulesJson = {
  version: "test-1",
  aircraft: { name: "SeaRey", min_chord_ft: 2000, min_takeoff_mph: 40 },
  aggregate: "worst_of",
  rules: [
    { id: "r1", when: { restriction_type: "no_vessels" }, verdict: "restricted", note: "no vessels" },
  ],
};

test("VERDICT_ORDER is the worst-to-best order from the data contract", () => {
  assert.deepEqual(VERDICT_ORDER, ["restricted", "conditional", "unknown", "clear"]);
});

test("loadRules accepts a well-formed document", () => {
  const rules = loadRules(baseRulesJson);
  assert.equal(rules.version, "test-1");
  assert.equal(rules.aircraft.min_chord_ft, 2000);
  assert.equal(rules.rules.length, 1);
});

test("loadRules rejects a non-object", () => {
  assert.throws(() => loadRules(null));
  assert.throws(() => loadRules("nope"));
  assert.throws(() => loadRules([]));
});

test("loadRules rejects a missing/blank version", () => {
  assert.throws(() => loadRules({ ...baseRulesJson, version: "" }));
  assert.throws(() => {
    const { version, ...rest } = baseRulesJson;
    loadRules(rest);
  });
});

test("loadRules rejects an aggregate other than worst_of", () => {
  assert.throws(() => loadRules({ ...baseRulesJson, aggregate: "best_of" }));
});

test("loadRules rejects a malformed aircraft block", () => {
  assert.throws(() => loadRules({ ...baseRulesJson, aircraft: { name: "SeaRey" } }));
  assert.throws(() =>
    loadRules({ ...baseRulesJson, aircraft: { ...baseRulesJson.aircraft, min_chord_ft: "2000" } }),
  );
});

test("loadRules rejects an empty rules array", () => {
  assert.throws(() => loadRules({ ...baseRulesJson, rules: [] }));
});

test("loadRules rejects a rule with an invalid verdict", () => {
  assert.throws(() =>
    loadRules({ ...baseRulesJson, rules: [{ id: "bad", when: {}, verdict: "maybe", note: "x" }] }),
  );
});

test("loadRules rejects duplicate rule ids", () => {
  assert.throws(() =>
    loadRules({
      ...baseRulesJson,
      rules: [
        { id: "dup", when: { restriction_type: "a" }, verdict: "clear", note: "x" },
        { id: "dup", when: { restriction_type: "b" }, verdict: "clear", note: "y" },
      ],
    }),
  );
});

test("evaluateRestriction and evaluateLake require a compiled rules object", () => {
  assert.throws(() => evaluateRestriction({}, baseRulesJson));
  assert.throws(() => evaluateLake({ id: 1, name: "x" }, [], baseRulesJson));
});

test("when: null means the field must be null/absent", () => {
  const rules = loadRules({
    ...baseRulesJson,
    rules: [{ id: "null-hours", when: { restriction_type: "slow_no_wake", hours: null }, verdict: "restricted", note: "n" }],
  });
  assert.equal(evaluateRestriction({ restriction_type: "slow_no_wake", hours: null }, rules).matched_rule, "null-hours");
  assert.equal(evaluateRestriction({ restriction_type: "slow_no_wake" }, rules).matched_rule, "null-hours");
  assert.equal(
    evaluateRestriction({ restriction_type: "slow_no_wake", hours: { text: "x" } }, rules).matched_rule,
    null,
  );
});

test('when: "*" means the field must be present and non-null', () => {
  const rules = loadRules({
    ...baseRulesJson,
    rules: [{ id: "has-hours", when: { restriction_type: "slow_no_wake", hours: "*" }, verdict: "conditional", note: "n" }],
  });
  assert.equal(
    evaluateRestriction({ restriction_type: "slow_no_wake", hours: { text: "x" } }, rules).matched_rule,
    "has-hours",
  );
  assert.equal(evaluateRestriction({ restriction_type: "slow_no_wake", hours: null }, rules).matched_rule, null);
  assert.equal(evaluateRestriction({ restriction_type: "slow_no_wake" }, rules).matched_rule, null);
});

test("when: an array value means any-of", () => {
  const rules = loadRules({
    ...baseRulesJson,
    rules: [{ id: "either", when: { restriction_type: ["no_towing", "no_pwc"] }, verdict: "clear", note: "n" }],
  });
  assert.equal(evaluateRestriction({ restriction_type: "no_towing" }, rules).matched_rule, "either");
  assert.equal(evaluateRestriction({ restriction_type: "no_pwc" }, rules).matched_rule, "either");
  assert.equal(evaluateRestriction({ restriction_type: "no_vessels" }, rules).matched_rule, null);
});

test("when: _lt and _gte numeric suffix operators", () => {
  const rules = loadRules({
    ...baseRulesJson,
    rules: [
      { id: "below-10", when: { restriction_type: "speed_limit", speed_mph_lt: 10 }, verdict: "restricted", note: "n" },
      { id: "at-least-10", when: { restriction_type: "speed_limit", speed_mph_gte: 10 }, verdict: "clear", note: "n" },
    ],
  });
  assert.equal(evaluateRestriction({ restriction_type: "speed_limit", speed_mph: 5 }, rules).matched_rule, "below-10");
  assert.equal(evaluateRestriction({ restriction_type: "speed_limit", speed_mph: 10 }, rules).matched_rule, "at-least-10");
  assert.equal(evaluateRestriction({ restriction_type: "speed_limit", speed_mph: 15 }, rules).matched_rule, "at-least-10");
});

test('when: a "$aircraft.x" value is substituted from rules.aircraft', () => {
  const rules = loadRules({
    ...baseRulesJson,
    rules: [
      {
        id: "below-takeoff",
        when: { restriction_type: "speed_limit", speed_mph_lt: "$aircraft.min_takeoff_mph" },
        verdict: "restricted",
        note: "n",
      },
    ],
  });
  assert.equal(evaluateRestriction({ restriction_type: "speed_limit", speed_mph: 39 }, rules).matched_rule, "below-takeoff");
  assert.equal(evaluateRestriction({ restriction_type: "speed_limit", speed_mph: 40 }, rules).matched_rule, null);
});

test("first match wins; later rules are not consulted once one matches", () => {
  const rules = loadRules({
    ...baseRulesJson,
    rules: [
      { id: "first", when: { restriction_type: "no_towing" }, verdict: "restricted", note: "first" },
      { id: "second", when: { restriction_type: "no_towing" }, verdict: "clear", note: "second" },
    ],
  });
  const result = evaluateRestriction({ restriction_type: "no_towing" }, rules);
  assert.equal(result.matched_rule, "first");
  assert.equal(result.verdict, "restricted");
});

test("an unmatched restriction gets verdict unknown and the default note", () => {
  const rules = loadRules(baseRulesJson);
  const result = evaluateRestriction({ restriction_type: "totally_unknown" }, rules);
  assert.equal(result.matched_rule, null);
  assert.equal(result.verdict, "unknown");
  assert.equal(result.note, "Unclassified restriction.");
});

test("renderNote substitutes flat and nested template fields", () => {
  const restriction = { scope_description: "the north bay", hours: { text: "6:30 p.m. to 10:00 a.m." }, speed_mph: 30 };
  assert.equal(
    renderNote("Zone: {scope_description}, hours {hours.text}, limit {speed_mph} mph.", restriction),
    "Zone: the north bay, hours 6:30 p.m. to 10:00 a.m., limit 30 mph.",
  );
});

test("renderNote substitutes missing/null fields as empty string", () => {
  assert.equal(renderNote("value=[{missing_field}]", {}), "value=[]");
  assert.equal(renderNote("value=[{hours.text}]", { hours: null }), "value=[]");
});

test("evaluateLake floors an unnamed lake's verdict at unknown but never downgrades a worse verdict", () => {
  const rules = loadRules({
    ...baseRulesJson,
    rules: [
      { id: "clear-rule", when: { restriction_type: "clear_type" }, verdict: "clear", note: "n" },
      { id: "restricted-rule", when: { restriction_type: "restricted_type" }, verdict: "restricted", note: "n" },
    ],
  });
  const clearOnly = evaluateLake({ id: 1, name: null }, [{ restriction_type: "clear_type" }], rules);
  assert.equal(clearOnly.verdict, "unknown");

  const restrictedToo = evaluateLake(
    { id: 2, name: null },
    [{ restriction_type: "clear_type" }, { restriction_type: "restricted_type" }],
    rules,
  );
  assert.equal(restrictedToo.verdict, "restricted");
});

test("normalizeName is re-exported and usable directly from the engine", () => {
  assert.equal(normalizeName("Mud Lake"), "mud");
});
