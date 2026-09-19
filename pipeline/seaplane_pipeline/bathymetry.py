"""Optional depth for a sample point.

Depth is what keeps shallow water honest: the deep-water SPM form overstates a three-foot bay, and
Lake St. Clair averages about eleven feet (`docs/big-water-design.md`). It stays optional because no
national bathymetry exists -- without it the model falls back to deep water, the conservative
direction, and the record carries `depth_dm = 65535`.

Source in hand (`docs/gis-sources.md`, addendum): NOAA NCEI "Bathymetry of Lake Erie and Lake Saint
Clair" (DOI 10.7289/V5KS6PHK), the *lld* grid -- land, lake and depth in one raster.
`https://www.ngdc.noaa.gov/mgg/greatlakes/erie/data/binary_float/erie_lld.flt.tar.gz`, 22.5 MB
packed / 69 MB raw: 7201 x 2401 cells of 3 arc-seconds covering 84W-78W, 41N-43N (Lake St. Clair sits
inside it), NAD83 geographic, values in **metres relative to Low Water Datum, positive up**, nodata
-9999. So depth = -elevation, and anything at or above the datum is land or shoal and reads unknown.

The reader is a plain ESRI BIL/`.flt` header pair on purpose: no rasterio, no netCDF, no new
dependency, and the file memory-maps so the stage never holds 69 MB of float.
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
#: Grids outside these bounds are not consulted; each entry is the cached archive's member prefix.
ERIE_GRID_DIR = "erie_lld"


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

    def elevation(self, lon, lat) -> np.ndarray:
        """Nearest-cell elevation in metres; NaN outside the grid or at nodata."""
        lon = np.asarray(lon, dtype=float)
        lat = np.asarray(lat, dtype=float)
        rows, cols = self.values.shape
        col = np.floor((lon - (self.ulx - self.dx / 2)) / self.dx).astype(np.int64)
        row = np.floor(((self.uly + self.dy / 2) - lat) / self.dy).astype(np.int64)
        ok = (col >= 0) & (col < cols) & (row >= 0) & (row < rows)
        out = np.full(lon.shape, np.nan)
        if ok.any():
            v = np.asarray(self.values[row[ok], col[ok]], dtype=float)
            v[v == self.nodata] = np.nan
            out[ok] = v
        return out

    def depth_dm(self, lon, lat) -> np.ndarray:
        """Depth in decimetres, positive down; `DEPTH_UNKNOWN` off the grid, at nodata, or on land."""
        elev = self.elevation(lon, lat)
        depth_m = -elev
        good = np.isfinite(depth_m) & (depth_m > 0)
        out = np.full(np.shape(depth_m), DEPTH_UNKNOWN, dtype=np.int64)
        if good.any():
            dm = np.floor(depth_m[good] * 10.0 + 0.5).astype(np.int64)
            out[good] = np.clip(dm, 0, DEPTH_UNKNOWN - 1)
        return out


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
        tf.extractall(dest_dir, members=members)
    hdrs = sorted(dest_dir.glob("**/*.hdr"))
    return hdrs[0].parent if hdrs else None


def load_grid(cfg: Config) -> DepthGrid | None:
    """The cached bathymetry grid, or `None` when it has not been fetched (depth then stays unknown)."""
    from . import gis

    key = "bathymetry_erie"
    if key not in gis.DATASETS:
        return None
    archive = gis.find_dataset(cfg, key)
    if archive is None:
        log.info("no bathymetry cached (`seaplane fetch --only %s`); every depth stays unknown", key)
        return None
    folder = extract_archive(archive, cfg.cache_dir / ERIE_GRID_DIR)
    if folder is None:
        log.warning("%s holds no ESRI .hdr; every depth stays unknown", archive)
        return None
    hdrs = sorted(folder.glob("*.hdr"))
    grid = read_flt_grid(hdrs[0])
    log.info(
        "bathymetry %s: %d x %d cells of %.6f deg, bounds %s",
        grid.name, grid.shape[0], grid.shape[1], grid.dx,
        ", ".join(f"{v:.3f}" for v in grid.bounds),
    )
    return grid
