"""Stage 5 (`overlay`): public access, federal unit and airspace flags per lake.

Input:  `data/work/lakes.parquet` plus the BAS, NPS, FWS and FAA airspace downloads.
Output: `data/work/overlays.json`, keyed by lake id:

    {"1234567": {"public_access": true, "access": "Cass Lake BAS",
                 "federal_unit": null, "airspace_class": "D"}}

Rules (docs/design.md section 5, docs/gis-sources.md sections 3-5):

- `public_access`: a DNR boating access site with `waterbodytype == "Inland Lake"` within 50 m of the
  polygon. The site `name` is stored as `access` (nearest site wins).
- `federal_unit`: the NPS unit (`STATE='MI'`) or USFWS refuge (`RSL_TYPE='NWR'`, Michigan `LIT`
  allowlist -- the layer has no STATE field) whose polygon contains the lake centroid.
- `airspace_class`: `CLASS` of the FAA class-airspace polygon containing the centroid. Polygons
  overlap (a Class B shelf sits inside its own Mode C veil, whose `CLASS` is null), so null classes
  are dropped and the most restrictive remaining letter wins. **Only polygons whose floor is the
  surface count** (`LOWER_CODE == 'SFC'` or `LOWER_VAL == 0`): a Detroit Class B shelf with a
  6,000 ft MSL floor is not the airspace you land in, and counting it would label 264 of Oakland's
  548 lakes "Class B". (`LOWER_CODE` is "SFC" even for a 700 ft Class E floor -- it means "measured
  from the surface" -- so the test is `LOWER_VAL == 0`.) Pass `--airspace-any-floor` for the literal containing-polygon behaviour.
  All airspace polygons are still drawn as a context layer in `overlays.pmtiles`.
"""
from __future__ import annotations

import json
import logging
import time

import geopandas as gpd
import pandas as pd

from . import gis
from .config import Config

log = logging.getLogger(__name__)

MEASURE_CRS = "EPSG:3078"
WGS84 = "EPSG:4326"
ACCESS_DISTANCE_M = 50.0
# docs/gis-sources.md section 4: the refuge layer has no STATE field; these are the Michigan units.
MI_FWS_LIT = {"DTR", "MCH", "SNY", "SHW", "HRN", "HBR", "KIW"}
AIRSPACE_ORDER = ["B", "C", "D", "E", "G"]


def add_args(sp) -> None:
    sp.add_argument("--access-distance-m", type=float, default=ACCESS_DISTANCE_M)
    sp.add_argument(
        "--airspace-any-floor",
        action="store_true",
        help="Count airspace whose floor is above the surface (Class B shelves) as well",
    )


