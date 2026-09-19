"""Stage `wavefield`: where on the water it is calm, for every water body big enough to have an answer.

Design: `docs/big-water-design.md`. Schema: `docs/data-contract.md`, section "Wave field" (authoritative).
Input:  `data/work/lakes.parquet` and `data/work/usable_water.parquet` (both from `geometry`), the GNIS
        Domestic Names bulk file (`gnis.py`), and an optional bathymetry grid (`bathymetry.py`).
Output: `data/work/wave_points.bin` + `wave_points.json`; `build` copies both into `data/out/`.

What it computes
----------------
Sample points on a deterministic grid inside each qualifying water body's usable water, and per point
`fetch[16]` (upwind distance to the first land, arc-averaged), `run[8]` (the straight usable line
through the point), an optional `depth_dm`, and a `label` -- a GNIS bay/channel name when one is near
enough, otherwise a position descriptor. Points that share a label inside one water body are a region,
and that is what the briefing and the client report ("Cass Lake: west end 3 in, open middle 7 in").

Ray casting
-----------
Fetch rays run across the **fetch mask**: the union of every water polygon of every kind, so a ray
crosses from one water body into a touching one without stopping. The union is never materialized.
Instead a ray takes the first crossing of any polygon's boundary, and where that boundary might be
shared with another polygon (`WaterMask.internal`, precomputed from the polygon-polygon intersection
pairs) the far side is point-tested: still water means the crossing was an internal edge and the ray
carries on. That keeps the whole mask as flat numpy arrays.

The caster itself is a scan-conversion, the same trick `geometry.scan_longest_run` uses: rotate so the
ray direction is +x, and a segment can only be crossed by rays whose y falls inside its y-span, which
a pair of `searchsorted` calls resolves. Cost per direction is O(segments) for the rotated y plus
O(candidate pairs), and the candidate pairs are few because shoreline segments are short. Segments are
found through a uniform bucket grid over a window around the points, grown until every ray either hits
something inside the window or reaches the 100 km cap.

Michigan-specific pieces (the measurement CRS, the state list behind the GNIS download) come from
`geometry.MEASURE_CRS` and `config`, not from constants here; see `docs/nationwide.md`.
"""
from __future__ import annotations

import collections
import json
import logging
import math
import struct
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import shapely
from shapely.geometry import LineString

from . import geometry as geom_mod
from .config import Config

log = logging.getLogger(__name__)

MEASURE_CRS = geom_mod.MEASURE_CRS
WGS84 = geom_mod.WGS84
M2_PER_ACRE = geom_mod.M2_PER_ACRE
FT_PER_M = geom_mod.FT_PER_M

# --- contract constants (docs/data-contract.md, "Wave field") -------------------

MIN_ACRES = 100.0
TARGET_POINTS = 60
BIG_TARGET_POINTS = 400
BIG_WATER_ACRES = 10_000.0
#: Water of 100,000 acres or more (Lake St. Clair is 280,000) aims for 2,000 points: at 400 the spacing is
#: 1.7 km and the 1-2 km bays of the St. Clair Flats get no points at all. The Great Lakes proper sit on the
#: `SPACING_MAX_M` clamp either way.
HUGE_TARGET_POINTS = 2000
HUGE_WATER_ACRES = 100_000.0
SPACING_MIN_M = 150.0
SPACING_MAX_M = 2000.0

FETCH_BINS = 16
RUN_BINS = 8
ARC_OFFSETS_DEG = (-12.0, -6.0, 0.0, 6.0, 12.0)
FETCH_CAP_M = 100_000.0
#: An **artificial edge** is a clip line rather than a shore, and a ray that leaves the mask through
#: one has no measurable fetch: it counts as the cap. Length alone does not find them, because every
#: source here densifies its clip lines -- Lake Huron's 104.5 km international line carries only three
#: segments over 1,500 m. So the test is straightness instead: a run of consecutive boundary vertices
#: that all sit within `ARTIFICIAL_OFFSET_M` of the chord joining the run's ends, with a run longer
#: than `ARTIFICIAL_MIN_LENGTH_M`. Candidate runs are seeded every `ARTIFICIAL_STRIDE_M` along each
#: ring, which is why the stride is well under the minimum length.
#:
#: A long straight breakwater (Lake Erie carries a 6.4 km one at Cleveland) or a reservoir dike also
#: passes this test and reads as the cap. That errs toward more waves, which is the conservative
#: direction, so it is accepted.
#: 10 m, not the 60 m first tried: drawn clip lines sit 0.0 m off their chord, while natural shore on
#: Torch, Houghton and Crystal lakes wanders 39-58 m over 2 km and was being read as open water (a 40 inch
#: band beside 3 inch neighbours). At 10 m every real clip is still caught and no inland lake is flagged.
ARTIFICIAL_OFFSET_M = 10.0
ARTIFICIAL_MIN_LENGTH_M = 2000.0
ARTIFICIAL_STRIDE_M = 250.0

FETCH_UNIT_M = 10.0
RUN_UNIT_FT = 10.0
RECORD_FORMAT = "<ffHH16H8H"
RECORD_BYTES = 60
U16_MAX = 65535
DEPTH_UNKNOWN = 65535
FORMAT_VERSION = 1

#: Position descriptors. Index 0 is the centre; 1..8 are the eight compass sectors clockwise from
#: true north. Cardinals are "ends", intercardinals are "sides" (the contract fixes the vocabulary).
DESCRIPTORS = (
    "middle",
    "north end",
    "northeast side",
    "east end",
    "southeast side",
    "south end",
    "southwest side",
    "west end",
    "northwest side",
)
#: Normalized radius (1.0 = the furthest water from the centre along that axis) inside which a point
#: is "middle" rather than a sector.
MIDDLE_RADIUS = 0.45
#: A descriptor sector holding fewer than this many points is merged into a neighbouring sector or
#: `middle`, so a twenty-point lake yields a handful of regions instead of nine singletons.
MIN_SECTOR_POINTS = 3
#: And a GNIS name that ends up with fewer than this many points is not a region either -- those
#: points go back to the descriptors. A region is what the briefing recommends, and on a 2 km grid
#: every marsh channel in the St. Clair Flats catches a single point: with no floor, "Lake St. Clair:
#: Baltimore Channel, 2 in" outranks Anchor Bay on the strength of one sample.
MIN_NAMED_POINTS = 2

#: Depth is only sampled on these kinds. The one grid in hand covers Lake Erie and Lake St. Clair, and
#: a DEM cell under an inland lake carries that lake's *surface* elevation, so sampling everything
#: would invent depths. "Everything outside the Great Lakes stays unknown for now" (data contract).
DEPTH_KINDS = ("great_lake", "connecting_water")

