"""Download helpers for the GIS datasets.

Every URL, field name and gotcha below comes from `docs/gis-sources.md` (live recon, 2026-09-15).
Highlights that shaped this module:

- Hydrography and PLSS are fetched through the Open Data **direct download** endpoint (one request,
  ~5 s, already WGS84) instead of 60/61 paginated FeatureServer queries.
- The `gisago.mcgi.state.mi.us` host resets connections; `gisagocss.state.mi.us` serves the same
  `OpenData/*` services and is used here.
- `gis.fws.gov` 502s; the Esri Living Atlas mirror of the refuge boundaries is used instead.
- Everything else is small enough for a paged `/query` (`resultOffset` + `exceededTransferLimit`).
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from .config import MICHIGAN_BBOX, USER_AGENT, Config

log = logging.getLogger(__name__)

OPENDATA_DOWNLOAD = "https://gis-michigan.opendata.arcgis.com/api/download/v1/items/{item}/geojson"
# gisago.mcgi.state.mi.us resets every request (docs/gis-sources.md network note) -- use gisagocss.
GISAGO = "https://gisagocss.state.mi.us/arcgis/rest/services"

_BBOX_PARAMS = {
    "geometry": ",".join(str(v) for v in MICHIGAN_BBOX),
    "geometryType": "esriGeometryEnvelope",
    "inSR": "4326",
    "spatialRel": "esriSpatialRelIntersects",
}


@dataclass(frozen=True)
class Dataset:
    key: str
    filename: str
    store: str  # "cache" (large, date-independent) | "raw" (small, per-run)
    kind: str  # "direct" | "query"
    url: str
    params: dict[str, str] = field(default_factory=dict)
    note: str = ""


DATASETS: dict[str, Dataset] = {
    "hydrography": Dataset(
        key="hydrography",
        filename="hydrography_polygons.geojson",
        store="cache",
        kind="direct",
        url=OPENDATA_DOWNLOAD.format(item="e6f0b7dfb22d4ed49a05969970441f4f"),
        params={"layers": "17"},
        note="Michigan Hydrography Polygons, 59,922 features (~124 MB), WGS84.",
    ),
    "plss": Dataset(
        key="plss",
        filename="plss_sections.geojson",
        store="cache",
        kind="direct",
        url=OPENDATA_DOWNLOAD.format(item="5e087317ab6a4fb28abe4d41d8204e95"),
        params={"layers": "4"},
        note="Public Land Survey Sections, 61,340 features (~75 MB). TOWN/RANGE are zero-padded.",
    ),
    "counties": Dataset(
        key="counties",
        filename="counties.geojson",
        store="cache",
        kind="query",
        url=f"{GISAGO}/OpenData/michigan_geographic_framework/MapServer/0/query",
        params={"where": "1=1", "outFields": "NAME,LABEL,FIPSCODE,CNTY_CODE", "geometryPrecision": "6"},
        note="83 county polygons; hydrography has no county field, so county comes from a centroid join.",
    ),
    "civil_townships": Dataset(
        key="civil_townships",
        filename="minor_civil_divisions.geojson",
        store="cache",
        kind="query",
        url=f"{GISAGO}/OpenData/michigan_geographic_framework/MapServer/2/query",
        params={"where": "1=1", "outFields": "NAME,LABEL,TYPE,FIPSCODE", "geometryPrecision": "6"},
        note="1,520 minor civil divisions (townships, cities, villages) for the optional township field.",
    ),
    "bas": Dataset(
        key="bas",
        filename="boating_access_sites.geojson",
        store="raw",
        kind="query",
        url=(
            "https://services3.arcgis.com/Jdnp1TjADvSDxMAX/arcgis/rest/services/"
            "PRDBASPublicView/FeatureServer/0/query"
        ),
        params={"where": "1=1", "outFields": "*"},
        note="1,203 DNR boating access sites; filter to waterbodytype='Inland Lake' in overlay.",
    ),
    "nps": Dataset(
        key="nps",
        filename="nps_boundary.geojson",
        store="raw",
        kind="query",
        url=(
            "https://services1.arcgis.com/fBc8EJBxQRMcHlei/arcgis/rest/services/"
            "NPS_Land_Resources_Division_Boundary_and_Tract_Data_Service/FeatureServer/2/query"
        ),
        params={"where": "STATE='MI'", "outFields": "*"},
        note="5 Michigan NPS units. STATE='MI' is exact; a bbox pulls in neighbouring states.",
    ),
    "fws": Dataset(
        key="fws",
        filename="fws_refuge_boundary.geojson",
        store="raw",
        kind="query",
        url=(
            "https://services.arcgis.com/QVENGdaPbd4LUkLV/arcgis/rest/services/"
            "National_Wildlife_Refuge_System_Boundaries/FeatureServer/0/query"
        ),
        params={"where": "RSL_TYPE='NWR'", "outFields": "*", **_BBOX_PARAMS},
        note="Living Atlas mirror (gis.fws.gov 502s). No STATE field: bbox + RSL_TYPE, trimmed later.",
    ),
    "faa_airspace": Dataset(
        key="faa_airspace",
        filename="faa_class_airspace.geojson",
        store="raw",
        kind="query",
        url=(
            "https://services6.arcgis.com/ssFJjBXIUyZDrSYZ/arcgis/rest/services/"
            "Class_Airspace/FeatureServer/0/query"
        ),
        params={"where": "STATE='MI'", "outFields": "*", **_BBOX_PARAMS},
        note="FAA class airspace polygons (B/C/D/E).",
    ),
    "faa_airports": Dataset(
        key="faa_airports",
        filename="faa_airports.geojson",
        store="raw",
        kind="query",
        url=(
            "https://services6.arcgis.com/ssFJjBXIUyZDrSYZ/arcgis/rest/services/"
            "US_Airport/FeatureServer/0/query"
        ),
        params={"where": "STATE='MI'", "outFields": "*", **_BBOX_PARAMS},
        note="490 Michigan airports/heliports/seaplane bases, context layer only.",
    ),
}


def dest_path(cfg: Config, ds: Dataset) -> Path:
    base = cfg.cache_dir if ds.store == "cache" else cfg.raw_dir
    return base / ds.filename


def find_dataset(cfg: Config, key: str) -> Path | None:
    """Locate a already-fetched dataset.

    Looks in this run's `raw/<date>/` first, then the newest earlier `raw/<date>/`, then `cache/`,
    so downstream stages still work on a day when `fetch` has not been re-run.
    """
    ds = DATASETS[key]
    primary = dest_path(cfg, ds)
    if primary.exists():
        return primary
    if ds.store == "raw":
        raw_root = cfg.data_dir / "raw"
        if raw_root.is_dir():
            for day in sorted((p for p in raw_root.iterdir() if p.is_dir()), reverse=True):
                candidate = day / ds.filename
                if candidate.exists():
                    return candidate
    fallback = cfg.cache_dir / ds.filename
    return fallback if fallback.exists() else None


def require_dataset(cfg: Config, key: str) -> Path:
    path = find_dataset(cfg, key)
    if path is None:
        raise FileNotFoundError(
            f"dataset '{key}' ({DATASETS[key].filename}) not found; run `seaplane fetch` first"
        )
    return path


# --- http --------------------------------------------------------------------


def _client(timeout: float = 120.0) -> httpx.Client:
    return httpx.Client(
        headers={"User-Agent": USER_AGENT, "Accept": "*/*"},
        follow_redirects=True,
        timeout=httpx.Timeout(timeout, connect=30.0),
    )


def _retrying(fn, *, attempts: int = 4, base_delay: float = 2.0, label: str = ""):
    last: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except (httpx.HTTPError, OSError, ValueError) as exc:  # network, TLS reset, bad JSON
            last = exc
            if attempt == attempts:
                break
            delay = base_delay * (2 ** (attempt - 1))
            log.warning("%s failed (attempt %d/%d): %s; retrying in %.0fs", label, attempt, attempts, exc, delay)
            time.sleep(delay)
    raise RuntimeError(f"{label} failed after {attempts} attempts: {last}") from last


def stream_download(url: str, dest: Path, params: dict[str, str] | None = None) -> int:
    """Stream a URL to `dest` (through a .part file), returning the byte count."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_suffix(dest.suffix + ".part")

    def _once() -> int:
        total = 0
        with _client() as client, client.stream("GET", url, params=params) as resp:
            resp.raise_for_status()
            with part.open("wb") as fh:
                for chunk in resp.iter_bytes(1 << 20):
                    fh.write(chunk)
                    total += len(chunk)
        if total == 0:
            raise ValueError("empty response")
        part.replace(dest)
        return total

    try:
        return _retrying(_once, label=f"download {url}")
    finally:
        part.unlink(missing_ok=True)


