#!/usr/bin/env node
/**
 * Rasterizes public/icons/icon.svg into the PNG sizes the manifest references.
 * Needs `rsvg-convert` (brew install librsvg). The PNGs are committed, so this only has
 * to run when the artwork changes.
 */
import { execFileSync } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const icons = path.resolve(here, "../public/icons");
const src = path.join(icons, "icon.svg");

for (const size of [180, 192, 512]) {
  const out = path.join(icons, `icon-${size}.png`);
  execFileSync("rsvg-convert", ["-w", String(size), "-h", String(size), src, "-o", out]);
  console.log(`wrote ${out}`);
}

// Maskable variant: square bleed with the art inside the 80% safe zone.
const maskableSrc = path.join(icons, "maskable.svg");
const maskable = path.join(icons, "maskable-512.png");
execFileSync("rsvg-convert", ["-w", "512", "-h", "512", maskableSrc, "-o", maskable]);
console.log(`wrote ${maskable}`);
