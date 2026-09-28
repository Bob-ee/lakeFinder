import * as maplibregl from "maplibre-gl";
import type { FeatureCollection, Point } from "geojson";
import type { Map as MlMap, MapMouseEvent } from "maplibre-gl";
import { WIND } from "../config";
import type { MapController } from "../map";
import { el } from "../ui/format";
import { readLocal, writeLocal, type ResolvedTheme } from "../ui/theme";
import type { WindData } from "./data";
import {
  SHAPE_BOX,
  SHAPES,
  formatAgo,
  shapeFor,
  sourceWord,
  speedLabel,
  windText,
  type WindShape,
} from "./format";
import type { Station } from "./types";

/**
 * The wind layer: every station in view as an arrow pointing downwind with its speed.
 *
 * It owns its own GeoJSON source, symbol layer and icons and puts them back after every
 * style swap (a theme change rebuilds the style from scratch), so `map/style.ts` does not
 * have to know it exists. Source is told by arrow shape only: METAR a solid arrow, buoy an
 * arrow in a ring, mesonet an open arrow. Age steps the opacity down and drops the station
 * past 90 minutes.
 */

const SOURCE_ID = "wind-stations";
const LAYER_ID = "wind-stations-symbol";
const SHAPE_LIST: WindShape[] = ["metar", "buoy", "mesonet", "calm"];
/** Rank for placement when two stations collide: aviation first, then buoys. */
const SOURCE_RANK: Record<WindShape, number> = { metar: 0, buoy: 1, mesonet: 2, calm: 3 };

const INK: Record<ResolvedTheme, { ink: string; halo: string }> = {
  light: { ink: "#0b1014", halo: "#ffffff" },
  dark: { ink: "#f0f3f6", halo: "#0d1117" },
};

const EMPTY: FeatureCollection = { type: "FeatureCollection", features: [] };

function imageName(shape: WindShape, theme: ResolvedTheme): string {
  return `wind-${shape}-${theme}`;
}

/** Draws one arrow icon: a wide halo pass under the ink so it reads on land and water. */
function drawIcon(shape: WindShape, theme: ResolvedTheme, ratio: number): ImageData {
  const size = 38;
  const px = Math.round(size * ratio);
  const canvas = document.createElement("canvas");
  canvas.width = px;
  canvas.height = px;
  const ctx = canvas.getContext("2d")!;
  const k = px / (SHAPE_BOX + 4);
  ctx.scale(k, k);
  ctx.translate(2, 2);
  ctx.lineCap = "round";
  ctx.lineJoin = "round";
  const { ink, halo } = INK[theme];
  const s = SHAPES[shape];
  const ring = s.ring ? new Path2D(s.ring) : null;
  const shaft = s.shaft ? new Path2D(s.shaft) : null;
  const head = s.head ? new Path2D(s.head) : null;

  for (const pass of ["halo", "ink"] as const) {
    const extra = pass === "halo" ? 3.5 : 0;
    const color = pass === "halo" ? halo : ink;
    ctx.strokeStyle = color;
    ctx.fillStyle = color;
    if (ring) {
      ctx.lineWidth = s.ringWidth + extra;
      ctx.stroke(ring);
    }
    if (shaft) {
      ctx.lineWidth = s.shaftWidth + extra;
      ctx.stroke(shaft);
    }
    if (head) {
      if (pass === "halo" || s.headFilled) ctx.fill(head);
      ctx.lineWidth = (s.headFilled ? 1.5 : 2.6) + extra;
      ctx.stroke(head);
      if (pass === "ink" && !s.headFilled) {
        // Open head: hollow, with the halo colour inside so the map does not show through.
        ctx.fillStyle = halo;
        ctx.fill(head);
        ctx.stroke(head);
      }
    }
  }
  return ctx.getImageData(0, 0, px, px);
}

interface StationProps {
  key: string;
  shape: WindShape;
  rot: number;
  label: string;
  opacity: number;
  sort: number;
}

function opacityForAge(min: number): number {
  if (min <= 30) return 1;
  if (min <= 60) return 0.72;
  return 0.45;
}

export class WindLayer {
  private on: boolean;
  private popup: maplibregl.Popup | null = null;
  private status: HTMLElement;
  private ageTimer: number | null = null;
  private byKey = new Map<string, Station>();

