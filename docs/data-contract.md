# Data contract

Shared schemas between the pipeline (`pipeline/`), the rules engine (`rules/`), and the client (`web/`).
Every agent building a piece of this repo builds against this file. Change it here first.

All output files are written to `data/out/` and served at `/data/` (dev server and Caddy alike).

## Identifiers

- **Lake `id`**: stable positive 31-bit integer. `id = int(sha1(source_key).hexdigest()[:8], 16) & 0x7fffffff`,
  where `source_key` is the hydrography source's permanent identifier for the polygon (fall back to
  `"{name_norm}|{lat:.4f}|{lon:.4f}"` if the source has none). The pipeline checks for collisions and bumps by 1.
  The same value is the GeoJSON feature `id` (top level) and the `id` property, so MapLibre `promoteId: "id"` works.
- **`restriction_id`**: 12 hex chars, `sha1(f"{county}|{lake_name_raw}|{township}|{raw_text}")[:12]`. Stable across runs
  as long as the source text does not change. When one entry decomposes into several records (clauses), the clause
  label, or the restriction type when clauses are unlabelled, is appended to the hashed key so siblings get distinct
  ids (`restriction.compute_restriction_id`).
- **`rule_id`**: the Michigan Administrative Code number when present (`"R 281.763.3"`), else `null`.

## Name normalization (`name_norm`)

Identical in Python (`seaplane_pipeline/names.py`) and JS (`web/src/search/normalize.ts`). Shared fixtures in
`rules/fixtures/names.json` as `[{"raw": ..., "norm": ...}]`.

1. Lowercase, strip diacritics, collapse whitespace.
2. Expand abbreviations as whole words: `lk`→`lake`, `mt`→`mount`, `st`→`saint`, `n`→`north`, `s`→`south`,
   `e`→`east`, `w`→`west`, `upr`→`upper`, `lwr`→`lower`, `twp`→`township`.
3. Remove leading/trailing generic words: `lake`, `pond`, `reservoir`, `impoundment`, `flowage`, `basin` (but keep
   them when they are the only word). `"Lake Angelus"`→`"angelus"`, `"Big School Lot Lake"`→`"big school lot"`,
   `"Mud Lake"`→`"mud"`.
4. Strip punctuation except spaces. `"St. Clair"`→`"saint clair"`.
5. Qualifiers `big little north south east west upper lower middle` stay in the string (they disambiguate).

**Implementation decision (rules engine, `rules/engine/index.js`):** a parenthetical qualifier in the raw
name is treated as a separate segment, not inline text. The parentheses are stripped, and the main name and
the parenthetical content are each run through the full pipeline above independently (lowercase, expand
abbreviations, strip leading/trailing generic words, strip punctuation), then joined with a space. This
matters because a generic word like "Lake" that ends up in the middle of the raw text (main name followed
by a trailing parenthetical) must still be stripped as if it were trailing on the main name alone.
Example: `"Crooked Lake (Big)"` → main `"Crooked Lake"` → `"crooked"`, parenthetical `"Big"` → `"big"` →
joined `"crooked big"`. See `rules/fixtures/names.json` for more parenthetical cases.

## `restrictions.jsonl` (pipeline stage `parse-dnr`) and `restrictions.json` (stage `build`)

One record per (lake mention, rule). `restrictions.json` is `{ "<restriction_id>": record }` with `lake_ids` filled in.

