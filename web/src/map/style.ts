import { layers as basemapLayers, namedFlavor } from "@protomaps/basemaps";
import type {
  ExpressionSpecification,
  LayerSpecification,
  StyleSpecification,
} from "maplibre-gl";
import { BASEMAP_ASSETS, DATA_FILES, USABLE_WATER_MIN_ZOOM } from "../config";
import type { ResolvedTheme } from "../ui/theme";
import type { Verdict } from "../types";

export const SOURCE = {
  basemap: "protomaps",
  lakes: "lakes",
  usableWater: "usable_water",
  overlays: "overlays",
} as const;

export const LAYER = {
  lakesFill: "lakes-fill",
  lakesOutline: "lakes-outline",
  lakesSelectedFill: "lakes-selected-fill",
  lakesSelectedLine: "lakes-selected-outline",
  lakesGlow: "lakes-selected-glow",
  usableWater: "usable-water-fill",
  federalFill: "overlay-federal-fill",
  federalLine: "overlay-federal-line",
  airspaceLine: "overlay-airspace-line",
  bas: "overlay-bas",
  airports: "overlay-airports",
  airportLabels: "overlay-airport-labels",
} as const;

/** Toggleable overlay groups. `bas` is the only one on by default (design.md 7.2). */
export const OVERLAY_GROUPS = {
  federal: [LAYER.federalFill, LAYER.federalLine],
  airspace: [LAYER.airspaceLine],
  bas: [LAYER.bas],
  airports: [LAYER.airports, LAYER.airportLabels],
} as const;
export type OverlayGroup = keyof typeof OVERLAY_GROUPS;

export const VERDICT_COLORS: Record<ResolvedTheme, Record<Verdict, string>> = {
  light: {
    restricted: "#d92d20",
    conditional: "#c2700a",
    clear: "#12783c",
    unknown: "#6b7280",
  },
  dark: {
    restricted: "#ff7a6e",
    conditional: "#f5a524",
    clear: "#3ecf7a",
    unknown: "#9aa4b2",
  },
};

export interface AvailableTiles {
  basemap: boolean;
  lakes: boolean;
  usableWater: boolean;
  overlays: boolean;
}

/** `verdict` comes off the tile as a string; unknown/missing falls through to gray. */
function verdictColor(theme: ResolvedTheme): ExpressionSpecification {
  const c = VERDICT_COLORS[theme];
  return [
    "match",
    ["get", "verdict"],
    "restricted",
    c.restricted,
    "conditional",
    c.conditional,
    "clear",
    c.clear,
    c.unknown,
  ];
}

/** Matches nothing; replaced on selection by `selectionFilter`. */
export const NO_FEATURE: ExpressionSpecification = ["==", ["id"], -1];

/**
 * Identifies the selected lake in the tiles. The contract puts an `id` property on every
 * lake feature and the source sets `promoteId: "id"`, so `["id"]` is the normal path. Tiles
 * built without that property still have to highlight, so name plus county is accepted as
 * a fallback rather than leaving the selection invisible.
 */
export function selectionFilter(
  id: number,
  name: string | null,
  county: string | null,
): ExpressionSpecification {
  const byId: ExpressionSpecification = ["==", ["id"], id];
  const byProperty: ExpressionSpecification = ["==", ["get", "id"], id];
  if (name == null || county == null) return ["any", byId, byProperty];
  return [
    "any",
    byId,
    byProperty,
    ["all", ["==", ["get", "name"], name], ["==", ["get", "county"], county]],
  ];
}

/**
 * Lake status is a thick outline, not a fill, so the water shape stays readable
 * (design.md 7.1). The fill underneath is deliberately faint.
 *
 * The selected lake is drawn by three filtered layers rather than by feature-state paint
 * expressions. `setFeatureState` is still called on selection (and works when the tiles
 * carry `id`), but the highlight cannot depend on it: tiles built without the `id`
 * property promote to an undefined feature id, and the selection would silently not show.
 */
function lakeLayers(theme: ResolvedTheme): LayerSpecification[] {
  const color = verdictColor(theme);
  return [
    {
      id: LAYER.lakesFill,
      type: "fill",
      source: SOURCE.lakes,
      "source-layer": "lakes",
      paint: { "fill-color": color, "fill-opacity": 0.09 },
    },
    {
      id: LAYER.lakesOutline,
      type: "line",
      source: SOURCE.lakes,
      "source-layer": "lakes",
      layout: { "line-join": "round", "line-cap": "round" },
      paint: {
        "line-color": color,
        "line-width": ["interpolate", ["linear"], ["zoom"], 6, 1.6, 10, 3, 14, 5],
        "line-opacity": 0.95,
      },
    },
    {
      id: LAYER.lakesSelectedFill,
      type: "fill",
      source: SOURCE.lakes,
      "source-layer": "lakes",
      filter: NO_FEATURE,
      paint: { "fill-color": color, "fill-opacity": 0.16 },
    },
    {
      id: LAYER.lakesSelectedLine,
      type: "line",
      source: SOURCE.lakes,
      "source-layer": "lakes",
      filter: NO_FEATURE,
      layout: { "line-join": "round", "line-cap": "round" },
      paint: {
        "line-color": color,
        "line-width": ["interpolate", ["linear"], ["zoom"], 6, 3.5, 10, 6, 14, 10],
      },
    },
    {
      // Animated halo. The filter is swapped on selection; the per-frame update is a single
      // scalar opacity, the cheapest thing MapLibre will repaint.
      id: LAYER.lakesGlow,
      type: "line",
      source: SOURCE.lakes,
      "source-layer": "lakes",
      filter: NO_FEATURE,
      layout: { "line-join": "round", "line-cap": "round" },
      paint: {
        "line-color": color,
        "line-width": ["interpolate", ["linear"], ["zoom"], 6, 9, 10, 15, 14, 24],
        "line-opacity": 0,
        "line-blur": 3,
      },
    },
  ];
}

