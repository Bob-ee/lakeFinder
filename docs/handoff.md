# Handoff: Michigan seaplane lake map

Written 2026-09-19 for the next agent picking this up. Read this first, then `docs/design.md` (the spec) and
`docs/data-contract.md` (the schemas every part is built against). `README.md` has the status table; `CLAUDE.md`
has the one-screen orientation.

Owner: Bobby. Aircraft: SeaRey. Remote: https://github.com/Bob-ee/lakeFinder, branch `main`.

## 1. What is built (phase 1, "static map")

Everything in design section 10 phase 1 exists, runs statewide, and is committed. Nine commits so far; the log
reads as a build order.

| part | state | verify with |
|---|---|---|
| `pipeline/` (Python 3.12, uv) | all 8 design stages plus `suggest`; statewide run takes ~5 min warm | `cd pipeline && uv run pytest -q` (252 pass) and `uv run ruff check seaplane_pipeline tests` |
| `rules/` (JS, zero deps) | 24 rules, shared fixtures, CLI used by the pipeline, module imported by the client | `cd rules && npm test` (117 pass) |
| `web/` (Vite, TS, MapLibre) | map with verdict outlines, search, 3-snap sheet / iPad panel, `?lake=`, disclaimer, client-side rules engine | `cd web && npm run typecheck && npm run build` |
| hosting | `Caddyfile`, `docker-compose.yml` written, **not deployed anywhere yet** | |
| data | `data/out/` holds a full statewide pack (158 MB); gitignored, rebuild with `uv run seaplane all` | `pmtiles show data/out/lakes.pmtiles` |

Statewide numbers as of the last run: 83 county pages, 1,134 restriction records (34 flagged), 10,783 lakes,
764 of 1,119 active restrictions matched, verdicts restricted 484 / conditional 207 / clear 9,612 / unknown 480.

The client was verified in headless Chrome against the real pack (see section 6 for the flags). Screenshots
confirmed: disclaimer with build date, docked panel at 1180 px, verdict outlines, restriction list with rule text
and DNR link, the "MAC record not loaded" notice, chord and bearing pair.

## 2. How the pieces fit

```
DNR county pages ──crawl──▶ raw html ──parse-dnr──▶ restrictions.jsonl ─┐
hydrography/PLSS/BAS/NPS/FWS/FAA ──fetch──▶ data/cache, data/raw        │
                                     └──geometry──▶ lakes.parquet ──match──▶ matches.json
                                                          │                   │
                                          overlay ──▶ overlays.json           │
                                                          └───── classify (node rules/engine/cli.js) ──▶ verdicts.json
                                                                                 └── build ──▶ data/out/{index.json, restrictions.json,
                                                                                               rules.json, *.pmtiles, pack.json}
web dev server / Caddy serve data/out at /data/ ──▶ client loads index.json, re-runs the same rules engine per lake
```

- **Contract first.** If a field or file changes, edit `docs/data-contract.md` before code. Three agents built the
  three parts in parallel against it and that is why they fit.
- **One `selectLake(id)` path** in `web/src/state/index.ts` serves search, map tap, lists, and the URL param. Add
  new entry points through it.
- **Verdict never says "legal".** Four values, always with reasons and citation. Disclaimer text is design section 13.
- Manual data lives in `data/manual/`: `overrides.yaml` (wins over matching), `mac_record.yaml`, and the generated
  `overrides.suggested.yaml`.

## 3. Decisions made along the way (deviations from the design doc)

1. **Basemap is z0–12, 125 MB.** The z14 Michigan extract is ~550 MB. Lakes carry their own z14 detail in
   `lakes.pmtiles`. `seaplane build --basemap-maxzoom 14` if you want to revisit. Basemap glyphs and sprites still
   load from protomaps.github.io while online; self-hosting them is part of the offline phase.
2. **Hydrography host.** `gisago.mcgi.state.mi.us` (named in the design) resets TLS from this Mac; the pipeline
   uses `gisagocss.state.mi.us`. Details and every dataset recipe: `docs/gis-sources.md`.
3. **County and township** come from the Michigan Geographic Framework counties and minor civil divisions layers
   joined on the lake centroid, not Census TIGER.
4. **Hydrography has no permanent id**, so lake ids hash `name_norm|lat|lon` (contract section "Identifiers"). Ids
   are stable as long as the polygon does not move; a re-digitized lake gets a new id.
5. **`restriction_id` for multi-clause entries** appends the clause label so siblings differ (contract).
6. **Two restriction types were added** after recon: `shore_buffer` (local echo of the statewide 100 ft rule) and
   `not_applicable` (airboats, mooring, rafts, headcount limits). Both are `clear`.
7. **No PWC rules exist** in the current DNR corpus; `no_pwc` is kept for the future and a hit flags review.
8. **Airspace flag counts only surface-floor airspace** (`LOWER_VAL == 0`); Detroit's Class B shelves at 2,500 ft
   and up are not a lake-landing concern. `--airspace-any-floor` restores the literal behavior.
9. **Matcher scoring** (documented at the top of `pipeline/seaplane_pipeline/match.py`): exact 1.0, qualifier-
   stripped 0.6, qualifier *conflict* (Little vs Big) 0.15 so it never auto-accepts, tokens, and a fuzzy tier that
   can only reach the review band. Rivers, creeks, channels, and bays are tagged `kind: waterway` in
   `unmatched.json` because they have no lake polygon.
10. **LLM extraction is optional.** `parse-dnr --llm` runs only when Anthropic credentials exist (none on this Mac).
    The regex pass classified every fixture entry without it (0% needs_review on fixtures, 3% statewide).