```jsonc
{
  "restriction_id": "3f2a9c1d0b7e",
  "rule_id": "R 281.763.3",              // or null
  "county": "Oakland",                   // title case, no "County"
  "township": "Rose Township",           // or null; comma-join if several
  "lake_name_raw": "Big School Lot Lake",
  "lake_name_norm": "big school lot",
  "plss": [{"township": "4N", "range": "7E", "sections": [16, 21]}],   // [] if none
  "restriction_type": "no_high_speed",  // see enum
  "scope": "lakewide",                   // "lakewide" | "zone"
  "scope_description": null,             // text for zones: "within 200 ft of the north shore"
  "hours": null,                         // or {"text": "6:30 p.m. to 10:00 a.m.", "start": "18:30", "end": "10:00", "days": null}
                                         //    days: null (every day) or text like "Sundays and holidays"
  "season": null,                        // or {"text": "Memorial Day through Labor Day", "start": "05-25", "end": "09-07"}
  "speed_mph": null,                     // number for speed_limit
  "status": "active",                    // "active" | "rescinded" (rescinded rows stay in jsonl, are dropped at build)
  "clause": null,                        // "(a)", "(b)"… when one rule number decomposes into several records
  "signage_required": false,             // true when the order says it is only enforceable when marked with signs/buoys
  "related_rule_ids": [],                // cross-county continuation references, e.g. ["R 281.747.1"]
  "raw_text": "…verbatim paragraph from the DNR page…",
  "source_url": "https://www.michigan.gov/dnr/…/oakland/local-watercraft-controls",
  "fetched_at": "2026-09-15T18:00:00Z",
  "parser": "regex",                     // "regex" | "llm" | "manual"
  "needs_review": false,
  "lake_ids": [1234567],                 // build stage only; [] when unmatched
  "match_confidence": 0.9                // build stage only; matcher score (< 0.8 also sets needs_review)
}
```

### `restriction_type` enum

| value | meaning | default verdict |
|---|---|---|
| `no_vessels` | all vessels / boating prohibited | restricted |
| `no_motorboats` | motorboats prohibited (electric-only counts) | restricted |
| `slow_no_wake` | slow-no-wake / no-wake speed; may carry `hours` or `season` | lakewide: restricted; zone: conditional; with hours/season: conditional |
| `no_high_speed` | high-speed boating / planing prohibited; may carry `hours`/`season` | same as slow_no_wake |
| `high_speed_hours` | high speed permitted only during `hours` (or `season`) | conditional |
| `speed_limit` | numeric limit; `speed_mph` set | lakewide and `speed_mph` < `aircraft.min_takeoff_mph`: restricted; zone: conditional; else clear |
| `no_towing` | water skiing / towing prohibited or hour-limited | clear |
| `no_pwc` | personal watercraft prohibited or hour-limited | clear |
| `no_wake_zone_marked` | buoyed no-wake zone (statewide-type marker rule) | conditional |
| `shore_buffer` | local echo of the statewide slow-no-wake within 100 ft of shore/docks/swimmers rule | clear |
| `not_applicable` | parsed confidently as not touching landing/takeoff: airboat bans, mooring/anchoring, rafts and flotation devices, towed-person headcount limits, swimming areas | clear |
| `mac_ordinance` | MAC-approved seaplane ordinance or interim order | restricted |
| `mac_conditional` | MAC record entry with conditions | conditional |
| `federal_no_landing` | NPS / USFWS unit without designated seaplane area | restricted |
| `other` | could not classify; `needs_review` true | unknown |

### Parsing rules learned from the corpus (see `docs/dnr-pages.md`)

- One DNR entry (one rule number) that bundles several clauses `(a)`, `(b)`, `(c)` becomes several records sharing
  `rule_id`, each with its own `restriction_type`, `scope`, `hours`, and `clause`. `raw_text` is the whole entry on
  every record.
- "Motorboats prohibited except electric motors" is `no_motorboats`. If the same clause also caps speed, emit a second
  `speed_limit` record.
- "High speed" clauses that also ban towing are one `no_high_speed` (or `high_speed_hours`) record, not two.
- A towing/skiing-only clause with no speed clause is `no_towing`.
- Entries whose header or body starts with "Rescinded" get `status: "rescinded"` and `restriction_type: "other"`
  with `needs_review: false`.
- A lake that straddles a county line appears once per county; both records keep their own `county` and the matcher
  attaches both to the same polygon. Parenthetical "(See R 281.747.1 for … Livingston county)" fills `related_rule_ids`.
- The "boundaries … marked with signs and/or buoys … only enforceable when properly marked" boilerplate sets
  `signage_required: true` and is otherwise ignored.
- Hours like "6:30 p.m. to 10:00 a.m. of the following day" → `start: "18:30", end: "10:00"`. A DST variant sentence
  is kept in `hours.text` only. Day qualifiers ("Sundays, Memorial Day, Independence Day, and Labor Day",
  "Saturdays and holidays") go in `hours.days`.
