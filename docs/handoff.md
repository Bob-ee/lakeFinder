# Handoff: Michigan seaplane lake map

Written 2026-09-19 for the next agent picking this up; updated the same day after the briefing (step B) and rivers
were built and Bobby set the direction in `docs/big-water-design.md` and `docs/nationwide.md`. **Read those two next.** Read this first, then `docs/design.md` (the spec) and
`docs/data-contract.md` (the schemas every part is built against). `README.md` has the status table; `CLAUDE.md`
has the one-screen orientation.

Owner: Bobby. Aircraft: SeaRey. Remote: https://github.com/Bob-ee/lakeFinder, branch `main`.

## 1. What is built (phase 1, "static map")

Everything in design section 10 phase 1 exists, runs statewide, and is committed. Nine commits so far; the log
reads as a build order.

| part | state | verify with |
|---|---|---|
| `pipeline/` (Python 3.12, uv) | all 8 design stages plus `suggest`; `geometry` also computes `extent_by_bearing`; statewide run takes ~6 min warm | `cd pipeline && uv run pytest -q` (275 pass) and `uv run ruff check seaplane_pipeline tests` |
| `rules/` (JS, zero deps) | 24 rules, shared fixtures, CLI used by the pipeline, module imported by the client | `cd rules && npm test` (117 pass) |
| `web/` (Vite, TS, MapLibre) | map with verdict outlines, search, 3-snap sheet / iPad panel, `?lake=`, disclaimer, client-side rules engine | `cd web && npm run typecheck && npm run build` |
| `api/` (FastAPI, Python 3.12, uv) | briefing generator with the evening outlook, in-process scheduler, settings, airport lookup; `/api/wind/*` is an empty slot | `cd api && uv run pytest -q` (219 pass) and `uv run ruff check .` |
| briefing client | Briefing tab + chip under the search bar, outlook hour strip and run-history chips, settings dialog, `?briefing=1` | same web commands; screenshots were checked at 390 px and 1180 px, light and dark |
| hosting | `Caddyfile`, `docker-compose.yml` written (api has rw data + manual mounts, `API_UPSTREAM` for a non-Docker install), **not deployed anywhere yet** | |
| data | `data/out/` holds a full statewide pack (158 MB) including `lake_extents.json` (6,054 lakes) and a live `briefing.json`; gitignored, rebuild with `uv run seaplane all` | `pmtiles show data/out/lakes.pmtiles` |

Statewide numbers as of the last run (2026-09-19, rivers included): 83 county pages, 1,134 restriction records,
12,563 water bodies (10,783 `kind: lake` + 1,780 `kind: river`), 910 of 1,119 active restrictions matched, verdicts
restricted 548 / conditional 281 / clear 11,246 / unknown 488. 181 waterway rules attach to 157 river polygons; 24 of
them could not be narrowed below the county and carry `reach_unresolved` (the sheet says the rule covers part of the
river). Lake ids did not change when rivers were added.

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

- **Direction from Bobby (2026-09-19):** he flies mostly Lake St. Clair (his saved home airport is KONZ, Grosse Ile)
  and wants to know *where on the lake* to land; the mechanism must work on every lake; the app is headed for a
  nationwide community release. Big water (St. Clair, Detroit River, the Great Lakes) is not on the map yet. Plan:
  `docs/big-water-design.md`; constraints: `docs/nationwide.md`; sources, with a verified St. Clair polygon and
  trial fetch rays: `docs/gis-sources.md` 2026-09-19 addendum.
- **Rivers:** excluded from the briefing's ranked water until the per-point wave field exists (`chord_ft` on a river
  says nothing about width). A county-level river rule marks every same-name polygon in the county restricted plus
  `reach_unresolved`; conservative on purpose. 59 waterway rules remain unmatched: 25 name Great Lakes water
  (Detroit River 11, St. Clair River 5, Little Traverse Bay 3, ...), 8 are an Antrim `parse-dnr` header bug
  (waterbody parsed as "Rivers"). `NAME_SUFFIX_RE` in `match.py` never trims a suffix that ends the string (worked
  around on the waterway track only). One match was lost to the lake/river gate and wants an override: Crawford
  "Lake Margrethe Channel in Harbor Beach Subdivision".
- **`index.json` is 4.84 MB of the 5 MB budget** after rivers, and `lakes.pmtiles` grew to 23 MB. Cheapest lever:
  drop `name_norm` from the payload (~330 KB; the client already imports `normalizeName`).

