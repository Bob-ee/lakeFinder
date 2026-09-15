# GIS source recon

API reconnaissance for `pipeline/fetch.py`. Every URL, field, and count below was hit live on
2026-09-15 with `curl` (Chrome UA, `-L`) or GDAL 3.12 (`ogrinfo`/`ogr2ogr`, ESRIJSON driver).
Sample responses (a handful of features each, real geometry) are saved at
`pipeline/tests/fixtures/gis/*.sample.json`. Full datasets were fetched into a scratch
directory to get real counts/sizes/timings, then discarded — none are in this repo.

Network note that applies to several sources below: this recon environment could not reach
`gisago.mcgi.state.mi.us` (TLS handshake completes, then the server resets every HTTP request —
looks like a WAF/JA3 fingerprint block, not a DNS/route problem) or `gis.fws.gov` (every request
returns `502`). Both may work fine from the homelab's network; if not, use the alternate hosts
identified below, which all responded normally.

---

## 1. Michigan hydrography polygons (lake polygons with names)

**Correction to `docs/design.md` section 4:** the dataset is *not* served by
`gisago.mcgi.state.mi.us/.../michigan_geographic_framework/MapServer`. I checked that MapServer
directly (via `gisagocss.state.mi.us`, see network note) — it has 26 layers (Counties, Cities,
Census tracts, School/Congressional/Senate/House districts, roads, railroads, …) and **no
hydrography layer at all**. The real backing service is a different one, `OpenData/hydro`:

- **MapServer/FeatureServer layer:** `https://gisagocss.state.mi.us/arcgis/rest/services/OpenData/hydro/MapServer/17`
  (also works as `.../FeatureServer/17`). Layer name "Hydrography Polygons". `gisago.mcgi.state.mi.us`
  is unreachable from here (see network note above); `gisagocss.state.mi.us` answered every request
  in under 0.5 s.
- **Open Data item:** `midnr::hydrography-polygons` (the old design-doc slug) 301-redirects to
  `https://gis-michigan.opendata.arcgis.com/datasets/midnr::michigan-hydrography-polygons` — title
  is now "Michigan Hydrography Polygons", item id `e6f0b7dfb22d4ed49a05969970441f4f`, owner
  `MichiganDNR`. A near-duplicate frozen copy also exists as item `27134add94a04e4384225734f821a0d7`
  ("Hydrography Polygons (v17a)") pointing at the same MapServer layer — ignore it, it's an alias.

### Direct download (recommended — use this, not paging)

```
GET https://gis-michigan.opendata.arcgis.com/api/download/v1/items/e6f0b7dfb22d4ed49a05969970441f4f/geojson?layers=17
```

302s to a pre-baked file on S3 (`hub.arcgis.com/api/v3/datasets/.../downloads/data?format=geojson&spatialRefId=4326`),
already in WGS84 lon/lat (CRS84). Tested: **124,482,356 bytes in 5.1 s**, `curl -L`. CSV/Shapefile/KML
variants exist at the same path with `csv`/`shapefile`/`kml` in place of `geojson`. This is the
approach `fetch.py` should use — one request instead of 60 paginated ones (see below).

### REST query / paging (works, but 60x slower for the full set)

- `maxRecordCount`: **1000**, regardless of a larger `resultRecordCount` request — server just sets
  `exceededTransferLimit: true` and caps at 1000.
- `resultOffset` paging works correctly (verified offset 0 vs offset 3 return disjoint, ordered
  results).
- `f=geojson` on the `/query` endpoint returns WGS84 lon/lat directly (no `outSR` needed) — same CRS
  as the direct download.
- Total feature count: **`{"count":59922}`** via `returnCountOnly=true`.
- GDAL: `ogrinfo -ro -al -so "ESRIJSON:https://gisagocss.state.mi.us/arcgis/rest/services/OpenData/hydro/MapServer/17/query?where=1%3D1&outFields=*&f=json"`
  works and independently confirms the 59922 count and field list (plain MapServer/FeatureServer
  root URLs do **not** open in GDAL — you must give it a `/query` URL with the `ESRIJSON:` prefix).