- Month-name seasons ("during September, October and November") → `season: {text, start: "09-01", end: "11-30"}`.

Synthetic restrictions (`mac_*`, `federal_*`) are produced by the pipeline from `data/manual/mac_record.yaml` and
the federal overlay, with `parser: "manual"` and `source_url` pointing at the record.

### Match stage outputs (`data/work/`)

`matches.json`: `[{restriction_id, lake_id, score, method, needs_review}]`. `unmatched.json`: one row per active
restriction with no acceptable lake, with `kind: "lake" | "waterway"` (rivers, creeks, channels, harbors, and bays have
no lake polygon and are expected to stay unmatched) and a `reason`. `seaplane review` prints the lake-kind rows first.

## `rules/rules.json`

```jsonc
{
  "version": "2026-09-15",
  "aircraft": {"name": "SeaRey", "min_chord_ft": 2000, "min_takeoff_mph": 40},
  "aggregate": "worst_of",
  "rules": [
    {"id": "no-planing-lakewide",
     "when": {"restriction_type": "no_high_speed", "scope": "lakewide", "hours": null, "season": null},
     "verdict": "restricted",
     "note": "High-speed/planing prohibited lakewide. Takeoff and landing require planing."}
  ]
}
```

`when` matching semantics (implemented once in `rules/engine/`):
- Each key must match the restriction. A key maps to a restriction field.
- Value `null` means the field must be null/absent. Value `"*"` means present and non-null.
- Array value means "any of".
- Suffix operators on numeric fields: `speed_mph_lt`, `speed_mph_gte`. A value string of the form `"$aircraft.min_takeoff_mph"` is
  substituted from `rules.aircraft`.
- Rules are evaluated in order; the **first** matching rule decides that restriction's verdict and note. If no rule
  matches, the restriction gets `verdict: "unknown"` and note `"Unclassified restriction."`.
- `note` templates substitute `{scope_description}`, `{hours.text}`, `{season.text}`, `{speed_mph}`, `{rule_id}`.

## Rules engine API (`rules/engine/index.js`, ESM, zero deps)

```js
import { evaluateLake, evaluateAll, loadRules } from "./index.js";

// lake: {id, name, chord_ft, area_acres, public_access: bool, federal_unit: string|null}
// restrictions: array of restriction records for this lake
// returns:
{
  id: 1234567,
  verdict: "conditional",                 // restricted | conditional | clear | unknown
  reasons: [ {restriction_id, rule_id, matched_rule: "no-planing-zone", verdict: "conditional", note: "…"} ],
  flags: ["chord_below_minimum", "no_public_access"]   // see flag list
}
```

Verdict order: `restricted > conditional > unknown > clear`. A lake with no restrictions and a name is `clear`;
a lake with no name is `unknown` (the pipeline sets `name: null` for unnamed polygons).

Flags: `no_public_access`, `chord_below_minimum`, `federal_overlay`, `needs_review`, `mac_pending`, plus client-only
`user_verified`, `user_note`, `saved`.

CLI for the pipeline: `node rules/engine/cli.js --rules rules/rules.json < input.json > output.json` where input is
`{"lakes": [...], "restrictions": {"<lake_id>": [records]}}` and output is an array of the result objects above.

## `index.json` (stage `build`)

A JSON array, target under 5 MB. One entry per named lake polygon (plus unnamed ones over 20 acres).

```jsonc
{"id": 1234567, "name": "Big School Lot Lake", "name_norm": "big school lot",
 "county": "Oakland", "township": "Rose Township",
 "lat": 42.7712, "lon": -83.5901, "bbox": [-83.60, 42.76, -83.58, 42.78],
 "area_acres": 41.2, "chord_ft": 2350, "chord_bearing_deg": 47,
 "verdict": "restricted", "flags": ["no_public_access"], "restriction_ids": ["3f2a9c1d0b7e"],
 "access": "School Lot Lake BAS"}      // launch name or null
```

`chord_bearing_deg` is 0–179 (a chord has two directions; the client shows both).

