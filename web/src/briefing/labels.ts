/**
 * Words and time arithmetic for the briefing card.
 *
 * All local times in `briefing.json` are HH:MM 24-hour strings in `briefing.timezone`, so
 * "now" has to be read in that zone rather than the browser's. Everything here goes
 * through `Intl.DateTimeFormat` with an explicit `timeZone`, which is the only DST-safe
 * way to do it without a date library.
 */

import type { Confidence, LimitingFactor, Score, Trend, Window } from "./types";

/** Sentence-case word shown to the pilot. Never "go", "safe", "legal" or "no-go". */
export const SCORE_WORD: Record<Score, string> = {
  favorable: "Favorable",
  marginal: "Marginal",
  unfavorable: "Unfavorable",
};

export const SCORE_RANK: Record<Score, number> = {
  favorable: 0,
  marginal: 1,
  unfavorable: 2,
};

/** Human labels for the `limiting` factor ids listed in the contract. */
export const LIMITING_LABEL: Record<LimitingFactor, string> = {
  wind: "wind",
  gusts: "gusts",
  xwind_runway: "runway crosswind",
  ceiling: "ceiling",
  visibility: "visibility",
  fog: "fog",
  precip: "precipitation",
  convection: "convection",
  density_altitude: "density altitude",
  temperature: "temperature",
  alert: "weather alert",
  daylight: "daylight",
  waves: "wave height",
  run: "usable run",
  xwind_water: "crosswind on the water",
  ice: "ice",
};

export function limitingLabel(id: LimitingFactor | null | undefined): string | null {
  if (!id) return null;
  return LIMITING_LABEL[id] ?? String(id);
}

export const CONFIDENCE_WORD: Record<Confidence, string> = {
  high: "High",
  medium: "Medium",
  low: "Low",
};

export const TREND_WORD: Record<Trend, string> = {
  improving: "Improving",
  steady: "Steady",
  worsening: "Worsening",
};

/** Arrows rather than colour, so the trend still reads in a screenshot or at a glance. */
export const TREND_ARROW: Record<Trend, string> = {
  improving: "↗",
  steady: "→",
  worsening: "↘",
};

/** "08:00–12:00" (en dash), or "no window" when the server found none. */
export function formatWindow(window: Window | null | undefined): string {
  if (!window) return "no window";
  return `${window[0]}–${window[1]}`;
}

/** `240/6 G9`, or `240/6` when no gust was reported. */
export function formatWind(wind: { dir: number; kt: number; gust: number | null }): string {
  const dir = String(Math.round(wind.dir)).padStart(3, "0");
  const base = `${dir}/${wind.kt}`;
  return wind.gust == null ? base : `${base} G${wind.gust}`;
}

/**
 * `4,500 ft`, `no ceiling`, or `ceiling unknown`.
 *
 * `ceiling_ft: null` only means "no ceiling" when `ceiling_known` is true; when it is
 * false the input simply had no ceiling data, which is not the same thing and must not be
 * shown as clear sky. A briefing written before `ceiling_known` existed omits the field,
 * and `!== false` treats that as known, which is what those files meant.
 */
export function formatCeiling(ft: number | null, known?: boolean): string {
  if (known === false) return "ceiling unknown";
  return ft == null ? "no ceiling" : `${ft.toLocaleString("en-US")} ft`;
}

/** True when any entry in a strip is missing ceiling data, so the card can say so once. */
export function anyCeilingUnknown(entries: Array<{ ceiling_known?: boolean }>): boolean {
  return entries.some((e) => e.ceiling_known === false);
}

// -- time in the briefing's own zone ---------------------------------------

interface ZoneNow {
  /** ISO date, YYYY-MM-DD. */
  date: string;
  /** HH:MM, 24-hour. */
  time: string;
  /** Minutes since local midnight. */
  minutes: number;
}

/** Reads the wall clock in `timezone`. Falls back to the browser zone if it is rejected. */
export function zoneNow(timezone: string, at: Date = new Date()): ZoneNow {
  const parts = zoneParts(timezone, at);
  return {
    date: `${parts.year}-${parts.month}-${parts.day}`,
    time: `${parts.hour}:${parts.minute}`,
    minutes: Number(parts.hour) * 60 + Number(parts.minute),
  };
}