### Fields (all of them — this is the full schema, no hidden GlobalID/permanent-id field exists)

| field | type | notes |
|---|---|---|
| `OBJECTID` | int | sequential, **not a stable permanent id across rebuilds** — there is no GUID/PERMANENT_IDENTIFIER field on this layer. Use the data-contract's `"{name_norm}\|{lat:.4f}\|{lon:.4f}"` fallback key. |
| **`NAME`** | string(30) | **the lake/river/swamp name.** Empty as `" "` (single space), not null/empty-string — DBF-style space padding. `.strip()` before checking truthiness. Max 30 chars, can truncate long names. |
| `NAME2` | string(30) | secondary/alternate name, same space-padding quirk. Mostly blank. |
| `ELEV` | string(6) | elevation, mostly blank in the sample. |
| `NAME_SRC` | string(5) | source of `NAME`, e.g. `"GNIS"`. |
| `NAME2_SRC` | string(5) | source of `NAME2`. |
| **`TYPE`** | string(5) | **the lake/pond vs. river vs. wetland discriminator.** Only 3 values exist (see below) — no separate "pond" code; small lakes and ponds are both `lake`. |
| `SQKM`, `SQMILES`, `ACRES` | double | precomputed area. |
| `VER` | string(3) | dataset version tag, e.g. `"17a"`. |
| `Shape.STArea()`, `Shape.STLength()` | double | native-projection area/length (meters², meters — layer's native SR is NAD83 / Michigan Oblique Mercator, EPSG:3078). |

`TYPE` code values with counts (via `groupByFieldsForStatistics`, whole state):

| `TYPE` | count |
|---|---|
| `lake` | 57,780 |
| `river` | 2,043 |
| `swamp` | 99 |
| **total** | **59,922** |

**Michigan lake count (named vs. unnamed), `TYPE='lake'` only, computed from the full downloaded file:**

- Named (`NAME` non-blank after strip): **10,284**
- Unnamed: **47,496**
- Total lake polygons: **57,780**

### Recommended fetch

```
GET https://gis-michigan.opendata.arcgis.com/api/download/v1/items/e6f0b7dfb22d4ed49a05969970441f4f/geojson?layers=17
```
One request, ~5 s, 124 MB, already WGS84. Filter to `TYPE == "lake"` in Python; keep `TYPE == "river"`/`"swamp"`
only if a later stage needs them (design.md doesn't currently use them). Strip whitespace from `NAME`/`NAME2`
before treating as null. Sample: `pipeline/tests/fixtures/gis/hydrography_polygons.sample.json` (2 named
lakes, 1 unnamed lake, 1 river, 1 swamp).

---

## 2. PLSS sections (township/range/section polygons)

Found via the site's DCAT feed (`https://gis-michigan.opendata.arcgis.com/api/feed/dcat-us/1.1.json`,
searched for "public land survey" — the portal's own full-text dataset search isn't exposed as a
simple REST call, but the DCAT feed lists every dataset with its item id and every download format
in one 8.8 MB JSON file, and is the fastest way to resolve any `midnr::<slug>` to a real service URL).

- **Layer:** `https://gisagocss.state.mi.us/arcgis/rest/services/OpenData/boundaries/MapServer/4`
  ("Public Land Survey Sections"), item id `5e087317ab6a4fb28abe4d41d8204e95`.
- Sibling layers in the same `boundaries` MapServer, in case they're useful later: layer 3 = PLSS
  quarter-quarter sections, layer 5 = PLSS town/range (no section subdivision).
- Total features: **61,340**. `maxRecordCount`: 1000 (same paging behavior as hydrography).

### Direct download (recommended)

```
GET https://gis-michigan.opendata.arcgis.com/api/download/v1/items/5e087317ab6a4fb28abe4d41d8204e95/geojson?layers=4
```
Tested: **74,593,642 bytes in 4.6 s**.

### Fields

| field | type | notes |
|---|---|---|
| `OBJECTID` | int | sequential, not a stable permanent id. |
| **`TOWN`** | string(3) | **township number+direction, e.g. `"68N"`, `"04N"`.** Always zero-padded to 2 digits + `N`/`S`. |
| **`RANGE`** | string(3) | **range number+direction, e.g. `"32W"`, `"07E"`.** Zero-padded to 2 digits + `E`/`W`. |
| **`SECTION`** | string(2) | **section number as zero-padded 2-digit string, e.g. `"03"`, `"25"`.** |
| `SEC` | smallint | same section number as a plain (non-padded) integer, e.g. `3`, `25`. |
| `TWNRNG` | string(6) | `TOWN + RANGE` concatenated, e.g. `"68N32W"`. |
| `TWNRNGSEC` | string(8) | `TOWN + RANGE + SECTION`, e.g. `"68N32W25"`. Useful as a single join key. |
| `CLAIM`, `OTHER` | string | mostly blank (space-padded) in the sample. |
| `COUNTY` | string(2) | numeric county code as a **2-char string** (e.g. `"42"`), not a county name — needs a lookup table (same code space is used by the boating-access-sites `county` field, see §3, though that one comes back as an integer). |
| `X_COORD`, `Y_COORD` | double | centroid or label point in the native projection. |
| `IcoMapAttr` | string(huge, 1073741822 declared length) | always null in the sample; an artifact of some ArcGIS "IcoMap" extension — ignore it. |
| `Shape.STArea()`, `Shape.STLength()` | double | native-projection area/length. |

**Important normalization note for the `match` stage:** `restrictions.jsonl`'s `plss` field
(per `docs/data-contract.md`) stores township/range **without zero-padding**, e.g.
`{"township": "4N", "range": "7E"}`. This PLSS layer's `TOWN`/`RANGE` fields are always
**zero-padded to 2 digits** (`"04N"`, `"07E"`). The match stage must zero-pad before joining
(`f"{int(t[:-1]):02d}{t[-1]}"`), or build the join key from `TWNRNGSEC` after normalizing both
sides the same way.

### Recommended fetch

```
GET https://gis-michigan.opendata.arcgis.com/api/download/v1/items/5e087317ab6a4fb28abe4d41d8204e95/geojson?layers=4
```
Sample: `pipeline/tests/fixtures/gis/plss_sections.sample.json` (first 5 features).

---

## 3. DNR boating access sites

Only one of the two candidate dataset names in `docs/design.md` corresponds to a real, current,
DNR-owned dataset.

- **`midnr::prd-boating-access-sites`** — real and current. Resolves (via the DCAT feed and the
  ArcGIS `sharing/rest/search` API) to item `3eaf9804bf6f4bafb8e03aea660c9fce`
  ("Michigan Boating Facilities (included in MiBFF)"), owner `MichiganDNR`/PRD, layer 0 =
  "PRD Boating Access Sites":
  ```
  https://services3.arcgis.com/Jdnp1TjADvSDxMAX/arcgis/rest/services/PRDBASPublicView/FeatureServer/0
  ```
  Same item's **layer 1 is "Watercraft Control Areas"** — not asked for here, but flagging it because
  it looks directly relevant to the `docs/design.md` §4 "DNR Special Local Watercraft Controls" source
  and might let the pipeline skip some of the county-page crawling/parsing in stage 2. Worth a look
  when that stage is built.

- **`midnr::michigan-public-boating-access-sites`** — **does not exist as an official dataset.**
  It's not in the current DCAT feed, and ArcGIS Online search for that exact title turns up only two
  items, both explicitly named `Michigan_Public_Boating_Access_Sites (1)` / `..._lab04_martin`, owned
  by `malepor1_msugis` / `mart2603_msugis` — Michigan State University **student GIS lab exercises**,
  not an authoritative DNR source. Do not use.

**Recommendation: use `prd-boating-access-sites` (PRDBASPublicView/FeatureServer/0).** It's the only
one that's actually maintained by DNR, and it's the richest of any dataset in this recon — 97 fields
including a `globalid`, county code, and (unexpectedly useful) `local_watercraft_controls` and
`controls_url` fields that point back at the same per-county watercraft-control pages the design doc's
stage-2 crawler targets.

- **Total features:** 1203. `maxRecordCount`: 2000 — **the whole dataset fits in one query, no
  paging needed.**
- `waterbodytype` breakdown: `Inland Lake` 798, `River/Stream` 315, `Great Lake` 90 (sums to 1203).

### Fields we care about

| field | type | notes |
|---|---|---|
| `globalid` | GlobalID (GUID) | **stable permanent id** — better than anything in the hydrography layer. |
| **`name`** | string(255) | facility/site name, e.g. `"North End Park"`. |
| **`waterbody`** | string(255) | **lake/river name to join against hydrography**, e.g. `"Hubbard Lake"`. |
| `waterbodytype` | string(50) | `Inland Lake` / `River/Stream` / `Great Lake` — filter to `Inland Lake` for the seaplane app's public-access flag. |
| **`county`** | integer | numeric county code (e.g. `1`), needs a name lookup. |
| **`latitude`, `longitude`** | double | already-parsed decimal degrees (also duplicated in the point geometry). |
| `ownedby` | string(50) | e.g. `"DNR-PRD"`, `"Municipal"`. |
| `launch_status` | string(25) | e.g. `"Open"`. |
| `nlanes`, `npiers`, `ntrailerableparking`, `nvehicleonlyparking` | int | capacity fields, not needed for the current design but present. |
| `local_watercraft_controls`, `controls_url`, `controls_terms` | string | **cross-reference into the DNR local-watercraft-controls pages** — same source as design.md §4's crawler target. |

### Recommended fetch

```
GET https://services3.arcgis.com/Jdnp1TjADvSDxMAX/arcgis/rest/services/PRDBASPublicView/FeatureServer/0/query
    ?where=1=1&outFields=*&f=geojson
```
Single request, no paging, no direct-download API needed (dataset is small). Verified: 5-feature
subset returned in 0.38 s. Sample: `pipeline/tests/fixtures/gis/boating_access_sites.sample.json`.

---

## 4. Federal unit boundaries (NPS + USFWS)

### NPS unit boundaries

```
https://services1.arcgis.com/fBc8EJBxQRMcHlei/arcgis/rest/services/NPS_Land_Resources_Division_Boundary_and_Tract_Data_Service/FeatureServer/2
```
(layer 2 = "NPS Boundary", polygons; layer 0 = centroids, layer 1 = "NPS Tracts" — use layer 2).
`maxRecordCount`: 2000.

Fields: `UNIT_CODE`, `UNIT_NAME`, `DATE_EDIT`, `STATE`, `REGION`, `UNIT_TYPE`, `PARKNAME`, `GlobalID`,
`AreaID`, `Status`, `Shape__Area`, `Shape__Length`.

**Best filter: `where=STATE='MI'`** — no bbox needed, and it's exact (unlike the bbox, which also
pulls in nearby-state units whose *envelope* clips the box, e.g. Illinois/Ohio/Wisconsin/Minnesota
park units, plus multi-state trails like the North Country NST that legitimately cross into MI but
aren't tagged `STATE='MI'`). Confirmed **5 Michigan units**:

| `UNIT_NAME` | `UNIT_CODE` | `UNIT_TYPE` |
|---|---|---|
| Keweenaw National Historical Park | KEWE | National Historical Parks |
| Isle Royale National Park | ISRO | National Parks |
| River Raisin National Battlefield Park | RIRA | National Battlefield Parks |
| Sleeping Bear Dunes National Lakeshore | SLBE | National Lakeshores |
| Pictured Rocks National Lakeshore | PIRO | National Lakeshores |

bbox recipe, if wanted instead/as well (`-90.5,41.6,-82.3,48.4`, WGS84 envelope):
```
GET .../FeatureServer/2/query?geometry=-90.5,41.6,-82.3,48.4&geometryType=esriGeometryEnvelope
    &inSR=4326&spatialRel=esriSpatialRelIntersects&outFields=*&f=geojson
```
returns 15 features (the 5 above plus units from IL/IN/MN/OH/WI whose polygon crosses the box) — so
`where=STATE='MI'` alone is simpler and more correct here.

Sample: `pipeline/tests/fixtures/gis/nps_boundary.sample.json` — trimmed to 3 of the 5 units
(River Raisin, Keweenaw, Pictured Rocks); Isle Royale and Sleeping Bear Dunes have huge multi-part
island/shoreline polygons (369–491 KB of GeoJSON each) and were left out of the fixture for size,
but are real, working features from the same query.

### USFWS refuge boundaries

The task's suggested official host, `gis.fws.gov`, returned `502 Bad Gateway` on every attempt
(retried) — currently unreachable from here, may be down or blocked. Use Esri Living Atlas's
mirror instead, which is fast and reliable:
```
https://services.arcgis.com/QVENGdaPbd4LUkLV/arcgis/rest/services/National_Wildlife_Refuge_System_Boundaries/FeatureServer/0
```
(layer 0 = "FWSBoundaries", polygons). `maxRecordCount`: 2000.

Fields: `ORGNAME`, `ORGCODE`, `LIT` (short station code), `RSL_TYPE`, `CostCenter`, `FWSREGION`,
`GlobalID`, `Shape__Area`, `Shape__Length`. **No `STATE` field** — this is the one gotcha: bbox alone
is not a clean Michigan filter, because FWS Region 3 (upper Midwest) spans MI/WI/OH/MN/IL/IN and the
`-90.5,41.6,-82.3,48.4` bbox genuinely clips real refuges/WPAs in all of those states (Necedah,
Horicon, Green Bay, Gravel Island NWRs in WI; Ottawa, Cedar Point, West Sister Island NWRs in OH;
Hackmatack NWR in IL/WI; etc. — 17 `RSL_TYPE='NWR'` features total in-bbox, only some Michigan's).

`RSL_TYPE` also mixes several FWS land categories in this layer — filter to `RSL_TYPE='NWR'` to drop
Waterfowl Production Areas (`WPA`), Farm Service Agency interests (`FSA`), fish hatcheries (`NFH`),
coordination areas (`COORD`), and administrative sites (`AS`), none of which are refuges.

**Confirmed Michigan NWRs (via bbox + `RSL_TYPE='NWR'`, cross-checked by name):**

| `ORGNAME` | `LIT` |
|---|---|
| Detroit River International Wildlife Refuge | DTR |
| Michigan Islands National Wildlife Refuge | MCH |
| Seney National Wildlife Refuge | SNY |
| Shiawassee National Wildlife Refuge | SHW |
| Huron National Wildlife Refuge | HRN |
| Harbor Island National Wildlife Refuge | HBR |
| (Kirtlands Warbler Wildlife Management Area — `LIT` KIW, `RSL_TYPE='NWR'` but not a "refuge" by name) | KIW |

Recommended query for the pipeline: bbox + `RSL_TYPE='NWR'`, then filter the ~17 results to Michigan
by `LIT` allowlist (the 6–7 codes above are stable and there's no cheaper alternative given the
missing `STATE` field — a full spatial join against a MI state-boundary polygon would also work but
is unnecessary complexity for 6 known refuges).

```
GET .../FeatureServer/0/query?geometry=-90.5,41.6,-82.3,48.4&geometryType=esriGeometryEnvelope
    &inSR=4326&spatialRel=esriSpatialRelIntersects&where=RSL_TYPE='NWR'&outFields=*&f=geojson
```

Sample: `pipeline/tests/fixtures/gis/fws_refuge_boundary.sample.json` (Huron, Harbor Island,
Shiawassee — 3 of the 6, others dropped from the fixture for size, same reasoning as NPS above).

**PAD-US alternative:** not tested — the two sources above were both reachable and sufficient, and
PAD-US would add a much larger multi-agency dataset for a need that's fully met by NPS + FWS alone.

---

## 5. FAA airspace and airports (low priority — kept brief per instructions)

Both confirmed live at the exact host the task suggested (`services6.arcgis.com/ssFJjBXIUyZDrSYZ`,
owner `AeronauticalInformationServices_FAA` — this is FAA's own official ArcGIS Online org account).

### Class Airspace
```
https://services6.arcgis.com/ssFJjBXIUyZDrSYZ/arcgis/rest/services/Class_Airspace/FeatureServer/0
```
`maxRecordCount`: 2000. Key fields: `NAME`, `CLASS` (`B`/`C`/`D`/`E`/null for Mode C veils etc.),
`LOWER_VAL`/`UPPER_VAL` + `LOWER_UOM`/`UPPER_UOM` (altitude floor/ceiling), `LOWER_CODE` (e.g. `SFC`),
`CITY`, `STATE`, `ONSHORE`, `Shape__Area`.

bbox query (`-90.5,41.6,-82.3,48.4`) returns 335 features (includes bordering states/Canada — combine
with `where=STATE='MI'` to get just Michigan-labeled airspace, tested working, returns Detroit Class B
+ Mode C veil plus Class E surface areas for MI towered fields):
```
GET .../FeatureServer/0/query?geometry=-90.5,41.6,-82.3,48.4&geometryType=esriGeometryEnvelope
    &inSR=4326&spatialRel=esriSpatialRelIntersects&where=STATE='MI'&outFields=*&f=geojson
```

### Airports
```
https://services6.arcgis.com/ssFJjBXIUyZDrSYZ/arcgis/rest/services/US_Airport/FeatureServer/0
```
(service name is `US_Airport`, layer 0 is named "Airports"). `maxRecordCount`: 1000. Key fields:
`IDENT`, `ICAO_ID`, `NAME`, `TYPE_CODE` (`AD` = airport, `HP` = heliport, etc.), `STATE`,
`LATITUDE`/`LONGITUDE` (**DMS strings**, e.g. `"42-56-30.1070N"` / `"085-29-11.0810W"` — parse before
use; geometry point is also present and already decimal), `PRIVATEUSE`, `OPERSTATUS`.

`where=STATE='MI'` + the same bbox returns **490 features** (public + private airports, heliports,
seaplane bases all mixed together under `TYPE_CODE`):
```
GET .../FeatureServer/0/query?geometry=-90.5,41.6,-82.3,48.4&geometryType=esriGeometryEnvelope
    &inSR=4326&spatialRel=esriSpatialRelIntersects&where=STATE='MI'&outFields=*&f=geojson
```

Samples: `pipeline/tests/fixtures/gis/faa_class_airspace.sample.json`,
`pipeline/tests/fixtures/gis/faa_airports.sample.json` (5 features each).

Not investigated further (time-boxed per instructions): FAA NASR 28-day subscription shapefiles as
an alternative source, and whether `TYPE_CODE` includes a distinct seaplane-base code worth surfacing
separately in the basemap/overlay layer.

---

## 6. Protomaps basemap

**The task's URL is stale.** `https://build.protomaps.com/` itself still hosts the actual `.pmtiles`
files (confirmed — see below), but there's no directory listing there (it's a Cloudflare R2 bucket
without public listing enabled; hitting it directly 404s with an R2 "Object not found" page). The
current docs (`docs.protomaps.com/basemaps/downloads`) point at a proper build browser instead:

- **Human-browsable list:** `https://maps.protomaps.com/builds` (client-rendered SPA).
- **Machine-readable manifest** (what `fetch.py` should actually hit):
  ```
  GET https://build-metadata.protomaps.dev/builds.json
  ```
  Returns a JSON array of every retained build: `{"key": "20260915.pmtiles", "size": 138031484053,
  "md5sum": "...", "b3sum": "...", "uploaded": "2026-09-15T09:02:55.277Z", "version": "4.15.2"}`.
  Sorted oldest→newest; take the last element for "latest". Retention per the docs: all builds from
  the past week, plus the latest build of each patch version.

**Latest build as of this recon (2026-09-15):** `20260915.pmtiles`, 138,031,484,053 bytes
(~138 GB, full planet, z0–15), version `4.15.2`, at:
```
https://build.protomaps.com/20260915.pmtiles
```
Confirmed reachable with a plain `curl -I` (200, `content-length` matches the manifest, `accept-ranges: bytes`,
served by Cloudflare) and with `pmtiles show <url>` (prints the archive header/metadata by fetching
only the directory, not the whole 138 GB — bounds -180..180 / -85.05..85.05, tile type mvt, gzip).

### Extract dry run (confirms `pmtiles extract` works over HTTP without downloading the archive)

```
pmtiles extract "https://build.protomaps.com/20260915.pmtiles" out.pmtiles \
  --bbox=-83.6,42.7,-83.5,42.8 --maxzoom=14
```
Ran exactly this into the scratchpad with the tiny bbox specified in the task. Result:
- **25 total HTTP requests**, 1.2 MB transferred, **completed in 4.7 s**.
- Output file: **1,139,249 bytes (1.1 MB)** for that ~9 km × 11 km box at z0–14.
- Overfetch ratio reported by the tool: 0.05 (i.e. only 5% more data fetched than strictly needed —
  range-request extraction is efficient, not a linear function of the source archive's 138 GB size).

This confirms the approach in `docs/design.md` §5/§7 works as designed: `fetch.py` (or `build.py`) can
extract the full Michigan bbox (`-90.5,41.6,-82.3,48.4`) directly from the remote daily build over
HTTP range requests, with no need to download the 138 GB planet file first. The full-Michigan extract
was not run during this recon (would be a multi-GB, multi-minute operation and isn't needed to confirm
the mechanism) — budget real time for it when `build.py` is written, and note that Michigan at z0–14
will pull a nontrivial fraction of total requests compared to this tiny test box, though nowhere near
proportional to the full planet's size.

No fixture sample was produced for this one (task didn't ask for a GeoJSON-style sample — the dry-run
extract itself, left in the scratchpad, is the evidence).

---

## Fixture files written

All under `pipeline/tests/fixtures/gis/` (repo-relative from `/Users/bobbywhiteley/Documents/Claude/Projects/lakeFinder`):

| file | features | source |
|---|---|---|
| `hydrography_polygons.sample.json` | 5 (2 named lakes, 1 unnamed lake, 1 river, 1 swamp) | OpenData/hydro MapServer/17 |
| `plss_sections.sample.json` | 5 | OpenData/boundaries MapServer/4 |
| `boating_access_sites.sample.json` | 5 | PRDBASPublicView/FeatureServer/0 |
| `nps_boundary.sample.json` | 3 of 5 MI units | NPS_Land_Resources_Division.../FeatureServer/2 |
| `fws_refuge_boundary.sample.json` | 3 of 6 MI refuges | National_Wildlife_Refuge_System_Boundaries/FeatureServer/0 |
| `faa_class_airspace.sample.json` | 5 | Class_Airspace/FeatureServer/0 |
| `faa_airports.sample.json` | 5 | US_Airport/FeatureServer/0 |

## Open blockers / things to verify from the homelab, not this sandbox

1. `gisago.mcgi.state.mi.us` resets every connection from here (TLS OK, HTTP request gets RST) —
   test again from the homelab; if it's blocked there too, `gisagocss.state.mi.us` is a confirmed
   working alternate host for the same `OpenData/*` services.
2. `gis.fws.gov` returned 502 on every attempt — the Living Atlas mirror used above is a fine
   permanent substitute regardless.
3. None of the ArcGIS Online/opendata hosts needed authentication or hit a rate limit during this
   recon; no API keys are required for anything above.


## Addendum (integration, 2026-09-15)

- **County and township names** come from `https://gisagocss.state.mi.us/arcgis/rest/services/OpenData/michigan_geographic_framework/MapServer`
  layer 0 (Counties, 83 features) and layer 2 (Minor Civil Divisions, 1,520 features), spatial-joined on the lake
  centroid. Both are cached in `data/cache/`. No Census TIGER download is needed.
- **Basemap zoom:** the z14 Michigan extract is ~550 MB; the z12 extract is 125 MB and is what `build` produces by
  default (`--basemap-maxzoom` overrides). Lakes carry their own z14 detail in `lakes.pmtiles`.
- `gisago.mcgi.state.mi.us` also reset connections from this MacBook; `gisagocss.state.mi.us` is used throughout.
