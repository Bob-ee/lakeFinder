# Where on the water: a wave field for every water body

Added 2026-09-19 from Bobby's feedback on the first build, then generalized the same day at his direction: whatever
is built for Lake St. Clair must apply to every lake, and the app is headed for a nationwide release to the seaplane
community (`docs/nationwide.md`). Status: **built 2026-09-19** for Michigan (sections 3-5, steps 1-4; step 5 partly: all five Great Lakes are in, Huron
and Superior clipped at the border). The authoritative spec is `docs/data-contract.md` "Wave field"; this file is the
reasoning. What changed while building is in section 7.

## 1. What Bobby asked for

He flies mostly around **Lake St. Clair**, and the question there is not "is this lake good" but **"where in the lake
do I go"**. His examples: Anchor Bay is a lot smoother than the main lake depending on the wind; Muscamoot Bay is
almost always glassy. The first build had no big water at all (the Michigan hydrography layer is inland only: no
Great Lakes, no Lake St. Clair, no Detroit, St. Clair, or St. Marys River), and its wave model gives one number per
lake.

Two constraints on the answer:

- **It must work on every water body**, not be a St. Clair special case. Houghton, Torch, Burt, and any reservoir in
  any state have sheltered ends too.
- **Nothing hand-drawn per lake.** Hand-made area polygons do not scale past one pilot's home water. They may exist
  as an optional override, never as the mechanism.

## 2. Why one number per lake is wrong

The first build takes fetch from `extent_by_bearing`: the longest line through the polygon along the wind. That is
the wave height at the downwind shore, the worst spot on the lake, and it is the same for every spot. What Bobby is
describing is two effects the model has to carry:

1. **Shelter depends on where you are and where the wind is from.** Fetch must be measured *from a spot on the water,
   upwind, to the first land*, across the whole water body including its islands.
2. **Depth limits waves.** Lake St. Clair averages about 11 ft and Muscamoot Bay is a few feet deep behind the delta
   marsh islands. Deep-water SPM overstates shallow water; the shallow-water SPM 1984 forms (eq. 3-39, 3-40) take
   depth as an input. Depth is unknown for most lakes in the country, so it must be optional: without it the model
   falls back to deep water, which is the conservative direction.

## 3. The general mechanism

**Sample points.** The pipeline scatters sample points over each water body's usable water (the polygon less the
shore buffer). One point for small water, more as area and shoreline complexity grow, capped per water body
(working numbers: 1 point under 100 acres, a grid giving up to ~60 points for ordinary lakes, up to ~400 for water
over 10,000 acres; tune on St. Clair so Muscamoot and Anchor Bay each get several).

**Per point, computed once in the pipeline:**

- `fetch_by_bearing[16]`: ray cast upwind to first land across the whole polygon, averaged over a small arc as SPM
  recommends. Islands are land.
- `run_by_bearing[16]`: the straight usable line through the point along the bearing, so a sheltered cove too small
  to land in is not recommended.
- `depth_ft`: optional, from bathymetry where a source exists.
- `label`: the nearest named feature inside the same water body (GNIS bays, channels, harbors: "Anchor Bay",
  "Muscamoot Bay") when one is close, otherwise a plain descriptor from the geometry ("north end", "west shore",
  "open middle"). Names are labels on computed results, never the unit of computation.

**Per forecast wind, computed in the api (briefing) and in the client (selected lake):** wave height at every sample
point from its fetch in the wind's bin, its depth if known, and the wind at the water body. From that field:

- the **calmest usable region** (points meeting `min_run_ft` into the wind), its label, and its wave height;
- the open-water figure for contrast;
- a lake's briefing row becomes "Cass Lake: west end 3 in, open middle 7 in", and lakes rank by their best usable
  region rather than their worst spot;
- on the map, the selected water body shows the field as a shaded overlay for the current or a chosen wind.

**Small lakes stay conservative.** With a single sample point the fetch is the full `extent_by_bearing`, exactly as
today, because on a 40-acre lake the touchdown zone is the whole lake.

**Pilot pins.** A saved landing spot snaps to the nearest sample point, so "my spot in Muscamoot" gets its own line
in the briefing. A **home water** setting keeps one water body's regions in every briefing regardless of `radius_nm`.

