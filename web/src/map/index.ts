import * as maplibregl from "maplibre-gl";
import type { LngLatBoundsLike, Map as MlMap, PaddingOptions } from "maplibre-gl";
import { Protocol } from "pmtiles";
import { DATA_FILES, FIXTURE_VIEW, HOME_VIEW, MAX_SELECT_ZOOM } from "../config";
import { pmtilesExists } from "../pack";
import type { Lake } from "../types";
import type { LayerPrefs } from "../ui/controls";
import type { ResolvedTheme } from "../ui/theme";
import {
  buildStyle,
  LAYER,
  OVERLAY_GROUPS,
  NO_FEATURE,
  selectionFilter,
  SOURCE,
  VERDICT_COLORS,
  type AvailableTiles,
  type OverlayGroup,
} from "./style";

/** What a map tap found, before the index turns it into a lake id. */
export interface LakeHit {
  id: number | null;
  name: string | null;
  county: string | null;
}

export interface MapCallbacks {
  /** Map padding that keeps the selected lake clear of the sheet or side panel. */
  padding: () => PaddingOptions;
  onLakeTap: (hit: LakeHit) => void;
  onBackgroundTap: () => void;
}

const PULSE_MS = 2400;
const PULSE_CYCLES = 3;
/** Where the halo rests once the pulse has finished drawing the eye. */
const SETTLED_GLOW = 0.22;

/** Registered once per page; MapLibre's protocol table is global. */
let protocolRegistered = false;
function registerPmtiles(): void {
  if (protocolRegistered) return;
  maplibregl.addProtocol("pmtiles", new Protocol().tile);
  protocolRegistered = true;
}

export class MapController {
  readonly map: MlMap;
  private available: AvailableTiles;
  private theme: ResolvedTheme;
  private selectedId: number | null = null;
  private selectedLake: Lake | null = null;
  private marker: maplibregl.Marker | null = null;
  private pulseHandle: number | null = null;
  private pulseStart = 0;
  private lastPulseWrite = 0;
  private overlayState: Record<OverlayGroup, boolean>;
  private usableWaterOn: boolean;
  /** Reset on every style swap; see reapplyLayerState. */
  private layerStateApplied = false;

  private constructor(
    container: HTMLElement,
    theme: ResolvedTheme,
    available: AvailableTiles,
    prefs: LayerPrefs,
    private cb: MapCallbacks,
  ) {
    this.theme = theme;
    this.available = available;
    this.overlayState = prefs.overlays;
    this.usableWaterOn = prefs.usableWater;

    const view = available.lakes ? FIXTURE_VIEW : HOME_VIEW;
    this.map = new maplibregl.Map({
      container,
      style: buildStyle(theme, available),
      center: view.center,
      zoom: view.zoom,
      maxZoom: 17,
      attributionControl: { compact: true },
      // Phase 2 turns this on with the heading-up toggle.
      pitchWithRotate: false,
      dragRotate: false,
    });
    this.map.touchZoomRotate.disableRotation();

    this.map.on("styledata", () => this.reapplyLayerState());
    this.map.on("click", (e) => this.handleClick(e));
    this.map.on("error", (e) => {
      // A missing tile in an otherwise valid archive is normal; do not spam the console.
      const message = (e.error as Error | undefined)?.message ?? "";
      if (message.includes("Tile not found")) return;
      console.warn("[map]", message || e);
    });
    document.addEventListener("visibilitychange", () => {
      // Backgrounded tabs get no frames anyway; drop the loop and leave the halo settled.
      if (document.hidden) this.stopPulse(this.selectedId == null ? 0 : SETTLED_GLOW);
    });
  }

  static async create(
    container: HTMLElement,
    theme: ResolvedTheme,
    prefs: LayerPrefs,
    cb: MapCallbacks,
  ): Promise<MapController> {
    registerPmtiles();
    const [basemap, lakes, usableWater, overlaysOk] = await Promise.all([
      pmtilesExists(DATA_FILES.basemap),
      pmtilesExists(DATA_FILES.lakes),
      pmtilesExists(DATA_FILES.usableWater),
      pmtilesExists(DATA_FILES.overlays),
    ]);
    const available: AvailableTiles = { basemap, lakes, usableWater, overlays: overlaysOk };
    return new MapController(container, theme, available, prefs, cb);
  }

  get tiles(): AvailableTiles {
    return this.available;
  }

  // -- theme ---------------------------------------------------------------

