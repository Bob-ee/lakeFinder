# rules/

Shared, data-driven rules engine for the seaplane lake map. One implementation
(`engine/index.js`, zero-dependency ESM) is used by both the Python pipeline
(via `engine/cli.js`, run through Node) and the web client (which imports
`engine/index.js` directly, including `normalizeName`). The schema below is
authoritative in `docs/data-contract.md`; this file just orients you.

- `rules.json` — the data: `version`, `aircraft` (SeaRey specs), and an ordered
  list of `{id, when, verdict, note}` rules. First matching rule wins per
  restriction; no match -> `verdict: "unknown"`.
- `engine/index.js` — `loadRules`, `evaluateRestriction`, `evaluateLake`,
  `evaluateAll`, `normalizeName`, `renderNote`, `VERDICT_ORDER`.
- `engine/cli.js` — batch CLI for the pipeline's `classify` stage.
- `fixtures/` — `restrictions.json`, `lakes.json`, `names.json`. These are the
  spec: both `engine/test/` and the (future) Python pipeline tests load them.

**Add a rule:** edit `rules.json`. Put more specific `when` clauses (extra
keys, zone/hours/season constraints, `_lt`/`_gte`) before general ones for the
same `restriction_type`, add a fixture case to `fixtures/restrictions.json`
with the expected `matched_rule`/`verdict`, then run the tests.

**Run tests:** `cd rules && npm test`, or directly:
`node --test engine/test/*.test.js` (a bare directory argument, e.g.
`node --test engine/test/`, does not recurse on this Node build — always pass
a glob).