11. **Rescinded rules** stay in `restrictions.jsonl` with `status: rescinded` and are dropped at build.
12. **Selection highlight** in the client is driven by filtered layers rather than only feature-state, and the pulse
    stops after three cycles so MapLibre can reach `idle` (needed for the fallback marker and for phase 2 wind
    fetches). Reasoning in `web/src/map/index.ts`.

## 4. Known issues and open items

- **Review queue.** 138 lake-named restrictions are unmatched and 76 matches are low-confidence. Bobby is working
  through `data/manual/overrides.suggested.yaml` (regenerate with `uv run seaplane suggest`). When he adds entries
  to `overrides.yaml`, rerun `match overlay classify build --skip-basemap`.
- **MAC record.** Not obtained. Every lake carries `mac_pending`. Draft request: `docs/mdot-mac-record-request.md`.
  When it arrives: fill `mac_record.yaml`, set `loaded: true`, rerun from `match`.
- **`index.json` is 3.98 MB** against a 5 MB target. Levers: raise `--min-unnamed-acres` in geometry, or drop
  `name_norm` from the payload and compute it client-side (the client already imports `normalizeName`).
- **480 unknown verdicts are the unnamed polygons over 20 acres.** By design; they still render gray so a pilot
  sees "no data" rather than "clear".
- **Unmatched restrictions do not appear on any lake**, so those lakes show clear. This is the biggest data-quality
  risk and the reason the review queue matters. A future improvement: render unmatched-restriction section
  centroids as a "restriction here, lake unresolved" marker so nothing is silently clear.
- **Cross-county lakes** appear once per county in the DNR data; both records attach to one polygon and the sheet
  shows both. Deduplicating by rule id in the sheet is a small client change if it looks noisy.
- **Design open items** still open: SB 626/627 effect on R 259.401, whether Bobby's iPad has GPS, and the real
  `min_chord_ft` for the SeaRey (2,000 ft placeholder flags 377 Oakland lakes).
- **Node 24 `node --test <dir>` does not recurse**; `rules/package.json` uses a glob.

## 5. Where to go next, in order

**A. Deploy to the headless MacBook (the rest of v1).** Design section 8. Decide Docker Compose (as written) versus
brew `caddy` + `launchd` (fewer moving parts on macOS); the `Caddyfile` works for either. Steps: build `web/dist`,
copy or rsync `data/out` (or run the pipeline there), Caddy on :8080 behind `tailscale serve`, confirm HTTPS on the
tailnet, open it on the phone and iPad, add to home screen. Then a `launchd` (or cron container) job for the weekly
pipeline with a diff notification (design phase 4 mentions this; the diff file already exists).

**B. Phase 2, flight mode.** Design sections 7.5, 7.7, 7.9. Client slots already exist and are wired as no-ops:
`web/src/location/` (geolocation, follow-me, heading-up, wake lock), `web/src/lists/` (Nearest tab with forward
cone; the row component has a `trailing` slot for distance/bearing), the recenter control (currently home view).
Wind needs the `api` service (FastAPI, `/api/wind/*`) that the Compose file references but that does not exist yet;
the Synoptic token is server-side. `chord_bearing_deg` is already in `index.json` for the crosswind component. The
usable-water layer already renders at z12+.

**C. Phase 3, offline.** Plan is written in `web/src/sw/README.md` (cache-first shell, OPFS pack with sha256, pmtiles
OPFS `Source`, install prompt, pack versioning). `pack.json` already has sizes and sha256s. Test on the real iPad
early; iOS storage behavior is the risk. Self-host basemap glyphs and sprites here.

**D. Phase 4, personal layer and MAC.** `web/src/saved/` slot, IndexedDB store `saved` already created (db
`seaplane`), star button currently toasts. Sync endpoint on the `api` service. MAC ingestion once the record arrives.

**E. Bobby's new ideas.** He has more he wants in the program. Capture them in `docs/design.md` as a new numbered
section (or `docs/ideas.md` if they are not yet decided), decide which phase each belongs to, and update the contract
first for anything that changes a schema. Ask him which of them should jump ahead of B, C, D.

## 6. Environment and working conventions

- Tools on this Mac: uv 0.10, Python 3.12 via uv, node 24, npm 11, tippecanoe 2.79, pmtiles CLI, GDAL 3.12, git,
  Chrome. **No Docker.** No Anthropic credentials (`ant` CLI absent, no `ANTHROPIC_API_KEY`).
- Headless Chrome screenshots of the client need software WebGL:
  `"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" --headless=new --use-angle=swiftshader
  --enable-unsafe-swiftshader --ignore-gpu-blocklist --virtual-time-budget=20000 --screenshot=out.png <url>`.
  Playwright is not installed; do not install heavy browser tooling without asking.
- `web` dev and preview servers serve `/data/` from `../data/out` when `pack.json` exists there, else from
  `web/dev-fixtures/` (`npm run fixtures` regenerates; pmtiles fixtures are gitignored). Range requests are handled
  by the Vite plugin in `vite.config.ts`.
- Bobby's working style: fan work out to subagents, cheaper models for recon and mechanical builds, stronger ones for
  parsing, geometry, and UI integration; define the contract first so agents run in parallel; keep the main session
  for integration and verification. He reads terse status updates and a final recap.
- Commits: plain messages describing the change, `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`
  trailer. Never commit `data/raw`, `data/work`, `data/out`, or `*.pmtiles`.
- Run the three test suites before every commit; they take under 10 seconds combined.
