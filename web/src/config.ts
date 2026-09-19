import type { RestrictionType, Verdict, WaterbodyKind } from "./types";
import { isBigWater } from "./types";

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
  /** Wave field: a small index, then one HTTP Range request per water body. */
  wavePointsIndex: `${DATA_BASE}/wave_points.json`,
  wavePoints: `${DATA_BASE}/wave_points.bin`,
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
/**
 * Floor on how far selecting a water body may zoom out. Lake Michigan's bbox spans the
 * state, and fitting it would drop the camera below z6, where `lakes.pmtiles` has no tiles
 * at all: the lake would vanish and only the fallback marker would remain. Staying just
 * above the archive's minzoom keeps the shore on screen even if the far end is off it.
 */
export const MIN_SELECT_ZOOM = 6.2;
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
  reach_unresolved: "Rule covers part of the river",
  user_verified: "Verified",
  user_note: "Has note",
  saved: "Saved",
};

/**
 * On a Great Lake or a connecting water a flag never describes the whole thing: a federal
 * unit, a missing launch or an unresolved rule covers *part* of it. Same flag ids, different
 * wording, so a pilot does not read "no public access" as "you cannot get onto Lake Huron".
 *
 * The pipeline owns which flags it actually sets on these kinds; anything not overridden here
 * falls back to FLAG_LABEL, so adding a flag on that side needs no change here.
 */
export const BIG_WATER_FLAG_LABEL: Record<string, string> = {
  no_public_access: "Parts have no known public access",
  federal_overlay: "Part of this water is in a federal unit",
  reach_unresolved: "Rule covers part of this water",
  chord_below_minimum: "Parts are below the minimum run",
  airspace: "Part of this water is inside controlled airspace",
};

/**
 * Label for one flag, worded for the kind of water it sits on, and named where the data
 * names it: `index.json` carries `federal_unit` on the entries that are inside one.
 */
export function flagLabel(
  flag: string,
  kind: WaterbodyKind,
  federalUnit?: string | null,
): string {
  if (flag === "federal_overlay" && federalUnit) {
    return isBigWater(kind) ? `Part of this water is inside ${federalUnit}` : `Inside ${federalUnit}`;
  }
  if (isBigWater(kind)) return BIG_WATER_FLAG_LABEL[flag] ?? FLAG_LABEL[flag] ?? flag;
  return FLAG_LABEL[flag] ?? flag;
}

/**
 * Word for a waterbody's `kind`. Only shown when it is not the default "lake", so the map keeps
 * reading as a lake map. "Great Lakes water" rather than "Great Lake" because Lake St. Clair is
 * served as `great_lake` and is not one of the five; the generic phrase is true of both.
 * An unknown future kind falls back to "".
 */
export const KIND_LABEL: Record<string, string> = {
  river: "River",
  great_lake: "Great Lakes water",
  connecting_water: "Connecting water",
};

/** Shown next to the restriction list when a rule could not be narrowed to a reach. */
export function reachUnresolvedNotice(kind: WaterbodyKind): string {
  const what = isBigWater(kind) ? "this water" : "this river";
  return (
    `At least one rule below covers part of ${what}, not all of it. ` +
    "The DNR order names the reach; read the rule text before relying on it."
  );
}

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

export const WAVES = {
  /** Contract default when `/api/settings` is not reachable (`limits.min_run_ft`). */
  defaultMinRunFt: 2000,
  /** Last-resort wind when the briefing has nothing to say: contract "Client". */
  fallbackWind: { dir: 270, kt: 10 },
  /** Cockpit-usable range for the speed control. Above this nobody is landing. */
  maxWindKt: 35,
  /** Region labels appear on the map from here up; below it they collide. */
  labelMinZoom: 10.5,
} as const;

/**
 * The honest limits of the number, said once and in one line. Every part of it is true of
 * every water body, so it never has to be tuned per lake.
 */
export const WAVE_CAVEAT =
  "Computed from this wind and the fetch to the nearest land, not a forecast.";
/** Shown while the speed control is still on the briefing's gust, which is what it scores. */
export const WAVE_CAVEAT_GUST = "Speed is the forecast gust, as in the briefing.";
export const WAVE_CAVEAT_NO_DEPTH =
  "No depth data here, so shallow water reads rougher than it is.";
export const WAVE_CAVEAT_DEPTH = "Depth is included where the survey covers it.";
