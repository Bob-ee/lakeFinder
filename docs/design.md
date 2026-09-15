# Michigan Seaplane Lake Map: Design Doc

Handover doc for Claude Code. Owner: Bobby. Status: design complete, not started.

## 1. Purpose

A self-hosted map and PWA that answers one question fast, from a phone or iPad, in the air and offline: **is there a known restriction on landing a seaplane on this lake?** Scope is Michigan inland lakes. Aircraft is a SeaRey (LSA amphibian).

The tool never says "legal." It says "no known restriction," "restricted," or "conditional," and always shows the reasons, the citation, and the physical numbers next to the verdict.

## 2. Legal model (Michigan)

Michigan's seaplane rule is Mich. Admin. Code R 259.401. Summary of what matters to the classifier:

1. Seaplanes may use any navigable public-trust waterway for landing, docking, and takeoff unless restricted.
2. **Aviation-side restriction:** a local ordinance restricting seaplanes that the Aeronautics Commission (MAC) has approved, or an MAC interim order (R 259.401(3), (4), (12)). MAC keeps a public record under (13). This record is not online. Obtain via email/FOIA to MDOT Aeronautics. Hand-curated data file.
3. **Watercraft-side restriction (R 259.401(2)(c)):** a seaplane may not do anything on the water that a motorboat may not do. Carve-out: the *statewide* speed limit does not apply during landing/takeoff, but the carve-out is void where "any other restrictions applicable to watercraft" conflict. So DNR **special local watercraft controls** (Mich. Admin. Code R 281.7xx) are the primary dataset. Example: Big and Little School Lot Lakes, Rose Twp, Oakland Co. (R 281.763.3) prohibit planing speeds, which effectively bars landing and takeoff.
4. **Statewide watercraft rules** shape where on the lake you can touch down: slow-no-wake within 100 ft of shore, docks, rafts, swimmers, anchored vessels. Used to draw a usable-water buffer, not to change the verdict.
5. **Federal overlays:** NPS units (36 CFR 2.17 bans aircraft landings except designated areas), USFWS refuges. Verdict = restricted unless a designated exception is on file.
6. **Property/public trust:** R 259.401 covers waters "available for use under the public trust doctrine" and does not override riparian rights. Proxy: public access (DNR boating access site, public park frontage). Not a verdict input; shown as a flag.

Not legal advice. Doc and app must carry a disclaimer.

## 3. Verdict taxonomy

| Verdict | Color | Meaning |
|---|---|---|
| `restricted` | red | Motorboats prohibited; lakewide slow-no-wake; no planing / high-speed prohibited lakewide; MAC-approved seaplane ordinance; federal no-landing unit |
| `conditional` | amber | Time-of-day or seasonal high-speed windows; restriction applies to a named basin/zone only; MAC conditions on file |
| `clear` | green | No restriction found that touches landing/takeoff (skiing/towing-only bans, PWC-only bans do not count) |
| `unknown` | gray | Unnamed waterbody, or parse failure flagged for review |

Secondary flags (independent of verdict): `no_public_access`, `chord_below_minimum`, `federal_overlay`, `user_verified`, `user_note`.

## 4. Data sources

| Source | What | Where | Notes |
|---|---|---|---|
| Michigan Geographic Framework Hydrography Polygons | Lake polygons, names, DNR lake keys | gis-michigan.opendata.arcgis.com, dataset `midnr::hydrography-polygons` (also served by `gisago.mcgi.state.mi.us/arcgis/rest/services/OpenData/michigan_geographic_framework/MapServer`) | Primary geometry. Filter to lake/pond feature types. Fall back to USGS NHD HR (Michigan) if fields are missing. |
| DNR Special Local Watercraft Controls | Per-county rule listings with lake name, township, PLSS section/T/R, restriction text, rule number | `https://www.michigan.gov/dnr/managing-resources/laws/controls/localcontrols/<county>/...` | URL slug varies by county (`/oakland/local-watercraft-controls`, `/cheboygan/watercraft`). Crawl the parent index page and follow links; do not hardcode 83 URLs. Same rules exist in the Michigan Administrative Code (R 281.7xx) as a cross-check. |
| PLSS sections | Section polygons for geolocating rules | Michigan GIS Open Data (search "PLSS") | Used to resolve "Section 16, T4N R7E" to a centroid. |
| DNR Boating Access Sites | Public launch points | `midnr::prd-boating-access-sites` and `midnr::michigan-public-boating-access-sites` | Public-access flag. |
| Federal boundaries | NPS and USFWS units | PAD-US or NPS/USFWS boundary services | Federal overlay flag. |
| FAA airspace | Class B/C/D, SUA | FAA NASR 28-day subscription (shapefiles) or FAA ADDS ArcGIS services | Context layer only. |
| FAA airports | Airport points | NASR APT | Context layer, basemap. |
| MAC seaplane record | Approved ordinances, overrides, interim orders | Request from MDOT Aeronautics | `data/manual/mac_record.yaml`, hand-maintained. Seed with Lake Angelus. |
| Overrides | Match fixes and manual verdicts | `data/manual/overrides.yaml` | Reviewed by hand. |

