# Daily briefing: "Is it a SeaRey day?"

Added 2026-09-19 from Bobby's idea. Status: in progress. Belongs to the roadmap as step B in `docs/handoff.md`
(it shares the `api` service with the wind layer). Section 3.4 (evening outlook for tomorrow morning) was added
the same day from Bobby's follow-up ask. **Schemas and endpoints live in `docs/data-contract.md`**; this file is
the reasoning and the algorithm.

## 1. Purpose

A short, automatically refreshed briefing, anchored on the home airport, that answers: **is today (and the next
day) a good day to fly the SeaRey amphib, and if so, which nearby lakes will have favorable water?** It considers
wind, gusts, crosswind on the runway and on each lake's usable run, predicted wave height per lake from wind and
fetch, ceiling and visibility, precipitation and convection, density altitude, temperature and ice, and daylight.

Hard constraints from Bobby:

- **No LLM, no tokens.** Every number comes from a deterministic algorithm over public feeds. The output is
  templated text plus structured data.
- **Updates itself every few hours** whether or not the app is open.
- **A forecast for tomorrow morning is ready at 18:00, 20:00, and 22:00 local every evening** so Bobby can plan the
  morning the night before and watch whether the forecast is holding (section 3.4).
- **Home airport is adjustable in the app.**

Not a weather product for legal preflight. The briefing card carries the same disclaimer posture as the verdicts:
it says "favorable / marginal / unfavorable" with the limiting factor, and links the raw METAR, TAF, and forecast.

## 2. Inputs

All keyless or free tier, all fetched server-side and cached. Failures degrade one input, never the whole briefing.

| input | source | endpoint | cadence |
|---|---|---|---|
| Home airport METAR, nearby METARs | aviationweather.gov | `/api/data/metar?ids=KPTK&format=json` and `?bbox=` | every run |
| Home airport TAF | aviationweather.gov | `/api/data/taf?ids=KPTK&format=json` | every run |
| Hourly forecast at the airport and at each candidate lake | Open-Meteo | `/v1/forecast?latitude=a,b,c&longitude=x,y,z&hourly=wind_speed_10m,wind_gusts_10m,wind_direction_10m,temperature_2m,dew_point_2m,precipitation,precipitation_probability,weather_code,cloud_cover,visibility,cape,pressure_msl&wind_speed_unit=kn&forecast_days=2&timezone=America/Detroit` (multi-point in one request; batch 50 lakes per call) | every run |
| NWS alerts (lake wind advisory, small craft advisory, wind advisory, convective) | api.weather.gov | `/alerts/active?point=lat,lon` | every run |
| NWS hourly gridpoint forecast (sky cover, wind, as a second opinion for ceiling) | api.weather.gov | `/points/{lat},{lon}` then the `forecastHourly` URL | every run |
| Great Lakes buoy observations (only for lakes flagged shoreline) | NDBC | `latest_obs.txt` filtered by bbox | every run |
| Sunrise, sunset, civil twilight | computed | NOAA solar algorithm, local code, no network | every run |
| Lake geometry: verdict, chord, extent by bearing, public access, distance from home | pipeline output | `index.json`, `lake_extents.json` (new, section 5) | at pipeline build |

Open-Meteo's marine API covers oceans and the Great Lakes only; inland lake waves are computed, not fetched.

Learned from the live feeds (2026-09-19): aviationweather airport `elev` is meters and runway `alignment` is degrees
true; METAR `altim` is hPa; TAF and METAR visibility can be strings ("10+", "1 1/2"); Open-Meteo visibility is
meters, has no ceiling product, and `pressure_msl` (not `surface_pressure`) is the altimeter source for density
altitude; NWS `forecastHourly` has no sky cover, so **ceiling comes only from the METAR and TAF** and is "unknown"
for hours neither covers. Lakes share forecast points on a 0.1° grid: 309 candidates near KPTK become 78 points and
3 Open-Meteo calls; a full run is 8 HTTP requests and under 2 seconds. NDBC is fetched and parsed but unused until
the pipeline tags Great Lakes shoreline water.

## 3. The algorithm

