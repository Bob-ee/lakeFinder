import type { Station, WindReading } from "./types";

/** Arrow shape per source. The owner reads shape, not colour, so this is the only cue. */
export type WindShape = "metar" | "buoy" | "mesonet" | "calm";

export function shapeFor(station: Station): WindShape {
  if (station.dir_deg == null) return "calm";
  return sourceShape(station.source);
}

export function sourceShape(source: string): Exclude<WindShape, "calm"> {
  if (source === "metar") return "metar";
  if (source === "ndbc") return "buoy";
  return "mesonet";
}

export const SOURCE_WORD: Record<string, string> = {
  metar: "METAR",
  ndbc: "Buoy",
  synoptic: "Mesonet",
};

export function sourceWord(source: string): string {
  return SOURCE_WORD[source] ?? source;
}

export function pad3(deg: number): string {
  const n = ((Math.round(deg) % 360) + 360) % 360;
  return String(n === 0 ? 360 : n).padStart(3, "0");
}

function kt(v: number | null): number | null {
  return v == null || !Number.isFinite(v) ? null : Math.round(v);
}

/** "240/12 G18", "VRB/4", "calm". Null when the reading has no speed. */
export function windText(w: WindReading): string | null {
  const speed = kt(w.speed_kt);
  if (speed == null) return null;
  if (speed === 0) return "calm";
  const gust = kt(w.gust_kt);
  const g = gust != null && gust > speed ? ` G${gust}` : "";
  const dir = w.dir_deg == null ? "VRB" : pad3(w.dir_deg);
  return `${dir}/${speed}${g}`;
}

/** Map label: "12", "12G18", "0". */
export function speedLabel(w: WindReading): string {
  const speed = kt(w.speed_kt);
  if (speed == null) return "";
  const gust = kt(w.gust_kt);
  return gust != null && gust > speed ? `${speed}G${gust}` : String(speed);
}

const NM_M = 1852;
const R_M = 6_371_008.8;
const rad = (d: number) => (d * Math.PI) / 180;

export function distanceNm(lat1: number, lon1: number, lat2: number, lon2: number): number {
  const dLat = rad(lat2 - lat1);
  const dLon = rad(lon2 - lon1);
  const a =
    Math.sin(dLat / 2) ** 2 + Math.cos(rad(lat1)) * Math.cos(rad(lat2)) * Math.sin(dLon / 2) ** 2;
  return (2 * R_M * Math.asin(Math.min(1, Math.sqrt(a)))) / NM_M;
}

/** Initial true bearing from point 1 to point 2, 0-359. */
export function bearingDeg(lat1: number, lon1: number, lat2: number, lon2: number): number {
  const y = Math.sin(rad(lon2 - lon1)) * Math.cos(rad(lat2));
  const x =
    Math.cos(rad(lat1)) * Math.sin(rad(lat2)) -
    Math.sin(rad(lat1)) * Math.cos(rad(lat2)) * Math.cos(rad(lon2 - lon1));
  return ((Math.atan2(y, x) * 180) / Math.PI + 360) % 360;
}

export function formatNm(nm: number): string {
  return nm < 10 ? `${nm.toFixed(1)} nm` : `${Math.round(nm)} nm`;
}

/** "14 min", "2 h 5 min". */
export function formatAge(min: number): string {
  const m = Math.max(0, Math.round(min));
  if (m < 60) return `${m} min`;
  const h = Math.floor(m / 60);
  const r = m % 60;
  return r === 0 ? `${h} h` : `${h} h ${r} min`;
}

/** "just now", "14 min ago". */
export function formatAgo(min: number): string {
  return min < 1 ? "just now" : `${formatAge(min)} ago`;
}

export interface Components {
  /** Landing heading, true, along the chord: whichever end has the headwind. */
  heading: number;
  headwind: number;
  crosswind: number;
  /** Which side the crosswind comes from, looking along `heading`. */
  side: "left" | "right" | null;
  gustHeadwind: number | null;
  gustCrosswind: number | null;
}

/**
 * Headwind and crosswind for landing along a chord. A chord runs both ways (`bearing` is
 * 0-179), so the landing heading is whichever of the two puts the wind on the nose.
 */