**Second opinions where they exist.** Real wave forecasts (Open-Meteo marine, NWS nearshore marine zones) and nearby
observations (METAR, buoys and shore stations in season) are shown beside the computed numbers, never instead of
them, since they exist only for the Great Lakes and coasts.

## 4. What this needs

- A water polygon for big water, with the far (Ontario) shore and the islands, since rays need the whole shoreline
  even where only the near side is scored. National source preferred (USGS NHD / 3DHP); recon is checking.
- `kind` on water bodies: `lake`, `river` (in progress), then `great_lake` / `connecting_water`, all flowing through
  the same stages.
- A new pack file for the points (compact binary; a few MB for Michigan), specified in `docs/data-contract.md`
  before any code.
- Rules side unchanged in kind: local watercraft controls, airspace (Selfridge Class D over Anchor Bay), federal
  units, and the international boundary (drawn; the far side is a customs matter) attach to water bodies as today.
  Restrictions that cover only part of a big water body need a geometry of their own eventually; until then they
  attach with the same `reach_unresolved` style notice rivers use.

## 5. Order of work

1. Rivers (running now) and the source decision from recon.
2. Lake St. Clair on the map as a water body: polygon, verdict inputs, overlays.
3. Sample points, fetch rays, labels in the pipeline for **all** water bodies; contract first.
4. Wave field in the api briefing (shallow-water SPM, region rows, home water) and in the client (overlay, region
   rows in the sheet).
5. The other Great Lakes waters through the same path.

## 6. Open questions

- Sources are settled (`docs/gis-sources.md`, 2026-09-19 addendum): Michigan hydro layer 13 for the St. Clair polygon
  (NHD has a hole where the eastern Flats should be), GNIS points for names, NCEI bathymetry for the Great Lakes and
  GLOBathy elsewhere, buoy 45147 and KMTC as ground truth. Trial rays from Muscamoot, Anchor Bay, and mid-lake
  reproduce what Bobby described, so the mechanism is sound. Still open: the NCEI grid cell size for St. Clair.
- Whether the Ontario half of St. Clair is scored or only drawn.
- Sample-point density and the cap; decide from St. Clair and one mid-size inland lake.

## 7. What changed while building (2026-09-19)

- **Clip edges.** Rays leaving through an artificial polygon edge count as open water. The first test (a boundary
  segment over 1,500 m) missed Lake Huron's densified 104 km border line; the second (vertices within 60 m of a 2 km
  chord) caught it but also caught ordinary straight shore on Torch and Houghton lakes and painted 40 inch bands on
  them. Drawn lines sit 0 m off their chord and natural shore 40 m or more, so the tolerance is 10 m.
- **Density.** 400 points put 1.7 km between samples on Lake St. Clair and gave the small Flats bays nothing. Water
  of 100,000 acres or more aims for 2,000 points (St. Clair: about 1,950 at 750 m).
- **Names.** GNIS gives a point and no extent, so a name's reach is measured from the width of the water at the
  name; a name needs at least 2 points and a compass sector at least 3. GNIS `Channel` names label only rivers and
  connecting waters: on Lake St. Clair they are fifty marsh cuts that buried the bays.
- **True bearings.** The trial table in `docs/gis-sources.md` was cast on grid north. Convergence at St. Clair is 2.3°,
  and single rays are sensitive to it (Anchor Bay's south ray 39 km grid, 11 km true); the 5-ray arc mean smooths it.
  The shipped field uses true bearings, as the wind does.
- **Depth only on big water.** A DEM cell under an inland lake is the lake's surface elevation, not its depth.
- **Overlaps.** The state's Great Lakes polygons swallow inland water (Torch Lake 97% inside "Lake Michigan") and run
  up the Detroit River; inland water is subtracted from big water and connecting waters from the Great Lakes.
- **Crosswind on water** is scored only when the run into the wind is too short and the long axis would have to be
  used; scoring it always made Lake St. Clair "marginal, crosswind" against its 28 mile chord.
- **First real output** (KONZ, wind 072/12 G14): Lake St. Clair regions from Campau / Fisher / Little Muscamoot Bay
  5 in, Big Muscamoot 8, Anchor Bay 11, L'anse Creuse 13, to the open middle 18 and the downwind west end 19.