def arcgis_query_all(url: str, params: dict[str, str], page_size: int = 1000) -> dict:
    """Page an ArcGIS `/query` endpoint with `resultOffset` until `exceededTransferLimit` clears."""
    features: list[dict] = []
    offset = 0
    with _client() as client:
        while True:
            page_params = {
                **params,
                "f": "geojson",
                "outSR": "4326",
                "resultOffset": str(offset),
                "resultRecordCount": str(page_size),
            }

            def _once(p=page_params) -> dict:
                resp = client.get(url, params=p)
                resp.raise_for_status()
                return resp.json()

            page = _retrying(_once, label=f"query {url} offset={offset}")
            batch = page.get("features") or []
            features.extend(batch)
            exceeded = bool(page.get("exceededTransferLimit")) or bool(
                (page.get("properties") or {}).get("exceededTransferLimit")
            )
            if not batch or not exceeded:
                break
            offset += len(batch)
    return {"type": "FeatureCollection", "features": features}


def download_dataset(cfg: Config, ds: Dataset, *, force: bool = False) -> tuple[Path, int, bool]:
    """Fetch one dataset. Returns (path, bytes, skipped)."""
    dest = dest_path(cfg, ds)
    if dest.exists() and not force:
        return dest, dest.stat().st_size, True
    if ds.kind == "direct":
        stream_download(ds.url, dest, params=dict(ds.params))
    else:
        fc = arcgis_query_all(ds.url, dict(ds.params))
        dest.parent.mkdir(parents=True, exist_ok=True)
        part = dest.with_suffix(dest.suffix + ".part")
        part.write_text(json.dumps(fc), encoding="utf-8")
        part.replace(dest)
        log.info("%s: %d features", ds.key, len(fc["features"]))
    return dest, dest.stat().st_size, False
