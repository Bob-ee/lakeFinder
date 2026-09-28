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
  "match_confidence": 0.9,               // build stage only; matcher score (< 0.8 also sets needs_review)
  "reach_unresolved": true,              // build stage only, and only when true; see "Waterway matching"
  "big_water_partial": true              // build stage only, and only when true; see "Waterway matching".
                                         //   The rules engine caps this restriction at `conditional`.
}
```

### `restriction_type` enum

| value | meaning | default verdict |
|---|---|---|
| `no_vessels` | all vessels / boating prohibited | lakewide: restricted; zone: conditional |
| `no_motorboats` | motorboats prohibited (electric-only counts) | lakewide: restricted; zone: conditional |
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
- A rule that names only a **connecting structure** ("Channel connecting Intermediate lake to Hanley lake",
  "CHANNEL CONNECTING BLACK LAKE AND RAWSON LAKE", "canals connected to X Lake") still attaches to the lake(s) it
  connects, because the structure has no polygon of its own, but as `scope: "zone"` with the structure phrase as
  `scope_description`: the rule covers the channel, not the lake. A header that names the lake itself *and* its
  channels ("BIG AND LITTLE SCHOOL LOT LAKES AND CONNECTING CHANNEL") stays as parsed. A river or stream named
  outright is the water body, not a structure, and is unaffected.
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
the federal overlay, with `parser: "manual"` and `source_url` pointing at the record. A federal record is one per
distinct hashed key (unit text, county, township, name): water bodies that share all four, which is every unnamed
pond inside one refuge township, share **one** record whose `lake_ids` lists them all. They never produce several
records with the same `restriction_id`.

### Match stage outputs (`data/work/`)

`matches.json`: `[{restriction_id, lake_id, score, method, needs_review, reach_unresolved?}]`. `unmatched.json`: one row
per active restriction with no acceptable waterbody, with `kind: "lake" | "waterway"` and a `reason`. `seaplane review`
prints the lake-kind rows first.

### Waterway matching

A restriction header names a **waterway** when it carries a river/creek/channel/canal/drain/bayou/harbor/bay word and
does *not* end in a lake generic (`Lake`, `Pond`, `Reservoir`, `Flowage`, `Impoundment`, `Basin`), so
`"Stoney Creek Lake"` stays a lake and `"Pine River"` is a waterway. The two sides never cross:

- a **lake** header only ever scores against `kind: "lake"` polygons;
- a **waterway** header only ever scores against `kind: "river"` polygons, the river-named `kind: "lake"`
  polygons (impoundments the hydrography layer types as lakes but names `"Au Sable River"`, `"Cornwall Creek
  Flooding"`), and the `kind: "great_lake"` / `kind: "connecting_water"` polygons.

Big water is reachable **only** from the waterway track. That is what keeps an inland lake-named restriction off
a Great Lake: `"Saint Clair Lake"` (Antrim), `"Huron Lake"` (Houghton), the 25-acre `"Lake Erie"` in Monroe and
`"Superior Lakes"` (Marquette) are lake headers matching lake polygons, while `"Detroit River"`,
`"St. Clair River"` and `"Lake St. Clair, Certain Creeks"` are waterway headers that reach the big-water
polygons by name.

A waterway header may attach to **several** polygons, because one river is digitized as several polygons sharing one
name (Grand River: 19, Sturgeon River: 29). Candidates are the same-name polygons that intersect the restriction's
county (geometric, not centroid — a river polygon crosses county lines), all polygons tied at the best score are kept,
and that set is then narrowed by, in order, the buffered PLSS section union and the restriction's township/city labels.
When neither narrows it, every same-name polygon in the county is matched and the match carries
`reach_unresolved: true`, which `build` copies onto the published restriction record and `classify` surfaces as the
lake flag `reach_unresolved`. It means "the rule covers part of this river; read the rule text", not "unverified".

Creeks, channels and canals with no polygon in any water layer stay unmatched waterways.

