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
  as long as the source text does not change.
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
  "lake_ids": [1234567]                  // build stage only; [] when unmatched
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
- IndexedDB db `seaplane`, stores `saved` (key `id`), `recent` (key `id`), `wind` (key bbox tile).
- OPFS directory `pack/` holds the downloaded data pack files by name (phase 3).