## 5. Pipeline (Python)

Runs on the homelab on a schedule (weekly). Tooling: `uv`, Python 3.12, GeoPandas/Shapely, httpx, BeautifulSoup, tippecanoe, pmtiles CLI.

Stages, each a CLI subcommand with cached inputs so reruns are cheap:

1. `fetch` : download hydrography, PLSS, BAS, federal, FAA, and crawl DNR county pages. Store raw responses under `data/raw/<date>/`.
2. `parse-dnr` : extract structured restrictions from each county page. Two passes:
   - Deterministic regex for the common templates (slow-no-wake, high-speed prohibited, motorboats prohibited, skiing bans, hour windows, section/T/R lines).
   - LLM extraction with a fixed JSON schema for entries the regex cannot fully parse. Every LLM-parsed entry gets `needs_review: true` until a human clears it.
   - Output: `restrictions.jsonl`, one row per (lake, rule) with fields: `rule_id`, `county`, `township`, `lake_name_raw`, `plss` (T/R/S list), `restriction_type` (enum), `scope` (`lakewide` | `zone` with description), `hours`, `season`, `raw_text`, `source_url`, `fetched_at`.
   - Diff against the previous run; changed or new entries go to a review queue file.
3. `match` : join restrictions to lake polygons. Strategy: PLSS section centroid, then candidate lakes intersecting or within 1 km of that section, then normalized name match (strip "Lake", expand "Lk", handle Big/Little/North/South/Upper/Lower). Score and pick; anything under a confidence threshold goes to `overrides.yaml` review. Overrides file wins over automatic matching.
4. `geometry` : per lake compute `area_acres`, `longest_chord_ft` (longest straight line fully inside the polygon; approximate via sampled pairs of hull vertices then verify containment), `shore_buffer` (polygon eroded by 100 ft, for the usable-water layer), centroid, bbox, stable numeric `id`.
5. `overlay` : flags for public access (BAS point within 50 m of polygon), federal unit containment, airspace class at centroid.
6. `classify` : run the shared rules engine (section 6) to prebake `verdict` and `reasons` into the index. Client re-runs the same engine so rules can change without a tile rebuild.
7. `build` : emit
   - `lakes.geojson` → `lakes.pmtiles` (tippecanoe, min zoom 6, max zoom 14, keep all small polygons at high zoom; include `id`, `name`, `verdict`, `flags`).
   - `usable_water.pmtiles` (eroded polygons, zoom 12+).
   - `overlays.pmtiles` (federal units, airspace, BAS points, airports).
   - `index.json` : array of `{id, name, name_norm, county, township, lat, lon, bbox, area_acres, chord_ft, verdict, flags, restriction_ids}`. Target under 5 MB.
   - `restrictions.json` : keyed by `restriction_id`, full detail including `raw_text` and `source_url`.
   - `basemap.pmtiles` : Protomaps Michigan extract trimmed to water, coastline, major roads, place labels, airports. Target under 150 MB.
   - `pack.json` : manifest with `version` (ISO date), file list, sizes, sha256.
8. `review` : prints the review queue (unmatched rules, low-confidence matches, LLM-parsed entries) as a table. Bobby edits `overrides.yaml` and reruns from `match`.

Tests: fixture county pages with known expected restrictions; matcher tests for the duplicate-name cases (Long, Mud, Round, Silver); geometry tests on a few hand-measured lakes; rules engine tests shared with the client (same JSON fixtures).

