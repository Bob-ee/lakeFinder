/**
 * Shapes from docs/data-contract.md. This file is the client's copy of the contract;
 * if the contract changes, change it here in the same commit.
 */

export type Verdict = "restricted" | "conditional" | "clear" | "unknown";

export type RestrictionType =
  | "no_vessels"
  | "no_motorboats"
  | "slow_no_wake"
  | "no_high_speed"
  | "high_speed_hours"
  | "speed_limit"
  | "no_towing"
  | "no_pwc"
  | "no_wake_zone_marked"
  | "shore_buffer"
  | "not_applicable"
  | "mac_ordinance"
  | "mac_conditional"
  | "federal_no_landing"
  | "other";

export type Flag =
  | "no_public_access"
  | "chord_below_minimum"
  | "federal_overlay"
  | "needs_review"
  | "mac_pending"
  | "user_verified"
  | "user_note"
  | "saved";

export interface TimeWindow {
  text: string;
  start: string;
  end: string;
  /** null means every day; otherwise text like "Sundays and holidays". Hours only. */
  days?: string | null;
}

export interface PlssEntry {
  township: string;
  range: string;
  sections: number[];
}

export interface Restriction {
  restriction_id: string;
  rule_id: string | null;
  county: string;
  township: string | null;
  lake_name_raw: string;
  lake_name_norm: string;
  plss: PlssEntry[];
  restriction_type: RestrictionType;
  scope: "lakewide" | "zone";
  scope_description: string | null;
  hours: TimeWindow | null;
  season: TimeWindow | null;
  speed_mph: number | null;
  /** `rescinded` rows are dropped at build; the client filters them anyway. */
  status: "active" | "rescinded";
  /** "(a)", "(b)" ... when one rule number decomposes into several records. */
  clause: string | null;
  /** Only enforceable when marked with signs or buoys. */
  signage_required: boolean;
  /** Cross-county continuation references, e.g. ["R 281.747.1"]. */
  related_rule_ids: string[];
  raw_text: string;
  source_url: string;
  fetched_at: string;
  parser: "regex" | "llm" | "manual";
  needs_review: boolean;
  lake_ids: number[];
}

export type RestrictionIndex = Record<string, Restriction>;

/** One entry of index.json. `bbox` is [west, south, east, north]. */
export interface Lake {
  id: number;
  name: string | null;
  name_norm: string;
  county: string;
  township: string | null;
  lat: number;
  lon: number;
  bbox: [number, number, number, number];
  area_acres: number;
  chord_ft: number;
  /** 0-179; a chord has two directions and the client shows both. */
  chord_bearing_deg: number;
  verdict: Verdict;
  flags: Flag[];
  restriction_ids: string[];
  access: string | null;
}

export interface PackFileEntry {
  name: string;
  bytes: number;
  sha256: string;
}

export interface Pack {
  version: string;
  built_at: string;
  rules_version: string;
  files: PackFileEntry[];
  counts: { lakes: number; restrictions: number; needs_review: number };
  mac_record_loaded: boolean;
}

export interface Aircraft {
  name: string;
  min_chord_ft: number;
  min_takeoff_mph: number;
}

export interface RuleDef {
  id: string;
  when: Record<string, unknown>;
  verdict: Verdict;
  note: string;
}

export interface RulesFile {
  version: string;
  aircraft: Aircraft;
  aggregate: string;
  rules: RuleDef[];
}

/** One element of the rules engine's `reasons`. */
export interface Reason {
  restriction_id: string;
  rule_id: string | null;
  matched_rule: string;
  verdict: Verdict;
  note: string;
}

export interface Evaluation {
  id: number;
  verdict: Verdict;
  reasons: Reason[];
  flags: string[];
  /** Client-only: false when the shared engine was unavailable and the prebaked verdict was used. */
  fromEngine: boolean;
}
