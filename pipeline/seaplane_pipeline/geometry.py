"""Stage 4 (`geometry`): per-lake measurements from the hydrography polygons.

Input:  `data/cache/hydrography_polygons.geojson` (+ counties / minor civil divisions).
Output: `data/work/lakes.parquet` and `data/work/usable_water.parquet` (GeoParquet, WGS84).

What it keeps
-------------
`TYPE in ('lake', 'river')` (the layer's remaining value is `swamp`), recorded as the contract's
`kind` column. `NAME` is space-padded in the source, so it is stripped and turned into `None` when
blank. Of the 57,780 lake polygons and 2,043 river polygons we keep **every named one plus unnamed
ones of at least 20 acres**; the rest are sub-20-acre unnamed ponds and river slivers that would
bloat `index.json` past its 5 MB target without ever being landable or searchable. The threshold is
`--min-unnamed-acres`. Rivers keep the same filter on purpose even though their median named polygon
is only 4.8 acres: a small polygon is still the thing a DNR river rule has to attach to, and dropping
it would silently drop the rule (`kind: "river"` polygons are 1,780 of the ~12,560 kept rows).

Everything the layer types `lake` stays `kind: "lake"`, including the ~513 impoundments it names
after a river ("Au Sable River", "Cornwall Creek Flooding"); `match` treats those as river-named lake
polygons rather than reclassifying them.

`counties` / `townships` list every county and minor civil division the polygon *intersects*, not the
one holding its centroid: a river polygon runs through several of both, and the matcher narrows a
river rule to a reach with them. `county` / `township` stay centroid-based (they are what the client
shows, and changing them would move lake ids, which hash `name_norm|lat|lon`).

Measurement CRS is EPSG:3078 (NAD83 / Michigan Oblique Mercator, metres) -- the layer's own native
projection. Centroids, bboxes and stored geometry are WGS84.

Longest chord
-------------
The longest straight line fully inside the polygon, computed on the largest polygon part:
the boundary is simplified to at most `--max-vertices` (200) points, all vertex pairs are ranked by
length descending, and the first pair whose segment is `covers()`-ed by the *unsimplified* polygon
wins. Work is capped per lake (`--max-candidates`, and 300 for lakes over 5,000 acres, where the
answer is almost always the first candidate). If no candidate is fully inside (rare, heavily
concave lakes), the top candidates are intersected with the polygon and the longest interior piece
is used, which is still a true interior chord.

That vertex-pair search is exact enough for a lake, where the answer is a near-diameter, but it
collapses on a **river**: 200 vertices out of the Muskegon River's 13,774 leave no pair lying along
any one straight reach, so every long candidate crosses a bend, fails `covers`, and the fallback
returns a short interior piece (measured: 1,871 ft against a real 4,643 ft reach; the big rivers came
in 2-4x short). River polygons therefore also get `sweep_longest_reach`, a rotating scan-line sweep
that finds the longest straight *reach* directly, and `chord_ft` is the longer of the two -- the
vertex-pair answer still wins on the wide, lake-like river polygons where a corner-to-corner chord
beats any axis-aligned run. Both are true interior segments either way: the scan line is clipped to
the polygon's own interior, and `reduce_ring` only ever drops boundary vertices, never invents ones
that could put a chord over land.
"""
from __future__ import annotations

import logging
import time

import geopandas as gpd
import numpy as np
import pandas as pd
import shapely
import shapely.affinity
from pyproj import Geod, Proj, Transformer
from shapely.geometry import LineString, MultiPolygon, Polygon

from . import gis, ids
from .config import Config

log = logging.getLogger(__name__)

MEASURE_CRS = "EPSG:3078"
WGS84 = "EPSG:4326"
M2_PER_ACRE = 4046.8564224
FT_PER_M = 3.280839895013123
SHORE_BUFFER_M = 30.48  # 100 ft statewide slow-no-wake buffer
BIG_LAKE_ACRES = 5000.0
BIG_LAKE_CANDIDATES = 300

EXTENT_BEARING_BINS = 16
EXTENT_MIN_CHORD_FT = 1000.0
EXTENT_SAMPLES = 40