Everything below runs in `api/briefing/` as pure functions with unit tests; the fetchers are separate so tests use
recorded fixtures.

### 3.1 Time blocks

Evaluate 3-hour blocks from now through the end of tomorrow's civil twilight, and only blocks that overlap
daylight (civil twilight to civil twilight). For each block use the forecast hour nearest the block center, with
the METAR overriding the first block at the airport when it is under 90 minutes old.

### 3.2 Per-block airport score

Compute, in order, and record the first factor that fails. Limits come from `settings.json` (section 6);
defaults are placeholders Bobby must confirm.

| factor | favorable | marginal | unfavorable | default limits |
|---|---|---|---|---|
| sustained wind | ≤ `wind_ok` | ≤ `wind_max` | above | 12 / 18 kt |
| gusts | gust spread ≤ `gust_spread_ok` | ≤ `gust_spread_max` | above | 8 / 12 kt |
| runway crosswind (best runway, from FAA airport runway headings; fall back to a single stored heading) | ≤ `xwind_runway_ok` | ≤ `xwind_runway_max` | above | 8 / 12 kt |
| ceiling | ≥ `ceiling_ok` | ≥ `ceiling_min` | below | 3000 / 1500 ft AGL |
| visibility | ≥ `vis_ok` | ≥ `vis_min` | below | 6 / 3 sm |
| precipitation | none, prob < 30% | light, prob < 60% | otherwise | |
| convection | `cape` < 500 and no TS weather code | `cape` < 1000 | otherwise, or any convective alert | J/kg |
| density altitude | ≤ `da_ok` | ≤ `da_max` | above | 3500 / 5000 ft |
| fog | otherwise | temp/dewpoint spread ≤ `fog_spread_f` with wind ≤ 5 kt ("fog risk") | model visibility < `vis_min`, or TAF carries FG/FZFG or vis < `vis_min` for the hour | 3 °F |
| temperature | ≥ `temp_water_min` (water ops) | ≥ freezing (land only) | below freezing | 40 °F |
| NWS alerts | none | wind advisory | lake wind advisory, small craft, any convective or winter alert | |

Block score = worst factor. A day is summarized by its best consecutive 3 hours of daylight, reported as
"favorable 10:00–16:00, limited by gusts after 16:00."

Crosswind and headwind components: `xw = wind * sin(wind_dir - heading)`, `hw = wind * cos(...)`, using gust
speed for the marginal/unfavorable checks and sustained speed for favorable.

Density altitude: pressure altitude `PA = field_elev + (29.92 - altimeter_inHg) * 1000`; `DA = PA + 120 * (OAT_C -
(15 - 2 * PA / 1000))`.

### 3.3 Per-lake water score

Candidate lakes: verdict `clear` or `conditional`, within `radius_nm` of the home airport (default 40 nm),
`chord_ft` ≥ `min_chord_ft`, optionally public access. For each candidate and each block:

1. **Wind at the lake** from the Open-Meteo point forecast for the lake centroid (not the airport).
2. **Usable run along the wind.** Landing and takeoff are into the wind, so the run available is the lake's extent
   along the wind bearing: `extent_by_bearing[bin(wind_dir)]` from `lake_extents.json` (16 bins of 22.5°). Require
   ≥ `min_run_ft` (default 2000, same knob as `min_chord_ft`). If the wind is light (< 5 kt) use the longest chord
   instead and skip the crosswind check.
3. **Fetch** is the largest extent of the wind bin and its two neighbors, `max(extent[b-1], extent[b], extent[b+1])`,
   as the fetch length `F` in meters. The run uses the wind bin alone. Reason: `extent_by_bearing` samples 40 lines
   per bearing and can undershoot on irregular shorelines (Orchard Lake's 90° bin reads 6,616 ft against an 8,084 ft
   chord that threads a narrow neck). Undershooting is the conservative direction for the run and the wrong one for
   fetch, and SPM practice is to consider an arc around the wind rather than one radial.