**Big water always carries `reach_unresolved`.** A match onto a `great_lake` or `connecting_water` polygon is
`reach_unresolved: true` whatever the narrowing found, because a township or a section never covers a 430 sq mi
lake or a 20 mile river: the rule is always about a reach. (On `river` polygons the existing behaviour is
unchanged — `reach_unresolved` only when nothing narrowed the set below the county.)

**Bay and harbor fallback.** A waterway header whose name *ends* in `Bay` or `Harbor` (optionally `Harbor of
Refuge`), and which matched no polygon by name, attaches to a Great Lake when both hold:

1. the restriction's county touches exactly one `great_lake` polygon (county polygon ∩ lake polygon > 0.1 km²);
   a county touching two — Chippewa, Mackinac, St. Clair, Wayne — is ambiguous and the rule stays unmatched;
2. the restriction's own narrowing geometry — the buffered PLSS section union, else the union of its named
   township/city polygons — intersects that lake.

Both are required. Condition 2 is what keeps an inland bay off the Great Lake: `"Pine Creek Bay"` (Ottawa) is on
Lake Macatawa and carries neither PLSS nor township, so it is not attached. A header that merely *contains* a bay
word (`"Bay of Lake Nettie"`, `"Sandy Creek Bay and North"` before its trailing clause is trimmed) is not a bay
header. These matches score 1.0 with method `bay+great-lake` and carry `reach_unresolved`.

**`big_water_partial`.** `build` publishes `big_water_partial: true` on a restriction whose every matched water
body is a `great_lake` or `connecting_water` (and therefore every match is `reach_unresolved`). The rules engine
reads that one field and caps the restriction at `conditional` — see "Rules engine API". It lives on the
published restriction record rather than on the water body so that the pipeline and the client, which re-runs
the engine from `restrictions.json`, reach the same verdict with no extra input.

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
- **Big-water cap, applied after the rule matched:** a restriction with `big_water_partial: true` can be no worse
  than `conditional`, so a `restricted` verdict becomes `conditional` and everything else is left alone. The note
  gains ` (Covers part of this water; verdict limited to conditional.)`. This is the "Water body kinds" rule —
  a slow-no-wake zone at a creek mouth must not turn Lake St. Clair red — and it is in the engine rather than in
  `rules.json` so the pipeline and the client cannot drift.

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