## 6. Rules engine (data-driven)

`rules/rules.json`, evaluated by one module compiled for both Python (pipeline) and JS (client). Simplest approach: write the engine once in JS, run it in the pipeline via Node, or port it in both languages with a shared fixture test suite. Pick the former.

Shape:

```json
{
  "version": "2026-09-15",
  "rules": [
    {
      "id": "no-planing-lakewide",
      "when": {"restriction_type": "no_high_speed", "scope": "lakewide"},
      "verdict": "restricted",
      "note": "High-speed/planing prohibited lakewide. Takeoff and landing require planing."
    },
    {
      "id": "no-planing-zone",
      "when": {"restriction_type": "no_high_speed", "scope": "zone"},
      "verdict": "conditional",
      "note": "Planing prohibited in {scope.description}. Use the rest of the lake."
    },
    {
      "id": "hours-window",
      "when": {"restriction_type": "high_speed_hours"},
      "verdict": "conditional",
      "note": "High speed allowed only {hours}."
    },
    {
      "id": "ski-only",
      "when": {"restriction_type": "no_towing"},
      "verdict": "clear",
      "note": "Towing ban only; does not affect landing."
    }
  ],
  "aggregate": "worst_of",
  "aircraft": {"name": "SeaRey", "min_chord_ft": 2000}
}
```

Rules are evaluated in order per restriction; lake verdict is the worst across its restrictions (`restricted` > `conditional` > `clear`). MAC record entries and federal overlays are injected as synthetic restrictions so the same engine handles them. `aircraft.min_chord_ft` drives the `chord_below_minimum` flag, editable in the app settings.

## 7. Client (PWA)

Stack: Vite, vanilla TS or Preact (keep it small), MapLibre GL JS, pmtiles JS, idb for IndexedDB, Workbox for the service worker. No framework-heavy UI.

### 7.1 Layout

- Full-screen map. Top: search box. Right edge: recenter, heading-up toggle, layers. Bottom: sheet (section 7.4) and a tab strip for Nearest / Saved.
- iPad landscape: sheet docks as a right-side panel (about 380 px). Same component, CSS breakpoint.
- High contrast, large touch targets (min 48 px), one-handed reach for primary controls. Lake status drawn as a thick colored outline, not a fill, so the water shape stays readable. Dark and light themes; default follows system.

### 7.2 Map layers

- Basemap (trimmed Protomaps).
- Lakes with verdict outline color. Feature-state highlight for the selected lake (thicker, pulsing outline plus light fill). Requires numeric `id` on tile features (`promoteId`).
- Usable-water erosion layer at zoom 12+ (subtle hatch or lighter fill).
- Overlays toggle: airspace, federal units, BAS launch points, airports.
- Saved-lake markers at all zooms.
- Own-position marker with course arrow.

### 7.3 Search

- Source: `index.json` loaded into memory on startup.
- Typeahead: prefix match on `name_norm` first, then substring, then fuzzy fallback (fuse.js or a small trigram scorer). Query normalization mirrors the pipeline's (strip "Lake", expand "Lk", etc.). Debounce 100 ms, max 8 rows.
- Row: verdict dot, name, county, township, distance and bearing if location is on. Sort by distance when location is on, else alphabetical. Recent searches shown on empty query.
- Select: `fitBounds` to bbox with padding, zoom capped at 15, then open the sheet at half. Fallback marker at centroid if the polygon is not rendered at the current zoom.
- One `selectLake(id)` path shared by search, map tap, list rows, and the `?lake=<id>` URL param.

### 7.4 Detail bottom sheet

Three snap points.

- **Peek (~90 px):** verdict color bar, name, county, one-line verdict phrase, star button, distance/bearing. Map stays interactive.
- **Half:** chord length, area, public access (launch name), restriction list (type, scope, hours), federal flags, user note, tags, verified toggle, last-landed date.
- **Full:** raw rule text, rule number, source URL, MAC record entry if any, data pack build date, "copy report" button (copies lake id and rule ids to clipboard).

Rule: nothing in peek requires reading a sentence.

### 7.5 Nearest list

- Tab next to Saved. Rows use the same component as search results. Sorted by distance from current position; optional forward-cone filter (±45° of GPS course) when moving above 30 kt.
- Tap row → `selectLake`.

### 7.6 Saved lakes

