/**
 * Checks the wave-band palette (src/waves/ramp.ts) for colour-blind separation.
 *
 *   node web/scripts/wave-palette-cvd.mjs
 *
 * For each theme it simulates protanopia, deuteranopia and tritanopia with the Machado,
 * Oliveira & Fernandes (2009) matrices at full severity (applied in linear sRGB), then
 * prints L* per band and the pairwise CIEDE2000 between bands. The `norun` band is a hollow
 * ring, so its ring colour stands for it. Also prints each band against the basemap water.
 * Exits 1 when any pair drops under MIN_DE, or two filled bands sit under MIN_DL apart in L*.
 */
import { BAND_STYLE, WAVE_BANDS } from "../src/waves/ramp.ts";

const MIN_DE = 20;
const MIN_DL = 18;

// Protomaps basemap water, light and dark flavors.
const WATER = { light: "#80deea", dark: "#31353f" };

const MACHADO = {
  normal: [[1, 0, 0], [0, 1, 0], [0, 0, 1]],
  protan: [[0.152286, 1.052583, -0.204868], [0.114503, 0.786281, 0.099216], [-0.003882, -0.048116, 1.051998]],
  deutan: [[0.367322, 0.860646, -0.227968], [0.280085, 0.672501, 0.047413], [-0.01182, 0.04294, 0.968881]],
  tritan: [[1.255528, -0.076749, -0.178779], [-0.078411, 0.930809, 0.147602], [0.004733, 0.691367, 0.3039]],
};

const hex = (h) => [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16) / 255);
const toLin = (c) => (c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4);
const clamp = (x) => Math.min(1, Math.max(0, x));

function simulate(rgb, m) {
  const lin = rgb.map(toLin);
  return m.map((row) => clamp(row[0] * lin[0] + row[1] * lin[1] + row[2] * lin[2]));
}

function lab(lin) {
  const [r, g, b] = lin;
  const X = (0.4124 * r + 0.3576 * g + 0.1805 * b) / 0.95047;
  const Y = 0.2126 * r + 0.7152 * g + 0.0722 * b;
  const Z = (0.0193 * r + 0.1192 * g + 0.9505 * b) / 1.08883;
  const f = (t) => (t > 216 / 24389 ? Math.cbrt(t) : (24389 / 27 * t + 16) / 116);
  return [116 * f(Y) - 16, 500 * (f(X) - f(Y)), 200 * (f(Y) - f(Z))];
}

function ciede2000([L1, a1, b1], [L2, a2, b2]) {
  const rad = Math.PI / 180;
  const C1 = Math.hypot(a1, b1), C2 = Math.hypot(a2, b2);
  const Cb = (C1 + C2) / 2;
  const G = 0.5 * (1 - Math.sqrt(Cb ** 7 / (Cb ** 7 + 25 ** 7)));
  const ap1 = (1 + G) * a1, ap2 = (1 + G) * a2;
  const Cp1 = Math.hypot(ap1, b1), Cp2 = Math.hypot(ap2, b2);
  const hp = (b, a) => (a === 0 && b === 0 ? 0 : (Math.atan2(b, a) / rad + 360) % 360);
  const hp1 = hp(b1, ap1), hp2 = hp(b2, ap2);
  const dL = L2 - L1, dC = Cp2 - Cp1;
  let dh = 0;
  if (Cp1 * Cp2 !== 0) {
    dh = hp2 - hp1;
    if (dh > 180) dh -= 360;
    else if (dh < -180) dh += 360;
  }
  const dH = 2 * Math.sqrt(Cp1 * Cp2) * Math.sin((dh / 2) * rad);
  const Lb = (L1 + L2) / 2, Cpb = (Cp1 + Cp2) / 2;
  let hb = hp1 + hp2;
  if (Cp1 * Cp2 !== 0) {
    if (Math.abs(hp1 - hp2) > 180) hb += hp1 + hp2 < 360 ? 360 : -360;
    hb /= 2;
  }
  const T = 1 - 0.17 * Math.cos((hb - 30) * rad) + 0.24 * Math.cos(2 * hb * rad)
    + 0.32 * Math.cos((3 * hb + 6) * rad) - 0.2 * Math.cos((4 * hb - 63) * rad);
  const dTheta = 30 * Math.exp(-(((hb - 275) / 25) ** 2));
  const Rc = 2 * Math.sqrt(Cpb ** 7 / (Cpb ** 7 + 25 ** 7));
  const Sl = 1 + (0.015 * (Lb - 50) ** 2) / Math.sqrt(20 + (Lb - 50) ** 2);
  const Sc = 1 + 0.045 * Cpb, Sh = 1 + 0.015 * Cpb * T;
  const Rt = -Math.sin(2 * dTheta * rad) * Rc;
  return Math.sqrt((dL / Sl) ** 2 + (dC / Sc) ** 2 + (dH / Sh) ** 2 + Rt * (dC / Sc) * (dH / Sh));
}

const shown = (style) => (style.fill === "transparent" ? style.ring : style.fill);
let failed = false;

for (const theme of ["light", "dark"]) {
  console.log(`\n== ${theme} ==`);
  for (const band of WAVE_BANDS) {
    const s = BAND_STYLE[theme][band];
    console.log(`  ${band.padEnd(9)} fill ${s.fill.padEnd(11)} ring ${s.ring}`);
  }
  for (const [vision, m] of Object.entries(MACHADO)) {
    const labs = Object.fromEntries(
      WAVE_BANDS.map((b) => [b, lab(simulate(hex(shown(BAND_STYLE[theme][b])), m))]),
    );
    const water = lab(simulate(hex(WATER[theme]), m));
    const Ls = WAVE_BANDS.map((b) => `${b} ${labs[b][0].toFixed(0)}`).join(", ");
    console.log(`  ${vision}: L* ${Ls}`);
    const pairs = [];
    for (let i = 0; i < WAVE_BANDS.length; i++) {
      for (let j = i + 1; j < WAVE_BANDS.length; j++) {
        const a = WAVE_BANDS[i], b = WAVE_BANDS[j];
        const de = ciede2000(labs[a], labs[b]);
        const dl = Math.abs(labs[a][0] - labs[b][0]);
        const filled = a !== "norun" && b !== "norun";
        const bad = de < MIN_DE || (filled && dl < MIN_DL);
        if (bad) failed = true;
        pairs.push(`${a}/${b} ${de.toFixed(1)}${bad ? "!" : ""}`);
      }
    }
    console.log(`    dE00 ${pairs.join("  ")}`);
    console.log(`    vs water ${WAVE_BANDS.map((b) => `${b} ${ciede2000(labs[b], water).toFixed(1)}`).join("  ")}`);
  }
}
console.log(failed ? "\nFAIL: a pair is under the threshold" : "\nok: every pair clears the thresholds");
process.exit(failed ? 1 : 0);
