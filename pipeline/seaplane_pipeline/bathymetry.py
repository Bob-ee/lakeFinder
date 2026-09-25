"""Optional depth for a sample point.

Depth is what keeps shallow water honest: the deep-water SPM form overstates a three-foot bay, and
Lake St. Clair averages about eleven feet (`docs/big-water-design.md`). It stays optional because no
national bathymetry exists -- without it the model falls back to deep water, the conservative
direction, and the record carries `depth_dm = 65535`.

Sources (`docs/gis-sources.md`): the NOAA NCEI Great Lakes bathymetry *lld* grids -- land, lake and
depth in one raster, one per lake, all in the same form: 3 arc-second cells, NAD83 geographic, values
in **metres relative to the lake's Low Water Datum, positive up**, nodata -9999, shipped as an ESRI
BIL `.flt` + `.hdr` pair in a `.tar.gz`. So depth = -elevation, and anything at or above the datum is
land or shoal. `GRID_SOURCES` is the list; the fetch stage takes the grids whose bbox meets the
region's (`grids_for_bbox`), so another grid is one entry here and nothing else changes.

Sampling (`DepthGrids.depth_dm`) runs in two passes, so an exact hit in any grid beats a snapped one:

1. the point's own cell, grid by grid in list order; the first wet cell (elevation < 0) wins;
2. for points still unknown, the nearest wet cell within `SNAP_CELLS` cells (ground distance, ties
   to the deeper cell), again in list order. The grids' shorelines are a DEM ramp that does not
   match the water polygons cell for cell: a polygon point on the shore often lands on a cell a few
   decimetres above the datum with the lake a cell or two away. Nothing is inferred further out.

The reader is plain numpy on purpose: no rasterio, no netCDF, no new dependency, and the files
memory-map so the stage never holds a few hundred MB of float.
"""
from __future__ import annotations

import logging
import re
import tarfile
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .config import Config

log = logging.getLogger(__name__)

DEPTH_UNKNOWN = 65535
#: Shore snap radius in cells. A 3 arc-second cell is ~93 m north-south and ~60-70 m east-west over
#: the Great Lakes, so this reaches ~190 m at most. Data contract, "Wave field": a point whose cell is
#: land or nodata takes the nearest wet cell within this many cells and stays unknown beyond it.
SNAP_CELLS = 2

NCEI_GREAT_LAKES = "https://www.ngdc.noaa.gov/mgg/greatlakes/{lake}/data/binary_float/{lake}_lld.flt.tar.gz"


@dataclass(frozen=True)
class GridSource:
    """One downloadable depth grid. `bbox` is (west, south, east, north) in degrees."""

    key: str  # dataset key in `gis.DATASETS`
    filename: str  # cached archive name
    url: str
    bbox: tuple[float, float, float, float]
    note: str = ""

    @property
    def folder(self) -> str:
        """Cache directory the archive unpacks into (`erie_lld.flt.tar.gz` -> `erie_lld`)."""
        return self.filename.split(".", 1)[0]


def _ncei(lake: str, bbox: tuple[float, float, float, float], citation: str) -> GridSource:
    return GridSource(
        key=f"bathymetry_{lake}",
        filename=f"{lake}_lld.flt.tar.gz",
        url=NCEI_GREAT_LAKES.format(lake=lake),
        bbox=bbox,
        note=(
            f"NCEI {citation}, the lld grid: 3 arc-second cells, NAD83, metres relative to Low Water "
            "Datum positive up, nodata -9999; ESRI float + .hdr, so it needs no raster library."
        ),
    )


#: Every depth grid the pipeline knows, in lookup order. Bboxes are the cell-edge bounds of each
#: grid's `.hdr` (checked 2026-09-25). Neighbors overlap at the edges (Huron and Superior over the
#: St. Marys River, Huron and Michigan at the Straits); the first grid with a wet cell wins.
GRID_SOURCES: tuple[GridSource, ...] = (
    _ncei("erie", (-84.0004, 40.9996, -77.9996, 43.0004),
          "'Bathymetry of Lake Erie and Lake Saint Clair' (doi:10.7289/V5KS6PHK), 7,201 x 2,401 cells, 22.5 MB"),
    _ncei("huron", (-84.5004, 42.9996, -79.6796, 46.5004),
          "'Bathymetry of Lake Huron' (doi:10.7289/V5G15XS5), 5,785 x 4,201 cells, 40 MB"),
    _ncei("michigan", (-88.0004, 41.6196, -84.4996, 46.0904),
          "'Bathymetry of Lake Michigan' (doi:10.7289/V5B85627), 4,201 x 5,365 cells, 47 MB"),
    _ncei("superior", (-92.2004, 45.9996, -83.9996, 49.5004),
          "Lake Superior draft grid (NCEI lists it 'incomplete', no DOI), 9,841 x 4,201 cells, 100 MB"),
)


