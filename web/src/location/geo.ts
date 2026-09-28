/** Small spherical helpers for the position marker and the Nearest list. */

const EARTH_RADIUS_NM = 3440.065;
const DEG = Math.PI / 180;
export const METERS_PER_NM = 1852;
export const MS_TO_KT = 3600 / METERS_PER_NM;

/** Great-circle distance in nautical miles. */
export function distanceNm(lat1: number, lon1: number, lat2: number, lon2: number): number {
  const p1 = lat1 * DEG;
  const p2 = lat2 * DEG;
  const dp = p2 - p1;
  const dl = (lon2 - lon1) * DEG;
  const a = Math.sin(dp / 2) ** 2 + Math.cos(p1) * Math.cos(p2) * Math.sin(dl / 2) ** 2;
  return 2 * EARTH_RADIUS_NM * Math.asin(Math.min(1, Math.sqrt(a)));
}

/** Initial true bearing from point 1 to point 2, 0-359. */
export function bearingDeg(lat1: number, lon1: number, lat2: number, lon2: number): number {
  const p1 = lat1 * DEG;
  const p2 = lat2 * DEG;
  const dl = (lon2 - lon1) * DEG;
  const y = Math.sin(dl) * Math.cos(p2);
  const x = Math.cos(p1) * Math.sin(p2) - Math.sin(p1) * Math.cos(p2) * Math.cos(dl);
  return norm360(Math.atan2(y, x) / DEG);
}

/** Smallest absolute difference between two bearings, 0-180. */
export function angleDiff(a: number, b: number): number {
  const d = Math.abs(norm360(a) - norm360(b)) % 360;
  return d > 180 ? 360 - d : d;
}

export function norm360(deg: number): number {
  return ((deg % 360) + 360) % 360;
}

/** Point `nm` along a true course from a start (flat-earth is plenty for a 1 s step). */
export function advance(lat: number, lon: number, courseDeg: number, nm: number): [number, number] {
  const dLat = (nm / 60) * Math.cos(courseDeg * DEG);
  const dLon = ((nm / 60) * Math.sin(courseDeg * DEG)) / Math.cos(lat * DEG);
  return [lat + dLat, lon + dLon];
}

/** Web-mercator ground resolution for MapLibre's 512 px tiles. */
export function metersPerPixel(lat: number, zoom: number): number {
  return (40075016.686 * Math.cos(lat * DEG)) / (512 * 2 ** zoom);
}

/** "047°" */
export function formatBearing(deg: number): string {
  return `${String(Math.round(norm360(deg)) % 360).padStart(3, "0")}°`;
}

/** "3.4 nm", one decimal as the contract asks. */
export function formatNm(nm: number): string {
  return `${nm.toFixed(1)} nm`;
}
