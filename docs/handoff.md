# Handoff: seaplane lake map

Rewritten 2026-09-20 for the next agent; updated 2026-09-25 (region wind, depth, colour-blind wave display) and 2026-09-28 (flight mode). Read this, then the docs
in section 2 as you need them. Everything described here is committed on `main` (local; **not pushed** since
`d793fca`, push when Bobby says).

Owner: Bobby. Aircraft: SeaRey amphibian. Base: KONZ (Grosse Ile, on the Detroit River). Home water: Lake St. Clair.
Remote: https://github.com/Bob-ee/lakeFinder.

## 1. What this is now

A self-hosted map + PWA that answers, for a seaplane pilot:

1. **Is there a known restriction on landing here?** Verdicts `restricted | conditional | clear | unknown`, always
   with the rule text and citation. Never "legal".
2. **Is it a day to fly, and tomorrow morning?** A deterministic, no-LLM briefing anchored on a home airport, rerun
   on a schedule, with an evening outlook at 18:00 / 20:00 / 22:00 for planning the next morning.
3. **Where on the water is it calm in this wind?** A per-point wave field for every water body of 100 acres or more,
   grouped into named regions ("Anchor Bay 11 in, Big Muscamoot Bay 8 in, open middle 18 in").

**Direction Bobby has set, and it overrides older docs:** he mostly flies Lake St. Clair and cares about *where in
the lake*; whatever is built must work on **every** lake with **nothing hand-drawn per lake**; the app is headed for
a **nationwide release to the seaplane community**, so Michigan is the pilot state. New code takes its region from
config (no new Michigan constants). A state without regulation data must read "no data", never "clear".

## 2. Docs, in the order they matter

| doc | what it is |
|---|---|
| `docs/data-contract.md` | **Authoritative schemas** between all parts. Change it before code. Sections "Wave field" and "Briefing" are the newest. |
| `docs/big-water-design.md` | Why and how of the wave field; section 7 lists what changed while building. |
| `docs/nationwide.md` | What going national changes, the rule for new work, suggested sequence. |
| `docs/briefing-design.md` | Briefing algorithm (scoring table, outlook, SPM wave model, live-feed quirks). Section 8 = numbers only Bobby can give. |
| `docs/gis-sources.md` | Every data source with recipes; the 2026-09-19 addendum covers NHD, GNIS, bathymetry, NWS marine zones, St. Clair stations. |
| `deploy/README.md` | How the app is deployed (no Docker: caddy + the api under launchd, pushed from the dev Mac). |
| `docs/design.md` | Original spec (phases, legal model, client layout, disclaimer text). Older than the direction above. |
| `docs/dnr-pages.md`, `docs/mdot-mac-record-request.md` | DNR page structure; the drafted MAC record request. |

## 3. State of each part