4. **Significant wave height** from the SPM 1984 deep-water fetch-limited relation (USACE Shore Protection Manual,
   1984, eq. 3-33/3-34):

   ```
   U10 = sustained wind in m/s (use gusts for the marginal check)
   UA  = 0.71 * U10^1.23                         # wind stress factor
   Hs  = 1.6e-3 * UA^2 / g * sqrt(g * F / UA^2)  # meters, simplifies to 5.1e-4 * UA * sqrt(F)
   Tp  = 0.2857 * UA / g * (g * F / UA^2)^(1/3)  # seconds
   ```

   Cap `Hs` at the fully developed value `Hs_fd = 2.433e-1 * UA^2 / g` (SPM 1984; an earlier draft of this doc had
   2.482e-2, ten times too small, which would have capped the worked example below). The cap only binds past
   roughly 370 km of fetch, so it never matters inland. Deep water overestimates on shallow
   inland lakes, which is the conservative direction. Worked check: 20 kt (10.3 m/s), fetch 5 km → about 0.45 m
   (1.5 ft); fetch 40 km (Lake St. Clair) → about 1.3 m (4.2 ft). Small lakes stay calm in wind that makes the big
   ones unusable, which is exactly what the feature is for.
5. **Wave verdict:** favorable if `Hs` ≤ `wave_ok` (default 8 in), marginal ≤ `wave_max` (default 12 in),
   unfavorable above. Also unfavorable when `Tp` < 2 s and `Hs` > 6 in (steep chop) once Bobby confirms that
   matters for the hull.
6. **Water crosswind:** with the run aligned to the wind the crosswind is near zero; report it anyway for the
   longest chord in case the pilot prefers the long axis: `xw` on `chord_bearing_deg`, limit `xwind_water_max`
   (default 10 kt).
7. **Ice gate:** if any of the last 5 days had a daily max temperature below 32 °F, or the date is between
   `ice_season_start` and `ice_season_end` (default Dec 1 to Apr 1), mark water ops "likely frozen, verify" and
   exclude from recommendations.
8. Lake score = the lake's own wave, run, water-crosswind, and ice checks. The airport weather score is *not* folded
   in (changed 2026-09-19 after the first real run: every row read "marginal, ceiling", which repeats the header
   and hides the water). Rank by score, then distance, then `Hs`. Ranking on `Hs` first put eight 2,000 ft ponds
   36 nm out ahead of Cass and Orchard; small water still wins on windy days through the score.

Output the top `n_lakes` (default 8) with, per lake: name, distance and bearing from home, verdict, `Hs` in inches,
run available in the wind, wind at the lake, and the limiting factor.

### 3.4 Evening outlook for tomorrow morning (added 2026-09-19)

Bobby plans morning flights the night before. Every run computes an `outlook` object; the 18:00, 20:00, and 22:00
local runs (`settings.outlook.times_local`) are the ones he will read, and each of them is recorded so the card can
show whether the forecast is holding.

- **Target date.** Tomorrow when local time is at or past `morning_end_local` (default 12:00), otherwise today. The
  06:00 run therefore refines *this* morning and its trend is measured against last night's 22:00 run.
- **Window.** `morning_start` (default `sunrise`; also `civil_twilight` or `HH:MM`) to `morning_end_local`.
- **Hourly, not 3-hour blocks.** Each whole hour that overlaps the window is scored with the section 3.2 factor table
  using the forecast for that hour. The TAF is used for ceiling, visibility, and fog whenever it covers the hour (an
  18Z TAF covers the next morning); otherwise Open-Meteo cloud cover and visibility with the NWS hourly sky cover as
  the second opinion. Fog matters most here: radiation fog over the lakes at sunrise is the common morning killer.
- **Outlook score** = the best level L for which at least `min_window_hours` (default 2) consecutive hours all score
  L or better. `best_window` = the earliest longest such run. When the score is not favorable, `limiting` is the
  limiting factor of the worst hour inside `best_window`. When it is favorable, `watch` names what ends the window,
  if anything ("gusts after 11:00").
- **Lakes.** The section 3.3 ranking evaluated over `best_window` (the whole window when there is none), each lake
  taking its worst hour (max `Hs`, min run).