## Tiles (stage `build`)

| file | layer | zooms | properties |
|---|---|---|---|
| `lakes.pmtiles` | `lakes` | 6–14 | `id` (int, also feature id), `name`, `verdict`, `flags` (comma-joined), `county` |
| `usable_water.pmtiles` | `usable_water` | 12–14 | `id` |
| `overlays.pmtiles` | `federal`, `airspace`, `bas`, `airports` | 6–14 | `name`, `kind`, plus source-specific |
| `basemap.pmtiles` | Protomaps schema | 0–14 | Protomaps basemap layers |

Tippecanoe flags for `lakes`: `-z14 -Z6 --no-feature-limit --no-tile-size-limit --detect-shared-borders
--coalesce-densest-as-needed --extend-zooms-if-still-dropping -l lakes`. Small polygons must survive at z14.

## `pack.json`

```jsonc
{"version": "2026-09-15", "built_at": "2026-09-15T18:30:00Z", "rules_version": "2026-09-15",
 "files": [{"name": "index.json", "bytes": 3102233, "sha256": "…"}, {"name": "lakes.pmtiles", …}],
 "counts": {"lakes": 12345, "restrictions": 987, "needs_review": 40},
 "mac_record_loaded": false}
```

## Manual files (`data/manual/`)

`overrides.yaml`:

```yaml
matches:                       # force a restriction onto a lake (wins over automatic matching)
  - restriction_id: 3f2a9c1d0b7e
    lake_id: 1234567
    note: "Big School Lot is the east basin"
unmatch:                       # drop an automatic match
  - restriction_id: abc123abc123
    lake_id: 7654321
verdicts:                      # force a lake verdict
  - lake_id: 1234567
    verdict: conditional
    note: "Confirmed with township clerk 2026-08"
```

`mac_record.yaml`:

```yaml
loaded: false                  # flip to true once MDOT Aeronautics record has been ingested
entries:
  - lake_name: Lake Angelus
    county: Oakland
    lake_id: null              # filled by match stage or by hand
    kind: ordinance            # ordinance | interim_order | conditional
    status: approved
    citation: "City of Lake Angelus v. Aeronautics Commission, 260 Mich App 371 (2004)"
    note: "City ordinance banning seaplanes; MAC approval history disputed. Treat as restricted."
    source_url: "https://www.courtlistener.com/opinion/1925799/city-of-lake-angelus-v-aeronautics-commission/"
```

## Client URL and storage

- `?lake=<id>` opens that lake via the single `selectLake(id)` path.
- IndexedDB db `seaplane`, stores `saved` (key `id`), `recent` (key `id`), `wind` (key bbox tile), `briefing` (key `"latest"`).
- OPFS directory `pack/` holds the downloaded data pack files by name (phase 3).

## Briefing

Algorithm and reasoning: `docs/briefing-design.md`. Three parts build against this section: the pipeline
(`lake_extents.json`), the `api/` service (settings, scheduler, `briefing.json`), and the client (card + settings).
Scores are `favorable | marginal | unfavorable` (rank 0, 1, 2); the briefing never says "go", "safe", or "legal".
All local times are `HH:MM` 24-hour strings in `settings.timezone`; all `*_at` / `generated_at` values are UTC ISO 8601
with `Z`. Every number is already rounded for display by the server (kt, °F, inches as integers; `ceiling_ft` and
`da_ft` to the nearest 100; `vis_sm` capped at 10); the client prints them as they are. `ceiling_ft: null` with
`ceiling_known: true` means no ceiling; `ceiling_known: false` means no data covered that hour (no TAF or METAR; the
models carry no cloud base) and the ceiling factor was not evaluated.

### `data/out/lake_extents.json` (pipeline stage `build`, listed in `pack.json`)

`{"<lake_id>": [ft × 16]}` for lakes with `chord_ft` ≥ 1000. Bin `i` is centered on bearing `i × 22.5°` true
(0 = north, 4 = east); a bearing maps to bin `round(bearing / 22.5) % 16`. The value is the longest straight segment
through the lake polygon along that bearing, in whole feet. A segment has two directions, so bin `i` always equals
bin `(i + 8) % 16`. Computed in `geometry` as the lake column `extent_by_bearing` (list of 16 ints).