export function chordComponents(bearing: number, w: WindReading): Components | null {
  if (w.dir_deg == null || w.speed_kt == null || !Number.isFinite(bearing)) return null;
  const a = ((bearing % 360) + 360) % 360;
  const b = (a + 180) % 360;
  const head = (h: number) => Math.cos(rad(w.dir_deg! - h));
  const heading = head(a) >= head(b) ? a : b;
  const d = rad(w.dir_deg - heading);
  const cross = Math.sin(d);
  const headwind = Math.round(w.speed_kt * Math.cos(d));
  const crosswind = Math.round(Math.abs(w.speed_kt * cross));
  const gust = w.gust_kt != null && w.gust_kt > w.speed_kt ? w.gust_kt : null;
  return {
    heading,
    headwind,
    crosswind,
    side: crosswind === 0 ? null : cross > 0 ? "right" : "left",
    gustHeadwind: gust == null ? null : Math.round(gust * Math.cos(d)),
    gustCrosswind: gust == null ? null : Math.round(Math.abs(gust * cross)),
  };
}

/**
 * The arrow shapes, pointing up (north, i.e. downwind for a north-going wind) in a
 * 40 x 40 box. The same paths draw the map icons (canvas Path2D) and the sheet's legend
 * glyphs (inline SVG), so what the pilot learns in the sheet is what the map shows.
 */
export const SHAPE_BOX = 40;
const HEAD = "M20 3 L29.5 17 L10.5 17 Z";
const SHAFT = "M20 16 L20 36";
/** Inside the buoy ring the arrow is shorter so the two never touch. */
const HEAD_IN = "M20 7.5 L27 18 L13 18 Z";
const SHAFT_IN = "M20 17 L20 32.5";
const RING = "M20 1.5 A18.5 18.5 0 1 1 19.99 1.5 Z";
const CALM = "M20 12 A8 8 0 1 1 19.99 12 Z";

export interface ShapeParts {
  /** Filled with ink (solid) or with the halo colour (open). */
  head: string | null;
  headFilled: boolean;
  shaft: string | null;
  shaftWidth: number;
  ring: string | null;
  ringWidth: number;
}

export const SHAPES: Record<WindShape, ShapeParts> = {
  metar: { head: HEAD, headFilled: true, shaft: SHAFT, shaftWidth: 4, ring: null, ringWidth: 0 },
  buoy: { head: HEAD_IN, headFilled: true, shaft: SHAFT_IN, shaftWidth: 3.5, ring: RING, ringWidth: 2.5 },
  mesonet: { head: HEAD, headFilled: false, shaft: SHAFT, shaftWidth: 2.5, ring: null, ringWidth: 0 },
  calm: { head: null, headFilled: false, shaft: null, shaftWidth: 0, ring: CALM, ringWidth: 3 },
};

/** Inline SVG glyph of a shape, `currentColor` ink, for the sheet and the layers panel. */
export function shapeSvg(shape: WindShape, rotateDeg = 0): string {
  const s = SHAPES[shape];
  const parts: string[] = [];
  if (s.ring) {
    parts.push(`<path d="${s.ring}" fill="none" stroke="currentColor" stroke-width="${s.ringWidth + 0.5}"/>`);
  }
  if (s.shaft) {
    parts.push(`<path d="${s.shaft}" stroke="currentColor" stroke-width="${s.shaftWidth + 0.5}" stroke-linecap="round"/>`);
  }
  if (s.head) {
    parts.push(
      s.headFilled
        ? `<path d="${s.head}" fill="currentColor" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round"/>`
        : `<path d="${s.head}" fill="var(--surface)" stroke="currentColor" stroke-width="2.5" stroke-linejoin="round"/>`,
    );
  }
  const rot = rotateDeg ? ` transform="rotate(${Math.round(rotateDeg)} 20 20)"` : "";
  return `<svg class="wind-glyph" viewBox="-2 -2 44 44" aria-hidden="true" focusable="false"><g${rot}>${parts.join("")}</g></svg>`;
}