def grids_for_bbox(bbox, sources: tuple[GridSource, ...] = GRID_SOURCES) -> list[GridSource]:
    """The sources whose bbox meets `bbox` (west, south, east, north), in lookup order."""
    w, s, e, n = bbox
    return [g for g in sources if g.bbox[0] <= e and g.bbox[2] >= w and g.bbox[1] <= n and g.bbox[3] >= s]


@dataclass
class DepthGrid:
    """A north-up geographic raster of elevation in metres, positive up (depth is its negative)."""

    values: np.ndarray  # (rows, cols)
    ulx: float  # longitude of the upper-left cell centre
    uly: float  # latitude of the upper-left cell centre
    dx: float
    dy: float
    nodata: float = -9999.0
    name: str = ""

    @property
    def shape(self) -> tuple[int, int]:
        return self.values.shape

    @property
    def bounds(self) -> tuple[float, float, float, float]:
        rows, cols = self.values.shape
        return (
            self.ulx - self.dx / 2,
            self.uly - self.dy * (rows - 0.5),
            self.ulx + self.dx * (cols - 0.5),
            self.uly + self.dy / 2,
        )

    def _cells(self, lon, lat) -> tuple[np.ndarray, np.ndarray]:
        lon = np.asarray(lon, dtype=float)
        lat = np.asarray(lat, dtype=float)
        col = np.floor((lon - (self.ulx - self.dx / 2)) / self.dx).astype(np.int64)
        row = np.floor(((self.uly + self.dy / 2) - lat) / self.dy).astype(np.int64)
        return row, col

    def _read(self, row: np.ndarray, col: np.ndarray) -> np.ndarray:
        """Elevation at cell indices; NaN outside the grid or at nodata."""
        rows, cols = self.values.shape
        ok = (col >= 0) & (col < cols) & (row >= 0) & (row < rows)
        out = np.full(row.shape, np.nan)
        if ok.any():
            v = np.asarray(self.values[row[ok], col[ok]], dtype=float)
            v[v == self.nodata] = np.nan
            out[ok] = v
        return out

    def elevation(self, lon, lat) -> np.ndarray:
        """Nearest-cell elevation in metres; NaN outside the grid or at nodata."""
        row, col = self._cells(lon, lat)
        return self._read(row, col)

    def depth_m(self, lon, lat, snap_cells: int = 0) -> np.ndarray:
        """Depth in metres, positive down; NaN when unknown.

        With `snap_cells > 0`, a point whose own cell is not wet takes the nearest wet cell within
        that many cells (ground distance at the point's latitude, ties to the deeper cell).
        """
        lat_arr = np.asarray(lat, dtype=float)
        row, col = self._cells(lon, lat)
        depth = -self._read(row, col)
        depth[~(depth > 0)] = np.nan
        if snap_cells <= 0:
            return depth
        todo = np.flatnonzero(np.isnan(depth))
        if not len(todo):
            return depth
        offsets = [
            (dr, dc)
            for dr in range(-snap_cells, snap_cells + 1)
            for dc in range(-snap_cells, snap_cells + 1)
            if (dr, dc) != (0, 0)
        ]
        dr = np.array([o[0] for o in offsets], dtype=np.int64)
        dc = np.array([o[1] for o in offsets], dtype=np.int64)
        r = row[todo][:, None] + dr[None, :]
        c = col[todo][:, None] + dc[None, :]
        cand = -self._read(r, c)
        wet = cand > 0
        # Ground distance in north-south cell units: east-west cells shrink with cos(latitude).
        shrink = np.cos(np.radians(lat_arr[todo]))[:, None] * (self.dx / self.dy)
        dist = np.hypot(dr[None, :].astype(float), dc[None, :] * shrink)
        dist = np.where(wet, dist, np.inf)
        best = dist.min(axis=1)
        near = wet & (dist <= best[:, None] + 1e-9)
        pick = np.where(near, cand, -np.inf).max(axis=1)
        found = np.isfinite(best)
        depth[todo[found]] = pick[found]
        return depth

    def depth_dm(self, lon, lat, snap_cells: int = 0) -> np.ndarray:
        """Depth in decimetres, positive down; `DEPTH_UNKNOWN` off the grid, at nodata, or on land."""
        return to_dm(self.depth_m(lon, lat, snap_cells))


def to_dm(depth_m: np.ndarray) -> np.ndarray:
    depth_m = np.asarray(depth_m, dtype=float)
    good = np.isfinite(depth_m) & (depth_m > 0)
    out = np.full(np.shape(depth_m), DEPTH_UNKNOWN, dtype=np.int64)
    if good.any():
        dm = np.floor(depth_m[good] * 10.0 + 0.5).astype(np.int64)
        out[good] = np.clip(dm, 0, DEPTH_UNKNOWN - 1)
    return out