KINDS = ("lake", "river")

#: Scan-line sweep (rivers only). Orientation is searched at `SWEEP_STEP_DEG`, then the best few
#: orientations are refined, because a 20,000 ft reach in a 200 ft channel is missed by 1.5 deg.
SWEEP_STEP_DEG = 3.0
SWEEP_REFINE_TOP = 3
SWEEP_REFINE_DEG = 1.5
SWEEP_REFINE_STEP_DEG = 0.25
#: Scan lines are spaced at half the polygon's mean width (2 * area / perimeter) so at least one
#: line runs near the centre of any reach, with a floor and a cap to bound the work.
SCAN_SPACING_FRACTION = 0.5
SCAN_MIN_SPACING_M = 8.0
SCAN_MIN_LINES = 40
SCAN_MAX_LINES = 3000

_GEOD = Geod(ellps="WGS84")
_MEASURE_PROJ = Proj(MEASURE_CRS)


def add_args(sp) -> None:
    sp.add_argument("--min-unnamed-acres", type=float, default=20.0, help="Keep unnamed lakes this big")
    sp.add_argument("--max-vertices", type=int, default=200, help="Boundary vertices used for the chord")
    sp.add_argument("--max-candidates", type=int, default=1000, help="Chord candidate pairs per lake")
    sp.add_argument("--limit", type=int, help="Process only the first N lakes (development)")
    sp.add_argument("--input", help="Override the hydrography GeoJSON path")


# --- chord -------------------------------------------------------------------


def _present(geoms) -> np.ndarray:
    """Boolean mask of non-null, non-empty geometries (avoids GeoSeries.notna()'s empty-geometry warning)."""
    return np.fromiter((g is not None and not g.is_empty for g in geoms), dtype=bool, count=len(geoms))


def largest_part(geom) -> Polygon:
    if isinstance(geom, MultiPolygon):
        return max(geom.geoms, key=lambda g: g.area)
    return geom


def reduce_ring(coords: np.ndarray, max_pts: int) -> np.ndarray:
    """Cut a boundary ring down to at most `max_pts` points.

    Douglas-Peucker with an adaptively doubled tolerance first (it keeps the corners that matter for
    a longest chord), then an even stride as a hard backstop.
    """
    if len(coords) <= max_pts:
        return coords
    line = LineString(coords)
    tol = max(line.length / (max_pts * 8.0), 1e-6)
    for _ in range(12):
        simplified = shapely.get_coordinates(line.simplify(tol))
        if len(simplified) <= max_pts:
            return simplified
        tol *= 2.0
    stride = int(np.ceil(len(coords) / max_pts))
    return coords[::stride]


