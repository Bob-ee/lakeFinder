# Michigan Seaplane Lake Map

A self-hosted map and PWA that answers one question fast, from a phone or iPad, offline:
**is there a known restriction on landing a seaplane on this lake?** Michigan inland lakes, SeaRey.

The tool never says "legal." It says *no known restriction*, *restricted*, or *conditional*, and always shows the
reasons, the citation, and the physical numbers next to the verdict. Not legal advice.

Spec: [`docs/design.md`](docs/design.md). Schemas: [`docs/data-contract.md`](docs/data-contract.md).

## Layout

| path | what |
|---|---|
| `pipeline/` | Python (uv) pipeline: fetch DNR pages and GIS, parse restrictions, match to lakes, geometry, classify, build tiles |
| `rules/` | Data-driven rules engine (JS, zero deps) shared by the pipeline and the client, plus `rules.json` and fixtures |
| `web/` | Vite + TypeScript + MapLibre GL PWA. Run instructions and module layout: [`web/README.md`](web/README.md) |
| `data/manual/` | Hand-maintained `overrides.yaml` and `mac_record.yaml` |
| `data/raw/`, `data/out/` | Pipeline inputs and outputs (gitignored; `data/out/` is what gets served at `/data/`) |
| `Caddyfile`, `docker-compose.yml` | Hosting |

## Quick start

```sh
# rules engine tests
cd rules && npm test

# pipeline (downloads sources on first run; see pipeline/README.md)
cd pipeline && uv sync && uv run seaplane fetch && uv run seaplane parse-dnr && uv run seaplane match \
  && uv run seaplane geometry && uv run seaplane overlay && uv run seaplane classify && uv run seaplane build

# client
cd web && npm install && npm run dev
```

## Phases

1. **v1 static map** (this build): pipeline stages 1 to 7 statewide, map with verdict outlines, tap for sheet, search, `?lake=`.
2. **v2 flight mode**: location, follow-me, heading-up, wake lock, nearest list, usable-water layer, wind.
3. **v3 offline**: service worker, data pack to OPFS, pmtiles OPFS source, install prompts.
4. **v4 personal layer and MAC**: saved lakes, sync, MAC record ingestion, review tooling, weekly cron.

## Disclaimer

This tool summarizes published Michigan DNR watercraft controls and other public data. It is not legal advice and
does not confirm that a landing is legal. Verify restrictions, property rights, and conditions before operating.