Flags: `no_public_access`, `chord_below_minimum`, `federal_overlay`, `needs_review`, `mac_pending`,
`reach_unresolved` (added by the pipeline's `classify` stage, not by the engine; see "Waterway matching"), plus
client-only `user_verified`, `user_note`, `saved`.

CLI for the pipeline: `node rules/engine/cli.js --rules rules/rules.json < input.json > output.json` where input is
`{"lakes": [...], "restrictions": {"<lake_id>": [records]}}` and output is an array of the result objects above.

## `index.json` (stage `build`)

A JSON array, target under 5 MB. One entry per named hydrography polygon (plus unnamed ones over 20 acres).

```jsonc
{"id": 1234567, "name": "Big School Lot Lake", "kind": "lake",
 "county": "Oakland", "township": "Rose Township",
 "lat": 42.7712, "lon": -83.5901, "bbox": [-83.60, 42.76, -83.58, 42.78],
 "area_acres": 41.2, "chord_ft": 2350, "chord_bearing_deg": 47,
 "verdict": "restricted", "flags": ["no_public_access"], "restriction_ids": ["3f2a9c1d0b7e"],
 "access": "School Lot Lake BAS",      // launch name or null
 "federal_unit": "Seney National Wildlife Refuge"}   // present only when set; absent otherwise
```

`name_norm` is **not** in the payload (it was until 2026-09-20; ~310 KB of the 5 MB budget). The client derives
it with `normalizeName(name)` on load, which is the same function by contract ("Name normalization" above); the
pipeline keeps `name_norm` as a column in `lakes.parquet` for matching. A reader must tolerate an older pack that
still carries the key.

`federal_unit` is the name behind the `federal_overlay` flag, written only on the entries that have
one (172 of 12,571; a fixed `null` on every row would cost 260 KB of the 5 MB budget). The client
reads it as optional.

`chord_bearing_deg` is 0–179 (a chord has two directions; the client shows both).

`kind` is `"lake"`, `"river"`, `"great_lake"` or `"connecting_water"`. `"lake"` and `"river"` come straight from
the hydrography layer's `TYPE`; everything the layer types `lake` stays `"lake"`, including the ~500 impoundments
it names after a river (`"Au Sable River"`). `"great_lake"` (the Great Lakes and Lake St. Clair) and
`"connecting_water"` (the Detroit, St. Clair and St. Marys Rivers) come from the separate big-water sources the
inland layer does not contain, and flow through every stage alongside the rest. `kind` does not change verdict
logic — the same DNR local watercraft controls apply — it labels the waterbody in the client, gates which
restrictions may match it (see "Waterway matching"), and tells the client how to word the flags below.

Water bodies **do not overlap each other**, whatever their kind: `geometry` subtracts the inland polygons from the
big-water polygons, and the connecting waters from the Great Lakes, so no square metre of water is published
twice (the source layers do overlap — Michigan's "Lake Michigan Shoreline" swallows Torch Lake and Lake
Charlevoix, and "Lake Erie Shoreline" runs up the Detroit River past Grosse Ile). Small slivers along shared
shorelines survive; duplicate features do not.

**On `great_lake` and `connecting_water` every per-water-body flag means "part of this water".** These bodies are
one feature covering hundreds of square miles, so `airspace_class`, `federal_unit` / the `federal_overlay` flag,
`access` / `no_public_access` and the restriction list all describe some part of the water, never the whole of it.
The client words them that way off `kind` alone; there is no extra field. Concretely: Lake St. Clair is
`airspace_class: "D"` because Selfridge's Class D covers Anchor Bay, not because the lake is inside Class D, and
the Detroit River carries `federal_overlay` because the Detroit River International Wildlife Refuge holds islands
and shoals in it. For that last reason `classify` does **not** synthesize the `federal_no_landing` restriction on
these two kinds: a refuge over a few islands does not close a Great Lake, so the overlay is a flag only and the
verdict is untouched. Per-region overlay geometry is future work.

For a `"river"` polygon, `chord_ft` / `chord_bearing_deg` are the longest straight *reach* of open water inside the
polygon, which is the number a pilot needs on a sinuous river.

## Tiles (stage `build`)

| file | layer | zooms | properties |
|---|---|---|---|
| `lakes.pmtiles` | `lakes` | 6–14 | `id` (int, also feature id), `name`, `kind`, `verdict`, `flags` (comma-joined), `county` |
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

## Wave field (`wave_points.bin` + `wave_points.json`, stage `wavefield`, listed in `pack.json`)

Design: `docs/big-water-design.md`. Answers "where on this water is it calm in this wind" for every water body big
enough to have an answer, with nothing hand-drawn. Three consumers read it: the api briefing (Python), the client
(`rules/waves/`, JS, zero deps, imported by `web/`), and tests. Shared fixtures: `rules/fixtures/waves.json`,
`rules/fixtures/wave_points.sample.bin|json`. **Python and JS must both pass those fixtures.**

### Which water bodies get points

Water bodies of 100 acres or more with non-empty usable water. Points lie on a regular grid (projected CRS, anchored
deterministically) inside the usable water; spacing `clamp(sqrt(area_m2 / target), 150 m, 2000 m)` with `target` 60,
400 for water of 10,000 acres or more, and 2,000 for 100,000 acres or more (so Lake St. Clair's 1-2 km bays get
points; the Great Lakes proper sit on the 2,000 m clamp). Nodes sit at `(k + 0.5) × spacing` from the projected CRS
origin, so a water body keeps its points when its neighbors change. A qualifying water body whose usable core holds
no node gets one representative point. A water body with no entry has no wave field and consumers fall back to
the lake-level numbers (`lake_extents.json`, fetch = three-bin arc), exactly as before.

### Per point, computed by the pipeline

- `fetch[16]`: distance in the direction of bearing bin `i` (`i × 22.5°` true, convergence corrected) from the point
  to the first land, mean of 5 rays at −12°, −6°, 0°, +6°, +12°. Rays run across the **fetch mask**, the union of all
  water polygons of every kind, so a ray crosses from the Detroit River into Lake Erie or between connected lakes
  without stopping. Capped at 100 km. A ray that leaves the mask through an **artificial edge** counts as the cap:
  a run of consecutive mask-boundary vertices that all lie within 10 m of the chord joining the run's ends, more
  than 2,000 m long in total (drawn clip lines sit 0 m off their chord; natural shore wanders 40 m or more). That is how a clip line looks (Lake Huron and Lake Superior stop at the international
  boundary, densified, so a single-segment length test misses them). A long straight breakwater also qualifies,
  which errs toward more waves. Wind **from** direction `d` uses
  `bin = floor(d / 22.5 + 0.5) % 16` and reads `fetch[bin]`.
- `run[8]`: length of the straight line through the point along bearing bin `i` (and `i + 8`) inside the water
  body's own usable water, both directions summed. Wind bin `b` reads `run[b % 8]`.
- `depth_dm`: depth at the point in decimeters below the grid's Low Water Datum, `65535` when unknown. Sources are
  the grids in `bathymetry.GRID_SOURCES` (the NCEI Great Lakes grids today), each with a bbox; a region uses the
  grids whose bbox meets its own. A cell is **wet** when its elevation is below the datum. Two passes, grids in
  list order within each: (1) the point's own cell, first wet cell wins; (2) for points still unknown, the nearest
  wet cell within **2 cells** (ground distance; a tie goes to the deeper cell). This absorbs the grids' shore ramp,
  which does not line up with the water polygons. Farther than 2 cells from any wet cell (on land, at the datum or
  above, at nodata, or off every grid) the depth stays unknown; nothing is interpolated.
- `label`: index into `labels`. A GNIS name (feature classes Bay, Channel, Harbor; point inside or within 200 m of
  the water body) when one is near enough, otherwise a position descriptor: `middle`, or `north end`, `northeast
  side`, `east end`, `southeast side`, `south end`, `southwest side`, `west end`, `northwest side`. Every point has
  a label. **Points sharing a label within a water body are one region.** GNIS gives a point and no extent, so a
  name's reach is measured from the water's width at the name; a name left with fewer than 2 points gives them back,
  and a descriptor sector with fewer than 3 merges into its fullest neighbor (rule and constants documented in
  `pipeline/seaplane_pipeline/wavefield.py`). Depth is sampled only on `great_lake` and `connecting_water`: a DEM
  cell under an inland lake is that lake's surface elevation, not its depth.

### Files

`wave_points.bin`: little-endian fixed records, 60 bytes, `struct "<ffHH16H8H"`: `lon` f32, `lat` f32, `depth_dm` u16,
`label` u16, `fetch[16]` u16 in units of 10 m, `run[8]` u16 in units of 10 ft (clamped to 65535). Records of one
water body are contiguous. `wave_points.json`:

```jsonc
{"version": 1, "record_bytes": 60, "fetch_unit_m": 10, "run_unit_ft": 10,
 "labels": ["Anchor Bay", "Big Muscamoot Bay", "middle", "north end"],
 "lakes": {"<lake_id>": [first_record, count]}}
```

The client reads one water body with an HTTP Range request (`first_record × 60`, `count × 60` bytes); the api reads
the whole file.

### Wave height at a point (SPM 1984, fetch limited; identical in Python and JS)

```
U  = wind_kt × 0.514444          UA = 0.71 × U^1.23          g = 9.80665
f  = g × fetch_m / UA²           d  = g × max(depth_m, 0.1) / UA²
depth unknown:  h = 1.6e-3 × sqrt(f)                          t = 0.2857 × f^(1/3)
depth known:    a = tanh(0.530 × d^0.75)   h = 0.283 × a × tanh(0.00565 × sqrt(f) / a)
                b = tanh(0.833 × d^0.375)  t = 7.54  × b × tanh(0.0379 × f^(1/3) / b)
h = min(h, 0.2433)   t = min(t, 8.134)     Hs_m = h × UA² / g      Tp_s = t × UA / g
wind_kt ≤ 0 or fetch ≤ 0  →  0, 0
```

Unknown depth is the deep-water form, which overstates shallow water: the conservative direction.

### Regions for one wind (`wind_dir` from, `wind_kt`, `min_run_ft`)

For each label in the water body: `usable` = its points with `run[b % 8] × 10 ≥ min_run_ft`. `hs_in` = 75th percentile
(nearest rank: sorted ascending, index `ceil(0.75 n) − 1`) of `Hs` over the usable points, in whole inches
(`floor(x / 0.0254 + 0.5)`); `run_ft` = lower median of the usable runs; `point` = the usable point with the lowest
`Hs` (ties: lowest record index), whose lon/lat stands for the region. `hs_all_in` = the same percentile over all the
label's points. A label with no usable point has `hs_in: null`. Regions sort by `hs_in` ascending with nulls last,
ties by label. The **best region** is the first with a non-null `hs_in`; the water body's open-water figure is the
largest `hs_all_in`.

The wind may differ per label (the briefing's per-region wind, below): the same rule applies, each label using its
own `wind_dir` and `wind_kt`. The client's Water section still applies one wind, from its dial, to every label.

### Water body kinds

`kind` is `lake | river | great_lake | connecting_water`. `great_lake` covers the Great Lakes and Lake St. Clair;
`connecting_water` the Detroit, St. Clair, and St. Marys Rivers. On those two kinds a restriction that is
`reach_unresolved` can raise the verdict no higher than `conditional` (a slow-no-wake zone at a creek mouth must not
turn Lake St. Clair red); on `river` it still can. Their `county` may be null; `counties` lists every county touched.

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
                  "runways": [{"id": "09L/27R", "heading": 88, "length_ft": 5676},
                              {"id": "09R/27L", "heading": 88, "length_ft": 6521},
                              {"id": "18/36", "heading": 172, "length_ft": 2582}]},
                              // heading: degrees TRUE of the first-named end. length_ft: optional (null or absent
                              // in files written before 2026-09-20); breaks a crosswind/headwind tie between
                              // parallel runways toward the longer one. The airport lookup fills it from
                              // aviationweather's `dimension` ("6521x150").
 "timezone": "America/Detroit",
 "radius_nm": 40, "n_lakes": 8, "public_access_only": false,
 "home_water": null,                                          // or {"id": 7654321, "name": "Lake St. Clair"}: always briefed, ignores radius_nm
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
   "home_water": null,                        // same shape as top-level home_water (no "observed"), worst hour inside best_window
   "confidence": "medium", "confidence_reasons": ["NWS and model wind differ by 6 kt"],
   "trend": "steady",                         // vs previous entry in runs: "improving" | "steady" | "worsening" | null
   "runs": [{"at": "18:00", "generated_at": "2026-09-19T22:00:03Z", "score": "favorable",
             "best_window": ["08:00", "12:00"], "limiting": null, "max_gust_kt": 12}],
   "summary": "Tomorrow morning (Sun): favorable 08:00–12:00. Wind 240/6 G9, no ceiling, vis 10. …"},
 "alerts": [{"event": "Lake Wind Advisory", "area": "Lake St. Clair", "ends": "2026-09-20T02:00:00Z"}],
 "lakes": [{"id": 1234567, "name": "Cass Lake", "kind": "lake", "score": "favorable", "limiting": null,
            "hs_in": 3, "run_ft": 4100,          // the best region's numbers when regions is non-empty
            "region": "west end",                // best region label, null without a wave field
            "hs_open_in": 7,                     // roughest region (hs_all_in); null without a wave field
            "regions": [{"label": "west end", "hs_in": 3, "run_ft": 4100, "lat": 42.61, "lon": -83.37,
                         "wind": {"dir": 250, "kt": 10, "gust": 15}}],  // calm to rough, max 4; wind = that region's forecast
            "wind": {"dir": 250, "kt": 10, "gust": 15}, // the best region's wind with a wave field, else the centroid's
            "distance_nm": 6.1, "bearing_deg": 118,
            "verdict": "conditional", "frozen": false}],
 "home_water": {"id": 7654321, "name": "Lake St. Clair", "kind": "great_lake", "score": "favorable", "limiting": null,
                "wind": {"dir": 250, "kt": 10, "gust": 15},
                "regions": [ /* every region, same shape, plus "score" per region; hs_in null = no usable run */ ],
                "hs_open_in": 14,
                "observed": [{"station": "45147", "name": "Lake St Clair buoy", "kind": "buoy", "at": "2026-09-19T13:00:00Z",
                              "wind": {"dir": 90, "kt": 12, "gust": 15}, "wave_ft": 1.0, "distance_nm": 9.4}],
                "marine_hs_in": 12},             // Open-Meteo marine at the centroid, null when it has none
                                                 // home_water is null when settings.home_water is null
 "sources": {"metar": "2026-09-19T21:53:00Z", "taf": "2026-09-19T17:20:00Z", "open_meteo": "…", "nws": "…"},  // null when that input failed
 "links": {"metar": "https://…", "taf": "https://…", "forecast": "https://…"},
 "errors": ["nws: timeout"]}