- Star on the sheet. IndexedDB store `saved` keyed by lake id: `{id, saved_at, tags[], note, verified, last_landed}`.
- Saved tab lists them with tag filter. Distinct map marker.
- Export/import JSON in settings.
- Phase 4: sync endpoint on the homelab (`PUT /api/saved/<device>`, `GET /api/saved`), last-write-wins per lake id, no auth beyond the tailnet.

### 7.7 Location and flight mode

- Geolocation API with `enableHighAccuracy`, `watchPosition`. Follow-me on by default when location permission is granted; any manual pan pauses it; recenter button resumes.
- Heading-up uses GPS `heading` when `speed` is above a threshold; otherwise north-up.
- Wake Lock API to keep the screen on while follow-me is active.
- iOS PWA: location permission is per standalone app; document in the in-app help.
- Wi-Fi-only iPads have no GPS. Support an MFi external GPS via system location; do not attempt to read Stratux directly.

### 7.8 Offline and data pack

- Service worker: app shell precached, cache-first for all app assets. Never network-first for the shell; a dead Tailscale tunnel in the air must not hang launch.
- Data pack: settings screen with "Download Michigan pack." Downloads files listed in `pack.json` into OPFS with progress and sha256 verification. Store `pack.json` version. On launch, if online, compare against server manifest and show "update available."
- pmtiles `Source` implementation that serves byte ranges from the OPFS file. Use `FileSystemSyncAccessHandle` in a worker where available, `File.slice` otherwise.
- `index.json`, `restrictions.json`, and `rules.json` are part of the pack; search and classification are fully client-side.
- Installed PWA required for durable storage on iOS. Show an install prompt hint on first run.

### 7.9 Wind (online only)

Two views: a **wind layer** of observation stations across the visible map, and **lake wind** in the detail sheet. Both degrade silently offline; last successful fetch stays visible with its age.

Sources, all keyless or free-tier, all proxied through the homelab (`/api/wind/*`) so keys stay server-side and responses are cached:

| Source | What | Endpoint | Notes |
|---|---|---|---|
| Synoptic Data Mesonet API | ASOS/AWOS, RAWS, CWOP/APRS amateur stations (most roof-mounted PWS that report to official channels) | `https://api.synopticdata.com/v2/stations/latest?bbox=..&vars=wind_speed,wind_direction,wind_gust&units=english` | Free Open Access tier, key required. Primary feed. |
| aviationweather.gov | METARs | `https://aviationweather.gov/api/data/metar?bbox=..&format=json` | No key. Aviation-grade layer. |
| NDBC | Great Lakes buoys | `https://www.ndbc.noaa.gov/data/latest_obs/latest_obs.txt` (filter by bbox) | For shoreline lakes. |
| Open-Meteo | Model wind at the lake point | `https://api.open-meteo.com/v1/forecast?latitude=..&longitude=..&current=wind_speed_10m,wind_direction_10m,wind_gusts_10m&wind_speed_unit=kn` | No key. Labeled "model". Fills gaps. |
| Wunderground PWS | Largest roof-mount pool | Paid IBM API | Not used. Leave a source adapter slot. |

Server proxy: `GET /api/wind/stations?bbox=` merges sources into one schema `{id, source, name, lat, lon, dir_deg, speed_kt, gust_kt, obs_time}`; cached 5 min per bbox tile. `GET /api/wind/point?lat=&lon=` returns Open-Meteo current wind, cached 15 min.

Client:

- Wind layer toggle. Fetch on map `idle` for the visible bbox (throttled, min zoom 8). Each station is an arrow pointing downwind with speed label; color by source (METAR, mesonet, buoy); opacity fades with age; hidden past 90 min. Tap a station for details.
- Selected lake: sheet shows nearest 3 observations with distance and age, plus model wind at the lake. Peek row: `Wind 240/12 G18 (KDET 14 min)`.
- Pipeline stores `chord_bearing_deg` with `longest_chord_ft`; client shows headwind/crosswind component for landing along the longest chord in either direction.
- Cache last responses in IndexedDB; "stale" badge past 60 min; nothing shown if no fetch has ever succeeded.

### 7.10 Platform targets

- Android Chrome: manifest + service worker + HTTPS → install prompt. Primary phone target.
- iPadOS Safari: Add to Home Screen. OPFS, Wake Lock, Geolocation all supported on current iPadOS.
- Both tested against the same data pack.

