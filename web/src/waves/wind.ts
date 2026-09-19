import { WAVES } from "../config";
import type { Briefing, BriefingLake, Wind } from "../briefing/types";
import { hhmmMinutes, zoneNow } from "../briefing/labels";

/** Wind as the wave field uses it: the direction it blows FROM, and a speed in knots. */
export interface WindSetting {
  dir: number;
  kt: number;
}

/**
 * The briefing's wind for a water body. `isGust` is true when `kt` is the reported gust
 * rather than the sustained wind: the api scores waves at the gust, so the sheet has to
 * open at the gust too or the same lake would read calmer here than in the briefing row.
 */
export interface ForecastWind extends WindSetting {
  isGust: boolean;
}

export const COMPASS_16 = [
  "N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
  "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW",
];

/** The 16 bin centres as whole degrees: round(i * 22.5). */
export const BIN_DEGREES = COMPASS_16.map((_, i) => Math.round(i * 22.5));

/** Compass name for a bearing, to the nearest of the 16 points. */
export function compassName(deg: number): string {
  return COMPASS_16[binOf(deg)]!;
}

/** Index of the 16-point bin a bearing falls in; matches `windBin` in rules/waves. */
export function binOf(deg: number): number {
  const raw = Math.floor(deg / 22.5 + 0.5);
  return ((raw % 16) + 16) % 16;
}

/** Snaps a bearing to the nearest of the 16 bin centres. */
export function snapToBin(deg: number): number {
  return BIN_DEGREES[binOf(deg)]!;
}

export function sameWind(a: WindSetting | null, b: WindSetting | null): boolean {
  if (!a || !b) return a === b;
  return a.dir === b.dir && a.kt === b.kt;
}

export function formatWindSetting(wind: WindSetting): string {
  return `${String(wind.dir).padStart(3, "0")}° ${compassName(wind.dir)} · ${wind.kt} kt`;
}

// -- the default wind for a water body -------------------------------------

/**
 * The wind the briefing is actually forecasting for this water body: its own row when it
 * has one (`home_water` counts, and the outlook's copies are read when the top-level ones
 * do not name it), else the airport wind for the block covering now. Null when the briefing
 * has nothing to say, which is when the sheet offers no "back to the forecast" button.
 */
export function forecastWindFor(briefing: Briefing | null, lakeId: number): ForecastWind | null {
  return lakeWind(briefing, lakeId) ?? airportWindNow(briefing);
}

/** Contract "Client": the forecast wind for this water body, else 270/10. */
export function defaultWindFor(briefing: Briefing | null, lakeId: number): WindSetting {
  const forecast = forecastWindFor(briefing, lakeId);
  return forecast ? { dir: forecast.dir, kt: forecast.kt } : { ...WAVES.fallbackWind };
}

/** The gust when there is one, because that is the speed the briefing scores waves at. */
function toSetting(wind: Wind | null | undefined): ForecastWind | null {
  if (!wind || !Number.isFinite(wind.dir) || !Number.isFinite(wind.kt)) return null;
  const gust = wind.gust != null && Number.isFinite(wind.gust) && wind.gust > wind.kt ? wind.gust : null;
  return { dir: normalizeDeg(wind.dir), kt: clampKt(gust ?? wind.kt), isGust: gust != null };
}

/** Every place `briefing.json` may carry a wind for one water body, nearest first. */
function lakeWind(briefing: Briefing | null, lakeId: number): ForecastWind | null {
  if (!briefing) return null;
  const rows: Array<BriefingLake | null | undefined> = [
    briefing.home_water,
    ...(briefing.lakes ?? []),
    briefing.outlook?.home_water,
    ...(briefing.outlook?.lakes ?? []),
  ];
  for (const row of rows) {
    if (row && row.id === lakeId) {
      const wind = toSetting(row.wind);
      if (wind) return wind;
    }
  }
  return null;
}

/**
 * The airport wind for the block covering now, in the briefing's own zone. Falls back to
 * the first block of the first day, then to the first outlook hour, so an older or thinner
 * briefing still seeds the control with something real.
 */
export function airportWindNow(briefing: Briefing | null): ForecastWind | null {
  if (!briefing) return null;
  const day = briefing.days?.[0];
  if (day && day.blocks.length > 0) {
    const now = zoneNow(briefing.timezone).minutes;
    for (const block of day.blocks) {
      const start = hhmmMinutes(block.start);
      const end = hhmmMinutes(block.end);
      if (start <= now && now < end) {
        const wind = toSetting(block.wind);
        if (wind) return wind;
      }
    }
    const first = toSetting(day.blocks[0]!.wind);
    if (first) return first;
  }
  const hour = briefing.outlook?.hours?.[0];
  return hour ? toSetting(hour.wind) : null;
}

export function normalizeDeg(deg: number): number {
  const d = Math.round(deg) % 360;
  return d < 0 ? d + 360 : d;
}

export function clampKt(kt: number): number {
  return Math.max(0, Math.min(WAVES.maxWindKt, Math.round(kt)));
}