#: GNIS feature classes that can name part of a water body.
NAME_CLASSES = ("Bay", "Channel", "Harbor")
#: `Channel` names label flowing water only. On a lake they are dredged cuts and marsh drains (the
#: St. Clair Flats alone has 50: "Schweinkart Cut", "North Highway", "Horseshoe Canal"), which crowded
#: the bays a pilot actually thinks in out of Lake St. Clair's region list.
CHANNEL_KINDS = ("river", "connecting_water")
#: A GNIS point this far outside a water body still belongs to it (the contract's 200 m rule).
NAME_SNAP_M = 200.0
#: How far a name reaches, as a multiple of its own water. GNIS gives a point and no extent, so the
#: extent is measured: `w_name` is the narrowest straight run through the name's position, which is
#: the width of the bay (or channel) it sits in, and a bay is usually longer than it is wide. Swept
#: on Lake St. Clair (docs/gis-sources.md addendum) over 0.75-1.5: below 1.25 the small Flats bays
#: lose points, above it the named share stops growing because the width test below binds. Anchor
#: Bay is ~7 km across and reaches the 6 km cap, Muscamoot is ~1 km across and reaches 1.3 km, and
#: the open lake -- 20 km wide -- is left to the descriptors at every value tried.
NAME_RADIUS_FRACTION = 1.25
NAME_RADIUS_MIN_M = 250.0
NAME_RADIUS_MAX_M = 6000.0
#: And a point may not be in water more than this much wider than the name's own: the far side of a
#: bay mouth is within reach of the bay's centre but is open water, and reads as open water.
NAME_WIDTH_FACTOR = 2.5
#: Channels are line-like; whatever their width says, they never reach further than this.
CHANNEL_RADIUS_MAX_M = 1000.0

_EPS_M = 1e-6
#: How far past a crossing the far side is sampled when deciding whether an edge was internal.
_STEP_OVER_M = 1.0


def add_args(sp) -> None:
    sp.add_argument("--limit", type=int, help="Process only the first N qualifying water bodies")
    sp.add_argument("--lake-id", action="append", type=int, help="Only this water body (repeatable)")
    sp.add_argument("--min-acres", type=float, default=MIN_ACRES, help="Smallest water body that gets points")
    sp.add_argument("--target", type=int, default=TARGET_POINTS,
                    help=f"Points aimed for on an ordinary water body (default {TARGET_POINTS})")
    sp.add_argument("--big-target", type=int, default=BIG_TARGET_POINTS,
                    help=f"Points aimed for on water of {BIG_WATER_ACRES:.0f} acres or more "
                         f"(default {BIG_TARGET_POINTS}; raise it to resolve bays 1-2 km across)")
    sp.add_argument("--artificial-offset", type=float, default=ARTIFICIAL_OFFSET_M,
                    help=f"How far a clip line may wander from its own chord (default "
                         f"{ARTIFICIAL_OFFSET_M:.0f} m; 10 m separates drawn clip lines from "
                         f"straight natural shore -- see `artificial_runs`)")
    sp.add_argument("--artificial-min-length", type=float, default=ARTIFICIAL_MIN_LENGTH_M,
                    help=f"Shortest run that counts as a clip line (default {ARTIFICIAL_MIN_LENGTH_M:.0f} m)")
    sp.add_argument("--skip-depth", action="store_true", help="Leave every depth unknown")
    sp.add_argument("--skip-names", action="store_true", help="Descriptors only; do not read GNIS names")


# --- segments ------------------------------------------------------------------


@dataclass
class Segments:
    """Boundary segments of a set of polygons, as flat arrays (a projected CRS, metres)."""

    x1: np.ndarray
    y1: np.ndarray
    x2: np.ndarray
    y2: np.ndarray
    length: np.ndarray
    owner: np.ndarray  # index of the polygon each segment came from
    #: `owner_slice[i]` is (start, stop) into the arrays for polygon `i`; polygons are contiguous.
    owner_slice: np.ndarray = field(default_factory=lambda: np.zeros((0, 2), dtype=np.int64))
    #: `(start, stop)` per boundary ring, in order. Rings are what `artificial_runs` walks.
    ring_slice: np.ndarray = field(default_factory=lambda: np.zeros((0, 2), dtype=np.int64))

    def __len__(self) -> int:
        return len(self.x1)

    def take(self, idx: np.ndarray) -> Segments:
        return Segments(
            self.x1[idx], self.y1[idx], self.x2[idx], self.y2[idx],
            self.length[idx], self.owner[idx],
        )

    @property
    def bounds(self) -> tuple[float, float, float, float]:
        if not len(self):
            return (0.0, 0.0, 0.0, 0.0)
        return (
            float(min(self.x1.min(), self.x2.min())),
            float(min(self.y1.min(), self.y2.min())),
            float(max(self.x1.max(), self.x2.max())),
            float(max(self.y1.max(), self.y2.max())),
        )

    @classmethod
    def from_geoms(cls, geoms) -> Segments:
        """Every exterior and interior ring segment of every polygon, in polygon order."""
        xs1: list[np.ndarray] = []
        ys1: list[np.ndarray] = []
        xs2: list[np.ndarray] = []
        ys2: list[np.ndarray] = []
        owner: list[np.ndarray] = []
        rings_at: list[tuple[int, int]] = []
        slices = np.zeros((len(geoms), 2), dtype=np.int64)
        total = 0
        for i, geom in enumerate(geoms):
            start = total
            if geom is not None and not geom.is_empty:
                for part in shapely.get_parts(geom):
                    rings = [part.exterior, *part.interiors] if hasattr(part, "exterior") else []
                    for ring in rings:
                        if ring is None:
                            continue
                        c = shapely.get_coordinates(ring)
                        if len(c) < 2:
                            continue
                        xs1.append(c[:-1, 0])
                        ys1.append(c[:-1, 1])
                        xs2.append(c[1:, 0])
                        ys2.append(c[1:, 1])
                        owner.append(np.full(len(c) - 1, i, dtype=np.int64))
                        rings_at.append((total, total + len(c) - 1))
                        total += len(c) - 1
            slices[i] = (start, total)
        ring_slice = np.array(rings_at, dtype=np.int64).reshape(-1, 2)
        if not xs1:
            empty = np.zeros(0)
            return cls(empty, empty, empty, empty, empty, np.zeros(0, dtype=np.int64), slices, ring_slice)
        x1 = np.concatenate(xs1)
        y1 = np.concatenate(ys1)
        x2 = np.concatenate(xs2)
        y2 = np.concatenate(ys2)
        return cls(
            x1, y1, x2, y2, np.hypot(x2 - x1, y2 - y1), np.concatenate(owner), slices, ring_slice
        )


