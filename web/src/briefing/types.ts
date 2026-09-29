/**
 * Shapes from docs/data-contract.md, section "Briefing". This file is the client's copy of
 * that part of the contract; if the contract changes, change it here in the same commit.
 *
 * Every number in `briefing.json` is already rounded for display by the api service
 * (kt, °F, inches as integers; `ceiling_ft` and `da_ft` to the nearest 100). The client
 * prints them as they arrive and never rounds again.
 */

import type { Verdict, WaterbodyKind } from "../types";

/** The briefing never says "go", "safe" or "legal". Rank order: 0, 1, 2. */
export type Score = "favorable" | "marginal" | "unfavorable";

/** `limiting` factor ids from the contract. Lakes add the last four. */
export type LimitingFactor =
  | "wind"
  | "gusts"
  | "xwind_runway"
  | "ceiling"
  | "visibility"
  | "fog"
  | "precip"
  | "convection"
  | "density_altitude"
  | "temperature"
  | "alert"
  | "daylight"
  | "waves"
  | "run"
  | "xwind_water"
  | "ice";

export type RunKind = "scheduled" | "outlook" | "manual" | "startup";
export type Confidence = "high" | "medium" | "low";
export type Trend = "improving" | "steady" | "worsening";

/** `gust` is typed nullable so a calm hour with no gust reported still renders. */
export interface Wind {
  dir: number;
  kt: number;
  gust: number | null;
}

/** `[start, end]` local HH:MM, or null when no window of min_window_hours exists. */
export type Window = [string, string];

export interface BriefingAirport {
  id: string;
  name: string;
  lat: number;
  lon: number;
}

export interface BriefingBlock {
  start: string;
  end: string;
  score: Score;
  limiting: LimitingFactor | null;
  wind: Wind;
  xwind_kt: number;
  runway: string;
  /** With `ceiling_known` true, null means no ceiling; false means the input had none. */
  ceiling_ft: number | null;
  /** Absent on a briefing written before this field existed; treat that as `true`. */
  ceiling_known: boolean;
  vis_sm: number;
  da_ft: number;
  temp_f: number;
  precip_prob: number;
}

export interface BriefingDay {
  date: string;
  score: Score;
  best_window: Window | null;
  blocks: BriefingBlock[];
}

export interface OutlookHour {
  time: string;
  score: Score;
  limiting: LimitingFactor | null;
  wind: Wind;
  xwind_kt: number;
  runway: string;
  ceiling_ft: number | null;
  /** Absent on a briefing written before this field existed; treat that as `true`. */
  ceiling_known: boolean;
  vis_sm: number;
  temp_f: number;
  dewpoint_f: number;
  fog_risk: boolean;
  precip_prob: number;
  da_ft: number;
}

export interface OutlookRun {
  /** Scheduled local time of the run, HH:MM. */
  at: string;
  generated_at: string;
  score: Score;
  best_window: Window | null;
  limiting: LimitingFactor | null;
  max_gust_kt: number;
}

export interface Sun {
  civil_dawn: string;
  sunrise: string;
  sunset: string;
  civil_dusk: string;
}

export interface Outlook {
  /** Tomorrow once local time is past morning_end_local, else today. */
  target_date: string;
  window: { start: string; end: string };
  sun: Sun;
  score: Score;
  limiting: LimitingFactor | null;
  /** What ends a favorable window, or null. */
  watch: string | null;
  best_window: Window | null;
  hours: OutlookHour[];
  lakes: BriefingLake[];
  /** Same shape as the top-level `home_water` but with no `observed`. Absent on older runs. */
  home_water?: BriefingLake | null;
  confidence: Confidence;
  confidence_reasons: string[];
  /** null on the first run for this target date. */
  trend: Trend | null;
  runs: OutlookRun[];
  summary: string;
}

export interface BriefingAlert {
  event: string;
  area: string;
  /** UTC ISO 8601. */
  ends: string;
}

/**
 * One region of a water body's wave field, as the api aggregates it for the briefing's
 * wind. `hs_in` is null when no point in the region has a long enough run into that wind.
 * `score` is only present on `home_water`, where every region is listed.
 */
export interface BriefingRegion {
  label: string;
  hs_in: number | null;
  run_ft: number | null;
  lat: number;
  lon: number;
  /**
   * That region's own forecast (its 0.1 degree cell), so the two ends of a big lake can
   * differ. Absent on a briefing written before per-region wind existed.
   */
  wind?: Wind | null;
  score?: Score;
}

/**
 * A nearby observation shown beside the computed numbers, never instead of them.
 *
 * NDBC buoys and shore stations have no `name`, so the station id is the label; `kind` is
 * an open string ("buoy", "metar", ...) and is rendered as it arrives rather than switched
 * on. Any station may be missing its wind or its wave height.
 */
export interface BriefingObservation {
  station: string;
  name: string | null;
  kind: string;
  /** UTC ISO 8601. */
  at: string;
  wind?: Wind | null;
  wave_ft?: number | null;
  distance_nm: number;
}

/**
 * `score` and `limiting` here are WATER-only: waves, usable run, crosswind on the water and
 * ice. The airport weather score lives in the header, the day blocks and the outlook hours.
 *
 * Everything from `kind` down arrived with the wave field. A briefing written before it, or
 * a water body with no wave points, simply omits them, so every one is optional and the
 * card falls back to the lake-level numbers.
 */