### `data/manual/settings.json` (written by the api service; gitignored; created with these defaults on first start)

```jsonc
{"home_airport": {"id": "KPTK", "name": "Oakland County Intl", "lat": 42.6655, "lon": -83.4187, "elev_ft": 981,
                  "runways": [{"id": "09L/27R", "heading": 88}, {"id": "09R/27L", "heading": 88},
                              {"id": "18/36", "heading": 172}]},   // heading: degrees TRUE of the first-named end
 "timezone": "America/Detroit",
 "radius_nm": 40, "n_lakes": 8, "public_access_only": false,
 "schedule": {"run_times_local": ["06:00", "09:00", "12:00", "15:00", "18:00", "20:00", "22:00"]},
 "outlook": {"times_local": ["18:00", "20:00", "22:00"],     // evening runs recorded in outlook.runs; always also run times
             "morning_start": "sunrise",                      // "sunrise" | "civil_twilight" | "HH:MM"
             "morning_end_local": "12:00", "min_window_hours": 2},
 "notify": {"ntfy_url": null},                                // when set, each outlook run POSTs outlook.summary there
 "limits": {"wind_ok": 12, "wind_max": 18, "gust_spread_ok": 8, "gust_spread_max": 12,
            "xwind_runway_ok": 8, "xwind_runway_max": 12, "xwind_water_max": 10,
            "ceiling_ok": 3000, "ceiling_min": 1500, "vis_ok": 6, "vis_min": 3,
            "da_ok": 3500, "da_max": 5000, "temp_water_min_f": 40, "fog_spread_f": 3,
            "wave_ok_in": 8, "wave_max_in": 12, "min_run_ft": 2000,
            "ice_season_start": "12-01", "ice_season_end": "04-01"}}
```

Unknown keys are rejected; missing keys are filled from the defaults, so an older file keeps working.

### `data/out/briefing.json` (written atomically by the api service; not part of `pack.json`)

```jsonc
{"schema": 1,
 "generated_at": "2026-09-19T22:00:04Z",
 "run_kind": "outlook",                       // "scheduled" | "outlook" | "manual" | "startup"
 "timezone": "America/Detroit",
 "home_airport": {"id": "KPTK", "name": "Oakland County Intl", "lat": 42.6655, "lon": -83.4187},
 "summary": "Favorable 10:00–16:00 today. KPTK wind 250/9 G14, ceiling 4,500, vis 10. …",
 "days": [{"date": "2026-09-19", "score": "favorable", "best_window": ["10:00", "16:00"],   // best_window null when none
           "blocks": [{"start": "07:00", "end": "10:00", "score": "marginal", "limiting": "gusts",
                       "wind": {"dir": 250, "kt": 9, "gust": 17}, "xwind_kt": 6, "runway": "27L",
                       "ceiling_ft": 4500, "ceiling_known": true, "vis_sm": 10, "da_ft": 2100, "temp_f": 61,
                       "precip_prob": 10}]}],
 "outlook": {
   "target_date": "2026-09-20",               // tomorrow once local time ≥ morning_end_local, else today
   "window": {"start": "07:18", "end": "12:00"},
   "sun": {"civil_dawn": "06:49", "sunrise": "07:18", "sunset": "19:34", "civil_dusk": "20:03"},
   "score": "favorable", "limiting": null,    // limiting: factor id when score is not favorable
   "watch": "gusts after 11:00",              // what ends a favorable window, or null
   "best_window": ["08:00", "12:00"],         // null when no run of min_window_hours exists
   "hours": [{"time": "08:00", "score": "favorable", "limiting": null,
              "wind": {"dir": 240, "kt": 6, "gust": 9}, "xwind_kt": 3, "runway": "27L",
              "ceiling_ft": null, "ceiling_known": true, "vis_sm": 10, "temp_f": 52, "dewpoint_f": 47, "fog_risk": false,
              "precip_prob": 5, "da_ft": 1200}],
   "lakes": [ /* same row shape as top-level "lakes", worst hour inside best_window */ ],
   "confidence": "medium", "confidence_reasons": ["NWS and model wind differ by 6 kt"],
   "trend": "steady",                         // vs previous entry in runs: "improving" | "steady" | "worsening" | null
   "runs": [{"at": "18:00", "generated_at": "2026-09-19T22:00:03Z", "score": "favorable",
             "best_window": ["08:00", "12:00"], "limiting": null, "max_gust_kt": 12}],
   "summary": "Tomorrow morning (Sun): favorable 08:00–12:00. Wind 240/6 G9, no ceiling, vis 10. …"},
 "alerts": [{"event": "Lake Wind Advisory", "area": "Lake St. Clair", "ends": "2026-09-20T02:00:00Z"}],
 "lakes": [{"id": 1234567, "name": "Cass Lake", "score": "favorable", "limiting": null, "hs_in": 6, "run_ft": 4100,
            "wind": {"dir": 250, "kt": 10, "gust": 15}, "distance_nm": 6.1, "bearing_deg": 118,
            "verdict": "conditional", "frozen": false}],
 "sources": {"metar": "2026-09-19T21:53:00Z", "taf": "2026-09-19T17:20:00Z", "open_meteo": "…", "nws": "…"},  // null when that input failed
 "links": {"metar": "https://…", "taf": "https://…", "forecast": "https://…"},
 "errors": ["nws: timeout"]}
```