```

Briefing candidates: every kind. A water body with wave points is scored by its **best region** for the wind
("Wave field" above); one without is scored at lake level as before, and `river` / `connecting_water` without wave
points are not candidates (a reach length says nothing about width).

Region details: a region with no usable run into the wind keeps `hs_in: null`, and its `lat`/`lon` are its calmest
point so the map still has somewhere to go. When *no* region is usable the row is `unfavorable`, `limiting: "run"`,
and reports the calmest region's label, its all-points wave height as `hs_in`, and its ungated median run. Region and
lake-level waves are both computed at the gust. A big water body is a candidate when any of its sample points is
within `radius_nm`; `distance_nm` / `bearing_deg` are to the best region's point.

**Wind per region.** Forecasts are taken on a 0.1° grid (a point is snapped to `round(lat / 0.1) × 0.1`,
`round(lon / 0.1) × 0.1`). A water body without a wave field takes the cell of its centroid. One with a wave field
takes, **for each region**, the cell of that region's centroid (mean lat / lon of the label's points), so the two ends
of Lake St. Clair can see different winds; the row's `wind` is then the best region's (or, when no region is usable,
the calmest region's), and each `regions[]` entry carries its own `wind`. A region whose cell has no forecast for
the hour is left out of that hour. For a candidate, only regions with at least one point within `radius_nm` are
scored, so the best water is never one the pilot would not fly to; `home_water` keeps every region. `observed[].name` is null for NDBC
stations (the feed has no names). `marine_hs_in` is null when the marine model has no value or its grid point snapped
more than 10 nm from the centroid (it otherwise reports the nearest Great Lake for an inland point).

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
  Home water is set from a water body's sheet ("Make this my home water") as well as cleared in settings.
- Selected water body with a wave field: a "Water" section in the sheet with a wind control (direction and speed,
  defaulting to that water body's wind in the briefing, else the briefing's current airport wind, else 270/10), the
  region list for that wind, and the points drawn on the map colored by wave height. All computed in the browser by
  `rules/waves/` from one Range request.

## Flight mode (phase 2, added 2026-09-28)

Design: `docs/design.md` 7.5, 7.7, 7.9. Wording rules as everywhere: no "legal", "go", or "safe".

### Wind proxy (`api/seaplane_api/wind.py`, router `/api/wind`)

Online only; the client degrades silently. Nothing here raises to the client: a failed source becomes an entry in
`errors` and the rest still answers.

`GET /api/wind/stations?bbox=west,south,east,north` (degrees, WGS84). The bbox is split into **1° tiles** (floor of
lon / lat); each tile is fetched and cached for **5 min** on its own, so panning reuses tiles. More than **16 tiles**
→ `400 {"detail": "bbox too large"}`. Stations outside the requested bbox are dropped from the reply.

```jsonc
{"stations": [{"id": "KDET", "source": "metar",          // "metar" | "ndbc" | "synoptic"
               "name": "Detroit/C Young Arpt, MI, US",   // null when the feed has none (NDBC)
               "lat": 42.409, "lon": -83.01,
               "dir_deg": 240,                            // wind FROM, true; null when variable or calm
               "speed_kt": 12, "gust_kt": 18,             // gust null when none reported; calm = speed 0, dir null
               "obs_time": "2026-09-28T15:53:00Z"}],
 "fetched_at": "2026-09-28T16:01:12Z",                   // oldest tile fetch time in this reply
 "errors": ["ndbc: timeout"]}