@dataclass
class DepthGrids:
    """Several grids read as one, in lookup order (see the module docstring for the two passes)."""

    grids: list[DepthGrid]
    snap_cells: int = SNAP_CELLS

    def depth_dm(self, lon, lat) -> np.ndarray:
        lon = np.asarray(lon, dtype=float)
        lat = np.asarray(lat, dtype=float)
        depth = np.full(lon.shape, np.nan)
        for radius in (0, self.snap_cells) if self.snap_cells > 0 else (0,):
            for grid in self.grids:
                todo = np.flatnonzero(np.isnan(depth))
                if not len(todo):
                    break
                w, s, e, n = grid.bounds
                inside = todo[(lon[todo] >= w) & (lon[todo] <= e) & (lat[todo] >= s) & (lat[todo] <= n)]
                if len(inside):
                    depth[inside] = grid.depth_m(lon[inside], lat[inside], radius)
        return to_dm(depth)


def read_bil_header(path: Path) -> dict:
    """Parse an ESRI `.hdr` (BIL/BSQ header, the `.flt` companion) into a lowercase dict."""
    meta: dict[str, str] = {}
    for line in Path(path).read_text(errors="replace").splitlines():
        parts = re.split(r"\s+", line.strip(), maxsplit=1)
        if len(parts) == 2:
            meta[parts[0].strip().lower()] = parts[1].strip()
    return meta


def read_flt_grid(hdr_path: Path, data_path: Path | None = None, mmap: bool = True) -> DepthGrid:
    """Read a float32 ESRI BIL/`.flt` raster described by `hdr_path`."""
    hdr_path = Path(hdr_path)
    meta = read_bil_header(hdr_path)
    cols = int(float(meta["ncols"]))
    rows = int(float(meta["nrows"]))
    nbits = int(float(meta.get("nbits", 32)))
    if nbits != 32 or meta.get("pixeltype", "FLOAT").upper() != "FLOAT":
        raise ValueError(f"{hdr_path}: only 32-bit float rasters are supported")
    dtype = np.dtype("<f4" if meta.get("byteorder", "I").upper().startswith("I") else ">f4")
    data_path = Path(data_path) if data_path else hdr_path.with_suffix(".flt")
    if mmap:
        values = np.memmap(data_path, dtype=dtype, mode="r", shape=(rows, cols))
    else:
        values = np.fromfile(data_path, dtype=dtype).reshape(rows, cols)
    # ULXMAP/ULYMAP are the *centre* of the upper-left cell in the ESRI world.
    return DepthGrid(
        values=values,
        ulx=float(meta.get("ulxmap", 0.0)),
        uly=float(meta.get("ulymap", 0.0)),
        dx=float(meta.get("xdim", 1.0)),
        dy=float(meta.get("ydim", 1.0)),
        nodata=float(meta.get("nodata", -9999.0)),
        name=hdr_path.stem,
    )


def extract_archive(archive: Path, dest_dir: Path) -> Path | None:
    """Unpack a cached `.tar.gz` grid beside itself once; return the directory holding the `.hdr`."""
    archive = Path(archive)
    dest_dir = Path(dest_dir)
    hdrs = sorted(dest_dir.glob("**/*.hdr"))
    if hdrs:
        return hdrs[0].parent
    if not archive.exists():
        return None
    dest_dir.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive) as tf:
        members = [m for m in tf.getmembers() if m.isfile() and ".." not in m.name]
        # Members are filtered to plain files with no parent references before extracting.
        tf.extractall(dest_dir, members=members, filter="data")
    hdrs = sorted(dest_dir.glob("**/*.hdr"))
    return hdrs[0].parent if hdrs else None


def load_grid(cfg: Config) -> DepthGrids | None:
    """Every cached grid the region uses, or `None` when none is fetched (depth then stays unknown)."""
    from . import gis

    sources = [s for s in GRID_SOURCES if s.key in gis.DATASETS]
    grids: list[DepthGrid] = []
    for src in sources:
        archive = gis.find_dataset(cfg, src.key)
        if archive is None:
            log.info("%s not cached (`seaplane fetch --only %s`); its water stays depth-unknown", src.key, src.key)
            continue
        folder = extract_archive(archive, cfg.cache_dir / src.folder)
        if folder is None:
            log.warning("%s holds no ESRI .hdr; its water stays depth-unknown", archive)
            continue
        grid = read_flt_grid(min(folder.glob("*.hdr")))
        log.info(
            "bathymetry %s: %d x %d cells of %.6f deg, bounds %s",
            grid.name, grid.shape[0], grid.shape[1], grid.dx,
            ", ".join(f"{v:.3f}" for v in grid.bounds),
        )
        grids.append(grid)
    if not grids:
        log.info("no bathymetry cached; every depth stays unknown")
        return None
    return DepthGrids(grids)