- **Run history and trend.** Runs whose scheduled time is in `outlook.times_local` (and the first run of the target
  morning) append `{at, generated_at, score, best_window, limiting, max_gust_kt}` to `outlook.runs`, carried forward
  from the previous `briefing.json` while `target_date` is unchanged. `trend` compares with the previous entry:
  score rank first, then `max_gust_kt` changing by 3 kt or more; `improving | steady | worsening`, `null` on the
  first run.
- **Confidence** (deterministic): `high` when the TAF covers the window and agrees with the model on the
  ceiling/visibility score, NWS and Open-Meteo peak morning wind differ by ≤ 4 kt, and the score did not change
  since the previous run; `low` when the two wind forecasts differ by more than 8 kt or the score moved two levels;
  `medium` otherwise. `confidence_reasons` lists the plain-language causes.
- **Push (optional).** When `settings.notify.ntfy_url` is set, each outlook run posts the outlook summary line to
  that ntfy topic. Off by default; nothing leaves the tailnet until Bobby sets it.

### 3.5 Text rendering

Template strings only, e.g.:

> **Favorable 10:00–16:00 today.** KPTK wind 250/9 G14, ceiling 4,500, vis 10. Density altitude 2,100 ft. Lake wind
> advisory on Lake St. Clair. Best water: Lake Angelus is restricted; Cass Lake 6 in chop with 4,100 ft run into
> the wind; Orchard Lake 5 in; Big Lake 3 in. Tomorrow: marginal, gusts to 22 after noon.

Outlook line, e.g.:

> **Tomorrow morning (Sun): favorable 08:00–12:00.** Wind 240/6 G9, no ceiling, vis 10. Fog risk until 08:00.
> Best water: Cass Lake 2 in, Orchard Lake 2 in. Steady since 18:00. Confidence medium: NWS and model wind differ
> by 6 kt.

All numbers in `briefing.json` are already rounded for display on the server (kt, °F, inches, and minutes as
integers; ceiling and density altitude to the nearest 100 ft); the client prints them as they are.

### 3.6 Wind model for the forecast timeline (measured 2026-09-28)

The timeline takes wind from `settings.forecast.wind_models`, tried per hour in order (speed, gust and direction
together from the first model with all three), `best_match` last. The default order was picked from a hindcast:
Open-Meteo's own values for the last 48 h at each station, against observations at KONZ, KMTC, KDET, KPTK (METAR,
aviationweather.gov, matched to the nearest top of the hour) and NDBC 45147 (realtime2, hourly rows; m/s to kt).
216 hour pairs: 48 + 48 + 47 + 48 METAR hours and 25 buoy hours. Bias is model minus observed (kt); MAE is the mean
absolute error.

| model | speed bias | speed MAE | gust bias | gust MAE | notes |
|---|---|---|---|---|---|
| `best_match` | -0.24 | 1.70 | +4.06 | 4.59 | identical to the next two over this window |
| `ncep_hrrr_conus` | -0.24 | 1.70 | +4.06 | 4.59 | ends near 48 h ahead |
| `gfs_seamless` | -0.24 | 1.70 | +4.06 | 4.59 | in CONUS this is HRRR blended into GFS |
| `ncep_nam_conus` | +0.78 | 1.87 | +4.01 | 4.57 | |
| `ncep_nbm_conus` | +0.96 | 1.83 | +5.05 | 5.22 | reaches ~10 d |
| `icon_seamless` | -0.53 | 2.09 | +5.04 | 5.38 | worst at the buoy (bias -2.9, MAE 4.4) |
| `gfs_global` | +1.14 | 2.02 | +5.92 | 6.35 | |

Per station, `best_match` speed MAE: KONZ 1.17, KMTC 2.68, KPTK 1.49, KDET 1.58, 45147 1.42; NBM: 1.31, 2.91, 1.45,
1.75, 1.67 (NBM is better only at KPTK, and only by 0.04). A 7 day run (278 pairs) gives the same ranking.

How to read it:

- **Gust** is scored against the observed gust where the station reported one and against the observed speed when
  it did not (a calm METAR has no gust, and a model gust of 9 over a 5 kt wind is an error). Only 6 of the 216
  hours reported a gust, so the gust bias is the number to read: every model's gust runs **4 to 6 kt high**, which is the known "3 kt G12" effect. Waves are computed at the gust, so the timeline's wave rows carry
  that bias; it is a reason to prefer the model with the smallest gust bias, not a reason to change the rule.
- **HRRR, `best_match` and `gfs_seamless` are the same numbers** here because Open-Meteo builds all three from HRRR
  for CONUS in the near term. The hindcast therefore cannot tell them apart, and it says nothing about skill at 48
  to 72 h, where `best_match` falls back to global models and NBM (calibrated, ~3 km) may well be better. The window
  was also calm (5 to 10 kt); a windy week would separate the models more.
- On this evidence NBM is **not** better: +0.9 kt speed bias and +1 kt more gust bias than `best_match`.

Default: `["ncep_hrrr_conus", "best_match"]`. HRRR is explicit for the first ~48 h (the same numbers `best_match`
gives today, but the timeline's `model` field then says which model an hour came from and the near term does not
change if Open-Meteo re-blends `best_match`), and `best_match` carries the rest. `["ncep_nbm_conus", "best_match"]`
(the contract's example) stays a one-line settings change; re-run the comparison across a windy stretch before
making it the default. Script: none is checked in; the method is the paragraph above.


## 4. Architecture

- **`api/` service (new, FastAPI, Python 3.12, uv).** Already referenced by `docker-compose.yml` and the Caddyfile.
  Holds the wind proxy from design section 7.9 and the briefing generator. On macOS it can run under `launchd`
  instead of Docker; either way it writes into the same directory Caddy serves.
- **Scheduler.** An in-process `asyncio` task wakes every 30 s and runs the briefing when a wall-clock time in
  `schedule.run_times_local ∪ outlook.times_local` (in `settings.timezone`, DST-safe via `zoneinfo`) has passed
  since the last run. Checking "did a scheduled time pass" rather than sleeping until the next one survives the
  MacBook sleeping; a missed time older than 90 minutes is skipped. It also runs on startup when `briefing.json`
  is missing or older than 3 hours, and immediately when settings change. `seaplane-api briefing --once` does one
  run from the CLI for a launchd/cron alternative. The host must stay awake for the evening runs (`pmset`/
  `caffeinate`; decide at deploy time).
- **Output.** `data/out/briefing.json` (schema in section 6), atomically replaced. The client reads it as a
  static file, so it works through the normal `/data/` path and caches like everything else.
- **Settings.** `GET/PUT /api/settings` reads and writes `data/manual/settings.json`. The app's Settings screen
  edits home airport (search the FAA airports layer already in `overlays.pmtiles`, or type an identifier), radius,
  and personal limits. Single user, tailnet only, no auth (design section 8).
- **Client.** A briefing card on the Nearest tab (or its own tab) showing the summary line, the block strip for
  today and tomorrow, and the ranked lakes as rows that call `selectLake(id)`. Age badge; "stale" past 6 hours;
  the last briefing stays visible offline (IndexedDB store `briefing`).
- **Notifications (later).** Web Push is not available on iOS PWAs without extra setup; a morning `ntfy` or email
  from the server is the pragmatic first version. Out of scope until asked.

## 5. Pipeline additions

- `geometry` computes `extent_by_bearing`: for each lake and each of 16 bearing bins, the longest straight segment
  through the polygon along that bearing (rotate the polygon so the bearing is the x axis, sample ~40 horizontal
  lines across its height, take the longest inside segment; verify containment as the chord code does). Store as
  16 integers (ft). `longest_chord_ft` and `chord_bearing_deg` stay as they are. Built 2026-09-19: bearings are
  corrected for grid convergence (EPSG:3078 grid north is up to ~2° off true north across Michigan), computed only
  for lakes with a chord of 1,000 ft or more (6,054 of 10,783), and add about 50 s to the statewide `geometry` run.
- `build` writes `data/out/lake_extents.json`: `{ "<lake_id>": [ft × 16] }` for lakes with `chord_ft` ≥ 1000.
  Separate file so `index.json` stays under its budget. Listed in `pack.json`.
- Lake depth is not available today. If a depth source appears (Michigan DNR lake maps), the shallow-water form
  of the SPM equations can replace the deep-water one per lake.

## 6. Schemas

**Superseded: the authoritative schemas are in `docs/data-contract.md` ("Briefing").** The sketches below are the
original design and lack the outlook, schedule, and notify fields.

`data/manual/settings.json`

```jsonc
{"home_airport": {"id": "KPTK", "name": "Oakland County Intl", "lat": 42.6655, "lon": -83.4187, "elev_ft": 981,
                  "runways": [{"id": "09R/27L", "heading": 91}, {"id": "18/36", "heading": 179}]},
 "radius_nm": 40, "refresh_hours": 3, "n_lakes": 8, "public_access_only": false,
 "limits": {"wind_ok": 12, "wind_max": 18, "gust_spread_ok": 8, "gust_spread_max": 12,
            "xwind_runway_ok": 8, "xwind_runway_max": 12, "xwind_water_max": 10,
            "ceiling_ok": 3000, "ceiling_min": 1500, "vis_ok": 6, "vis_min": 3,
            "da_ok": 3500, "da_max": 5000, "temp_water_min_f": 40,
            "wave_ok_in": 8, "wave_max_in": 12, "min_run_ft": 2000,
            "ice_season_start": "12-01", "ice_season_end": "04-01"}}
```

`data/out/briefing.json`

```jsonc
{"generated_at": "2026-09-19T11:00:00Z", "home_airport": "KPTK", "valid_from": "...", "valid_to": "...",
 "summary": "Favorable 10:00–16:00 today. …",
 "days": [{"date": "2026-09-19", "best_window": ["10:00", "16:00"], "score": "favorable",
           "blocks": [{"start": "07:00", "end": "10:00", "score": "marginal", "limiting": "gusts",
                       "wind": {"dir": 250, "kt": 9, "gust": 17}, "ceiling_ft": 4500, "vis_sm": 10,
                       "da_ft": 2100, "temp_f": 61, "precip_prob": 10}]}],
 "alerts": [{"event": "Lake Wind Advisory", "area": "Lake St. Clair", "ends": "..."}],
 "lakes": [{"id": 1234567, "name": "Cass Lake", "score": "favorable", "hs_in": 6, "run_ft": 4100,
            "wind": {"dir": 250, "kt": 10, "gust": 15}, "distance_nm": 6.1, "bearing_deg": 118,
            "verdict": "conditional", "limiting": null}],
 "sources": {"metar": "2026-09-19T10:53:00Z", "taf": "...", "open_meteo": "...", "nws": "..."},
 "errors": []}
```

`data/out/lake_extents.json`: `{"<id>": [ft × 16]}`, bins centered on 0°, 22.5°, … 337.5° (true).

## 7. Tests

- Wave model against the worked values above and against the SPM nomogram points (pick three).
- Crosswind and density altitude against hand calculations.
- Block scoring with recorded Open-Meteo and METAR fixtures for a calm day, a gusty day, an IFR day, a summer
  convective afternoon, and a January day (ice gate).
- Lake ranking on a synthetic set: a big lake and a small lake in the same 18 kt wind; the small one must win.
- `extent_by_bearing` on the rect30 fixture: the 30° bin equals the long side, the 120° bin equals the short side.

## 8. Questions only Bobby can answer

1. Home airport identifier and whether "nearby" means from home or from current GPS position when airborne.
2. SeaRey numbers to replace the placeholders: max wave height for the hull, crosswind on water and on the runway,
   wind and gust personal minimums, density altitude comfort, minimum water temperature or an ice rule.
3. Personal VFR minimums (ceiling, visibility).
4. Radius for candidate lakes and whether public access should be required in the recommendations.
5. Whether the evening outlook should also push to the phone (ntfy is built in but off; it needs the ntfy app and
   a topic URL), or the in-app card is enough.
6. Morning window: sunrise to noon is the default. Earlier start (civil twilight) or a different end?