## 8. Hosting

- Homelab, Docker Compose, three services: `web` (Caddy serving static files with range requests and correct MIME for `.pmtiles`), `api` (small FastAPI service: wind proxy in v2, saved-lake sync in v4), and `pipeline` (cron container running the Python pipeline and writing into the volume `web` serves).
- TLS via Tailscale: `tailscale serve` in front of Caddy, or `tailscale cert` fed to Caddy. Hostname on the tailnet. Secure context is required for Geolocation, service worker, OPFS.
- Phase 4 sync endpoint is a tiny FastAPI or Go service behind the same Caddy.
- Optional later: Tailscale Funnel for sharing with other pilots.

## 9. Repo layout

```
seaplane-map/
  README.md
  docker-compose.yml
  Caddyfile
  pipeline/            # Python, uv
    pyproject.toml
    seaplane_pipeline/
      fetch.py parse_dnr.py match.py geometry.py overlay.py classify.py build.py review.py cli.py
    tests/
      fixtures/dnr_pages/  fixtures/lakes/
  rules/
    rules.json
    engine/            # JS engine, used by pipeline (via node) and client
    fixtures/          # shared test cases
  data/
    raw/               # gitignored
    manual/overrides.yaml  manual/mac_record.yaml
    out/               # gitignored, served by web
  web/                 # Vite PWA
    src/
      map/ search/ sheet/ lists/ saved/ location/ pack/ sw/
    public/manifest.webmanifest
  docs/
    design.md          # this file
```

## 10. Phases

1. **v1 Static map.** Pipeline stages 1 to 7 for all 83 counties of DNR data plus hydrography, BAS, geometry. Build the DNR parser against one county with fixtures first, then run statewide. Map with verdict outlines, tap for sheet, search with typeahead, `?lake=` param. Served over Tailscale. Rules engine in JS, shared fixtures.
2. **v2 Flight mode.** Location, follow-me, heading-up, wake lock, nearest list with forward cone, usable-water layer, wind (METAR + Open-Meteo) with chord-based crosswind.
3. **v3 Offline.** Service worker, data pack download to OPFS, pmtiles OPFS source, install prompts, pack versioning.
4. **v4 Personal layer and MAC.** Saved lakes with tags/notes/verified, export/import, sync endpoint, MAC record ingestion, review tooling polish, weekly cron with diff notifications.

## 11. Open items

- Obtain the MAC seaplane record from MDOT Aeronautics (email/FOIA). Until then the app must state that MAC-approved ordinances are not yet loaded.
- Confirm whether the 2017 to 2018 seaplane bills (SB 626/627) were enacted and whether they changed R 259.401's override process. Does not affect architecture.
- Confirm Bobby's iPad has GPS (cellular model) or plan for an MFi external GPS.
- Basemap size target; trim further if over 150 MB.
- `min_chord_ft` default for the SeaRey; 2000 ft is a placeholder.

## 12. Non-goals

- Weather, NOTAMs, flight planning.
- Other states (design keeps state as a parameter, but only Michigan is built).
- Public multi-user accounts.

## 13. Disclaimer text (in-app)

"This tool summarizes published Michigan DNR watercraft controls and other public data. It is not legal advice and does not confirm that a landing is legal. Verify restrictions, property rights, and conditions before operating. Data build: {version}."

## References

- Mich. Admin. Code R 259.401, Seaplane Operations: https://www.law.cornell.edu/regulations/michigan/Mich-Admin-Code-R-259-401
- DNR Local Watercraft Controls, Oakland County: https://www.michigan.gov/dnr/managing-resources/laws/controls/localcontrols/oakland/local-watercraft-controls
- Hydrography Polygons: https://gis-michigan.opendata.arcgis.com/datasets/midnr::hydrography-polygons/about
- PRD Boating Access Sites: https://gis-michigan.opendata.arcgis.com/datasets/midnr::prd-boating-access-sites
- City of Lake Angelus v. Aeronautics Commission: https://www.courtlistener.com/opinion/1925799/city-of-lake-angelus-v-aeronautics-commission/
- AOPA on SB 626/627: https://www.aopa.org/news-and-media/all-news/2017/november/14/michigan-bill-would-protect-seaplane-access
