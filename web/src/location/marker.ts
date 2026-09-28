import * as maplibregl from "maplibre-gl";
import type { Map as MlMap } from "maplibre-gl";
import { type Fix, usableCourse } from "./index";
import { metersPerPixel } from "./geo";

/** A fix older than this is drawn hollow: the position is a memory, not a reading. */
const STALE_MS = 30_000;

const ARROW_SVG =
  '<svg viewBox="0 0 40 40" aria-hidden="true" focusable="false">' +
  '<path d="M20 3 L33 34 L20 27 L7 34 Z"/></svg>';

/**
 * Own-position marker: a course arrow when the course is usable, otherwise a dot, with a
 * dashed accuracy ring. Shape carries the meaning (arrow = moving with a course, dot = no
 * course, hollow = stale); the colours are one dark-on-white pairing that holds up on
 * both basemaps and does not lean on hue, since the owner is colour-blind.
 *
 * A DOM marker with `rotationAlignment: "map"` so the arrow stays on the true course when
 * the map rotates for heading-up.
 */
export class OwnMarker {
  private marker: maplibregl.Marker | null = null;
  private root: HTMLElement;
  private ring: HTMLElement;
  private glyph: HTMLElement;
  private fix: Fix | null = null;

  constructor(private map: MlMap) {
    this.root = document.createElement("div");
    this.root.className = "own-pos";
    this.root.setAttribute("role", "img");
    this.ring = document.createElement("div");
    this.ring.className = "own-pos-ring";
    this.glyph = document.createElement("div");
    this.glyph.className = "own-pos-glyph";
    this.root.append(this.ring, this.glyph);
    map.on("zoom", () => this.sizeRing());
  }

  update(fix: Fix | null): void {
    this.fix = fix;
    if (!fix) {
      this.marker?.remove();
      this.marker = null;
      return;
    }
    const course = usableCourse(fix);
    const moving = course != null;
    if (this.root.dataset["shape"] !== (moving ? "arrow" : "dot")) {
      this.root.dataset["shape"] = moving ? "arrow" : "dot";
      this.glyph.innerHTML = moving ? ARROW_SVG : "";
    }
    this.root.classList.toggle("is-stale", Date.now() - new Date(fix.time).getTime() > STALE_MS);
    this.root.setAttribute(
      "aria-label",
      moving ? `Your position, course ${Math.round(course)} degrees` : "Your position",
    );

    if (!this.marker) {
      this.marker = new maplibregl.Marker({
        element: this.root,
        anchor: "center",
        rotationAlignment: "map",
        pitchAlignment: "map",
      })
        .setLngLat([fix.lon, fix.lat])
        .addTo(this.map);
    } else {
      this.marker.setLngLat([fix.lon, fix.lat]);
    }
    this.marker.setRotation(course ?? 0);
    this.sizeRing();
  }

  private sizeRing(): void {
    if (!this.fix) return;
    const px = (2 * this.fix.accuracy_m) / metersPerPixel(this.fix.lat, this.map.getZoom());
    // Under the glyph it says nothing; past a screen it is just a wash.
    const show = px > 44 && px < 3000;
    this.ring.hidden = !show;
    if (show) {
      this.ring.style.width = `${px}px`;
      this.ring.style.height = `${px}px`;
    }
  }
}
