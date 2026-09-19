# Daily briefing: "Is it a SeaRey day?"

Added 2026-09-19 from Bobby's idea. Status: designed, not started. Belongs to the roadmap as step B in
`docs/handoff.md` (it shares the `api` service with the wind layer).

## 1. Purpose

A short, automatically refreshed briefing, anchored on the home airport, that answers: **is today (and the next
day) a good day to fly the SeaRey amphib, and if so, which nearby lakes will have favorable water?** It considers
wind, gusts, crosswind on the runway and on each lake's usable run, predicted wave height per lake from wind and
fetch, ceiling and visibility, precipitation and convection, density altitude, temperature and ice, and daylight.

Hard constraints from Bobby:

- **No LLM, no tokens.** Every number comes from a deterministic algorithm over public feeds. The output is
  templated text plus structured data.
- **Updates itself every few hours** whether or not the app is open.
- **Home airport is adjustable in the app.**

Not a weather product for legal preflight. The briefing card carries the same disclaimer posture as the verdicts:
it says "favorable / marginal / unfavorable" with the limiting factor, and links the raw METAR, TAF, and forecast.

## 2. Inputs

All keyless or free tier, all fetched server-side and cached. Failures degrade one input, never the whole briefing.

| input | source | endpoint | cadence |
|---|---|---|---|
| Home airport METAR, nearby METARs | aviationweather.gov | `/api/data/metar?ids=KPTK&format=json` and `?bbox=` | every run |
| Home airport TAF | aviationweather.gov | `/api/data/taf?ids=KPTK&format=json` | every run |
| Hourly forecast at the airport and at each candidate lake | Open-Meteo | `/v1/forecast?latitude=a,b,c&longitude=x,y,z&hourly=wind_speed_10m,wind_gusts_10m,wind_direction_10m,temperature_2m,precipitation,precipitation_probability,weather_code,cloud_cover,visibility,cape,surface_pressure&wind_speed_unit=kn&forecast_days=2&timezone=America/Detroit` (multi-point in one request; batch 50 lakes per call) | every run |
| NWS alerts (lake wind advisory, small craft advisory, wind advisory, convective) | api.weather.gov | `/alerts/active?point=lat,lon` | every run |
| NWS hourly gridpoint forecast (sky cover, wind, as a second opinion for ceiling) | api.weather.gov | `/points/{lat},{lon}` then the `forecastHourly` URL | every run |
| Great Lakes buoy observations (only for lakes flagged shoreline) | NDBC | `latest_obs.txt` filtered by bbox | every run |
| Sunrise, sunset, civil twilight | computed | NOAA solar algorithm, local code, no network | every run |
| Lake geometry: verdict, chord, extent by bearing, public access, distance from home | pipeline output | `index.json`, `lake_extents.json` (new, section 5) | at pipeline build |

Open-Meteo's marine API covers oceans and the Great Lakes only; inland lake waves are computed, not fetched.

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
3. **Fetch** is the same extent (waves build along the wind over the same line the aircraft uses). Being exact is
   not needed: use the extent bin as the fetch length `F` in meters.
4. **Significant wave height** from the SPM 1984 deep-water fetch-limited relation (USACE Shore Protection Manual,
   1984, eq. 3-33/3-34):

   ```
   U10 = sustained wind in m/s (use gusts for the marginal check)
   UA  = 0.71 * U10^1.23                         # wind stress factor
   Hs  = 1.6e-3 * UA^2 / g * sqrt(g * F / UA^2)  # meters, simplifies to 5.1e-4 * UA * sqrt(F)
   Tp  = 0.2857 * UA / g * (g * F / UA^2)^(1/3)  # seconds
   ```

   Cap `Hs` at the fully developed value `Hs_fd = 2.482e-2 * UA^2 / g`. Deep water overestimates on shallow
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
8. Lake score = worst of the airport block score (you have to get there) and the lake's own wave, run, and
   crosswind checks. Rank by score, then by predicted `Hs` ascending, then by distance.

Output the top `n_lakes` (default 8) with, per lake: name, distance and bearing from home, verdict, `Hs` in inches,
run available in the wind, wind at the lake, and the limiting factor.

### 3.4 Text rendering

Template strings only, e.g.:

> **Favorable 10:00–16:00 today.** KPTK wind 250/9 G14, ceiling 4,500, vis 10. Density altitude 2,100 ft. Lake wind
> advisory on Lake St. Clair. Best water: Lake Angelus is restricted; Cass Lake 6 in chop with 4,100 ft run into
> the wind; Orchard Lake 5 in; Big Lake 3 in. Tomorrow: marginal, gusts to 22 after noon.

Numbers are formatted by the same helpers the sheet uses (`web/src/ui/format.ts`), or their Python equivalents on
the server, with a shared fixture so both round the same way.

## 4. Architecture

- **`api/` service (new, FastAPI, Python 3.12, uv).** Already referenced by `docker-compose.yml` and the Caddyfile.
  Holds the wind proxy from design section 7.9 and the briefing generator. On macOS it can run under `launchd`
  instead of Docker; either way it writes into the same directory Caddy serves.
- **Scheduler.** An in-process loop (`asyncio` task) runs the briefing every `refresh_hours` (default 3) and
  immediately when settings change. A cron/launchd trigger hitting `POST /api/briefing/refresh` is an acceptable
  alternative; pick one and document it.
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
  16 integers (ft). `longest_chord_ft` and `chord_bearing_deg` stay as they are.
- `build` writes `data/out/lake_extents.json`: `{ "<lake_id>": [ft × 16] }` for lakes with `chord_ft` ≥ 1000.
  Separate file so `index.json` stays under its budget. Listed in `pack.json`.
- Lake depth is not available today. If a depth source appears (Michigan DNR lake maps), the shallow-water form
  of the SPM equations can replace the deep-water one per lake.

## 6. Schemas (add to `docs/data-contract.md` when implementing)

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
5. Whether a morning push (ntfy/email) is wanted in the first version or the in-app card is enough.
