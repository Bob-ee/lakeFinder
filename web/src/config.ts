import type { RestrictionType, Verdict } from "./types";

/** Everything is served under /data/ by the dev server and by Caddy in production. */
export const DATA_BASE = "/data";

export const DATA_FILES = {
  pack: `${DATA_BASE}/pack.json`,
  index: `${DATA_BASE}/index.json`,
  restrictions: `${DATA_BASE}/restrictions.json`,
  rules: `${DATA_BASE}/rules.json`,
  basemap: `${DATA_BASE}/basemap.pmtiles`,
  lakes: `${DATA_BASE}/lakes.pmtiles`,
  usableWater: `${DATA_BASE}/usable_water.pmtiles`,
  overlays: `${DATA_BASE}/overlays.pmtiles`,
  /** Written by the api service, read as a plain static file. Not part of pack.json. */
  briefing: `${DATA_BASE}/briefing.json`,
} as const;

/** The api service (settings, refresh, health). Caddy and the Vite dev server proxy this. */
export const API_BASE = "/api";

/** Protomaps hosts the glyph and sprite assets that @protomaps/basemaps layers reference. */
export const BASEMAP_ASSETS = {
  glyphs: "https://protomaps.github.io/basemaps-assets/fonts/{fontstack}/{range}.pbf",
  sprite: "https://protomaps.github.io/basemaps-assets/sprites/v4/",
};

/** Michigan, roughly. Used as the opening view and as the map's max bounds guard rail. */
export const HOME_VIEW = { center: [-84.5, 43.6] as [number, number], zoom: 6.4 };
/** Where the dev fixtures live, so a fixture run opens on data instead of empty water. */
export const FIXTURE_VIEW = { center: [-83.45, 42.67] as [number, number], zoom: 10.2 };

export const MAX_SELECT_ZOOM = 15;
export const USABLE_WATER_MIN_ZOOM = 12;

export const VERDICT_ORDER: Record<Verdict, number> = {
  restricted: 4,
  conditional: 3,
  unknown: 2,
  clear: 1,
};

/** One-line phrase for the peek row. Section 7.4: nothing in peek requires reading a sentence. */
export const VERDICT_PHRASE: Record<Verdict, string> = {
  restricted: "Restricted",
  conditional: "Conditional",
  clear: "No known restriction",
  unknown: "Unknown",
};

export const RESTRICTION_LABEL: Record<RestrictionType, string> = {
  no_vessels: "All vessels prohibited",
  no_motorboats: "Motorboats prohibited",
  slow_no_wake: "Slow / no wake",
  no_high_speed: "High speed / planing prohibited",
  high_speed_hours: "High speed by hours only",
  speed_limit: "Speed limit",
  no_towing: "Towing / water skiing prohibited",
  no_pwc: "PWC prohibited",
  no_wake_zone_marked: "Marked no-wake zone",
  shore_buffer: "Shore buffer (statewide 100 ft)",
  not_applicable: "Does not affect landing",
  mac_ordinance: "MAC-approved seaplane ordinance",
  mac_conditional: "MAC record, with conditions",
  federal_no_landing: "Federal unit, no landing",
  other: "Unclassified",
};

export const FLAG_LABEL: Record<string, string> = {
  no_public_access: "No known public access",
  chord_below_minimum: "Chord below minimum",
  federal_overlay: "Federal overlay",
  needs_review: "Needs review",
  mac_pending: "MAC record pending",
  user_verified: "Verified",
  user_note: "Has note",
  saved: "Saved",
};

/** Disclaimer from design.md section 13. `{version}` is substituted from pack.json. */
export const DISCLAIMER =
  "This tool summarizes published Michigan DNR watercraft controls and other public data. " +
  "It is not legal advice and does not confirm that a landing is legal. Verify restrictions, " +
  "property rights, and conditions before operating. Data build: {version}.";

export const MAC_NOT_LOADED_NOTICE = "MAC-approved seaplane ordinances not yet loaded";

export const STORAGE_KEYS = {
  theme: "seaplane.theme",
  disclaimerAck: "seaplane.disclaimerAck",
  layers: "seaplane.layers",
} as const;

/** v2 added the `briefing` store; state/db.ts creates every store behind a contains check. */
export const IDB = {
  name: "seaplane",
  version: 2,
  stores: { saved: "saved", recent: "recent", wind: "wind", briefing: "briefing" },
} as const;

export const BRIEFING = {
  /** Key of the single row in the `briefing` store. */
  cacheKey: "latest",
  /** Refetch this often while the tab is visible. */
  pollMs: 10 * 60 * 1000,
  /** Past this the age badge is styled "stale". */
  staleMinutes: 6 * 60,
  /** `?briefing=1` opens the card; also useful as a home-screen shortcut. */
  urlParam: "briefing",
} as const;

/**
 * Same posture as the verdict disclaimer, for weather rather than law. It must not read as
 * an authorization: no "legal", no "safe", no "go" or "no-go".
 */
export const BRIEFING_DISCLAIMER =
  "Not a preflight weather briefing. Check the official METAR, TAF and forecast before operating.";

export const SEARCH = {
  debounceMs: 100,
  maxRows: 8,
  maxRecent: 8,
} as const;
