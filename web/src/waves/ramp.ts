import type { ResolvedTheme } from "../ui/theme";

/**
 * Wave-height bands for the map, the map key and the sheet.
 *
 * The number is the answer; colour only sorts it against the pilot's own limits. So there is
 * no continuous ramp any more: every point falls in one of four bands, keyed to the briefing
 * settings `wave_ok_in` and `wave_max_in` (the same limits that score a lake in the
 * briefing), and every band differs from the others by more than hue.
 *
 *   ok        hs <= wave_ok_in                     dark, small solid dot
 *   marginal  wave_ok_in < hs <= wave_max_in       mid, solid dot
 *   over      hs > wave_max_in                     light, larger dot with a heavy ring
 *   norun     run into this wind too short          hollow ring, no fill
 *
 * Colour choice. The owner is colour blind (type unknown), so the bands are separated first
 * by lightness, at least ~20 L* apart in each theme, which survives every kind of colour
 * blindness and greyscale; then by the ring and size cues above; then by hue along a
 * viridis-like navy / teal / yellow path, which protan, deutan and tritan viewers all
 * still see as ordered. `node web/scripts/wave-palette-cvd.mjs` simulates the three types
 * (Machado 2009, full severity) and prints the pairwise CIEDE2000 between bands.
 *
 * It must not be the verdict palette (red / amber / green / grey) or the weather-score
 * palette (blue / violet / magenta): a dot on the water must never read as "this lake is
 * clear" or "the weather is favourable". Yellow for over the limit also points attention the
 * right way: the loudest mark is the water you do not want.
 *
 * The dark theme lifts the calm end, which would otherwise vanish into near-black water.
 */

export type WaveBand = "ok" | "marginal" | "over" | "norun";

export interface WaveLimits {
  /** `limits.wave_ok_in`: at or under this is within the limit. */
  okIn: number;
  /** `limits.wave_max_in`: over this is over the limit. */
  maxIn: number;
}

export interface BandStyle {
  fill: string;
  /** Ring around the dot. For `norun` this is the only thing drawn. */
  ring: string;
}

export const WAVE_BANDS: WaveBand[] = ["ok", "marginal", "over", "norun"];

export const BAND_STYLE: Record<ResolvedTheme, Record<WaveBand, BandStyle>> = {
  light: {
    ok: { fill: "#17396b", ring: "#ffffff" },
    marginal: { fill: "#1f9e89", ring: "#ffffff" },
    over: { fill: "#fde725", ring: "#11181c" },
    norun: { fill: "transparent", ring: "#5c3d1e" },
  },
  dark: {
    ok: { fill: "#3f5f9f", ring: "#0d1117" },
    marginal: { fill: "#2fae8f", ring: "#0d1117" },
    over: { fill: "#fde725", ring: "#f6f8fa" },
    norun: { fill: "transparent", ring: "#e6edf3" },
  },
};

/** Relative dot size; the over-limit dot is bigger so size alone tells it apart. */
export const BAND_SCALE: Record<WaveBand, number> = { ok: 1, marginal: 1, over: 1.25, norun: 1 };

/** The band a wave height falls in. A point with too short a run is `norun` whatever its height. */
export function waveBand(hsIn: number | null | undefined, usable: boolean, limits: WaveLimits): WaveBand {
  if (!usable || hsIn == null || !Number.isFinite(hsIn)) return "norun";
  if (hsIn <= limits.okIn) return "ok";
  if (hsIn <= limits.maxIn) return "marginal";
  return "over";
}

/**
 * The key's wording for each band, from the limits: "≤ 8 in", "9–12 in", "> 12 in",
 * "no run". Heights are whole inches, so the marginal band starts one inch over the limit.
 * With `wave_ok_in` equal to `wave_max_in` there is no marginal band and it returns null.
 */
export function bandText(band: WaveBand, limits: WaveLimits): string | null {
  const { okIn, maxIn } = limits;
  switch (band) {
    case "ok":
      return `≤ ${okIn} in`;
    case "marginal":
      if (maxIn <= okIn) return null;
      return okIn + 1 === maxIn ? `${maxIn} in` : `${okIn + 1}–${maxIn} in`;
    case "over":
      return `> ${Math.max(okIn, maxIn)} in`;
    case "norun":
      return "no run";
  }
}

/** Longer wording for tooltips and screen readers. */
export const BAND_WORD: Record<WaveBand, string> = {
  ok: "within your wave limit",
  marginal: "marginal for your wave limit",
  over: "over your wave limit",
  norun: "run too short into this wind",
};

/**
 * Publishes the band colours as CSS custom properties (`--wave-ok-fill`, `--wave-ok-ring`,
 * ...) so the key and the sheet's swatches draw from the same table as the map. Call it at
 * start and on every theme change.
 */
export function applyWaveBandVars(theme: ResolvedTheme, root: HTMLElement = document.documentElement): void {
  for (const band of WAVE_BANDS) {
    const style = BAND_STYLE[theme][band];
    root.style.setProperty(`--wave-${band}-fill`, style.fill);
    root.style.setProperty(`--wave-${band}-ring`, style.ring);
  }
}

function bandMatch<T>(pick: (band: WaveBand) => T): unknown[] {
  const expr: unknown[] = ["match", ["get", "band"]];
  for (const band of WAVE_BANDS.slice(0, 3)) expr.push(band, pick(band));
  expr.push(pick("norun"));
  return expr;
}

/** MapLibre expressions over the `band` feature property, for the circle layer. */
export function waveBandPaint(theme: ResolvedTheme): {
  fill: unknown[];
  fillOpacity: unknown[];
  ring: unknown[];
  scale: unknown[];
} {
  const styles = BAND_STYLE[theme];
  return {
    fill: bandMatch((b) => (b === "norun" ? styles.ok.fill : styles[b].fill)),
    fillOpacity: bandMatch((b) => (b === "norun" ? 0 : 1)),
    ring: bandMatch((b) => styles[b].ring),
    scale: bandMatch((b) => BAND_SCALE[b]),
  };
}