function zoneParts(
  timezone: string,
  at: Date,
): { year: string; month: string; day: string; hour: string; minute: string } {
  const opts: Intl.DateTimeFormatOptions = {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  };
  let fmt: Intl.DateTimeFormat;
  try {
    fmt = new Intl.DateTimeFormat("en-US", { ...opts, timeZone: timezone });
  } catch {
    // An unknown zone string must not take the card down; use the device zone.
    fmt = new Intl.DateTimeFormat("en-US", opts);
  }
  const out: Record<string, string> = {};
  for (const p of fmt.formatToParts(at)) {
    if (p.type !== "literal") out[p.type] = p.value;
  }
  return {
    year: out["year"] ?? "1970",
    month: out["month"] ?? "01",
    day: out["day"] ?? "01",
    hour: out["hour"] ?? "00",
    minute: out["minute"] ?? "00",
  };
}

/** Adds whole days to a YYYY-MM-DD string without touching the local zone. */
export function addDays(isoDate: string, days: number): string {
  const [y, m, d] = isoDate.split("-").map(Number);
  if (y == null || m == null || d == null) return isoDate;
  const t = Date.UTC(y, m - 1, d) + days * 86_400_000;
  return new Date(t).toISOString().slice(0, 10);
}

export type DayRelation = "today" | "tomorrow" | "other";

export function relateDay(isoDate: string, today: string): DayRelation {
  if (isoDate === today) return "today";
  if (isoDate === addDays(today, 1)) return "tomorrow";
  return "other";
}

/** "Tomorrow morning" / "This morning" / "Sunday morning". */
export function morningHeading(targetDate: string, today: string): string {
  const rel = relateDay(targetDate, today);
  if (rel === "today") return "This morning";
  if (rel === "tomorrow") return "Tomorrow morning";
  return `${weekdayName(targetDate)} morning`;
}

/** "Today" / "Tomorrow" / "Sunday 20 Sep". */
export function dayHeading(isoDate: string, today: string): string {
  const rel = relateDay(isoDate, today);
  if (rel === "today") return "Today";
  if (rel === "tomorrow") return "Tomorrow";
  return `${weekdayName(isoDate)} ${shortDate(isoDate)}`;
}

export function weekdayName(isoDate: string): string {
  const d = parseIsoDate(isoDate);
  if (!d) return isoDate;
  return d.toLocaleDateString("en-US", { weekday: "long", timeZone: "UTC" });
}

export function shortWeekday(isoDate: string): string {
  const d = parseIsoDate(isoDate);
  if (!d) return isoDate;
  return d.toLocaleDateString("en-US", { weekday: "short", timeZone: "UTC" });
}

export function shortDate(isoDate: string): string {
  const d = parseIsoDate(isoDate);
  if (!d) return isoDate;
  return d.toLocaleDateString("en-US", { month: "short", day: "numeric", timeZone: "UTC" });
}

function parseIsoDate(isoDate: string): Date | null {
  const [y, m, d] = isoDate.split("-").map(Number);
  if (y == null || m == null || d == null || !Number.isFinite(y)) return null;
  return new Date(Date.UTC(y, m - 1, d));
}

/** A UTC instant (alert `ends`, a run's `generated_at`) as HH:MM in the briefing zone. */
export function utcToZoneTime(iso: string, timezone: string): string {
  const at = new Date(iso);
  if (Number.isNaN(at.getTime())) return iso;
  return zoneNow(timezone, at).time;
}

/** Minutes since midnight for an HH:MM string; -1 when unparseable. */
export function hhmmMinutes(hhmm: string): number {
  const m = /^(\d{1,2}):(\d{2})$/.exec(hhmm.trim());
  if (!m) return -1;
  return Number(m[1]) * 60 + Number(m[2]);
}

export function isHhmm(value: string): boolean {
  const m = /^(\d{1,2}):(\d{2})$/.exec(value.trim());
  if (!m) return false;
  return Number(m[1]) <= 23 && Number(m[2]) <= 59;
}

/**
 * Contract ordering rule: the outlook section leads when `outlook.target_date` is tomorrow
 * and local time is 17:00 or later, or when it is today and the window has not ended.
 */
export function outlookLeads(
  target: { target_date: string; window: { start: string; end: string } } | null,
  timezone: string,
  at: Date = new Date(),
): boolean {
  if (!target) return false;
  const now = zoneNow(timezone, at);
  const rel = relateDay(target.target_date, now.date);
  if (rel === "tomorrow") return now.minutes >= 17 * 60;
  if (rel === "today") return now.minutes < hhmmMinutes(target.window.end);
  return false;
}
