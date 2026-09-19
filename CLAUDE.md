# Michigan seaplane lake map

Self-hosted map + PWA answering "is there a known restriction on landing a seaplane on this lake?" for Michigan
inland lakes. Owner: Bobby. Aircraft: SeaRey.

- **New session? Read `docs/handoff.md` first** (state, decisions, known issues, roadmap).
- `docs/design.md` is the spec. `docs/data-contract.md` is the schema contract between the three parts below; change it first.
- `pipeline/` Python (uv, 3.12): fetch → parse-dnr → match → geometry → overlay → classify → build → review. Writes `data/out/`.
- `rules/` JS rules engine + `rules.json` + shared fixtures. Pipeline runs it via `node rules/engine/cli.js`; client imports it.
- `web/` Vite + TypeScript + MapLibre PWA. Dev server serves `/data/` from `data/out/` (or `web/dev-fixtures/`).
- `data/manual/` hand-maintained overrides and the MAC record. `data/raw/` and `data/out/` are gitignored.

Commands: `cd pipeline && uv run seaplane --help` · `cd rules && npm test` · `cd web && npm run dev`.
Tests: `cd pipeline && uv run pytest -q` · `cd rules && npm test` · `cd web && npm run typecheck && npm run build`.

Conventions: the app never says "legal"; verdicts are restricted / conditional / clear / unknown and always show the
citation. Deployment target is a headless MacBook on the tailnet (Docker Compose per the design doc, or brew caddy +
launchd; decide when deploying). Never commit `data/raw` or `data/out`.
