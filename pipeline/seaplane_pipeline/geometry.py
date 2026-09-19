"""Stage 4 (`geometry`): per-lake measurements from the hydrography polygons.

Input:  `data/cache/hydrography_polygons.geojson` (+ counties / minor civil divisions).
Output: `data/work/lakes.parquet` and `data/work/usable_water.parquet` (GeoParquet, WGS84).

What it keeps
-------------
`TYPE == 'lake'` only (the layer's other values are `river` and `swamp`). `NAME` is space-padded in
the source, so it is stripped and turned into `None` when blank. Of the 57,780 lake polygons we keep
**every named lake (10,284) plus unnamed lakes of at least 20 acres**; the rest are sub-20-acre
unnamed ponds that would bloat `index.json` past its 5 MB target without ever being landable or
searchable. The threshold is `--min-unnamed-acres`.

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


def extent_by_bearing(poly, convergence_deg: float, n_samples: int = EXTENT_SAMPLES) -> list[int]:
    """16 ints (ft): longest straight segment inside `poly` (`MEASURE_CRS`) along each bearing bin.

    Bin `i` is centered on true bearing `i * 22.5` deg (0 = north). A segment has two ends, so only
    8 directions are computed; bin `i` always equals bin `(i + 8) % 16`. Algorithm: rotate the
    polygon (on the largest part, same as `longest_chord`) so the bearing becomes the x axis, sample
    `n_samples` evenly spaced horizontal lines across its height, and take the longest single inside
    segment per line -- not the sum of pieces (see `_longest_segment`).
    """
    part = largest_part(poly)
    if part.is_empty:
        return [0] * EXTENT_BEARING_BINS
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
    """Read the hydrography layer, keep lakes, clean names, compute area, apply the size filter."""
    gdf = gpd.read_file(path, columns=["NAME", "NAME2", "TYPE", "ACRES"])
    gdf = gdf[gdf["TYPE"].astype(str).str.strip() == "lake"].copy()
    name = gdf["NAME"].astype("string").str.strip()
    gdf["name"] = name.where(name.str.len() > 0, other=pd.NA)
    if gdf.crs is None:
        gdf = gdf.set_crs(WGS84)
    gdf = gdf[_present(gdf.geometry)].copy()
    proj = gdf.geometry.to_crs(MEASURE_CRS)
    gdf["area_acres"] = (proj.area / M2_PER_ACRE).round(2)
    keep = gdf["name"].notna() | (gdf["area_acres"] >= min_unnamed_acres)
    dropped = int((~keep).sum())
    log.info(
        "hydrography: %d lake polygons -> keeping %d (%d named, %d unnamed >= %.0f acres); dropped %d small unnamed",
        len(gdf),
        int(keep.sum()),
        int(gdf["name"].notna().sum()),
        int((keep & gdf["name"].isna()).sum()),
        min_unnamed_acres,
        dropped,
    )
    return gdf[keep].reset_index(drop=True)


def _join_boundaries(cfg: Config, centroids: gpd.GeoSeries) -> tuple[pd.Series, pd.Series]:
    """Spatial-join lake centroids to county and civil-township names."""
    pts = gpd.GeoDataFrame(geometry=centroids, crs=MEASURE_CRS)
    county = pd.Series([None] * len(pts), index=pts.index, dtype=object)
    township = pd.Series([None] * len(pts), index=pts.index, dtype=object)

    cpath = gis.find_dataset(cfg, "counties")
    if cpath is None:
        log.warning("counties.geojson missing; county will be null (run `seaplane fetch`)")
    else:
        counties = gpd.read_file(cpath).to_crs(MEASURE_CRS)
        joined = gpd.sjoin(pts, counties[["NAME", "geometry"]], how="left", predicate="within")
        joined = joined[~joined.index.duplicated(keep="first")]
        county = joined["NAME"].astype(object).str.title().where(joined["NAME"].notna(), None)

    tpath = gis.find_dataset(cfg, "civil_townships")
    if tpath is None:
        log.info("minor_civil_divisions.geojson missing; township stays null (it is optional)")
    else:
        mcd = gpd.read_file(tpath).to_crs(MEASURE_CRS)
        label = np.where(
            mcd["TYPE"].astype(str).str.strip().str.lower() == "township",
            mcd["NAME"].astype(str).str.strip() + " Township",
            mcd["LABEL"].astype(str).str.strip(),
        )
        mcd = mcd.assign(township=label)
        joined = gpd.sjoin(pts, mcd[["township", "geometry"]], how="left", predicate="within")
        joined = joined[~joined.index.duplicated(keep="first")]
        township = joined["township"].where(joined["township"].notna(), None)
    return county, township


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

    county, township = _join_boundaries(cfg, centroids)
    gdf["county"] = county.to_numpy()
    gdf["township"] = township.to_numpy()

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

    # Stable order -> stable collision bumps across runs.
    order = np.lexsort((gdf["lon"].to_numpy(), gdf["lat"].to_numpy(), gdf["name_norm"].to_numpy()))
    gdf = gdf.iloc[order].reset_index(drop=True)
    proj = gdf.geometry.to_crs(MEASURE_CRS)
    used: set[int] = set()
    gdf["id"] = [
        ids.assign_lake_id(ids.lake_source_key(nn, la, lo), used)
        for nn, la, lo in zip(gdf["name_norm"], gdf["lat"], gdf["lon"], strict=True)
    ]

    t0 = time.monotonic()
    lengths, p1, p2 = [], [], []
    for geom, acres in zip(proj.to_numpy(), gdf["area_acres"].to_numpy(), strict=True):
        cap = BIG_LAKE_CANDIDATES if acres > BIG_LAKE_ACRES else max_candidates
        length, a, b = longest_chord(geom, max_vertices, cap)
        lengths.append(length)
        p1.append(a)
        p2.append(b)
    log.info("longest chord for %d lakes in %.1fs", len(gdf), time.monotonic() - t0)

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
        extents[i] = extent_by_bearing(proj_geoms[i], float(convergence[i]))
    gdf["extent_by_bearing"] = extents
    log.info(
        "extent_by_bearing for %d of %d lakes (chord >= %.0f ft) in %.1fs",
        int(qualifies.sum()), len(gdf), EXTENT_MIN_CHORD_FT, time.monotonic() - t1,
    )

    bounds = gdf.geometry.bounds
    gdf["minx"] = bounds["minx"].round(6)
    gdf["miny"] = bounds["miny"].round(6)
    gdf["maxx"] = bounds["maxx"].round(6)
    gdf["maxy"] = bounds["maxy"].round(6)

    cfg.work_dir.mkdir(parents=True, exist_ok=True)
    cols = [
        "id", "name", "name_norm", "county", "township", "lat", "lon",
        "minx", "miny", "maxx", "maxy", "area_acres", "chord_ft", "chord_bearing_deg",
        "extent_by_bearing", "geometry",
    ]
    out = gpd.GeoDataFrame(gdf[cols], geometry="geometry", crs=WGS84)
    out["name"] = out["name"].astype(object).where(out["name"].notna(), None)
    lakes_path = cfg.work_dir / "lakes.parquet"
    out.to_parquet(lakes_path, index=False)

    eroded = proj.buffer(-SHORE_BUFFER_M)
    usable = gpd.GeoDataFrame({"id": gdf["id"]}, geometry=eroded, crs=MEASURE_CRS).to_crs(WGS84)
    usable = usable[_present(usable.geometry)].reset_index(drop=True)
    usable_path = cfg.work_dir / "usable_water.parquet"
    usable.to_parquet(usable_path, index=False)

    log.info(
        "wrote %s (%d lakes) and %s (%d with a usable-water core) in %.1fs total",
        lakes_path, len(out), usable_path, len(usable), time.monotonic() - started,
    )
    return 0