  setTheme(theme: ResolvedTheme): void {
    if (theme === this.theme) return;
    this.theme = theme;
    this.layerStateApplied = false;
    this.map.setStyle(buildStyle(theme, this.available));
    // `styledata` fires after the swap and reapplies selection, toggles and glow colour.
  }

  // -- layer toggles -------------------------------------------------------

  setOverlay(group: OverlayGroup, on: boolean): void {
    this.overlayState[group] = on;
    this.applyOverlay(group);
  }

  overlayEnabled(group: OverlayGroup): boolean {
    return this.overlayState[group];
  }

  setUsableWater(on: boolean): void {
    this.usableWaterOn = on;
    this.applyVisibility(LAYER.usableWater, on);
  }

  usableWaterEnabled(): boolean {
    return this.usableWaterOn;
  }

  private applyOverlay(group: OverlayGroup): void {
    for (const id of OVERLAY_GROUPS[group]) this.applyVisibility(id, this.overlayState[group]);
  }

  private applyVisibility(layerId: string, on: boolean): void {
    if (!this.map.getLayer(layerId)) return;
    this.map.setLayoutProperty(layerId, "visibility", on ? "visible" : "none");
  }

  /**
   * Re-applies everything that lives outside the style object: layer toggles and the
   * selection. Runs as soon as the style's layers exist, which is well before
   * `isStyleLoaded()` goes true (that also waits on every tile). A lake selected from
   * `?lake=` during startup lands here rather than being dropped.
   */
  private reapplyLayerState(): void {
    if (this.layerStateApplied) return;
    const style = this.map.getStyle();
    if (!style || style.layers.length === 0) return;

    for (const group of Object.keys(OVERLAY_GROUPS) as OverlayGroup[]) this.applyOverlay(group);
    this.applyVisibility(LAYER.usableWater, this.usableWaterOn);
    if (this.selectedLake) {
      this.setFeatureSelected(this.selectedLake.id, true);
      this.setHighlight(this.selectedLake);
      this.startPulse();
    }
    this.layerStateApplied = true;
  }

  // -- selection -----------------------------------------------------------

  private handleClick(e: maplibregl.MapMouseEvent): void {
    if (!this.map.getLayer(LAYER.lakesFill)) {
      this.cb.onBackgroundTap();
      return;
    }
    // A generous box so a fat finger still hits a small lake.
    const pad = 6;
    const box: [maplibregl.PointLike, maplibregl.PointLike] = [
      [e.point.x - pad, e.point.y - pad],
      [e.point.x + pad, e.point.y + pad],
    ];
    const hits = this.map.queryRenderedFeatures(box, {
      layers: [LAYER.lakesFill, LAYER.lakesOutline],
    });
    const hit = hits[0];
    if (!hit) {
      this.cb.onBackgroundTap();
      return;
    }
    // `promoteId: "id"` gives a numeric feature id when the tiles follow the contract.
    // Fall back to the property, then to name plus county, so a tap still resolves against
    // index.json when the tiles were built without an `id`.
    const props = hit.properties as Record<string, unknown>;
    const raw = hit.id ?? props["id"];
    const id = typeof raw === "number" ? raw : typeof raw === "string" ? Number(raw) : null;
    this.cb.onLakeTap({
      id: id != null && Number.isFinite(id) ? id : null,
      name: typeof props["name"] === "string" ? props["name"] : null,
      county: typeof props["county"] === "string" ? props["county"] : null,
    });
  }

  private setFeatureSelected(id: number, selected: boolean): void {
    if (!this.map.getSource(SOURCE.lakes)) return;
    try {
      this.map.setFeatureState(
        { source: SOURCE.lakes, sourceLayer: "lakes", id },
        { selected },
      );
    } catch (err) {
      console.warn("[map] setFeatureState failed", err);
    }
  }

  /** Points the three selection layers at one lake, or at nothing. */
  private setHighlight(lake: Lake | null): void {
    const filter = lake ? selectionFilter(lake.id, lake.name, lake.county) : NO_FEATURE;
    for (const id of [LAYER.lakesSelectedFill, LAYER.lakesSelectedLine, LAYER.lakesGlow]) {
      if (this.map.getLayer(id)) this.map.setFilter(id, filter);
    }
  }

