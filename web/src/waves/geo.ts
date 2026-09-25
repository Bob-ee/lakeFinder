import type { Feature, FeatureCollection } from "geojson";
import { metresToInches, waveHeight, windBin } from "@rules/waves/index.js";
import { FETCH_UNIT_M, RUN_UNIT_FT } from "@rules/waves/index.js";
import { waveBand } from "./ramp";
import type { WaveState } from "./section";

/**
 * Turns a wave field plus a wind into the two GeoJSON collections the map draws: one point
 * per sample with its own wave height, and one label point per region.
 *
 * The geometry never changes for a water body, so the features are built once per lake and
 * only their `hs_in` property is rewritten when the wind moves. That keeps a 400-point lake
 * at a few hundred property writes per wind change instead of four hundred object
 * allocations, which is what an older iPad notices while a finger is on the speed slider.
 */

export interface WaveGeo {
  points: FeatureCollection;
  regions: FeatureCollection;
}

/** Enough lakes that flipping between a few keeps its cache, small enough to stay tiny. */
const MAX_CACHED = 8;
const cache = new Map<number, Feature[]>();

export function waveGeo(state: WaveState): WaveGeo {
  const { lake, field, wind, minRunFt, limits } = state;
  const bin = windBin(wind.dir);
  const runBin = bin % 8;

  let features = cache.get(lake.id);
  if (!features || features.length !== field.points.length) {
    features = field.points.map((p) => ({
      type: "Feature" as const,
      geometry: { type: "Point" as const, coordinates: [p.lon, p.lat] },
      properties: { hs_in: 0, usable: true, band: "ok", text: "0" },
    }));
    if (cache.size >= MAX_CACHED) cache.clear();
    cache.set(lake.id, features);
  }

  for (let i = 0; i < field.points.length; i++) {
    const p = field.points[i]!;
    const depth = p.depth_dm === 0xffff ? null : p.depth_dm / 10;
    const { hsM } = waveHeight(wind.kt, p.fetch[bin]! * FETCH_UNIT_M, depth);
    const props = features[i]!.properties!;
    const hsIn = metresToInches(hsM);
    // A sheltered spot you cannot get out of is not a landing spot; it is drawn as a hollow
    // ring rather than dropped, because a pilot should still see that it was measured.
    const usable = p.run[runBin]! * RUN_UNIT_FT >= minRunFt;
    props["hs_in"] = hsIn;
    props["usable"] = usable;
    props["band"] = waveBand(hsIn, usable, limits);
    props["text"] = String(hsIn);
  }

  // Regions arrive calm to rough, so the index is also the label's placement priority:
  // when two labels collide the calmer region keeps its place.
  const regions: Feature[] = [];
  for (const [rank, region] of state.regions.entries()) {
    if (region.point == null) continue;
    const p = field.points[region.point];
    if (!p) continue;
    regions.push({
      type: "Feature",
      geometry: { type: "Point", coordinates: [p.lon, p.lat] },
      properties: {
        label: region.label,
        hs_in: region.hs_in,
        text: `${region.label} ${region.hs_in} in`,
        band: waveBand(region.hs_in, true, limits),
        rank,
      },
    });
  }

  return {
    points: { type: "FeatureCollection", features },
    regions: { type: "FeatureCollection", features: regions },
  };
}