def _max_offset(px: np.ndarray, py: np.ndarray, i: int, j: int) -> float:
    """Greatest perpendicular distance of the vertices between `i` and `j` from the chord `i`->`j`."""
    if j - i < 2:
        return 0.0
    ax, ay = px[i], py[i]
    dx, dy = px[j] - ax, py[j] - ay
    chord = math.hypot(dx, dy)
    if chord <= 0.0:
        return math.inf
    return float(np.max(np.abs((px[i + 1 : j] - ax) * dy - (py[i + 1 : j] - ay) * dx)) / chord)


def artificial_runs(
    segs: Segments,
    max_offset_m: float = ARTIFICIAL_OFFSET_M,
    min_length_m: float = ARTIFICIAL_MIN_LENGTH_M,
    stride_m: float = ARTIFICIAL_STRIDE_M,
) -> np.ndarray:
    """Boolean per segment: does it lie on a clip line rather than a shore?

    A clip line is straight over a long way, however finely it is digitized, and a natural shore is
    not. So this walks each ring, seeds a candidate every `stride_m` of path length, asks whether the
    next `min_length_m` of boundary stays within `max_offset_m` of its own chord, and if it does
    extends the run vertex by vertex for as long as that holds. The seeding stride is what keeps the
    scan linear in the number of vertices; it is far below `min_length_m`, so no qualifying run can
    fall between two seeds.
    """
    flags = np.zeros(len(segs), dtype=bool)
    if not len(segs) or not len(segs.ring_slice):
        return flags
    for start, stop in segs.ring_slice:
        n = int(stop - start)
        if n < 2:
            continue
        px = np.empty(n + 1)
        py = np.empty(n + 1)
        px[:n] = segs.x1[start:stop]
        py[:n] = segs.y1[start:stop]
        px[n] = segs.x2[stop - 1]
        py[n] = segs.y2[stop - 1]
        cum = np.empty(n + 1)
        cum[0] = 0.0
        np.cumsum(segs.length[start:stop], out=cum[1:])
        total = float(cum[n])
        if total < min_length_m:
            continue
        seeds = np.arange(0.0, total - min_length_m + 1e-9, stride_m)
        candidates = np.unique(np.maximum(np.searchsorted(cum, seeds, side="right") - 1, 0))
        reached = 0
        for i in candidates:
            i = int(i)
            if i < reached:
                continue
            j = int(np.searchsorted(cum, cum[i] + min_length_m, side="left"))
            if j > n:
                break
            if _max_offset(px, py, i, j) > max_offset_m:
                continue
            while j < n and _max_offset(px, py, i, j + 1) <= max_offset_m:
                j += 1
            flags[start + i : start + j] = True
            reached = j
    return flags


def _rotation(angle_deg: float) -> tuple[float, float]:
    a = math.radians(angle_deg)
    return math.cos(a), math.sin(a)


def cast_rays(
    segs: Segments,
    ox: np.ndarray,
    oy: np.ndarray,
    angle_deg: float,
    cap_m: float,
    min_dist: np.ndarray | None = None,
    chunk: int = 1 << 19,
) -> tuple[np.ndarray, np.ndarray]:
    """First crossing of `segs` by a ray from each origin, all rays in one direction.

    `angle_deg` is the rotation (counter-clockwise, `geometry.rotate_points`' convention) that puts
    the ray direction on +x, i.e. `bearing_true - meridian_convergence - 90`. Returns
    `(distance_m, segment_index)`; distance is `inf` and the index `-1` where nothing was hit inside
    `cap_m`. `min_dist` restarts a ray past an earlier crossing.
    """
    n_rays = len(ox)
    dist = np.full(n_rays, np.inf)
    hit = np.full(n_rays, -1, dtype=np.int64)
    if len(segs) == 0 or n_rays == 0:
        return dist, hit
    ca, sa = _rotation(angle_deg)
    ray_x = ox * ca - oy * sa
    ray_y = ox * sa + oy * ca
    floor = np.zeros(n_rays) if min_dist is None else np.asarray(min_dist, dtype=float)

    order = np.argsort(ray_y, kind="stable")
    sy = ray_y[order]
    sx = ray_x[order]
    sfloor = floor[order]

    for start in range(0, len(segs), chunk):
        sl = slice(start, start + chunk)
        ex1, ey1 = segs.x1[sl], segs.y1[sl]
        ex2, ey2 = segs.x2[sl], segs.y2[sl]
        ry1 = ex1 * sa + ey1 * ca
        ry2 = ex2 * sa + ey2 * ca
        ylo = np.minimum(ry1, ry2)
        yhi = np.maximum(ry1, ry2)
        # Half-open [ylo, yhi): a vertex shared by two segments is counted once.
        lo = np.searchsorted(sy, ylo, side="left")
        hi = np.searchsorted(sy, yhi, side="left")
        counts = np.maximum(hi - lo, 0)
        total = int(counts.sum())
        if total == 0:
            continue
        seg_idx = np.repeat(np.arange(len(ylo)), counts)
        base = np.repeat(np.cumsum(counts) - counts, counts)
        ray_idx = np.repeat(lo, counts) + (np.arange(total) - base)

        y = sy[ray_idx]
        a_y = ry1[seg_idx]
        a_x = ex1[seg_idx] * ca - ey1[seg_idx] * sa
        b_x = ex2[seg_idx] * ca - ey2[seg_idx] * sa
        d = a_x + (y - a_y) * (b_x - a_x) / (ry2[seg_idx] - a_y) - sx[ray_idx]
        ok = (d > sfloor[ray_idx] + _EPS_M) & (d <= cap_m)
        sel = np.flatnonzero(ok)
        if not len(sel):
            continue
        r = ray_idx[sel]
        dd = d[sel]
        ss = seg_idx[sel] + start
        o = np.lexsort((dd, r))
        r, dd, ss = r[o], dd[o], ss[o]
        first = np.empty(len(r), dtype=bool)
        first[0] = True
        np.not_equal(r[1:], r[:-1], out=first[1:])
        fi = np.flatnonzero(first)
        target = order[r[fi]]
        better = dd[fi] < dist[target]
        dist[target[better]] = dd[fi][better]
        hit[target[better]] = ss[fi][better]
    return dist, hit