export interface BriefingLake {
  id: number;
  name: string;
  kind?: WaterbodyKind;
  score: Score;
  limiting: LimitingFactor | null;
  /** The best region's numbers when `regions` is non-empty, else the lake-level figure. */
  hs_in: number;
  run_ft: number;
  /** Best region label; null or absent without a wave field. */
  region?: string | null;
  /** The roughest region's open-water figure; null or absent without a wave field. */
  hs_open_in?: number | null;
  /** Calm to rough, at most 4 on a ranked row and every region on `home_water`. */
  regions?: BriefingRegion[];
  /** The best region's wind with a wave field (calmest region's when none is usable), else the centroid's. */
  wind: Wind;
  distance_nm: number;
  bearing_deg: number;
  verdict: Verdict;
  frozen: boolean;
  /** `home_water` only: buoys and shore stations near the water body. */
  observed?: BriefingObservation[];
  /** `home_water` only: Open-Meteo marine at the centroid; null when it has none. */
  marine_hs_in?: number | null;
}

export interface BriefingSources {
  metar: string | null;
  taf: string | null;
  open_meteo: string | null;
  nws: string | null;
}

export interface BriefingLinks {
  metar?: string | null;
  taf?: string | null;
  forecast?: string | null;
}

export interface Briefing {
  schema: number;
  generated_at: string;
  run_kind: RunKind;
  timezone: string;
  home_airport: BriefingAirport;
  summary: string;
  days: BriefingDay[];
  /** null only when the forecast input failed entirely. */
  outlook: Outlook | null;
  alerts: BriefingAlert[];
  lakes: BriefingLake[];
  /** null when `settings.home_water` is null; absent on a briefing written before it existed. */
  home_water?: BriefingLake | null;
  sources: BriefingSources;
  links: BriefingLinks;
  errors: string[];
  /** Absent on a briefing written before the timeline existed. */
  timeline?: Timeline | null;
}

// -- settings.json (through /api/settings) ---------------------------------

export interface Runway {
  id: string;
  /** Degrees true of the first-named end. */
  heading: number;
}

export interface HomeAirport {
  id: string;
  name: string;
  lat: number;
  lon: number;
  elev_ft: number;
  runways: Runway[];
}

export interface Limits {
  wind_ok: number;
  wind_max: number;
  gust_spread_ok: number;
  gust_spread_max: number;
  xwind_runway_ok: number;
  xwind_runway_max: number;
  xwind_water_max: number;
  ceiling_ok: number;
  ceiling_min: number;
  vis_ok: number;
  vis_min: number;
  da_ok: number;
  da_max: number;
  temp_water_min_f: number;
  fog_spread_f: number;
  wave_ok_in: number;
  wave_max_in: number;
  min_run_ft: number;
  /** MM-DD. */
  ice_season_start: string;
  ice_season_end: string;
}

export type LimitKey = keyof Limits;

export interface OutlookSettings {
  /** Evening runs recorded in outlook.runs; always also run times. */
  times_local: string[];
  /** "sunrise" | "civil_twilight" | "HH:MM". */
  morning_start: string;
  morning_end_local: string;
  min_window_hours: number;
}

/** `settings.home_water`: one water body that is always briefed, whatever `radius_nm` says. */
export interface HomeWater {
  id: number;
  name: string;
}

export interface Settings {
  home_airport: HomeAirport;
  timezone: string;
  /** null until a water body's sheet sets it. Absent on a settings file written before it. */
  home_water?: HomeWater | null;
  radius_nm: number;
  n_lakes: number;
  public_access_only: boolean;
  schedule: { run_times_local: string[] };
  outlook: OutlookSettings;
  /** When set, each outlook run POSTs outlook.summary to this ntfy topic. */
  notify: { ntfy_url: string | null };
  limits: Limits;
}

export interface Health {
  ok: boolean;
  briefing_generated_at: string | null;
  next_run_local: string | null;
}

/* ---- Forecast timeline (contract: "Forecast timeline and waves over time") ---- */

export interface TimelineHour {
  /** Local ISO 8601 with offset. */
  t: string;
  past: boolean;
  daylight: boolean;
  /** Airport weather only. */
  score: Score;
  limiting: LimitingFactor | null;
  wind: Wind;
  /** The Open-Meteo model that supplied this hour's wind. */
  model: string;
  ceiling_ft: number | null;
  ceiling_known: boolean;
  vis_sm: number | null;
  fog_risk: boolean;
  precip_prob: number | null;
  temp_f: number | null;
}

export interface TimelineWindow {
  start: string;
  /** Exclusive. */
  end: string;
  score: Score;
  limiting_after: LimitingFactor | null;
}

export interface TimelineObservation {
  t: string;
  station: string;
  wave_in: number | null;
  wind: Wind | null;
}

/** Every per-hour array has the same length and index as `timeline.hours`. */
export interface TimelineHomeWater {
  id: number;
  name: string;
  labels: string[];
  /** [hour][label]; null = no usable run into that hour's wind. */
  hs_in: (number | null)[][];
  wind: (Wind | null)[][];
  best: ({ label: string; hs_in: number } | null)[];
  open_in: (number | null)[];
  /** Water only, best region. */
  score: Score[];
  limiting: (LimitingFactor | null)[];
  observed: TimelineObservation[];
  marine_in: (number | null)[];
}

export interface Timeline {
  hours: TimelineHour[];
  windows: TimelineWindow[];
  home_water: TimelineHomeWater | null;
}

/** `GET /api/forecast/wind?lake=<id>`. */
export interface ForecastWind {
  lake_id: number;
  cell_deg: number;
  times: string[];
  past: number;
  models: string[];
  cells: { lat: number; lon: number; dir: (number | null)[]; kt: (number | null)[]; gust: (number | null)[] }[];
  fetched_at: string;
  errors: string[];
}