```

Sources: aviationweather METAR by bbox and NDBC `latest_obs.txt` (both fetchers exist in `api/seaplane_api/fetch/`).
Synoptic is an adapter slot used only when `SYNOPTIC_TOKEN` is set; unset means it is skipped with no error. The same
station reported twice (same `source` + `id`) keeps the newest `obs_time`. Observations older than **3 h** at reply
time (not fetch time) are dropped, and so is a station with neither direction nor speed (sensor out). A failed
`/point` lookup is not cached, so the next request retries. NDBC has no server-side bbox, so each new tile reads the
whole file; a shared NDBC cache is a later optimization. The Synoptic adapter is written from its docs and has never
seen a live payload.

`GET /api/wind/point?lat=&lon=` → Open-Meteo current wind at the **0.1° cell** of the point (same snap as the
briefing), cached **15 min** per cell:

```jsonc
{"lat": 42.4, "lon": -82.7, "dir_deg": 250, "speed_kt": 10, "gust_kt": 15,
 "time": "2026-09-28T16:00:00Z", "source": "model"}      // 502 {"detail": "..."} when Open-Meteo fails
```

### Location (client, `web/src/location/`)

- `watchPosition` with `enableHighAccuracy`. A fix is `{lat, lon, accuracy_m, speed_kt | null, course_deg | null,
  time}`; `course_deg` is used only when `speed_kt ≥ 3` (GPS course is noise when stopped).
- Follow-me turns on by default when permission is granted; any user pan or rotate pauses it; the recenter button
  resumes it. Heading-up applies while following and `speed_kt ≥ 10`, otherwise north-up; a toggle can force
  north-up.
- Screen wake lock is held while following and released otherwise (and re-requested on `visibilitychange`).
- Own-position marker: a course arrow when `course_deg` is usable, a dot otherwise, with an accuracy ring.
- Denied permission or no GPS: no marker, recenter goes to the data's home view as in phase 1, and a one-line note
  says why. A Wi-Fi iPad without GPS falls in this case.

### Nearest tab (client, `web/src/lists/`)

- Origin: the current fix; without one, the **map center**, labeled "from map center".
- By default only water long enough to use: `chord_ft ≥ min_run_ft` (the briefing setting, else the settings
  default), every verdict included. A "Show all water" toggle drops the length filter. Sorted by distance from the
  origin to the centroid, first **25** shown. (Listing every water body put a page of unnamed ponds under Lake St.
  Clair.) Rows use the
  search-result row plus distance (nm, one decimal) and bearing (true, three digits), and the verdict word.
- Forward cone: when `speed_kt > 30` and the course is usable, only water within **±45°** of the course (plus
  anything within 2 nm in any direction) is listed, labeled "ahead". A toggle turns the cone off.
- Updates at most every **5 s** or every 0.2 nm of movement, whichever comes later. Tap → `selectLake`.

### Wind on the map and in the sheet (client)

- Wind layer toggle in the layers control; fetch `/api/wind/stations` for the visible bbox on map `idle`, throttled
  to one request per 30 s and only at zoom ≥ 8. Each station is an arrow pointing **downwind** with its speed (and
  `G<gust>`) as text. Source is shown by **arrow shape** (METAR solid arrow, buoy arrow with a ring, mesonet open
  arrow), never by color alone. Opacity steps down with age; hidden past **90 min**. Tap a station for its details.
- Selected water body: a "Wind" block with the nearest 3 stations (distance, age) and the model wind at the lake from
  `/api/wind/point`; peek line `Wind 240/12 G18 (KDET 14 min)` from the nearest station within 15 nm, else the
  model with "(model)". Headwind / crosswind components for landing along the longest chord (`chord_bearing_deg`),
  in the direction with the headwind.
- Last responses cached in IndexedDB store `wind` (key: tile id or `point:<cell>`); shown with their age; a "stale"
  badge past 60 min; nothing drawn if no fetch has ever succeeded.