  /**
   * Frames the lake and marks it selected. Zoom is capped so a pond does not slam the
   * camera to z17; padding keeps it out from under the sheet.
   */
  select(lake: Lake): void {
    if (this.selectedId != null && this.selectedId !== lake.id) {
      this.setFeatureSelected(this.selectedId, false);
    }
    this.selectedId = lake.id;
    this.selectedLake = lake;
    this.setFeatureSelected(lake.id, true);
    this.setHighlight(lake);
    this.startPulse();

    const bounds: LngLatBoundsLike = [
      [lake.bbox[0], lake.bbox[1]],
      [lake.bbox[2], lake.bbox[3]],
    ];
    this.map.fitBounds(bounds, {
      padding: this.cb.padding(),
      maxZoom: MAX_SELECT_ZOOM,
      duration: 600,
    });
    this.syncFallbackMarker(lake);
    // Re-check once the camera has settled and again once tiles have actually painted:
    // queryRenderedFeatures only sees what is on screen right now.
    this.map.once("moveend", () => this.syncFallbackMarker(lake));
    this.map.once("idle", () => {
      if (this.selectedId === lake.id) this.syncFallbackMarker(lake);
    });
  }

  clearSelection(): void {
    if (this.selectedId != null) this.setFeatureSelected(this.selectedId, false);
    this.selectedId = null;
    this.selectedLake = null;
    this.setHighlight(null);
    this.stopPulse();
    this.removeMarker();
  }

  /**
   * lakes.pmtiles starts at z6, and small polygons can still drop out of a tile. If the
   * selected lake is not actually rendered, drop a marker at its centroid so the tap and
   * the sheet still line up with something on screen.
   */
  private syncFallbackMarker(lake: Lake): void {
    const rendered = this.map.getLayer(LAYER.lakesSelectedFill)
      ? this.map.queryRenderedFeatures({ layers: [LAYER.lakesSelectedFill] }).length > 0
      : false;
    if (rendered) {
      this.removeMarker();
      return;
    }
    const color = VERDICT_COLORS[this.theme][lake.verdict];
    if (!this.marker) {
      const node = document.createElement("div");
      node.className = "lake-marker";
      // setLngLat before addTo: Marker._update() reads the position on attach.
      this.marker = new maplibregl.Marker({ element: node, anchor: "center" })
        .setLngLat([lake.lon, lake.lat])
        .addTo(this.map);
    } else {
      this.marker.setLngLat([lake.lon, lake.lat]);
    }
    this.marker.getElement().style.setProperty("--marker-color", color);
  }

  private removeMarker(): void {
    this.marker?.remove();
    this.marker = null;
  }

  // -- selection pulse -----------------------------------------------------

  /**
   * A breathing halo, three 2.4 s cycles and then a steady glow. The only per-frame work
   * is one scalar setPaintProperty, throttled to about 20 Hz.
   *
   * It stops on purpose rather than looping forever: an endless paint loop keeps the map
   * from ever firing `idle`, which the fallback-marker check and the phase 2 wind fetch
   * both wait on, and it burns battery in the air for no extra information.
   */
  private startPulse(): void {
    if (!this.map.getLayer(LAYER.lakesGlow)) return;
    this.stopPulse(SETTLED_GLOW);
    if (matchMedia("(prefers-reduced-motion: reduce)").matches) return;

    this.pulseStart = performance.now();
    const step = (now: number) => {
      const elapsed = now - this.pulseStart;
      if (elapsed >= PULSE_MS * PULSE_CYCLES) {
        this.stopPulse(SETTLED_GLOW);
        return;
      }
      this.pulseHandle = requestAnimationFrame(step);
      if (now - this.lastPulseWrite < 50) return;
      this.lastPulseWrite = now;
      if (!this.map.getLayer(LAYER.lakesGlow)) return;
      const phase = (elapsed % PULSE_MS) / PULSE_MS;
      const opacity = 0.14 + 0.3 * (0.5 - 0.5 * Math.cos(phase * Math.PI * 2));
      this.map.setPaintProperty(LAYER.lakesGlow, "line-opacity", opacity);
    };
    this.pulseHandle = requestAnimationFrame(step);
  }

  /** Cancels the animation and leaves the halo at `opacity`. */
  private stopPulse(opacity = 0): void {
    if (this.pulseHandle != null) cancelAnimationFrame(this.pulseHandle);
    this.pulseHandle = null;
    if (this.map.getLayer(LAYER.lakesGlow)) {
      this.map.setPaintProperty(LAYER.lakesGlow, "line-opacity", opacity);
    }
  }

  /** Phase 2 replaces this with follow-me. For now it returns to the data's home view. */
  recenter(): void {
    const view = this.available.lakes ? FIXTURE_VIEW : HOME_VIEW;
    this.map.easeTo({ center: view.center, zoom: view.zoom, duration: 500 });
  }

  resize(): void {
    this.map.resize();
  }
}