def _to_proj(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    if gdf.crs is None:
        gdf = gdf.set_crs(WGS84)
    return gdf.to_crs(MEASURE_CRS)


def load_federal_units(cfg: Config) -> gpd.GeoDataFrame:
    """NPS units + Michigan USFWS refuges as one `name`/`kind` frame (WGS84)."""
    frames = []
    nps_path = gis.find_dataset(cfg, "nps")
    if nps_path is not None:
        nps = gpd.read_file(nps_path)
        frames.append(
            gpd.GeoDataFrame(
                {"name": nps["UNIT_NAME"].astype(str).str.strip(), "kind": "nps"},
                geometry=nps.geometry,
                crs=nps.crs or WGS84,
            )
        )
    fws_path = gis.find_dataset(cfg, "fws")
    if fws_path is not None:
        fws = gpd.read_file(fws_path)
        if "RSL_TYPE" in fws:
            fws = fws[fws["RSL_TYPE"].astype(str).str.strip() == "NWR"]
        if "LIT" in fws:
            fws = fws[fws["LIT"].astype(str).str.strip().isin(MI_FWS_LIT)]
        frames.append(
            gpd.GeoDataFrame(
                {"name": fws["ORGNAME"].astype(str).str.strip().str.title(), "kind": "fws"},
                geometry=fws.geometry,
                crs=fws.crs or WGS84,
            )
        )
    if not frames:
        return gpd.GeoDataFrame({"name": [], "kind": []}, geometry=[], crs=WGS84)
    return gpd.GeoDataFrame(pd.concat(frames, ignore_index=True), crs=WGS84)


def _reaches_surface(air: gpd.GeoDataFrame):
    """True where the airspace floor is the surface (what a seaplane on the water is actually in)."""
    # LOWER_CODE is "SFC" even for a Class E floor of 700 ft (it means "measured from the
    # surface", not "starts at the surface"), so LOWER_VAL == 0 is the real test.
    mask = pd.Series(True, index=air.index)
    if "LOWER_VAL" in air:
        mask &= pd.to_numeric(air["LOWER_VAL"], errors="coerce").fillna(-1).eq(0)
    elif "LOWER_CODE" in air:
        mask &= air["LOWER_CODE"].astype("string").str.strip().str.upper().eq("SFC").fillna(False)
    return mask


def compute_overlays(
    lakes: gpd.GeoDataFrame,
    bas: gpd.GeoDataFrame | None = None,
    federal: gpd.GeoDataFrame | None = None,
    airspace: gpd.GeoDataFrame | None = None,
    access_distance_m: float = ACCESS_DISTANCE_M,
    airspace_any_floor: bool = False,
) -> dict[str, dict]:
    lakes = lakes.reset_index(drop=True)
    if lakes.crs is None:
        lakes = lakes.set_crs(WGS84)
    proj = _to_proj(lakes)
    out: dict[str, dict] = {
        str(int(i)): {"public_access": False, "access": None, "federal_unit": None, "airspace_class": None}
        for i in lakes["id"]
    }

    if bas is not None and len(bas):
        sites = bas
        if "waterbodytype" in sites:
            sites = sites[sites["waterbodytype"].astype(str).str.strip() == "Inland Lake"]
        sites = sites[~sites.geometry.isna()]
        if len(sites):
            joined = gpd.sjoin_nearest(
                proj[["id", "geometry"]],
                _to_proj(sites.reset_index(drop=True))[["name", "geometry"]],
                how="inner",
                max_distance=access_distance_m,
                distance_col="_dist",
            ).sort_values("_dist")
            for lake_id, name in zip(joined["id"], joined["name"], strict=True):
                entry = out[str(int(lake_id))]
                if not entry["public_access"]:
                    entry["public_access"] = True
                    entry["access"] = None if pd.isna(name) else str(name).strip() or None

    centroids = gpd.GeoDataFrame({"id": lakes["id"]}, geometry=proj.geometry.centroid, crs=MEASURE_CRS)

    if federal is not None and len(federal):
        joined = gpd.sjoin(centroids, _to_proj(federal)[["name", "geometry"]], how="inner", predicate="within")
        for lake_id, name in zip(joined["id"], joined["name"], strict=True):
            if out[str(int(lake_id))]["federal_unit"] is None and not pd.isna(name):
                out[str(int(lake_id))]["federal_unit"] = str(name)

    if airspace is not None and len(airspace):
        air = airspace.copy()
        col = "CLASS" if "CLASS" in air else "class"
        air[col] = air[col].astype("string").str.strip().str.upper()
        air = air[air[col].notna() & (air[col] != "") & (air[col] != "NONE")]
        if not airspace_any_floor:
            air = air[_reaches_surface(air)]
        if len(air):
            joined = gpd.sjoin(
                centroids, _to_proj(air.reset_index(drop=True))[[col, "geometry"]],
                how="inner", predicate="within",
            )
            for lake_id, klass in zip(joined["id"], joined[col], strict=True):
                entry = out[str(int(lake_id))]
                current = entry["airspace_class"]
                rank = AIRSPACE_ORDER.index(klass) if klass in AIRSPACE_ORDER else len(AIRSPACE_ORDER)
                crank = AIRSPACE_ORDER.index(current) if current in AIRSPACE_ORDER else len(AIRSPACE_ORDER) + 1
                if current is None or rank < crank:
                    entry["airspace_class"] = str(klass)
    return out


def run(cfg: Config, args) -> int:
    started = time.monotonic()
    lakes_path = cfg.work_dir / "lakes.parquet"
    if not lakes_path.exists():
        log.error("%s not found; run `seaplane geometry` first", lakes_path)
        return 2
    lakes = gpd.read_parquet(lakes_path)

    bas_path = gis.find_dataset(cfg, "bas")
    bas = gpd.read_file(bas_path) if bas_path else None
    air_path = gis.find_dataset(cfg, "faa_airspace")
    airspace = gpd.read_file(air_path) if air_path else None
    federal = load_federal_units(cfg)

    overlays = compute_overlays(
        lakes,
        bas,
        federal,
        airspace,
        access_distance_m=getattr(args, "access_distance_m", ACCESS_DISTANCE_M),
        airspace_any_floor=getattr(args, "airspace_any_floor", False),
    )
    out_path = cfg.work_dir / "overlays.json"
    out_path.write_text(json.dumps(overlays, indent=1), encoding="utf-8")
    log.info(
        "overlays for %d lakes in %.1fs: %d with public access, %d in a federal unit, %d in class airspace",
        len(overlays),
        time.monotonic() - started,
        sum(1 for v in overlays.values() if v["public_access"]),
        sum(1 for v in overlays.values() if v["federal_unit"]),
        sum(1 for v in overlays.values() if v["airspace_class"]),
    )
    return 0
