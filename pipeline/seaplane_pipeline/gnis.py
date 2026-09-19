"""GNIS Domestic Names: the names that label regions of a water body.

Source (`docs/gis-sources.md`, addendum): the USGS bulk per-state text file,
`DomesticNames_<ST>_Text.zip` (Michigan 0.86 MB), pipe-delimited, public domain. It is points only --
no source hands over a Muscamoot Bay polygon -- so a name is attached to a water body and then used
as a label on computed sample points, never as the unit of computation.

Which states are downloaded comes from `config.GNIS_STATES` (or `cfg.gnis_states`), not from a
constant here: the wave field has to work in every state (`docs/nationwide.md`).

The contract's classes are Bay, Channel and Harbor. GNIS has no `Harbor` class in practice -- it
files harbours under `Bay` ("Agate Harbor") -- but the name is accepted so a later vintage of the
data still works.
"""
from __future__ import annotations

import csv
import io
import logging
import zipfile
from pathlib import Path

import numpy as np

from .config import GNIS_STATES, Config

log = logging.getLogger(__name__)

NAME_CLASSES = ("Bay", "Channel", "Harbor")
#: A GNIS point this far outside a water body still belongs to it (docs/data-contract.md).
SNAP_M = 200.0

_NAME_COL = "feature_name"
_CLASS_COL = "feature_class"
_LAT_COL = "prim_lat_dec"
_LON_COL = "prim_long_dec"


def state_list(cfg: Config | None = None) -> tuple[str, ...]:
    return tuple(getattr(cfg, "gnis_states", None) or GNIS_STATES)


def read_names(path: Path, classes=NAME_CLASSES) -> list[dict]:
    """Parse one `DomesticNames_<ST>_Text.zip` (or the extracted `.txt`) into name records."""
    wanted = {c.lower() for c in classes}
    rows: list[dict] = []
    path = Path(path)
    if path.suffix.lower() == ".zip":
        with zipfile.ZipFile(path) as zf:
            members = [n for n in zf.namelist() if n.lower().endswith(".txt")]
            if not members:
                raise ValueError(f"{path} holds no .txt member")
            raw = zf.read(min(members))
    else:
        raw = path.read_bytes()
    # The file is UTF-8 with a BOM and pipe-delimited.
    reader = csv.DictReader(io.StringIO(raw.decode("utf-8-sig", errors="replace")), delimiter="|")
    for row in reader:
        klass = (row.get(_CLASS_COL) or "").strip()
        if klass.lower() not in wanted:
            continue
        name = (row.get(_NAME_COL) or "").strip()
        try:
            lat = float(row.get(_LAT_COL) or "nan")
            lon = float(row.get(_LON_COL) or "nan")
        except ValueError:
            continue
        if not name or not (np.isfinite(lat) and np.isfinite(lon)) or (lat == 0.0 and lon == 0.0):
            continue
        rows.append({"name": name, "feature_class": klass, "lat": lat, "lon": lon})
    rows.sort(key=lambda r: (r["name"], r["lat"], r["lon"]))
    return rows


def load_names(cfg: Config, classes=NAME_CLASSES) -> list[dict]:
    """Every Bay/Channel/Harbor record for the configured states, or `[]` when nothing is cached."""
    from . import gis

    rows: list[dict] = []
    for state in state_list(cfg):
        key = f"gnis_{state.lower()}"
        if key not in gis.DATASETS:
            log.warning("no GNIS dataset registered for %s", state)
            continue
        path = gis.find_dataset(cfg, key)
        if path is None:
            log.warning("GNIS names for %s not downloaded; run `seaplane fetch --only %s`", state, key)
            continue
        found = read_names(path, classes)
        log.info("GNIS %s: %d %s features", state, len(found), "/".join(classes))
        rows.extend(found)
    return rows


def names_by_water_body(cfg: Config, lakes, proj_geoms, snap_m: float = SNAP_M) -> dict[int, list[dict]]:
    """`{lake_id: [{name, feature_class, x, y}]}` in the measurement CRS.

    A name belongs to a water body when its point is inside it or within `snap_m` of it, so the 13 of
    14 St. Clair bay points that fall inside the polygon and the one just outside all attach.
    """
    import shapely
    from pyproj import Transformer

    from .wavefield import MEASURE_CRS, WGS84

    rows = load_names(cfg, classes=classes_for(cfg))
    out: dict[int, list[dict]] = {}
    if not rows or not len(proj_geoms):
        return out
    to_proj = Transformer.from_crs(WGS84, MEASURE_CRS, always_xy=True)
    xs, ys = to_proj.transform(
        np.array([r["lon"] for r in rows]), np.array([r["lat"] for r in rows])
    )
    pts = shapely.points(np.asarray(xs), np.asarray(ys))
    tree = shapely.STRtree(np.asarray(list(proj_geoms), dtype=object))
    try:
        pairs = tree.query(pts, predicate="dwithin", distance=snap_m)
    except (TypeError, ValueError):  # GEOS without dwithin
        pairs = tree.query(shapely.buffer(pts, snap_m), predicate="intersects")
    ids = lakes["id"].to_numpy()
    for pi, gi in zip(pairs[0], pairs[1], strict=True):
        rec = rows[int(pi)]
        out.setdefault(int(ids[gi]), []).append(
            {
                "name": rec["name"],
                "feature_class": rec["feature_class"],
                "x": float(xs[int(pi)]),
                "y": float(ys[int(pi)]),
            }
        )
    for recs in out.values():
        recs.sort(key=lambda r: (r["name"], r["x"], r["y"]))
    log.info("GNIS: %d names attached to %d water bodies", sum(len(v) for v in out.values()), len(out))
    return out


def classes_for(cfg: Config) -> tuple[str, ...]:
    return tuple(getattr(cfg, "gnis_classes", None) or NAME_CLASSES)