function overlayLayers(theme: ResolvedTheme): LayerSpecification[] {
  const dark = theme === "dark";
  const hidden = { visibility: "none" } as const;
  return [
    {
      id: LAYER.federalFill,
      type: "fill",
      source: SOURCE.overlays,
      "source-layer": "federal",
      layout: hidden,
      paint: { "fill-color": dark ? "#8b6cd4" : "#6d4bc0", "fill-opacity": 0.16 },
    },
    {
      id: LAYER.federalLine,
      type: "line",
      source: SOURCE.overlays,
      "source-layer": "federal",
      layout: { ...hidden, "line-join": "round" },
      paint: { "line-color": dark ? "#a48cec" : "#5b3ba8", "line-width": 2 },
    },
    {
      id: LAYER.airspaceLine,
      type: "line",
      source: SOURCE.overlays,
      "source-layer": "airspace",
      layout: { ...hidden, "line-join": "round" },
      paint: {
        "line-color": dark ? "#6fb4ff" : "#1f5fbf",
        "line-width": 2,
        "line-dasharray": [3, 2],
      },
    },
    {
      id: LAYER.bas,
      type: "circle",
      source: SOURCE.overlays,
      "source-layer": "bas",
      paint: {
        "circle-radius": ["interpolate", ["linear"], ["zoom"], 8, 3, 14, 7],
        "circle-color": dark ? "#4cd3d3" : "#0d7f88",
        "circle-stroke-width": 1.5,
        "circle-stroke-color": dark ? "#0d1117" : "#ffffff",
      },
    },
    {
      id: LAYER.airports,
      type: "circle",
      source: SOURCE.overlays,
      "source-layer": "airports",
      layout: hidden,
      paint: {
        "circle-radius": ["interpolate", ["linear"], ["zoom"], 8, 4, 14, 8],
        "circle-color": dark ? "#d0d7de" : "#24292f",
        "circle-stroke-width": 1.5,
        "circle-stroke-color": dark ? "#0d1117" : "#ffffff",
      },
    },
    {
      id: LAYER.airportLabels,
      type: "symbol",
      source: SOURCE.overlays,
      "source-layer": "airports",
      minzoom: 9,
      layout: {
        ...hidden,
        "text-field": ["coalesce", ["get", "ident"], ["get", "name"]],
        "text-font": ["Noto Sans Regular"],
        "text-size": 12,
        "text-offset": [0, 1.1],
        "text-anchor": "top",
      },
      paint: {
        "text-color": dark ? "#d0d7de" : "#24292f",
        "text-halo-color": dark ? "#0d1117" : "#ffffff",
        "text-halo-width": 1.5,
      },
    },
  ];
}

/**
 * Builds the whole style. Sources whose pmtiles archive returned 404 are left out entirely
 * along with their layers, so a partial data directory renders what it has.
 */
export function buildStyle(theme: ResolvedTheme, available: AvailableTiles): StyleSpecification {
  const flavor = namedFlavor(theme === "dark" ? "dark" : "light");
  const sources: StyleSpecification["sources"] = {};
  const styleLayers: LayerSpecification[] = [];

  if (available.basemap) {
    sources[SOURCE.basemap] = {
      type: "vector",
      url: `pmtiles://${DATA_FILES.basemap}`,
      attribution:
        '<a href="https://protomaps.com">Protomaps</a> &copy; <a href="https://openstreetmap.org">OpenStreetMap</a>',
    };
    styleLayers.push(...basemapLayers(SOURCE.basemap, flavor, { lang: "en" }));
  } else {
    // Without a basemap the verdict outlines still need something to sit on.
    styleLayers.push({
      id: "background",
      type: "background",
      paint: { "background-color": flavor.background },
    });
  }

  if (available.usableWater) {
    sources[SOURCE.usableWater] = {
      type: "vector",
      url: `pmtiles://${DATA_FILES.usableWater}`,
    };
    styleLayers.push({
      id: LAYER.usableWater,
      type: "fill",
      source: SOURCE.usableWater,
      "source-layer": "usable_water",
      minzoom: USABLE_WATER_MIN_ZOOM,
      layout: { visibility: "none" },
      paint: {
        "fill-color": theme === "dark" ? "#7fd3ff" : "#3aa8e0",
        "fill-opacity": 0.22,
      },
    });
  }

  if (available.lakes) {
    sources[SOURCE.lakes] = {
      type: "vector",
      url: `pmtiles://${DATA_FILES.lakes}`,
      // Contract: the tile property `id` is the stable lake id.
      promoteId: "id",
    };
    styleLayers.push(...lakeLayers(theme));
  }

  if (available.overlays) {
    sources[SOURCE.overlays] = {
      type: "vector",
      url: `pmtiles://${DATA_FILES.overlays}`,
    };
    styleLayers.push(...overlayLayers(theme));
  }

  return {
    version: 8,
    glyphs: BASEMAP_ASSETS.glyphs,
    sprite: `${BASEMAP_ASSETS.sprite}${theme}`,
    sources,
    layers: styleLayers,
  };
}