def _box_exit(ox: np.ndarray, oy: np.ndarray, angle_deg: float, box) -> np.ndarray:
    """Distance from each origin to the edge of `box` along the ray direction (`inf` if it never leaves)."""
    ux, uy = _direction(angle_deg)
    minx, miny, maxx, maxy = box
    out = np.full(len(ox), np.inf)
    for u, o, lo, hi in ((ux, ox, minx, maxx), (uy, oy, miny, maxy)):
        if abs(u) < 1e-12:
            continue
        t = np.where(u > 0, (hi - o) / u, (lo - o) / u)
        out = np.minimum(out, t)
    return np.maximum(out, 0.0)


# --- the fetch mask ------------------------------------------------------------


class _SegmentGrid:
    """Uniform bucket index over segment bounding boxes, as CSR arrays."""

    def __init__(self, segs: Segments, cell_m: float = 2000.0, max_cells: int = 64):
        self.cell = float(cell_m)
        self.oversized = np.zeros(0, dtype=np.int64)
        if not len(segs):
            self.x0 = self.y0 = 0.0
            self.nx = self.ny = 0
            self.starts = np.zeros(1, dtype=np.int64)
            self.items = np.zeros(0, dtype=np.int64)
            return
        minx, miny, maxx, maxy = segs.bounds
        self.x0, self.y0 = minx, miny
        self.nx = max(int((maxx - minx) // self.cell) + 1, 1)
        self.ny = max(int((maxy - miny) // self.cell) + 1, 1)
        cx0 = np.clip(((np.minimum(segs.x1, segs.x2) - minx) // self.cell).astype(np.int64), 0, self.nx - 1)
        cx1 = np.clip(((np.maximum(segs.x1, segs.x2) - minx) // self.cell).astype(np.int64), 0, self.nx - 1)
        cy0 = np.clip(((np.minimum(segs.y1, segs.y2) - miny) // self.cell).astype(np.int64), 0, self.ny - 1)
        cy1 = np.clip(((np.maximum(segs.y1, segs.y2) - miny) // self.cell).astype(np.int64), 0, self.ny - 1)
        spans = (cx1 - cx0 + 1) * (cy1 - cy0 + 1)
        big = spans > max_cells
        self.oversized = np.flatnonzero(big)
        keep = np.flatnonzero(~big)
        cx0, cx1, cy0, cy1, spans = cx0[keep], cx1[keep], cy0[keep], cy1[keep], spans[keep]
        total = int(spans.sum())
        seg_of = np.repeat(keep, spans)
        base = np.repeat(np.cumsum(spans) - spans, spans)
        off = np.arange(total) - base
        width = (cx1 - cx0 + 1)
        w = np.repeat(width, spans)
        gx = np.repeat(cx0, spans) + off % w
        gy = np.repeat(cy0, spans) + off // w
        cell_id = gy * self.nx + gx
        order = np.argsort(cell_id, kind="stable")
        self.items = seg_of[order]
        self.starts = np.zeros(self.nx * self.ny + 1, dtype=np.int64)
        np.cumsum(np.bincount(cell_id, minlength=self.nx * self.ny), out=self.starts[1:])

    def query(self, box) -> np.ndarray:
        """Indices of every segment whose bounding box may touch `box` (minx, miny, maxx, maxy)."""
        if not len(self.items) and not len(self.oversized):
            return np.zeros(0, dtype=np.int64)
        minx, miny, maxx, maxy = box
        gx0 = int(np.clip((minx - self.x0) // self.cell, 0, self.nx - 1))
        gx1 = int(np.clip((maxx - self.x0) // self.cell, 0, self.nx - 1))
        gy0 = int(np.clip((miny - self.y0) // self.cell, 0, self.ny - 1))
        gy1 = int(np.clip((maxy - self.y0) // self.cell, 0, self.ny - 1))
        if maxx < self.x0 or maxy < self.y0:
            return self.oversized
        rows = []
        for gy in range(gy0, gy1 + 1):
            row = gy * self.nx
            lo = self.starts[row + gx0]
            hi = self.starts[row + gx1 + 1]
            if hi > lo:
                rows.append(self.items[lo:hi])
        if len(self.oversized):
            rows.append(self.oversized)
        if not rows:
            return np.zeros(0, dtype=np.int64)
        return np.unique(np.concatenate(rows))


class WaterMask:
    """Every water polygon of every kind, indexed for fetch rays.

    `fetch_rays` casts from points inside the mask and returns the distance to the first land, where
    "land" means the first boundary crossing that is not an internal edge between two water polygons.
    """

    def __init__(
        self,
        geoms,
        cap_m: float = FETCH_CAP_M,
        cell_m: float = 2000.0,
        max_offset_m: float = ARTIFICIAL_OFFSET_M,
        min_artificial_m: float = ARTIFICIAL_MIN_LENGTH_M,
    ):
        self.geoms = np.asarray(list(geoms), dtype=object)
        self.cap_m = float(cap_m)
        self.segments = Segments.from_geoms(self.geoms)
        self.grid = _SegmentGrid(self.segments, cell_m)
        self.tree = shapely.STRtree(self.geoms) if len(self.geoms) else None
        self.internal = self._internal_flags()
        self.artificial = artificial_runs(self.segments, max_offset_m, min_artificial_m)

    def _internal_flags(self) -> np.ndarray:
        """Segments that might lie on a boundary shared with (or inside) another water polygon."""
        flags = np.zeros(len(self.segments), dtype=bool)
        if self.tree is None or len(self.geoms) < 2:
            return flags
        pairs = self.tree.query(self.geoms, predicate="intersects")
        keep = pairs[0] != pairs[1]
        if not keep.any():
            return flags
        bounds = shapely.bounds(self.geoms)
        segs = self.segments
        for i, j in zip(pairs[0][keep], pairs[1][keep], strict=True):
            start, stop = segs.owner_slice[i]
            if stop <= start:
                continue
            jminx, jminy, jmaxx, jmaxy = bounds[j]
            sl = slice(start, stop)
            touches = (
                (np.maximum(segs.x1[sl], segs.x2[sl]) >= jminx)
                & (np.minimum(segs.x1[sl], segs.x2[sl]) <= jmaxx)
                & (np.maximum(segs.y1[sl], segs.y2[sl]) >= jminy)
                & (np.minimum(segs.y1[sl], segs.y2[sl]) <= jmaxy)
            )
            flags[start:stop] |= touches
        return flags

    def _in_water(self, x: np.ndarray, y: np.ndarray) -> np.ndarray:
        if self.tree is None or not len(x):
            return np.zeros(len(x), dtype=bool)
        pts = shapely.points(x, y)
        found = self.tree.query(pts, predicate="intersects")
        out = np.zeros(len(x), dtype=bool)
        out[found[0]] = True
        return out

    def fetch_rays(
        self,
        xs: np.ndarray,
        ys: np.ndarray,
        angles_deg,
        margins=(5_000.0, 25_000.0, None),
        batch: int = 2000,
    ) -> np.ndarray:
        """`(len(xs), len(angles_deg))` distances in metres to the first land, capped.

        `angles_deg` are rotations that put each ray direction on +x (see `cast_rays`). A ray that
        leaves the mask on an artificial edge -- a clip line or a breakwater, see `artificial_runs`
        -- is capped, and so is one that finds no land inside the cap.

        Points are taken `batch` at a time. They arrive in grid order, so a batch is a band of the
        water body: it keeps the segment window small and bounds the candidate-pair arrays on a
        Great Lake, where one water body can hold tens of thousands of points.
        """
        xs = np.asarray(xs, dtype=float)
        ys = np.asarray(ys, dtype=float)
        angles = [float(a) for a in angles_deg]
        out = np.full((len(xs), len(angles)), np.nan)
        if not len(xs):
            return out
        if batch and len(xs) > batch:
            for start in range(0, len(xs), batch):
                sl = slice(start, start + batch)
                out[sl] = self.fetch_rays(xs[sl], ys[sl], angles, margins=margins, batch=0)
            return out
        pending = {k: np.arange(len(xs)) for k in range(len(angles))}
        for margin in margins:
            if not pending:
                break
            m = self.cap_m + self.grid.cell if margin is None else float(margin)
            box = (xs.min() - m, ys.min() - m, xs.max() + m, ys.max() + m)
            idx = self.grid.query(box)
            sub = self.segments.take(idx)
            local_internal = self.internal[idx]
            local_artificial = self.artificial[idx]
            still: dict[int, np.ndarray] = {}
            for k, rays in pending.items():
                angle = angles[k]
                ox, oy = xs[rays], ys[rays]
                dist, artificial = self._first_land(
                    sub, local_internal, local_artificial, ox, oy, angle
                )
                exit_d = _box_exit(ox, oy, angle, box)
                found = np.isfinite(dist) & (dist <= exit_d)
                capped = (~found & (exit_d >= self.cap_m)) | (found & artificial)
                out[rays[found & ~artificial], k] = dist[found & ~artificial]
                out[rays[capped], k] = self.cap_m
                unresolved = rays[~(found | capped)]
                if len(unresolved):
                    still[k] = unresolved
            pending = still
        for k, rays in pending.items():
            out[rays, k] = self.cap_m
        return np.minimum(np.nan_to_num(out, nan=self.cap_m), self.cap_m)

    def _first_land(
        self,
        sub: Segments,
        internal: np.ndarray,
        artificial: np.ndarray,
        ox,
        oy,
        angle_deg,
        max_steps: int = 8,
    ):
        """First crossing that is not an internal water-water edge.

        Returns `(distance, artificial)`; `artificial` marks a ray that left through a clip line
        rather than a shore, which the contract counts as the cap.
        """
        dist, hit = cast_rays(sub, ox, oy, angle_deg, self.cap_m)
        live = np.flatnonzero(np.isfinite(dist) & (hit >= 0))
        ux, uy = _direction(angle_deg)
        for _ in range(max_steps):
            maybe = live[internal[hit[live]]] if len(live) else live
            if not len(maybe):
                break
            step = dist[maybe] + _STEP_OVER_M
            wet = self._in_water(ox[maybe] + ux * step, oy[maybe] + uy * step)
            again = maybe[wet]
            if not len(again):
                break
            floor = dist[again] + _STEP_OVER_M
            d2, h2 = cast_rays(sub, ox[again], oy[again], angle_deg, self.cap_m, min_dist=floor)
            dist[again] = d2
            hit[again] = h2
            live = again[np.isfinite(d2) & (h2 >= 0)]
        if not len(sub):
            return dist, np.zeros(len(ox), dtype=bool)
        return dist, (hit >= 0) & artificial[np.maximum(hit, 0)]


def _direction(angle_deg: float) -> tuple[float, float]:
    """Unit vector of travel for a ray whose rotation-to-+x angle is `angle_deg`."""
    a = math.radians(-angle_deg)
    return math.cos(a), math.sin(a)


def ray_angles(convergence_deg: float, arc: bool = True, bins: int = FETCH_BINS) -> np.ndarray:
    """Rotation angles that put each fetch ray on +x, shape `(bins, len(offsets))`.

    Bin `i` is true bearing `i * 360/bins`; `arc` adds the contract's five-ray arc
    (-12, -6, 0, +6, +12 degrees), which SPM recommends and which is what `fetch[i]` averages.
    """
    offsets = ARC_OFFSETS_DEG if arc else (0.0,)
    step = 360.0 / bins
    return np.array(
        [[b * step + o - convergence_deg - 90.0 for o in offsets] for b in range(bins)], dtype=float
    )


def fetch_by_bearing(
    mask: WaterMask, xs, ys, convergence_deg: float, arc: bool = True, bins: int = FETCH_BINS
) -> np.ndarray:
    """`(n_points, bins)` fetch in metres: first land upwind, averaged over the arc."""
    angles = ray_angles(convergence_deg, arc=arc, bins=bins)
    flat = mask.fetch_rays(xs, ys, angles.reshape(-1))
    return flat.reshape(len(np.asarray(xs)), bins, angles.shape[1]).mean(axis=2)


def run_by_bearing(usable_geom, xs, ys, convergence_deg: float, bins: int = RUN_BINS) -> np.ndarray:
    """`(n_points, bins)` metres: the straight line through each point inside `usable_geom`.

    Bin `i` is true bearing `i * 22.5` and its reciprocal, so the two directions are summed -- this is
    the water a pilot can actually run along, not a one-sided distance.
    """
    xs = np.asarray(xs, dtype=float)
    ys = np.asarray(ys, dtype=float)
    out = np.zeros((len(xs), bins))
    segs = Segments.from_geoms([usable_geom])
    if not len(segs) or not len(xs):
        return out
    minx, miny, maxx, maxy = segs.bounds
    cap = math.hypot(maxx - minx, maxy - miny) + 1.0
    step = 180.0 / bins
    for b in range(bins):
        angle = b * step - convergence_deg - 90.0
        fwd, _ = cast_rays(segs, xs, ys, angle, cap)
        back, _ = cast_rays(segs, xs, ys, angle + 180.0, cap)
        total = np.where(np.isfinite(fwd), fwd, 0.0) + np.where(np.isfinite(back), back, 0.0)
        out[:, b] = total
    return out


# --- sample points -------------------------------------------------------------


def point_spacing(
    area_m2: float,
    target: int | None = None,
    big_target: int = BIG_TARGET_POINTS,
) -> float:
    """Grid spacing for a water body of `area_m2`, per the contract."""
    if target is None:
        target = big_target if area_m2 >= BIG_WATER_ACRES * M2_PER_ACRE else TARGET_POINTS
    elif area_m2 >= BIG_WATER_ACRES * M2_PER_ACRE:
        target = big_target
    if area_m2 >= HUGE_WATER_ACRES * M2_PER_ACRE:
        target = max(target, HUGE_TARGET_POINTS)
    return float(min(max(math.sqrt(area_m2 / target), SPACING_MIN_M), SPACING_MAX_M))


def grid_points(usable_geom, spacing_m: float) -> np.ndarray:
    """`(n, 2)` grid nodes inside `usable_geom`, anchored to the CRS origin so a run is repeatable.

    Nodes sit at `(k + 0.5) * spacing` on both axes -- anchored to the projected CRS, never to the
    polygon's own bounding box, so the same water body keeps the same points when its neighbours
    change. Ordered south to north, then west to east. A water body whose usable core is too thin to
    hold any node falls back to one representative point in its largest part, so a qualifying water
    body always gets a wave field.
    """
    if usable_geom is None or usable_geom.is_empty or spacing_m <= 0:
        return np.zeros((0, 2))
    minx, miny, maxx, maxy = usable_geom.bounds
    kx0 = math.ceil(minx / spacing_m - 0.5)
    kx1 = math.floor(maxx / spacing_m - 0.5)
    ky0 = math.ceil(miny / spacing_m - 0.5)
    ky1 = math.floor(maxy / spacing_m - 0.5)
    if kx1 >= kx0 and ky1 >= ky0:
        gx = (np.arange(kx0, kx1 + 1) + 0.5) * spacing_m
        gy = (np.arange(ky0, ky1 + 1) + 0.5) * spacing_m
        xx, yy = np.meshgrid(gx, gy)
        xx = xx.reshape(-1)
        yy = yy.reshape(-1)
        shapely.prepare(usable_geom)
        inside = shapely.contains_xy(usable_geom, xx, yy)
        if inside.any():
            return np.stack([xx[inside], yy[inside]], axis=1)
    parts = shapely.get_parts(usable_geom)
    if not len(parts):
        return np.zeros((0, 2))
    biggest = max(parts, key=lambda p: p.area)
    rp = biggest.representative_point()
    return np.array([[rp.x, rp.y]])


# --- labels --------------------------------------------------------------------


def _true_axes(convergence_deg: float) -> tuple[np.ndarray, np.ndarray]:
    """Unit vectors of true east and true north in the projected CRS."""
    c = math.radians(convergence_deg)
    return np.array([math.cos(c), math.sin(c)]), np.array([-math.sin(c), math.cos(c)])


def descriptor_sectors(xs, ys, geom, convergence_deg: float) -> np.ndarray:
    """Index into `DESCRIPTORS` for every point: 0 = middle, 1..8 = compass sector clockwise from N."""
    xs = np.asarray(xs, dtype=float)
    ys = np.asarray(ys, dtype=float)
    if not len(xs):
        return np.zeros(0, dtype=np.int64)
    east, north = _true_axes(convergence_deg)
    centre = shapely.centroid(geom)
    cx, cy = centre.x, centre.y
    coords = shapely.get_coordinates(geom)
    de = (coords[:, 0] - cx) * east[0] + (coords[:, 1] - cy) * east[1]
    dn = (coords[:, 0] - cx) * north[0] + (coords[:, 1] - cy) * north[1]
    e_half = max(float(np.abs(de).max()) if len(de) else 1.0, 1.0)
    n_half = max(float(np.abs(dn).max()) if len(dn) else 1.0, 1.0)
    pe = ((xs - cx) * east[0] + (ys - cy) * east[1]) / e_half
    pn = ((xs - cx) * north[0] + (ys - cy) * north[1]) / n_half
    r = np.hypot(pe, pn)
    bearing = np.degrees(np.arctan2(pe, pn)) % 360.0
    sector = (np.floor(bearing / 45.0 + 0.5).astype(np.int64) % 8) + 1
    return np.where(r < MIDDLE_RADIUS, 0, sector)


def merge_small_sectors(sectors: np.ndarray, min_points: int = MIN_SECTOR_POINTS) -> np.ndarray:
    """Fold descriptor sectors with too few points into a neighbour, or into `middle`.

    Deterministic: the smallest undersized sector goes first (ties by the `DESCRIPTORS` order), into
    whichever of its two compass neighbours or `middle` already holds the most points (ties again by
    `DESCRIPTORS` order); when none of those exists the sector becomes `middle`. Repeats until every
    sector is big enough or nothing can move. `middle` itself is never merged away -- it is the
    catch-all.
    """
    out = np.asarray(sectors, dtype=np.int64).copy()
    for _ in range(16):
        counts = np.bincount(out, minlength=9)
        small = [s for s in range(1, 9) if 0 < counts[s] < min_points]
        if not small:
            break
        small.sort(key=lambda s: (counts[s], s))
        moved = False
        for s in small:
            left = (s - 2) % 8 + 1
            right = s % 8 + 1
            options = [o for o in (0, left, right) if counts[o] > 0 and o != s]
            target = max(options, key=lambda o: (counts[o], -o)) if options else 0
            if target == s:
                continue
            out[out == s] = target
            moved = True
            break
        if not moved:
            break
    return out


def snap_into(geom, xs, ys) -> tuple[np.ndarray, np.ndarray]:
    """Move any point that is not inside `geom` to the nearest position on it."""
    xs = np.asarray(xs, dtype=float).copy()
    ys = np.asarray(ys, dtype=float).copy()
    if geom is None or geom.is_empty or not len(xs):
        return xs, ys
    shapely.prepare(geom)
    outside = np.flatnonzero(~shapely.contains_xy(geom, xs, ys))
    for i in outside:
        line = shapely.shortest_line(geom, shapely.points(xs[i], ys[i]))
        if line is None or line.is_empty:
            continue
        c = shapely.get_coordinates(line)
        xs[i], ys[i] = c[0]
    return xs, ys


def name_reach(usable_geom, xs, ys, convergence_deg: float) -> np.ndarray:
    """The width of the water each named feature sits in: its narrowest straight run, in metres."""
    if not len(xs):
        return np.zeros(0)
    return run_by_bearing(usable_geom, xs, ys, convergence_deg).min(axis=1)


def assign_labels(
    xs,
    ys,
    geom,
    usable_geom,
    names: list[dict],
    widths_m: np.ndarray,
    convergence_deg: float,
    min_named_points: int = MIN_NAMED_POINTS,
) -> list[str]:
    """A label for every point: a GNIS name when one owns it, else a position descriptor.

    GNIS hands over a point and no extent, so each name's extent is measured: `w_name`, the narrowest
    straight run through its position, is the width of the bay or channel it sits in. A name then
    claims a point when

    1. the point is within `NAME_RADIUS_FRACTION * w_name` of it (clamped, and a `Channel` never
       reaches past `CHANNEL_RADIUS_MAX_M` -- channels are line-like),
    2. the water at the point is no more than `NAME_WIDTH_FACTOR` times as wide as the name's own,
       which is what separates a bay from the open lake it opens into,
    3. the straight line from the point to the name stays on water, so a bay does not reach around
       a headland,

    and of the names that qualify, the nearest wins. A name left holding fewer than
    `MIN_NAMED_POINTS` points is not a region, and gives them back. Everything else falls through to
    a descriptor: `middle`, or one of the eight compass sectors, with thin sectors merged.
    """
    xs = np.asarray(xs, dtype=float)
    ys = np.asarray(ys, dtype=float)
    n = len(xs)
    labels: list[str | None] = [None] * n
    if n and names:
        nx, ny = snap_into(
            usable_geom,
            np.array([rec["x"] for rec in names], dtype=float),
            np.array([rec["y"] for rec in names], dtype=float),
        )
        reach = name_reach(usable_geom, nx, ny, convergence_deg)
        channel = np.array([rec["feature_class"] == "Channel" for rec in names], dtype=bool)
        limit = np.clip(NAME_RADIUS_FRACTION * reach, NAME_RADIUS_MIN_M, NAME_RADIUS_MAX_M)
        limit = np.where(channel, np.minimum(limit, CHANNEL_RADIUS_MAX_M), limit)
        dist = np.hypot(nx[None, :] - xs[:, None], ny[None, :] - ys[:, None])
        allowed = dist <= limit[None, :]
        allowed &= np.asarray(widths_m)[:, None] <= NAME_WIDTH_FACTOR * np.maximum(reach, 1.0)[None, :]
        shapely.prepare(geom)
        for i in np.flatnonzero(allowed.any(axis=1)):
            for j in np.flatnonzero(allowed[i])[np.argsort(dist[i][allowed[i]], kind="stable")]:
                if dist[i, j] > _EPS_M and not shapely.covers(
                    geom, LineString([(xs[i], ys[i]), (nx[j], ny[j])])
                ):
                    continue
                labels[i] = names[j]["name"]
                break
    tally = collections.Counter(lab for lab in labels if lab is not None)
    labels = [None if lab is not None and tally[lab] < min_named_points else lab for lab in labels]
    unnamed = np.flatnonzero(np.array([lab is None for lab in labels], dtype=bool))
    if len(unnamed):
        sectors = descriptor_sectors(xs[unnamed], ys[unnamed], usable_geom, convergence_deg)
        sectors = merge_small_sectors(sectors)
        for idx, s in zip(unnamed, sectors, strict=True):
            labels[idx] = DESCRIPTORS[int(s)]
    return [lab or DESCRIPTORS[0] for lab in labels]


# --- the binary file -----------------------------------------------------------


@dataclass
class WavePoint:
    lon: float
    lat: float
    depth_dm: int
    label: int
    fetch: list[int]  # 16 bins, units of FETCH_UNIT_M metres
    run: list[int]  # 8 bins, units of RUN_UNIT_FT feet


def _u16(value: float) -> int:
    return int(min(max(math.floor(float(value) + 0.5), 0), U16_MAX))


def pack_point(
    lon: float, lat: float, fetch_m, run_ft, depth_dm: int = DEPTH_UNKNOWN, label: int = 0
) -> WavePoint:
    return WavePoint(
        lon=float(lon),
        lat=float(lat),
        depth_dm=int(depth_dm),
        label=int(label),
        fetch=[_u16(v / FETCH_UNIT_M) for v in fetch_m],
        run=[_u16(v / RUN_UNIT_FT) for v in run_ft],
    )


def write_wave_points(bin_path: Path, json_path: Path, lakes: list[tuple[int, list[WavePoint]]], labels) -> dict:
    """Write `wave_points.bin` + `wave_points.json`. Records of one water body stay contiguous."""
    buf = bytearray()
    index: dict[str, list[int]] = {}
    for lake_id, points in lakes:
        if not points:
            continue
        index[str(int(lake_id))] = [len(buf) // RECORD_BYTES, len(points)]
        for p in points:
            buf += struct.pack(
                RECORD_FORMAT, p.lon, p.lat, int(p.depth_dm), int(p.label), *p.fetch, *p.run
            )
    meta = {
        "version": FORMAT_VERSION,
        "record_bytes": RECORD_BYTES,
        "fetch_unit_m": int(FETCH_UNIT_M),
        "run_unit_ft": int(RUN_UNIT_FT),
        "labels": list(labels),
        "lakes": index,
    }
    Path(bin_path).parent.mkdir(parents=True, exist_ok=True)
    Path(bin_path).write_bytes(bytes(buf))
    Path(json_path).write_text(json.dumps(meta, indent=1) + "\n", encoding="utf-8")
    return meta


def read_wave_points(bin_path: Path, json_path: Path) -> tuple[dict[int, list[WavePoint]], list[str], dict]:
    """Decode the pair written by `write_wave_points`: `({lake_id: [WavePoint]}, labels, meta)`."""
    meta = json.loads(Path(json_path).read_text())
    raw = Path(bin_path).read_bytes()
    size = int(meta.get("record_bytes", RECORD_BYTES))
    if size != RECORD_BYTES:
        raise ValueError(f"unsupported record size {size}")
    if len(raw) % RECORD_BYTES:
        raise ValueError(f"{bin_path} is not a whole number of {RECORD_BYTES}-byte records")
    out: dict[int, list[WavePoint]] = {}
    for key, (first, count) in meta["lakes"].items():
        points = []
        for r in range(first, first + count):
            values = struct.unpack_from(RECORD_FORMAT, raw, r * RECORD_BYTES)
            points.append(
                WavePoint(
                    lon=values[0],
                    lat=values[1],
                    depth_dm=values[2],
                    label=values[3],
                    fetch=list(values[4:20]),
                    run=list(values[20:28]),
                )
            )
        out[int(key)] = points
    return out, list(meta["labels"]), meta


# --- stage ---------------------------------------------------------------------


def _log_distribution(counts: list[int]) -> None:
    if not counts:
        log.warning("no water body qualified for a wave field")
        return
    arr = np.array(sorted(counts))
    log.info(
        "points: %d over %d water bodies (min %d, median %d, mean %.1f, max %d)",
        int(arr.sum()), len(arr), int(arr.min()), int(np.median(arr)), float(arr.mean()), int(arr.max()),
    )


def run(cfg: Config, args) -> int:
    import geopandas as gpd
    from pyproj import Transformer

    from . import bathymetry, gnis

    started = time.monotonic()
    work = cfg.work_dir
    lakes_path = work / "lakes.parquet"
    usable_path = work / "usable_water.parquet"
    if not lakes_path.exists() or not usable_path.exists():
        log.error("%s / %s not found; run `seaplane geometry` first", lakes_path, usable_path)
        return 2

    lakes = gpd.read_parquet(lakes_path)
    usable = gpd.read_parquet(usable_path)
    log.info("read %d water bodies, %d with a usable core", len(lakes), len(usable))

    proj = lakes.geometry.to_crs(MEASURE_CRS).to_numpy()
    usable_proj = usable.geometry.to_crs(MEASURE_CRS).to_numpy()
    usable_by_id = dict(zip(usable["id"].to_numpy(), usable_proj, strict=True))

    t0 = time.monotonic()
    mask = WaterMask(
        proj,
        max_offset_m=getattr(args, "artificial_offset", None) or ARTIFICIAL_OFFSET_M,
        min_artificial_m=getattr(args, "artificial_min_length", None) or ARTIFICIAL_MIN_LENGTH_M,
    )
    log.info(
        "fetch mask: %d polygons, %d boundary segments, %d internal, %d artificial (%.0f km), "
        "built in %.1fs",
        len(proj), len(mask.segments), int(mask.internal.sum()), int(mask.artificial.sum()),
        float((mask.segments.length * mask.artificial).sum()) / 1000.0, time.monotonic() - t0,
    )

    names_by_lake: dict[int, list[dict]] = {}
    if not getattr(args, "skip_names", False):
        names_by_lake = gnis.names_by_water_body(cfg, lakes, proj)

    depth_grid = None
    if not getattr(args, "skip_depth", False):
        depth_grid = bathymetry.load_grid(cfg)

    min_acres = getattr(args, "min_acres", None) or MIN_ACRES
    wanted = getattr(args, "lake_id", None)
    qualifies = (lakes["area_acres"].to_numpy() >= min_acres) & np.array(
        [i in usable_by_id for i in lakes["id"].to_numpy()]
    )
    rows = np.flatnonzero(qualifies)
    if wanted:
        keep = {int(v) for v in wanted}
        rows = np.array([i for i in rows if int(lakes["id"].iloc[i]) in keep], dtype=np.int64)
    if getattr(args, "limit", None):
        rows = rows[: args.limit]
    order = np.argsort(lakes["id"].to_numpy()[rows], kind="stable")
    rows = rows[order]
    log.info("%d water bodies qualify (>= %.0f acres with usable water)", len(rows), min_acres)

    to_wgs = Transformer.from_crs(MEASURE_CRS, WGS84, always_xy=True)
    results: list[tuple[int, list[WavePoint]]] = []
    all_labels: set[str] = set()
    pending: list[tuple[int, np.ndarray, np.ndarray, np.ndarray, np.ndarray, list[str]]] = []
    counts: list[int] = []
    fallback = 0
    t1 = time.monotonic()
    for n, i in enumerate(rows):
        t_lake = time.monotonic()
        lake_id = int(lakes["id"].iloc[i])
        geom = proj[i]
        core = usable_by_id[lake_id]
        area_m2 = float(lakes["area_acres"].iloc[i]) * M2_PER_ACRE
        spacing = point_spacing(
            area_m2,
            getattr(args, "target", None) or TARGET_POINTS,
            getattr(args, "big_target", None) or BIG_TARGET_POINTS,
        )
        pts = grid_points(core, spacing)
        if not len(pts):
            continue
        if len(pts) == 1 and core.area > spacing * spacing:
            fallback += 1
        xs, ys = pts[:, 0], pts[:, 1]
        kind = str(lakes["kind"].iloc[i]) if "kind" in lakes.columns else "lake"
        convergence = float(geom_mod.meridian_convergence_deg(
            float(lakes["lon"].iloc[i]), float(lakes["lat"].iloc[i])
        ))
        fetch_m = fetch_by_bearing(mask, xs, ys, convergence)
        run_m = run_by_bearing(core, xs, ys, convergence)
        widths = run_m.min(axis=1)
        lake_names = names_by_lake.get(lake_id, [])
        if kind not in CHANNEL_KINDS:
            lake_names = [rec for rec in lake_names if rec["feature_class"] != "Channel"]
        labels = assign_labels(xs, ys, geom, core, lake_names, widths, convergence)
        all_labels.update(labels)
        lon, lat = to_wgs.transform(xs, ys)
        pending.append((lake_id, kind, np.asarray(lon), np.asarray(lat), fetch_m, run_m * FT_PER_M, labels))
        counts.append(len(pts))
        if time.monotonic() - t_lake > 2.0:
            log.info(
                "  %s (%s, %.0f acres): %d points in %.1fs",
                lakes["name"].iloc[i] or f"#{lake_id}", kind, float(lakes["area_acres"].iloc[i]),
                len(pts), time.monotonic() - t_lake,
            )
        if n and n % 200 == 0:
            log.info("  %d/%d water bodies, %d points, %.1fs", n, len(rows), sum(counts), time.monotonic() - t1)
    log.info("measured %d points in %.1fs", sum(counts), time.monotonic() - t1)
    if fallback:
        log.info("%d water bodies fell back to a single representative point (grid missed the core)", fallback)

    label_list = sorted(all_labels)
    label_index = {lab: k for k, lab in enumerate(label_list)}
    depth_hits = 0
    for lake_id, kind, lon, lat, fetch_m, run_ft, labels in pending:
        depths = (
            depth_grid.depth_dm(lon, lat)
            if depth_grid is not None and kind in DEPTH_KINDS
            else np.full(len(lon), DEPTH_UNKNOWN, dtype=np.int64)
        )
        depth_hits += int((np.asarray(depths) != DEPTH_UNKNOWN).sum())
        points = [
            pack_point(lon[k], lat[k], fetch_m[k], run_ft[k], int(depths[k]), label_index[labels[k]])
            for k in range(len(lon))
        ]
        results.append((lake_id, points))

    bin_path = work / "wave_points.bin"
    json_path = work / "wave_points.json"
    write_wave_points(bin_path, json_path, results, label_list)
    _log_distribution(counts)
    log.info(
        "wrote %s (%.2f MB) and %s (%d labels, %d with a depth) in %.1fs total",
        bin_path, bin_path.stat().st_size / 1e6, json_path, len(label_list), depth_hits,
        time.monotonic() - started,
    )
    return 0