  constructor(
    private controller: MapController,
    private data: WindData,
    private theme: () => ResolvedTheme,
  ) {
    this.on = readLocal(WIND.storageKey) === "1";
    this.status = el("div", "wind-status");
    this.status.hidden = true;
    this.status.setAttribute("role", "status");

    const map = this.map;
    map.on("styledata", () => this.ensureLayer());
    map.on("styleimagemissing", (e: { id: string }) => this.addImage(e.id));
    map.on("idle", () => this.onIdle());
    map.on("zoomend", () => this.paintStatus());
    this.controller.addTapHandler((e) => this.onTap(e));
    this.data.onChange(() => this.paint());
    this.ensureLayer();
    this.syncTimer();
  }

  private get map(): MlMap {
    return this.controller.map;
  }

  get enabled(): boolean {
    return this.on;
  }

  /** The age / stale chip; the caller places it. */
  get statusElement(): HTMLElement {
    return this.status;
  }

  setEnabled(on: boolean): void {
    this.on = on;
    writeLocal(WIND.storageKey, on ? "1" : "0");
    if (this.map.getLayer(LAYER_ID)) {
      this.map.setLayoutProperty(LAYER_ID, "visibility", on ? "visible" : "none");
    }
    if (!on) this.popup?.remove();
    this.syncTimer();
    this.paint();
    if (on) this.onIdle();
  }

  private syncTimer(): void {
    if (this.on && this.ageTimer == null) {
      // Ages move on their own; repaint once a minute so opacity and the chip keep up.
      this.ageTimer = window.setInterval(() => this.paint(), 60_000);
    } else if (!this.on && this.ageTimer != null) {
      clearInterval(this.ageTimer);
      this.ageTimer = null;
    }
  }

  private addImage(id: string): void {
    const m = /^wind-(metar|buoy|mesonet|calm)-(light|dark)$/.exec(id);
    if (!m || this.map.hasImage(id)) return;
    const ratio = Math.max(2, Math.ceil(window.devicePixelRatio || 1));
    this.map.addImage(id, drawIcon(m[1] as WindShape, m[2] as ResolvedTheme, ratio), {
      pixelRatio: ratio,
    });
  }

  /** Puts the source and layer (back) into the style. Safe to call any number of times. */
  private ensureLayer(): void {
    const map = this.map;
    const style = map.getStyle();
    if (!style || style.layers.length === 0) return;
    if (map.getLayer(LAYER_ID)) return;
    const theme = this.theme();
    const { ink, halo } = INK[theme];
    try {
      for (const shape of SHAPE_LIST) this.addImage(imageName(shape, theme));
      if (!map.getSource(SOURCE_ID)) {
        map.addSource(SOURCE_ID, { type: "geojson", data: EMPTY });
      }
      map.addLayer({
        id: LAYER_ID,
        type: "symbol",
        source: SOURCE_ID,
        minzoom: WIND.minZoom,
        layout: {
          visibility: this.on ? "visible" : "none",
          "icon-image": ["concat", "wind-", ["get", "shape"], "-", theme],
          "icon-rotate": ["get", "rot"],
          "icon-rotation-alignment": "map",
          "icon-size": ["interpolate", ["linear"], ["zoom"], 8, 0.85, 12, 1.05],
          "icon-padding": 2,
          "text-field": ["get", "label"],
          "text-font": ["Noto Sans Medium"],
          "text-size": ["interpolate", ["linear"], ["zoom"], 8, 12, 12, 13.5],
          // Beside the arrow, never on it; the anchor walks round before the station drops.
          "text-variable-anchor": ["left", "right", "top", "bottom"],
          "text-radial-offset": 1.25,
          "text-justify": "auto",
          "text-padding": 2,
          "symbol-sort-key": ["get", "sort"],
          "icon-allow-overlap": false,
          "text-allow-overlap": false,
          // Arrow and speed together or not at all: an arrow with no speed says too little.
          "text-optional": false,
          "icon-optional": false,
        },
        paint: {
          "icon-opacity": ["get", "opacity"],
          "text-opacity": ["get", "opacity"],
          "text-color": ink,
          "text-halo-color": halo,
          "text-halo-width": 2,
        },
      });
    } catch (err) {
      console.warn("[wind] could not add the layer", err);
      return;
    }
    this.paint();
  }

  private onIdle(): void {
    if (!this.on) return;
    this.paintStatus();
    if (this.map.getZoom() < WIND.minZoom) return;
    const b = this.map.getBounds();
    this.data.requestView([b.getWest(), b.getSouth(), b.getEast(), b.getNorth()]);
  }

