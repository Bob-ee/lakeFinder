/**
 * Wall-clock pieces of an axis hour. The api writes every hour as local ISO 8601 with its
 * offset ("2026-09-29T06:00:00-04:00"), so the wall time is read straight out of the string:
 * the bar says the same hour as the briefing whatever zone the phone is set to.
 */

const DAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];

export interface Wall {
  /** "Tue" */
  day: string;
  /** "2026-09-29", for telling days apart. */
  date: string;
  hour: number;
  /** "14:00" */
  hhmm: string;
}

export function wall(iso: string): Wall {
  const date = iso.slice(0, 10);
  const y = Number(iso.slice(0, 4));
  const m = Number(iso.slice(5, 7));
  const d = Number(iso.slice(8, 10));
  const hour = Number(iso.slice(11, 13));
  const dow = new Date(Date.UTC(y, m - 1, d)).getUTCDay();
  return { day: DAYS[dow] ?? "", date, hour, hhmm: iso.slice(11, 16) };
}

/** "Tue 14:00" */
export function hourLabel(iso: string): string {
  const w = wall(iso);
  return `${w.day} ${w.hhmm}`;
}

/** Local ISO 8601 with offset for a Date, in the device's zone (the dev fake only). */
export function localIso(date: Date): string {
  const pad = (n: number) => String(Math.trunc(Math.abs(n))).padStart(2, "0");
  const off = -date.getTimezoneOffset();
  const sign = off >= 0 ? "+" : "-";
  return (
    `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}` +
    `T${pad(date.getHours())}:${pad(date.getMinutes())}:00${sign}${pad(off / 60)}:${pad(off % 60)}`
  );
}

/** Wind as the pilot writes it: `250/12 G18`, the gust only when it is above the wind. */
export function windText(w: { dir: number; kt: number; gust?: number | null }): string {
  const dir = String(Math.round(((w.dir % 360) + 360) % 360)).padStart(3, "0");
  const gust = w.gust != null && w.gust > w.kt ? ` G${Math.round(w.gust)}` : "";
  return `${dir}/${Math.round(w.kt)}${gust}`;
}
