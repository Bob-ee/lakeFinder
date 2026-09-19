import type { ResolvedTheme } from "../ui/theme";

/**
 * Colour ramp for wave height on the map and in the sheet.
 *
 * It must not be the verdict palette (red / amber / green / grey) and must not be the
 * weather-score palette (blue / violet / magenta, see styles/briefing.css), because a dot
 * on the water must never read as "this lake is legally clear" or "the weather is
 * favourable". That rules out most of the hue circle: the map already spends red, amber,
 * green and grey on verdicts, teal on boating access sites, blue on the basemap water and
 * violet on the federal overlay.
 *
 * What is left, and what a sea-state plot wants anyway, is a *luminance* ramp rather than a
 * hue ramp: a cividis-style deep-navy-to-yellow scale. It is monotonic in lightness, so it
 * orders correctly in greyscale and under every kind of colour blindness; its dark end is
 * far darker than the score blue and its light end sits in the one hue nothing else in the
 * app uses. Yellow for rough also points attention the right way: the loud colour is the
 * water you do not want.
 *
 * It is continuous rather than banded because the question is "which corner of this lake",
 * and a lake in one wind usually spans only a few inches; bands would flatten the whole
 * field to one or two colours and hide the answer. The scale itself is absolute inches, so
 * the same colour means the same water on every lake and can be compared with the
 * `wave_ok_in` limit in the briefing settings.
 *
 * Both ends have to survive being drawn on water, which is light blue in the light theme
 * and near-black in the dark one, so the dark theme lifts the whole ramp and every dot gets
 * a thin ring in the surface colour.
 */

export interface WaveStop {
  /** Wave height in whole inches. */
  at: number;
  color: string;
}

const RAMP: Record<ResolvedTheme, WaveStop[]> = {
  light: [
    { at: 0, color: "#00305f" },
    { at: 4, color: "#3f5877" },
    { at: 8, color: "#7c7b78" },
    { at: 12, color: "#bcaf6f" },
    { at: 16, color: "#ffe23f" },
  ],
  dark: [
    { at: 0, color: "#41618f" },
    { at: 4, color: "#6a7d8c" },
    { at: 8, color: "#93907e" },
    { at: 12, color: "#c6b672" },
    { at: 16, color: "#ffe94a" },
  ],
};

/** Ring around each dot, so both ends of the ramp separate from the water under them. */
export const WAVE_DOT_RING: Record<ResolvedTheme, string> = {
  light: "#ffffff",
  dark: "#0d1117",
};

/** Ticks under the legend's gradient bar. The top one is open-ended. */
export const WAVE_LEGEND_TICKS = ["0", "4", "8", "12", "16+"];

export function waveStops(theme: ResolvedTheme): WaveStop[] {
  return RAMP[theme];
}

/** The ramp colour for a wave height in whole inches, interpolated between the stops. */
export function waveColor(hsIn: number | null | undefined, theme: ResolvedTheme): string {
  const stops = RAMP[theme];
  const last = stops[stops.length - 1]!;
  if (hsIn == null || !Number.isFinite(hsIn)) return last.color;
  if (hsIn <= stops[0]!.at) return stops[0]!.color;
  for (let i = 1; i < stops.length; i++) {
    const hi = stops[i]!;
    if (hsIn > hi.at) continue;
    const lo = stops[i - 1]!;
    return mix(lo.color, hi.color, (hsIn - lo.at) / (hi.at - lo.at));
  }
  return last.color;
}

/** `linear-gradient(...)` stops for the legend bar, in the same order as the ramp. */
export function waveGradient(theme: ResolvedTheme): string {
  const stops = RAMP[theme];
  const span = stops[stops.length - 1]!.at - stops[0]!.at;
  const parts = stops.map((s) => `${s.color} ${Math.round(((s.at - stops[0]!.at) / span) * 100)}%`);
  return `linear-gradient(to right, ${parts.join(", ")})`;
}

/**
 * The same ramp as a MapLibre `interpolate` expression over the `hs_in` feature property,
 * so the GPU does the blend and a wind change only has to rewrite the property.
 */
export function waveColorExpression(theme: ResolvedTheme): unknown[] {
  const expr: unknown[] = ["interpolate", ["linear"], ["coalesce", ["get", "hs_in"], 0]];
  for (const stop of RAMP[theme]) expr.push(stop.at, stop.color);
  return expr;
}

function mix(a: string, b: string, t: number): string {
  const ca = rgb(a);
  const cb = rgb(b);
  const f = Math.max(0, Math.min(1, t));
  const out = ca.map((v, i) => Math.round(v + (cb[i]! - v) * f));
  return `rgb(${out[0]}, ${out[1]}, ${out[2]})`;
}

function rgb(hex: string): number[] {
  const n = Number.parseInt(hex.slice(1), 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}