  /** Rebuilds the features from the cache: new data, a new minute, a theme swap. */
  paint(): void {
    const src = this.map.getSource(SOURCE_ID) as maplibregl.GeoJSONSource | undefined;
    this.paintStatus();
    if (!src) return;
    if (!this.on) {
      src.setData(EMPTY);
      return;
    }
    const now = Date.now();
    this.byKey.clear();
    const features: FeatureCollection<Point, StationProps>["features"] = [];
    for (const s of this.data.stations()) {
      const t = Date.parse(s.obs_time);
      if (Number.isNaN(t) || s.speed_kt == null) continue;
      const age = Math.max(0, (now - t) / 60_000);
      if (age > WIND.hideAfterMin) continue;
      const shape = shapeFor(s);
      const key = `${s.source}:${s.id}`;
      this.byKey.set(key, s);
      features.push({
        type: "Feature",
        geometry: { type: "Point", coordinates: [s.lon, s.lat] },
        properties: {
          key,
          shape,
          // The arrow is drawn pointing up; wind FROM 240 blows toward 060.
          rot: s.dir_deg == null ? 0 : (s.dir_deg + 180) % 360,
          label: s.dir_deg == null && s.speed_kt > 0 ? `VRB ${speedLabel(s)}` : speedLabel(s),
          opacity: opacityForAge(age),
          sort: SOURCE_RANK[shape] * 1000 + Math.round(age),
        },
      });
    }
    src.setData({ type: "FeatureCollection", features });
  }

  /** "Wind 4 min" / "Wind 72 min · stale" / "Wind: zoom in". Nothing before a first fetch. */
  private paintStatus(): void {
    const s = this.status;
    if (!this.on) {
      s.hidden = true;
      return;
    }
    if (this.map.getZoom() < WIND.minZoom) {
      s.hidden = false;
      s.replaceChildren(el("span", undefined, "Wind: zoom in"));
      delete s.dataset["stale"];
      return;
    }
    const b = this.map.getBounds();
    const at = this.data.fetchedAt([b.getWest(), b.getSouth(), b.getEast(), b.getNorth()]);
    if (at == null) {
      s.hidden = true;
      return;
    }
    const age = (Date.now() - at) / 60_000;
    s.hidden = false;
    const parts: HTMLElement[] = [el("span", undefined, `Wind updated ${formatAgo(age)}`)];
    if (age > WIND.staleAfterMin) {
      parts.push(el("span", "stale-badge", "stale"));
      s.dataset["stale"] = "1";
    } else {
      delete s.dataset["stale"];
    }
    s.replaceChildren(...parts);
  }

  /** A tap within 24 px of a station opens its details instead of selecting the lake under it. */
  private onTap(e: MapMouseEvent): boolean {
    if (!this.on || !this.map.getLayer(LAYER_ID)) return false;
    const r = WIND.tapRadiusPx;
    const hits = this.map.queryRenderedFeatures(
      [
        [e.point.x - r, e.point.y - r],
        [e.point.x + r, e.point.y + r],
      ],
      { layers: [LAYER_ID] },
    );
    if (hits.length === 0) return false;
    let best = hits[0]!;
    let bestD = Infinity;
    for (const h of hits) {
      const g = h.geometry as Point;
      const p = this.map.project(g.coordinates as [number, number]);
      const d = (p.x - e.point.x) ** 2 + (p.y - e.point.y) ** 2;
      if (d < bestD) {
        bestD = d;
        best = h;
      }
    }
    const station = this.byKey.get(String(best.properties?.["key"]));
    if (!station) return false;
    this.showPopup(station);
    return true;
  }

  private showPopup(s: Station): void {
    this.popup?.remove();
    const body = el("div", "wind-popup-body");
    body.append(el("div", "wind-popup-name", s.name ?? s.id));
    const meta = el("div", "wind-popup-meta", `${s.id} · ${sourceWord(s.source)}`);
    body.append(meta);
    const text = windText(s);
    body.append(el("div", "wind-popup-wind", text ? `Wind ${text} kt` : "No wind reported"));
    const t = Date.parse(s.obs_time);
    if (!Number.isNaN(t)) {
      const age = (Date.now() - t) / 60_000;
      const ageLine = el("div", "wind-popup-age", `Observed ${formatAgo(age)}`);
      if (age > WIND.staleAfterMin) ageLine.append(" ", el("span", "stale-badge", "stale"));
      body.append(ageLine);
    }
    this.popup = new maplibregl.Popup({
      closeButton: false,
      closeOnClick: true,
      className: "wind-popup",
      maxWidth: "260px",
      offset: 18,
    })
      .setLngLat([s.lon, s.lat])
      .setDOMContent(body)
      .addTo(this.map);
  }
}
