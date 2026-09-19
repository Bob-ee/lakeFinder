/**
 * Shapes from docs/data-contract.md, section "Briefing". This file is the client's copy of
 * that part of the contract; if the contract changes, change it here in the same commit.
 *
 * Every number in `briefing.json` is already rounded for display by the api service
 * (kt, °F, inches as integers; `ceiling_ft` and `da_ft` to the nearest 100). The client
 * prints them as they arrive and never rounds again.
 */

import type { Verdict } from "../types";

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
 * `score` and `limiting` here are WATER-only: waves, usable run, crosswind on the water and
 * ice. The airport weather score lives in the header, the day blocks and the outlook hours.
 */
export interface BriefingLake {
  id: number;
  name: string;
  score: Score;
  limiting: LimitingFactor | null;
  hs_in: number;
  run_ft: number;
  wind: Wind;
  distance_nm: number;
  bearing_deg: number;
  verdict: Verdict;
  frozen: boolean;
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
  sources: BriefingSources;
  links: BriefingLinks;
  errors: string[];
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

export interface Settings {
  home_airport: HomeAirport;
  timezone: string;
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
