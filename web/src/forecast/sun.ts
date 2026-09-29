/**
 * Civil twilight for shading night on the time bar when no briefing timeline is loaded
 * (the briefing's `daylight` is for the home airport; this is for the water on screen).
 * Low-precision solar position (the Astronomical Almanac's approximation, about 0.01°),
 * which is far finer than an hour-wide column needs.
 */

const RAD = Math.PI / 180;
/** 2000-01-01T12:00:00Z */
const J2000_MS = 946_728_000_000;

/** Sun altitude in degrees above the horizon at `ms` for a position. */
export function sunAltitude(ms: number, lat: number, lon: number): number {
  const d = (ms - J2000_MS) / 86_400_000;
  const g = (357.529 + 0.98560028 * d) * RAD;
  const q = 280.459 + 0.98564736 * d;
  const L = (q + 1.915 * Math.sin(g) + 0.02 * Math.sin(2 * g)) * RAD;
  const e = (23.439 - 0.00000036 * d) * RAD;
  const ra = Math.atan2(Math.cos(e) * Math.sin(L), Math.cos(L)) / RAD;
  const dec = Math.asin(Math.sin(e) * Math.sin(L));
  const gmst = 280.46061837 + 360.98564736629 * d;
  const ha = (gmst + lon - ra) * RAD;
  const phi = lat * RAD;
  return Math.asin(Math.sin(phi) * Math.sin(dec) + Math.cos(phi) * Math.cos(dec) * Math.cos(ha)) / RAD;
}

/** Between civil dawn and civil dusk: the sun above −6°. */
export function isDaylight(ms: number, lat: number, lon: number): boolean {
  return sunAltitude(ms, lat, lon) > -6;
}