def _candidate_pairs(pts: np.ndarray, max_candidates: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Vertex-pair indices and lengths, longest first, capped at `max_candidates`."""
    iu, ju = np.triu_indices(len(pts), k=1)
    d = np.hypot(pts[iu, 0] - pts[ju, 0], pts[iu, 1] - pts[ju, 1])
    if len(d) > max_candidates:
        top = np.argpartition(d, -max_candidates)[-max_candidates:]
    else:
        top = np.arange(len(d))
    order = top[np.argsort(-d[top])]
    return iu[order], ju[order], d[order]


def longest_chord(poly, max_vertices: int = 200, max_candidates: int = 1000) -> tuple[float, tuple, tuple]:
    """Return (length_m, (x1, y1), (x2, y2)) for the longest segment inside `poly` (EPSG:3078)."""
    part = largest_part(poly)
    if part.is_empty:
        return 0.0, (0.0, 0.0), (0.0, 0.0)
    pts = reduce_ring(shapely.get_coordinates(part.exterior), max_vertices)
    if len(pts) < 2:
        return 0.0, (0.0, 0.0), (0.0, 0.0)
    i_idx, j_idx, dists = _candidate_pairs(pts, max_candidates)
    if len(i_idx) == 0:
        return 0.0, (0.0, 0.0), (0.0, 0.0)

    shapely.prepare(part)
    for start in range(0, len(i_idx), 64):
        sl = slice(start, start + 64)
        lines = shapely.linestrings(
            np.stack([pts[i_idx[sl]], pts[j_idx[sl]]], axis=1).reshape(-1, 2),
            indices=np.repeat(np.arange(len(i_idx[sl])), 2),
        )
        inside = shapely.covers(part, lines)
        hit = np.flatnonzero(inside)
        if len(hit):
            k = start + hit[0]
            return float(dists[k]), tuple(pts[i_idx[k]]), tuple(pts[j_idx[k]])

    # Nothing was fully inside: take the longest interior piece of the best candidates instead.
    best = (0.0, (0.0, 0.0), (0.0, 0.0))
    for k in range(min(32, len(i_idx))):
        pieces = part.intersection(LineString([pts[i_idx[k]], pts[j_idx[k]]]))
        for piece in getattr(pieces, "geoms", [pieces]):
            if isinstance(piece, LineString) and piece.length > best[0]:
                c = shapely.get_coordinates(piece)
                best = (float(piece.length), tuple(c[0]), tuple(c[-1]))
        if best[0] >= dists[min(k + 1, len(dists) - 1)]:
            break  # no later candidate can beat this
    return best


# --- scan-line runs ------------------------------------------------------------


def part_edges(part) -> np.ndarray:
    """`(N, 2, 2)`: every boundary segment of `part` (exterior ring and holes) as (start, end)."""
    rings = [part.exterior, *part.interiors]
    segs = []
    for ring in rings:
        coords = np.asarray(ring.coords, dtype=float)
        if len(coords) < 2:
            continue
        segs.append(np.stack([coords[:-1], coords[1:]], axis=1))
    if not segs:
        return np.empty((0, 2, 2), dtype=float)
    return np.concatenate(segs, axis=0)


def rotate_points(points: np.ndarray, deg: float, origin: np.ndarray) -> np.ndarray:
    """Counter-clockwise rotation about `origin`, matching `shapely.affinity.rotate`'s convention."""
    a = np.radians(deg)
    ca, sa = np.cos(a), np.sin(a)
    d = points - origin
    return np.stack([d[..., 0] * ca - d[..., 1] * sa, d[..., 0] * sa + d[..., 1] * ca], axis=-1) + origin


def scan_spacing(part) -> float:
    """Scan-line spacing for `part`: half its mean width (`2 * area / perimeter`), floored."""
    perimeter = float(part.length)
    width = 2.0 * float(part.area) / perimeter if perimeter > 0 else 0.0
    return max(width * SCAN_SPACING_FRACTION, SCAN_MIN_SPACING_M)


def scan_longest_run(edges: np.ndarray, spacing: float, max_lines: int = SCAN_MAX_LINES):
    """Longest horizontal interior segment over evenly spaced scan lines. `(length, x1, x2, y)`.

    A classic even-odd scan conversion, vectorized: every boundary segment is assigned the scan
    lines it spans (half-open in y, so a shared vertex is counted once), the crossing abscissae are
    sorted per line, and consecutive pairs starting at an even position are interior. Cost is
    O(crossings), not O(edges x lines), which is what makes a 14,000-vertex river polygon cheap.
    """
    if len(edges) == 0:
        return 0.0, 0.0, 0.0, 0.0
    y1, y2 = edges[:, 0, 1], edges[:, 1, 1]
    ylo, yhi = np.minimum(y1, y2), np.maximum(y1, y2)
    lo, hi = float(ylo.min()), float(yhi.max())
    if not np.isfinite(lo) or not np.isfinite(hi) or hi <= lo:
        return 0.0, 0.0, 0.0, 0.0
    n = int(np.clip(np.ceil((hi - lo) / max(spacing, 1e-9)), SCAN_MIN_LINES, max_lines))
    ys = lo + (np.arange(n) + 0.5) * (hi - lo) / n

    first = np.searchsorted(ys, ylo, side="left")
    last = np.searchsorted(ys, yhi, side="left")
    counts = np.maximum(last - first, 0)
    total = int(counts.sum())
    if total == 0:
        return 0.0, 0.0, 0.0, 0.0
    edge_idx = np.repeat(np.arange(len(edges)), counts)
    base = np.repeat(np.cumsum(counts) - counts, counts)
    line_idx = np.repeat(first, counts) + (np.arange(total) - base)

    e = edges[edge_idx]
    yv = ys[line_idx]
    x = e[:, 0, 0] + (yv - e[:, 0, 1]) * (e[:, 1, 0] - e[:, 0, 0]) / (e[:, 1, 1] - e[:, 0, 1])

    order = np.lexsort((x, line_idx))
    li, xs = line_idx[order], x[order]
    pos = np.arange(total) - np.searchsorted(li, li, side="left")
    interior = (pos[:-1] % 2 == 0) & (li[:-1] == li[1:])
    if not interior.any():
        return 0.0, 0.0, 0.0, 0.0
    lengths = np.where(interior, xs[1:] - xs[:-1], -1.0)
    k = int(np.argmax(lengths))
    if lengths[k] <= 0:
        return 0.0, 0.0, 0.0, 0.0
    return float(lengths[k]), float(xs[k]), float(xs[k + 1]), float(ys[li[k]])


def sweep_longest_reach(poly, spacing: float | None = None) -> tuple[float, tuple, tuple]:
    """Longest straight interior segment of a sinuous polygon: `(length_m, (x1,y1), (x2,y2))`.

    Rotates the polygon through `SWEEP_STEP_DEG` steps, scan-converts each orientation, then
    refines the best `SWEEP_REFINE_TOP` orientations at `SWEEP_REFINE_STEP_DEG`. Unlike
    `longest_chord` this does not need the endpoints to be boundary vertices, which is the whole
    point on a river: the ends of a reach are wherever the bends cut it off.
    """
    part = largest_part(poly)
    if part.is_empty:
        return 0.0, (0.0, 0.0), (0.0, 0.0)
    edges = part_edges(part)
    if len(edges) == 0:
        return 0.0, (0.0, 0.0), (0.0, 0.0)
    origin = np.asarray(part.centroid.coords[0], dtype=float)
    step = spacing if spacing is not None else scan_spacing(part)

    def measure(angle: float):
        length, x1, x2, y = scan_longest_run(rotate_points(edges, angle, origin), step)
        return length, angle, x1, x2, y

    coarse = [measure(a) for a in np.arange(0.0, 180.0, SWEEP_STEP_DEG)]
    best = max(coarse, key=lambda r: r[0])
    for _, angle, *_ in sorted(coarse, key=lambda r: -r[0])[:SWEEP_REFINE_TOP]:
        for a in np.arange(angle - SWEEP_REFINE_DEG, angle + SWEEP_REFINE_DEG + 1e-9, SWEEP_REFINE_STEP_DEG):
            cand = measure(float(a))
            if cand[0] > best[0]:
                best = cand
    length, angle, x1, x2, y = best
    if length <= 0:
        return 0.0, (0.0, 0.0), (0.0, 0.0)
    ends = rotate_points(np.array([[x1, y], [x2, y]]), -angle, origin)
    if not _covers_segment(part, ends[0], ends[1]):
        # Even-odd scan conversion assumes a valid ring; 11 of the state's river polygons are not.
        # Rather than publish a reach that might cross land, drop back to the vertex-pair chord,
        # which is checked against the polygon itself.
        return 0.0, (0.0, 0.0), (0.0, 0.0)
    return float(length), tuple(ends[0]), tuple(ends[1])


def _covers_segment(part, a: np.ndarray, b: np.ndarray, inset_m: float = 0.01) -> bool:
    """Is the segment inside `part`? Pulled `inset_m` off both ends, which sit on the boundary."""
    d = b - a
    n = float(np.hypot(d[0], d[1]))
    if n <= 2 * inset_m:
        return True
    u = d / n
    return bool(part.covers(LineString([a + u * inset_m, b - u * inset_m])))


def longest_reach(poly, max_vertices: int = 200, max_candidates: int = 1000) -> tuple[float, tuple, tuple]:
    """`longest_chord` for a river: the better of the vertex-pair chord and the scan-line sweep."""
    chord = longest_chord(poly, max_vertices, max_candidates)
    sweep = sweep_longest_reach(poly)
    return sweep if sweep[0] > chord[0] else chord


# --- extent by bearing ---------------------------------------------------------


def meridian_convergence_deg(lon, lat):
    """Angle (deg) from grid north (`MEASURE_CRS`'s +y) to true north at (lon, lat).

    EPSG:3078 (Michigan Oblique Mercator) grid north is only true north on the projection's
    central line; convergence reaches a couple of degrees at the ends of the state. `extent_by_bearing`
    targets a *true* compass bearing, so every rotation below is corrected by this per-lake amount.
    Vectorized: `lon`/`lat` may be scalars or arrays.
    """
    return _MEASURE_PROJ.get_factors(lon, lat, radians=False).meridian_convergence


def _rotate_for_bearing(part, bearing_deg: float, convergence_deg: float):
    """Rotate `part` (in `MEASURE_CRS`) so the true bearing `bearing_deg` aligns with +x."""
    return shapely.affinity.rotate(part, bearing_deg - convergence_deg - 90.0, origin="centroid", use_radians=False)


def _longest_segment(inter) -> float:
    """Longest single `LineString` length in a line/polygon intersection result.

    Islands and concavities can split one sample line into several pieces; the run must be one
    uninterrupted segment, so this takes the longest single piece, never their sum.
    """
    if inter is None or inter.is_empty:
        return 0.0
    parts = shapely.get_parts(inter) if hasattr(inter, "geoms") else (inter,)
    best = 0.0
    for piece in parts:
        if isinstance(piece, LineString) and not piece.is_empty and piece.length > best:
            best = piece.length
    return best


def extent_by_bearing(
    poly, convergence_deg: float, n_samples: int = EXTENT_SAMPLES, spacing_m: float | None = None
) -> list[int]:
    """16 ints (ft): longest straight segment inside `poly` (`MEASURE_CRS`) along each bearing bin.

    Bin `i` is centered on true bearing `i * 22.5` deg (0 = north). A segment has two ends, so only
    8 directions are computed; bin `i` always equals bin `(i + 8) % 16`. Algorithm: rotate the
    polygon (on the largest part, same as `longest_chord`) so the bearing becomes the x axis, sample
    `n_samples` evenly spaced horizontal lines across its height, and take the longest single inside
    segment per line -- not the sum of pieces (see `_longest_segment`).

    `spacing_m` switches to the scan-line implementation with lines spaced that far apart instead of
    `n_samples` across the whole height. Rivers need it: 40 lines across the Muskegon's 40 km bounding
    box is one line every kilometre, and a 60 m wide channel falls between them, so the fixed-count
    sampler reads a long river as nearly dry. It is the same measurement either way -- longest single
    interior run per line -- so lakes keep the cheaper shapely path and their published values.
    """
    part = largest_part(poly)
    if part.is_empty:
        return [0] * EXTENT_BEARING_BINS
    if spacing_m is not None:
        edges = part_edges(part)
        origin = np.asarray(part.centroid.coords[0], dtype=float)
        scan = [
            scan_longest_run(rotate_points(edges, k * 22.5 - convergence_deg - 90.0, origin), spacing_m)[0]
            for k in range(8)
        ]
        ft = [round(v * FT_PER_M) for v in scan]
        return ft + ft
    values = [0.0] * 8
    for k in range(8):
        rotated = _rotate_for_bearing(part, k * 22.5, convergence_deg)
        minx, miny, maxx, maxy = rotated.bounds
        if maxy <= miny:
            continue
        pad = max((maxx - minx) * 0.01, 1.0)
        ys = miny + (np.arange(n_samples) + 0.5) / n_samples * (maxy - miny)
        coords = np.empty((n_samples, 2, 2))
        coords[:, 0, 0] = minx - pad
        coords[:, 0, 1] = ys
        coords[:, 1, 0] = maxx + pad
        coords[:, 1, 1] = ys
        lines = shapely.linestrings(coords)
        inter = shapely.intersection(rotated, lines)
        best = 0.0
        for g in inter:
            best = max(best, _longest_segment(g))
        values[k] = best
    ft = [round(v * FT_PER_M) for v in values]
    return ft + ft


# --- stage -------------------------------------------------------------------


def load_lakes(path, min_unnamed_acres: float) -> gpd.GeoDataFrame:
    """Read the hydrography layer, keep lakes and rivers, clean names, area, size filter, `kind`."""
    gdf = gpd.read_file(path, columns=["NAME", "NAME2", "TYPE", "ACRES"])
    kind = gdf["TYPE"].astype(str).str.strip().str.lower()
    gdf = gdf[kind.isin(KINDS)].copy()
    gdf["kind"] = kind[kind.isin(KINDS)].to_numpy()
    name = gdf["NAME"].astype("string").str.strip()
    gdf["name"] = name.where(name.str.len() > 0, other=pd.NA)
    if gdf.crs is None:
        gdf = gdf.set_crs(WGS84)
    gdf = gdf[_present(gdf.geometry)].copy()
    proj = gdf.geometry.to_crs(MEASURE_CRS)
    gdf["area_acres"] = (proj.area / M2_PER_ACRE).round(2)
    keep = gdf["name"].notna() | (gdf["area_acres"] >= min_unnamed_acres)
    kept = gdf[keep]
    log.info(
        "hydrography: %d polygons -> keeping %d (%d named, %d unnamed >= %.0f acres); dropped %d small unnamed",
        len(gdf),
        len(kept),
        int(gdf["name"].notna().sum()),
        int((keep & gdf["name"].isna()).sum()),
        min_unnamed_acres,
        int((~keep).sum()),
    )
    log.info(
        "  by kind: %s",
        ", ".join(f"{k}={v}" for k, v in sorted(kept["kind"].value_counts().items())),
    )
    return kept.reset_index(drop=True)


def _mcd_labels(mcd: gpd.GeoDataFrame) -> np.ndarray:
    """"Rose Township" for a township row, "City of Wakefield" for a city row."""
    return np.where(
        mcd["TYPE"].astype(str).str.strip().str.lower() == "township",
        mcd["NAME"].astype(str).str.strip() + " Township",
        mcd["LABEL"].astype(str).str.strip(),
    )


def _intersecting(polys: gpd.GeoSeries, areas: gpd.GeoDataFrame, column: str) -> pd.Series:
    """For each polygon, the sorted distinct `column` values of the areas it intersects."""
    left = gpd.GeoDataFrame(geometry=polys.reset_index(drop=True), crs=MEASURE_CRS)
    joined = gpd.sjoin(left, areas[[column, "geometry"]], how="left", predicate="intersects")
    grouped = joined.groupby(level=0)[column].apply(
        lambda s: sorted({str(v).strip() for v in s.dropna() if str(v).strip()})
    )
    return grouped.reindex(range(len(left))).apply(lambda v: v if isinstance(v, list) else [])


def _join_boundaries(
    cfg: Config, centroids: gpd.GeoSeries, polys: gpd.GeoSeries
) -> tuple[pd.Series, pd.Series, pd.Series, pd.Series]:
    """Centroid county/township (what the client shows) plus every county/MCD the polygon touches.

    A river polygon runs through several counties and townships, and its centroid may sit in a
    county the DNR rule never mentions, so `match` narrows river rules with the intersection lists.
    """
    pts = gpd.GeoDataFrame(geometry=centroids, crs=MEASURE_CRS)
    n = len(pts)
    county = pd.Series([None] * n, index=pts.index, dtype=object)
    township = pd.Series([None] * n, index=pts.index, dtype=object)
    counties = pd.Series([[] for _ in range(n)], index=range(n), dtype=object)
    townships = pd.Series([[] for _ in range(n)], index=range(n), dtype=object)

    cpath = gis.find_dataset(cfg, "counties")
    if cpath is None:
        log.warning("counties.geojson missing; county will be null (run `seaplane fetch`)")
    else:
        src = gpd.read_file(cpath).to_crs(MEASURE_CRS)
        src = src.assign(county_name=src["NAME"].astype(str).str.strip().str.title())
        joined = gpd.sjoin(pts, src[["county_name", "geometry"]], how="left", predicate="within")
        joined = joined[~joined.index.duplicated(keep="first")]
        county = joined["county_name"].where(joined["county_name"].notna(), None)
        counties = _intersecting(polys, src, "county_name")

    tpath = gis.find_dataset(cfg, "civil_townships")
    if tpath is None:
        log.info("minor_civil_divisions.geojson missing; township stays null (it is optional)")
    else:
        mcd = gpd.read_file(tpath).to_crs(MEASURE_CRS)
        mcd = mcd.assign(township=_mcd_labels(mcd))
        joined = gpd.sjoin(pts, mcd[["township", "geometry"]], how="left", predicate="within")
        joined = joined[~joined.index.duplicated(keep="first")]
        township = joined["township"].where(joined["township"].notna(), None)
        townships = _intersecting(polys, mcd, "township")
    return county, township, counties, townships


def run(cfg: Config, args) -> int:
    started = time.monotonic()
    path = getattr(args, "input", None) or gis.require_dataset(cfg, "hydrography")
    min_unnamed = getattr(args, "min_unnamed_acres", 20.0)
    max_vertices = getattr(args, "max_vertices", 200)
    max_candidates = getattr(args, "max_candidates", 1000)

    gdf = load_lakes(path, min_unnamed)
    log.info("read + filtered in %.1fs", time.monotonic() - started)

    proj = gdf.geometry.to_crs(MEASURE_CRS)
    centroids = proj.centroid
    to_wgs = Transformer.from_crs(MEASURE_CRS, WGS84, always_xy=True)
    lon, lat = to_wgs.transform(centroids.x.to_numpy(), centroids.y.to_numpy())
    gdf["lat"] = np.round(lat, 6)
    gdf["lon"] = np.round(lon, 6)

    county, township, counties, townships = _join_boundaries(cfg, centroids, proj)
    gdf["county"] = county.to_numpy()
    gdf["township"] = township.to_numpy()
    gdf["counties"] = counties.to_numpy()
    gdf["townships"] = townships.to_numpy()

    if cfg.counties:
        wanted = {c.lower() for c in cfg.counties}
        mask = gdf["county"].astype("string").str.lower().isin(wanted)
        log.info("--county %s: %d of %d lakes", ",".join(sorted(wanted)), int(mask.sum()), len(gdf))
        gdf = gdf[mask.fillna(False)].reset_index(drop=True)
        proj = gdf.geometry.to_crs(MEASURE_CRS)
    if getattr(args, "limit", None):
        gdf = gdf.head(args.limit).reset_index(drop=True)
        proj = gdf.geometry.to_crs(MEASURE_CRS)

    gdf["name_norm"] = [ids.normalize_name(n) if isinstance(n, str) else "" for n in gdf["name"]]

    # Stable order -> stable collision bumps across runs. `kind` is the primary key so every lake is
    # numbered before any river: the lake subsequence, and therefore every existing lake id, is
    # exactly what it was before rivers joined the table.
    kind_rank = np.array([KINDS.index(k) for k in gdf["kind"]])
    order = np.lexsort(
        (gdf["lon"].to_numpy(), gdf["lat"].to_numpy(), gdf["name_norm"].to_numpy(), kind_rank)
    )
    gdf = gdf.iloc[order].reset_index(drop=True)
    proj = gdf.geometry.to_crs(MEASURE_CRS)
    used: set[int] = set()
    gdf["id"] = [
        ids.assign_lake_id(ids.lake_source_key(nn, la, lo), used)
        for nn, la, lo in zip(gdf["name_norm"], gdf["lat"], gdf["lon"], strict=True)
    ]

    t0 = time.monotonic()
    is_river = (gdf["kind"] == "river").to_numpy()
    lengths, p1, p2 = [], [], []
    for geom, acres, river in zip(proj.to_numpy(), gdf["area_acres"].to_numpy(), is_river, strict=True):
        cap = BIG_LAKE_CANDIDATES if acres > BIG_LAKE_ACRES else max_candidates
        measure = longest_reach if river else longest_chord
        length, a, b = measure(geom, max_vertices, cap)
        lengths.append(length)
        p1.append(a)
        p2.append(b)
    log.info(
        "longest chord for %d waterbodies (%d by river reach sweep) in %.1fs",
        len(gdf), int(is_river.sum()), time.monotonic() - t0,
    )

    p1 = np.array(p1, dtype=float).reshape(-1, 2)
    p2 = np.array(p2, dtype=float).reshape(-1, 2)
    lon1, lat1 = to_wgs.transform(p1[:, 0], p1[:, 1])
    lon2, lat2 = to_wgs.transform(p2[:, 0], p2[:, 1])
    az, _, _ = _GEOD.inv(lon1, lat1, lon2, lat2)
    gdf["chord_ft"] = np.round(np.array(lengths) * FT_PER_M, 1)
    bearing = np.mod(np.round(az), 180.0)
    gdf["chord_bearing_deg"] = np.where(np.array(lengths) > 0, bearing, np.nan)

    t1 = time.monotonic()
    chord_ft = gdf["chord_ft"].to_numpy()
    qualifies = chord_ft >= EXTENT_MIN_CHORD_FT
    convergence = meridian_convergence_deg(gdf["lon"].to_numpy(), gdf["lat"].to_numpy())
    extents: list = [None] * len(gdf)
    proj_geoms = proj.to_numpy()
    for i in np.flatnonzero(qualifies):
        # Rivers are scan-converted at their own width instead of 40 lines across the bbox; see
        # `extent_by_bearing`. Lakes keep the fixed-count sampler, so their published values do not move.
        spacing = scan_spacing(largest_part(proj_geoms[i])) if is_river[i] else None
        extents[i] = extent_by_bearing(proj_geoms[i], float(convergence[i]), spacing_m=spacing)
    gdf["extent_by_bearing"] = extents
    log.info(
        "extent_by_bearing for %d of %d waterbodies (chord >= %.0f ft) in %.1fs",
        int(qualifies.sum()), len(gdf), EXTENT_MIN_CHORD_FT, time.monotonic() - t1,
    )

    bounds = gdf.geometry.bounds
    gdf["minx"] = bounds["minx"].round(6)
    gdf["miny"] = bounds["miny"].round(6)
    gdf["maxx"] = bounds["maxx"].round(6)
    gdf["maxy"] = bounds["maxy"].round(6)

    cfg.work_dir.mkdir(parents=True, exist_ok=True)
    cols = [
        "id", "name", "name_norm", "kind", "county", "township", "counties", "townships", "lat", "lon",
        "minx", "miny", "maxx", "maxy", "area_acres", "chord_ft", "chord_bearing_deg",
        "extent_by_bearing", "geometry",
    ]
    out = gpd.GeoDataFrame(gdf[cols], geometry="geometry", crs=WGS84)
    out["name"] = out["name"].astype(object).where(out["name"].notna(), None)
    lakes_path = cfg.work_dir / "lakes.parquet"
    out.to_parquet(lakes_path, index=False)

    # A reach narrower than 200 ft erodes to nothing under the 100 ft shore buffer, so most river
    # polygons drop out here. That is the right answer, not a bug: there is no water on such a reach
    # that is 100 ft off both banks. `_present` filters the empties.
    eroded = proj.buffer(-SHORE_BUFFER_M)
    usable = gpd.GeoDataFrame({"id": gdf["id"]}, geometry=eroded, crs=MEASURE_CRS).to_crs(WGS84)
    usable = usable[_present(usable.geometry)].reset_index(drop=True)
    usable_path = cfg.work_dir / "usable_water.parquet"
    usable.to_parquet(usable_path, index=False)

    log.info(
        "wrote %s (%d waterbodies) and %s (%d with a usable-water core) in %.1fs total",
        lakes_path, len(out), usable_path, len(usable), time.monotonic() - started,
    )
    return 0