| part | state | verify |
|---|---|---|
| `pipeline/` Python 3.12, uv | `fetch → parse-dnr → match → geometry → overlay → classify → wavefield → build`, plus `review`, `suggest`. Statewide warm run ≈ 8 min (`geometry` 2 min, `wavefield` 1.5 min, `build --skip-basemap` 35 s). | `cd pipeline && uv run pytest -q` (405) · `uv run ruff check seaplane_pipeline tests` |
| `rules/` JS, zero deps | `engine/` (24 rules, verdict cap via `big_water_partial`) and `waves/` (wave math shared with the client). Shared fixtures in `rules/fixtures/` are the agreement between JS and Python. | `cd rules && npm test` (146) |
| `api/` FastAPI, uv | Briefing generator, in-process scheduler (06/09/12/15/18/20/22 local), settings, airport lookup, wave-field reader, regions, `home_water` with observations and marine second opinion. `/api/wind/*` is an empty slot. | `cd api && uv run pytest -q` (365) · `uv run ruff check .` |
| `web/` Vite, TS, MapLibre | Map, search, 3-snap sheet / iPad panel, client-side rules engine, Briefing tab + chip, settings dialog, Water section (wind dial, region list), wave overlay + legend, home-water action. No test runner. | `cd web && npm run typecheck && npm run build` |
| hosting | **Deployed on `labmac`** (Bobby's home server, tailnet): https://labmac.tail22b52.ts.net:10000. `deploy/push.sh labmac` updates it. `docker-compose.yml` is the unused alternative. | `curl https://labmac.tail22b52.ts.net:10000/api/health` |
| data | `data/out/` is a full pack (gitignored): 12,571 water bodies (10,783 lake, 1,780 river, 5 great_lake incl. Lake St. Clair, 3 connecting_water); 942 of 1,118 active DNR rules matched; verdicts 544 restricted / 299 conditional / 11,240 clear / 488 unknown; 81,436 wave points over 1,286 water bodies, 41,015 with depth (after the 2026-09-25 rebuild). | `uv run seaplane all` rebuilds |

Run it locally: `cd api && uv run seaplane-api` · `cd web && npm run dev` · open `/?briefing=1` or `/?lake=657423876`
(Lake St. Clair; Detroit River is `1625759164`). One-off briefing without touching the live file:
`uv run seaplane-api briefing --once --out /tmp/b.json` (set `SEAPLANE_DATA_MANUAL` to a temp dir to try settings).

## 4. How the pieces fit

```
DNR pages ─▶ parse-dnr ─▶ restrictions ─┐
state hydro + big-water layers ─▶ geometry ─▶ lakes.parquet ─▶ match ─▶ overlay ─▶ classify (node rules engine)
                                     │                                                  │
                                     └─▶ wavefield (points: fetch[16], run[8], depth, label) ─┴─▶ build ─▶ data/out/
data/out/ served at /data/ ─▶ client (re-runs the rules engine and the wave math in the browser)
                         └──▶ api reads index.json, lake_extents.json, wave_points.* ─▶ writes data/out/briefing.json
```

- **Contract first**, then parallel agents on disjoint directories, then integrate. That is how every piece here was
  built and why they fit.
- **Same math on both sides.** Verdicts: `rules/engine` runs in the pipeline and in the client. Waves: `rules/waves`
  (JS) and `api/seaplane_api/briefing/wave.py` (Python) both pass `rules/fixtures/waves.json`;
  `rules/fixtures/make_wave_fixtures.py` regenerates the fixtures.
- **One `selectLake(id)` path** in `web/src/state/index.ts` for search, taps, lists, URL.
- **Ids** hash `name_norm|lat|lon` and must not change for existing water: `KIND_ORDER` in `geometry.py` is
  append-only for that reason. Every change so far verified the old id set byte-identical.
- **Wording:** verdicts never say "legal"; briefing scores are `favorable | marginal | unfavorable` with the limiting
  factor, never "go" or "safe"; on `great_lake` / `connecting_water` every flag means "part of this water".
- Manual data in `data/manual/`: `overrides.yaml` (hand, wins over matching), `mac_record.yaml`,
  `overrides.suggested.yaml` (generated), `settings.json` (**Bobby's live settings, gitignored; never overwrite it**).

## 5. Waiting on Bobby

- **Review of the wave field on water he knows.** Do the bay names and relative numbers on Lake St. Clair match what
  he sees? This is the best calibration available. First ground truth (2026-09-19 15:00): buoy 45147 16 in mid-lake;
  computed 18 in at the gust; marine model 14 in.
- **Real limits** (wave height for the hull, gust spread, crosswind, VFR minimums, radius, morning window): all
  placeholders, all editable in the app's briefing settings. Open-Meteo gusts read high (3 kt G12 scored "marginal,
  gusts"), so expect `gust_spread_ok` to move or a gust floor to be requested.
- Setting his **home water** (button on the Lake St. Clair sheet), whether he wants the **ntfy push**, the MAC record
  request, and working the review queue in `overrides.suggested.yaml`.
- He was running his own `seaplane-api` (port 8000) and `vite` (5173) on the dev Mac while testing; that api process
  predates the wave-field code and needs a restart to show regions (the deployed one on labmac is current). Leave
  his processes alone; use other ports.
- **The LaunchDaemon install on labmac** (section 6A), then the app on his phone and iPad home screens.

## 6. Next work, in order

**A. Deploy: done 2026-09-20, one step left for Bobby.** `labmac` is a shared home server (OrbStack containers,
Jellyfin, two other `tailscale serve` sites on 443 and 8443), so lakeFinder has its own ports in
`~/lakeFinder/deploy/local.env` there: caddy on 127.0.0.1:8100, the api on 127.0.0.1:8000, HTTPS on **10000**
(`tailscale serve` only offers 443 / 8443 / 10000). Sleep was already disabled on that Mac. It is installed as
**LaunchAgents** (smoke test, verified end to end from the dev Mac); Bobby chose LaunchDaemons, which needs his sudo
password: `ssh -t labmac 'cd lakeFinder && deploy/install.sh --daemon --serve'` (removes the agents first). **The
daemon path has not been run yet**; check `launchctl print system/com.lakefinder.api` and the two logs after it.
Then: home-screen install on the phone and iPad, and watch that the 18:00 / 20:00 / 22:00 runs land in
`outlook.runs`. The server's `settings.json` was seeded from the dev Mac once and is now its own file. A data or
code change reaches the server only through `deploy/push.sh labmac`; there is no pipeline on the server.

**B. Small fixes.** Done 2026-09-20: colliding federal ids (one record, many `lake_ids`), `name_norm` out of
`index.json` (4.54 MB now), the Antrim "Rivers" header and "LAKE MACATAWA, PINE CREEK BAY", `NAME_SUFFIX_RE`,
runway `length_ft`, and a rule that names only a connecting channel or canal is now `scope: "zone"` on the lakes it
connects (12 lakes went restricted to conditional; see the contract's parsing rules). Still open:
1. Overrides to consider: Crawford "Lake Margrethe Channel in Harbor Beach Subdivision" (lost to the lake/river
   gate); "Saugatuck Harbor" (Allegan) is unattached and may be a Kalamazoo River rule, not Lake Michigan.
2. "LAKE OAKLAND CANAL" names only a canal in the reverse word order and stays lakewide. The trailing form is
   ambiguous against "HI-LAND LAKE AND CONNECTING CANALS AND CHANNELS", which names the lake too.
3. ~~`no_vessels` and `no_motorboats` ignore `scope`~~ **done 2026-09-25** (831fbac): zone-scoped ones are
   `conditional`. The corpus did have them: 12 records on 12 water bodies (Rouge River ×4, Saint Joseph River ×3, Au
   Train, Flint, Pere Marquette, Two Hearted, Whites Lake) go restricted → conditional. Needs `seaplane classify` +
   `build` + `deploy/push.sh labmac` to reach the served data.
4. MAC synthetic records have the same latent id collision the federal ones had (each entry carries its own
   `lake_id`, and the record is not loaded yet).
5. Antrim "Clam river" is unmatched: no Clam River polygon in the county's river layer.

**C. Wave field, second pass.**
- **Wind per region: done 2026-09-25** (f96939a). Each region takes the forecast of its centroid's 0.1° cell
  (KONZ: 63 cells instead of 45, still two Open-Meteo calls); `regions[]` rows carry `wind`. Candidates also keep only
  regions with water inside `radius_nm` (before, a far region could be a candidate's "best water"); the home water
  keeps all. First real run: St. Clair regions ranged 4-12 kt in one morning.
- **Depth: done 2026-09-25** (c507542): every NCEI Great Lakes grid (Erie, Huron, Michigan, Superior; data-driven
  `GRID_SOURCES` with bboxes) plus a 2-cell shore snap. Points with depth 8,552 → 41,015 of 81,436. 131 St. Clair
  shoal / Flats points stay unknown (grid above datum, nothing wet within 2 cells); ~60% of the St. Clair River too
  (the Erie grid has it as land). Still open: GLOBathy as an optional lake-level depth nationwide.
- Lake Huron and Lake Superior polygons stop at the international line (rays through that edge count as the 100 km
  cap). Whole-lake polygons come with NHD.
- Saved landing spots snapping to the nearest sample point (`web/src/saved/` is still a stub); `steep_chop` exists in
  `wave.py` but is not scored; NDBC is used only for home-water observations.
- Rivers: 171 have points, nearly all read "run too short", which is right; a county-level river rule marks every
  same-name polygon in the county restricted + `reach_unresolved` (conservative on purpose).

**D. National groundwork** (`docs/nationwide.md`): move Michigan's polygons to USGS NHD (water = NHDWaterbody ∪
NHDArea, dissolved; bound name queries by bbox; render each big water body once before trusting it; a second test
lake for the 17% area gap seen on Cass Lake), pull Michigan constants into a region config, airspace beyond
`STATE='MI'`, then a second state to discover the regulation-adapter interface. Hosting / accounts before inviting
anyone; Open-Meteo's free tier is non-commercial.

**E. Original phases.** **Flight mode (phase 2): done 2026-09-28** (contract "Flight mode"; commits ad81267..e097977):
`/api/wind/stations` + `/api/wind/point` (METAR + NDBC; Synoptic adapter written from docs, never seen live, needs
`SYNOPTIC_TOKEN`), location / follow-me / heading-up (on at 10 kt, off below 7) / wake lock / own marker
(`web/src/location/`), Nearest tab with forward cone and a min-run filter (`web/src/lists/`), wind layer with
source-by-shape arrows and the sheet's Wind block (`web/src/wind/`; lands into the wind when the run allows, chord
components only when it does not). Dev: `?fakefix=lat,lon,speed,course[,hold]` (dev build only),
`SEAPLANE_WIND_FIXTURES=1`. Screenshots used fixtures: verify against the live wind API on the real phone/iPad.
Still open: offline (plan in
`web/src/sw/README.md`; test on the real iPad early), saved lakes + sync, MAC ingestion when the record arrives.

**Standing data-quality risk:** an unmatched restriction appears on no lake, so that lake reads clear. 150 lake-named
and 34 waterway rules are unmatched. Idea not built: draw unmatched rules at their PLSS section as "restriction here,
lake unresolved".

## 7. Decisions that are easy to undo by accident

- Lake rows in the briefing are **water-only** (waves, run, ice); airport weather lives in the header and blocks.
  Rank = score, then distance. Waves are computed at the **gust**.
- Water crosswind is scored only when the run into the wind is too short and the long axis must be used.
- Ceiling comes only from METAR / TAF (`ceiling_known: false` otherwise). No model ceiling is invented.
- Lake-level fetch = max of the wind bin and its two neighbors; run = the wind bin. With a wave field, regions
  replace both.
- Artificial (clip) edge rule is **10 m** off a 2 km chord: 60 m painted 40 inch bands on Torch and Houghton.
- GNIS `Channel` names label only rivers and connecting waters; a name needs 2 points, a compass sector 3.
- Depth is sampled only on `great_lake` / `connecting_water` (a DEM cell under an inland lake is its surface).
- Inland polygons are subtracted from big water, connecting waters from the Great Lakes (the state's Lake Michigan
  polygon covers 97% of Torch Lake). Big water joins the **waterway** matching track only, so the inland "Lake Erie"
  pond and "Saint Clair Lake" keep their own rules.
- `big_water_partial` on the restriction record drives the verdict cap; do not add `kind` to the engine's lake input.
- The marine wave API snaps to the nearest wet cell; values snapped more than 10 nm are dropped.
- Basemap is z0–12 (125 MB); lakes carry their own z14 detail. Selection outline is thinner on rivers and big water.
- The fetch-ray trial table in `docs/gis-sources.md` was cast on grid north; the shipped field uses true bearings.

## 8. Environment and working conventions

- **Server `labmac`** (ssh works from the dev Mac, user `bobbywhiteley`, Apple silicon): brew `uv` + `caddy`, the
  Tailscale CLI only inside the app bundle, checkout at `~/lakeFinder` (rsynced, not a git clone), logs in
  `~/Library/Logs/lakefinder-{api,web}.log`. It runs Bobby's other services: never take ports 8080 / 8090 or the
  443 / 8443 serve entries, and `deploy/install.sh` refuses to.
- This Mac: uv, Python 3.12, node 24, npm 11, tippecanoe, pmtiles CLI, GDAL, Chrome. **No Docker, no Anthropic
  credentials** (the pipeline's optional LLM pass skips itself; the briefing must stay LLM-free by requirement).
- Hosts: `gisago.mcgi.state.mi.us` resets TLS from here, use `gisagocss.state.mi.us`; `gis.fws.gov` 502s (Living
  Atlas mirror is used); OSM Overpass is blocked (and OSM is ODbL: avoid for core layers). NWS needs the User-Agent
  `lakeFinder (https://github.com/Bob-ee/lakeFinder)`; never put an email address in a request.
- Screenshots: `node web/scripts/screenshot.mjs <url> <out.png> <w> <h> [dark|light] [scrollTopPx]` drives headless
  Chrome over CDP with software WebGL and seeds the disclaimer ack (`CDP_PORT` to run two at once). Playwright is not
  installed; do not add heavy browser tooling without asking. Vite listens on `localhost` (IPv6), not `127.0.0.1`.
- `web` dev server serves `/data/` from `../data/out` when `pack.json` exists, falling back per file to
  `web/dev-fixtures/` (`npm run fixtures` regenerates them, including a synthetic wave field).
- Node 24 `node --test <dir>` does not recurse; `rules/package.json` lists globs.
- A local hook blocks writing files whose names contain findings, report, summary, or analysis.
- **How Bobby works:** fan out to subagents (cheaper models for recon and mechanical work, stronger for geometry,
  parsing, UI), contract first so they run in parallel, main session for decisions, integration, verification. He
  wants terse status and a recap, and he will redirect mid-task; when he does, generalize rather than special-case.
  Look at real output before calling anything done: most of the bugs fixed on 2026-09-19 were only visible in a real
  run or a screenshot.
- Commits: plain messages, `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>` trailer, all four suites green
  first. Never commit `data/raw`, `data/work`, `data/out`, `data/cache`, `*.pmtiles`, or `data/manual/settings.json`.