- **Briefing limits are placeholders.** `docs/briefing-design.md` section 8 lists what only Bobby can supply (SeaRey
  wave, crosswind, wind and gust numbers, VFR minimums, radius, morning window). He edits them in the app's briefing
  settings; nothing needs code. First real run note: Open-Meteo gusts read high (3 kt G12 scored "marginal, gusts"
  on the 8 kt gust-spread default), so expect him to loosen `gust_spread_ok` or ask for a gust floor.
- **Ceiling comes only from the METAR and TAF** (neither Open-Meteo nor NWS hourly carries a cloud base). Hours the
  TAF does not cover read `ceiling_known: false` and the ceiling factor is skipped. KPTK's TAF covers the next
  morning from the 18:00 run on, so the evening outlook is normally covered.
- **Briefing nits:** `home_airport.runways` has no length, so parallel runways tie and the label can name the shorter
  one (add `length_ft`; aviationweather returns `dimension`). NDBC buoys are fetched and parsed but unused until the
  pipeline tags Great Lakes shoreline lakes. The steep-chop rule exists (`wave.steep_chop`) but is not wired in.
  `web/` has no test runner.

- **Review queue.** 138 lake-named restrictions are unmatched and 76 matches are low-confidence. Bobby is working
  through `data/manual/overrides.suggested.yaml` (regenerate with `uv run seaplane suggest`). When he adds entries
  to `overrides.yaml`, rerun `match overlay classify build --skip-basemap`.
- **MAC record.** Not obtained. Every lake carries `mac_pending`. Draft request: `docs/mdot-mac-record-request.md`.
  When it arrives: fill `mac_record.yaml`, set `loaded: true`, rerun from `match`.
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

**B. Daily briefing: BUILT 2026-09-19** (`docs/briefing-design.md`, schemas in the contract's "Briefing" section).
Deterministic, no LLM. Runs at 06, 09, 12, 15, 18, 20, 22 local; the 18:00, 20:00, and 22:00 runs are Bobby's
"plan tomorrow morning" outlook (hourly sunrise-to-noon scoring, best window, fog factor, run history with trend,
confidence, optional ntfy push). What is left: deploy it with step A (the api needs a `launchd` job, see
`api/README.md`, and the Mac must stay awake for the evening runs), Bobby's real limits, and deciding on ntfy.
To try it locally: `cd api && uv run seaplane-api` and `cd web && npm run dev`, then open `/?briefing=1`.

**C. Phase 2, flight mode.** Design sections 7.5, 7.7, 7.9. Client slots already exist and are wired as no-ops:
`web/src/location/` (geolocation, follow-me, heading-up, wake lock), `web/src/lists/` (Nearest tab with forward
cone; the row component has a `trailing` slot for distance/bearing), the recenter control (currently home view).
Wind needs `/api/wind/*` on the `api` service: the service now exists (`api/seaplane_api/wind.py` is the documented
empty slot, and `fetch/aviationweather.py`, `fetch/openmeteo.py`, `fetch/ndbc.py` already cover three of the
sources); the Synoptic token is server-side. `chord_bearing_deg` is already in `index.json` for the crosswind component. The
usable-water layer already renders at z12+.

**D. Phase 3, offline.** Plan is written in `web/src/sw/README.md` (cache-first shell, OPFS pack with sha256, pmtiles
OPFS `Source`, install prompt, pack versioning). `pack.json` already has sizes and sha256s. Test on the real iPad
early; iOS storage behavior is the risk. Self-host basemap glyphs and sprites here.

**E. Phase 4, personal layer and MAC.** `web/src/saved/` slot, IndexedDB store `saved` already created (db
`seaplane`), star button currently toasts. Sync endpoint on the `api` service. MAC ingestion once the record arrives.

**F. More of Bobby's ideas.** The briefing was the first; he may have more. Capture each in `docs/design.md` as a
new numbered section (or `docs/ideas.md` if not yet decided), decide which step it belongs to, and update the
contract first for anything that changes a schema. Ask him which should jump the queue.

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
- Run the four test suites before every commit (pipeline, rules, api, web typecheck + build); under 15 seconds combined.
- Vite's dev server listens on `localhost` (IPv6), not `127.0.0.1`; curl it as `http://localhost:<port>`.