Lake rows are **water-only**: `score` and `limiting` come from waves, run, water crosswind, and ice at that lake, not
from the airport weather, which lives in the header, blocks, and hours. Rows are ranked by score, then distance, then
`hs_in`. Frozen lakes stay in the list as `frozen: true`, `unfavorable`, `limiting: "ice"`. When target_date is today,
`outlook.hours` holds only hours that have not ended, while `outlook.window` stays the nominal morning window; the
client lays out its hour strip from `hours`, not from `window`.

`limiting` factor ids: `wind`, `gusts`, `xwind_runway`, `ceiling`, `visibility`, `fog`, `precip`, `convection`,
`density_altitude`, `temperature`, `alert`, `daylight`; lakes add `waves`, `run`, `xwind_water`, `ice`. `outlook.runs`
is carried forward from the previous `briefing.json` while `target_date` is unchanged (an entry with the same `at`
is replaced). `outlook` is `null` only when the forecast input failed entirely.

### api service (`api/`, FastAPI, Python 3.12, uv, package `seaplane_api`)

Run: `cd api && uv run seaplane-api` (uvicorn on `127.0.0.1:8000`, scheduler in-process) ·
`uv run seaplane-api briefing --once` (one run, no server) · tests `cd api && uv run pytest -q`. Directories come from
`SEAPLANE_DATA_OUT` and `SEAPLANE_DATA_MANUAL` (default `../data/out`, `../data/manual`). Caddy and the Vite dev
server proxy `/api/*` to it. Single user, tailnet only, no auth.

| endpoint | behavior |
|---|---|
| `GET /api/health` | `{"ok": true, "briefing_generated_at": "…" \| null, "next_run_local": "20:00"}` |
| `GET /api/settings` | the settings object |
| `PUT /api/settings` | full object, validated (422 on bad input), written atomically, triggers a `manual` run in the background; returns the saved object |
| `GET /api/airports/{ident}` | resolves an identifier to the `home_airport` shape (aviationweather.gov airport info); 404 when unknown |
| `POST /api/briefing/refresh` | runs now, returns the new `briefing.json` body |

The client reads the briefing as the static file `/data/briefing.json`, never through `/api`.

### Client

- Briefing card: summary, block strip per day, the outlook section (hour strip, best window, run-history chips with
  trend, confidence), ranked lakes as rows through `selectLake(id)`, links to raw METAR/TAF/forecast, age badge,
  "stale" past 6 hours. The outlook section leads when `outlook.target_date` is tomorrow and local time is 17:00 or
  later, or when it is today and the window has not ended.
- IndexedDB store `briefing` (key `"latest"`) keeps the last briefing for offline.
- Settings screen edits `settings.json` through `/api/settings`; hidden with a notice when `/api/health` fails.
