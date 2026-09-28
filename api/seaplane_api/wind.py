"""The wind proxy (`docs/design.md` 7.9, data contract "Wind proxy"): `GET /api/wind/stations?bbox=`
and `GET /api/wind/point?lat=&lon=`.

Online only; the client degrades silently. Nothing here raises to the client except a malformed
request or an oversized bbox -- a failed upstream *source* becomes an entry in `errors` and the rest
of the reply still answers.

**Stations.** The bbox is split into 1 degree tiles (floor of lon/lat); each tile's three sources
(METAR, NDBC, Synoptic when enabled) are fetched together and cached as one unit for 5 minutes, so
panning the map reuses every tile it has already seen and only fetches the new ones. A request whose
bbox spans more than 16 tiles is refused outright (`400`) rather than fanning out that many upstream
calls. The merge, dedupe-by-`source+id`-keeping-newest, 3 hour age cut, and the final clip back to the
*requested* bbox (tiles are whole-degree boxes and usually overrun it) all happen at request time over
whatever tiles answered, not at fetch time -- so a station does not linger past its 3 hour cutoff just
because the tile holding it is still within its 5 minute cache window.

**Point.** One Open-Meteo current-wind lookup, snapped to the 0.1 degree cell of the request (the
same snap the briefing uses, `briefing/lakes.cell_of` / `cell_point`) and cached 15 minutes per cell.
A failure is the one case that does reach the client, as `502`, because there is no second source to
fall back to and no stale reply worth returning in its place.

Concurrency: an `asyncio.Lock` per tile/cell so two requests that land while the same key is being
fetched do not double the upstream calls; the cache is bounded (`_prune`) rather than growing for the
life of the process.
"""
from __future__ import annotations

import asyncio
import logging
import math
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, HTTPException, Query

from .briefing.lakes import cell_of, cell_point
from .fetch import aviationweather, ndbc, openmeteo, synoptic
from .fetch.http import client

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/wind", tags=["wind"])

STATIONS_TTL_S = 5 * 60
POINT_TTL_S = 15 * 60
MAX_TILES = 16
STALE_OBS = timedelta(hours=3)
MAX_CACHE_ENTRIES = 512  # a single-user tailnet service; this is generous, not a real limit

Tile = tuple[int, int]  # (floor(lon), floor(lat))
Cell = tuple[int, int]  # briefing.lakes.Cell: (round(lat / 0.1), round(lon / 0.1))

Bbox = tuple[float, float, float, float]  # (west, south, east, north)


def _now() -> datetime:
    """`datetime.now(UTC)`, indirected so tests can freeze it without touching wall time."""
    return datetime.now(UTC)


# --- tiling -------------------------------------------------------------------------------------


def tiles_for_bbox(bbox: Bbox) -> list[Tile]:
    """1 degree tiles `(floor(lon), floor(lat))` covering `(west, south, east, north)`.

    An edge sitting exactly on a whole degree belongs to the tile below it, so a bbox that is itself
    an exact multiple of 1 degree does not pull in an extra, empty row or column.
    """
    west, south, east, north = bbox
    if east <= west or north <= south:
        return []
    lon_lo, lon_hi = _span(west, east)
    lat_lo, lat_hi = _span(south, north)
    return [(lon, lat) for lon in range(lon_lo, lon_hi + 1) for lat in range(lat_lo, lat_hi + 1)]


def _span(lo: float, hi: float) -> tuple[int, int]:
    start = math.floor(lo)
    end = math.floor(hi)
    if end == hi and end > start:
        end -= 1
    return start, end


def _tile_bbox(tile: Tile) -> Bbox:
    lon, lat = tile
    return (float(lon), float(lat), float(lon + 1), float(lat + 1))


def _parse_bbox(raw: str) -> Bbox:
    parts = raw.split(",")
    if len(parts) != 4:
        raise HTTPException(status_code=400, detail="bbox must be west,south,east,north")
    try:
        west, south, east, north = (float(p) for p in parts)
    except ValueError as e:
        raise HTTPException(status_code=400, detail="bbox must be four numbers") from e
    return (west, south, east, north)


# --- row mapping: each source's own shape -> the contract's common station row ------------------


def _int_or_none(value) -> int | None:
    if value is None:
        return None
    try:
        return round(float(value))
    except (TypeError, ValueError):
        return None


def _clean_name(raw) -> str | None:
    if not raw:
        return None
    return " ".join(str(raw).split()) or None


def _metar_dir(raw, speed_kt: float | None) -> int | None:
    """`wdir` is a bearing, `"VRB"`, or missing. Calm (`speed_kt == 0`) forces `None` either way."""
    if speed_kt is not None and speed_kt == 0:
        return None
    if raw is None:
        return None
    if isinstance(raw, str):
        if raw.strip().upper() == "VRB":
            return None
        try:
            raw = float(raw)
        except ValueError:
            return None
    return round(float(raw)) % 360


def _metar_rows(rows: list[dict]) -> list[dict]:
    out: list[dict] = []
    for m in rows:
        ident = str(m.get("icaoId") or m.get("stationId") or "").strip()
        lat, lon = m.get("lat"), m.get("lon")
        obs_time = _unix(m.get("obsTime"))
        if not ident or lat is None or lon is None or obs_time is None:
            continue
        speed_kt = _int_or_none(m.get("wspd"))
        dir_deg = _metar_dir(m.get("wdir"), speed_kt)
        if speed_kt is None and dir_deg is None:
            continue  # a field whose wind sensor is out reports nothing worth showing
        out.append(
            {
                "id": ident,
                "source": "metar",
                "name": _clean_name(m.get("name")),
                "lat": round(float(lat), 4),
                "lon": round(float(lon), 4),
                "dir_deg": dir_deg,
                "speed_kt": speed_kt,
                "gust_kt": _int_or_none(m.get("wgst")),
                "obs_time": obs_time,
            }
        )
    return out


def _unix(value) -> datetime | None:
    if value is None:
        return None
    try:
        return datetime.fromtimestamp(float(value), tz=UTC)
    except (TypeError, ValueError, OSError):
        return None


def _ndbc_rows(rows: list[dict]) -> list[dict]:
    out: list[dict] = []
    for r in rows:
        at = r.get("at")
        speed_kt = _int_or_none(r.get("speed_kt"))
        raw_dir = r.get("dir_deg")
        dir_deg = None if (speed_kt == 0 or raw_dir is None) else round(float(raw_dir)) % 360
        if at is None or (speed_kt is None and dir_deg is None):
            continue
        out.append(
            {
                "id": str(r.get("id") or ""),
                "source": "ndbc",
                "name": None,  # latest_obs.txt carries no station names
                "lat": round(float(r["lat"]), 4),
                "lon": round(float(r["lon"]), 4),
                "dir_deg": dir_deg,
                "speed_kt": speed_kt,
                "gust_kt": _int_or_none(r.get("gust_kt")),
                "obs_time": at,
            }
        )
    return out


# --- per-tile fetch + cache -----------------------------------------------------------------


@dataclass
class _TileEntry:
    stations: list[dict] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    fetched_at: datetime = field(default_factory=_now)


_tile_cache: dict[Tile, _TileEntry] = {}
_tile_locks: dict[Tile, asyncio.Lock] = {}


async def _fetch_tile(tile: Tile) -> _TileEntry:
    west, south, east, north = _tile_bbox(tile)
    stations: list[dict] = []
    errors: list[str] = []

    async with client() as c:
        metars, err = await aviationweather.fetch_metar_bbox(c, (south, west, north, east))
        if err:
            errors.append(err)
        else:
            stations.extend(_metar_rows(metars or []))

        buoys, err = await ndbc.fetch_latest_obs(c, (west, south, east, north))
        if err:
            errors.append(err)
        else:
            stations.extend(_ndbc_rows(buoys or []))

        if synoptic.enabled():
            syn_rows, err = await synoptic.fetch_stations(c, (west, south, east, north))
            if err:
                errors.append(err)
            else:
                stations.extend(syn_rows)

    return _TileEntry(stations=stations, errors=errors, fetched_at=_now())


async def _get_tile(tile: Tile) -> _TileEntry:
    entry = _tile_cache.get(tile)
    if entry is not None and (_now() - entry.fetched_at).total_seconds() < STATIONS_TTL_S:
        return entry
    lock = _tile_locks.setdefault(tile, asyncio.Lock())
    async with lock:
        entry = _tile_cache.get(tile)  # another waiter may have just filled it
        if entry is not None and (_now() - entry.fetched_at).total_seconds() < STATIONS_TTL_S:
            return entry
        entry = await _fetch_tile(tile)
        _tile_cache[tile] = entry
        _prune(_tile_cache)
        _prune_locks(_tile_locks, _tile_cache)
        return entry


def _prune(cache: dict) -> None:
    if len(cache) <= MAX_CACHE_ENTRIES:
        return
    oldest = sorted(cache, key=lambda k: cache[k].fetched_at)[: len(cache) - MAX_CACHE_ENTRIES]
    for key in oldest:
        del cache[key]


def _prune_locks(locks: dict, cache: dict) -> None:
    for key in [k for k in locks if k not in cache and not locks[k].locked()]:
        del locks[key]


# --- merge: dedupe, age cut, clip to the requested bbox --------------------------------------


def _iso_z(dt: datetime) -> str:
    return dt.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def merge_stations(entries: list[_TileEntry], bbox: Bbox, now: datetime) -> tuple[list[dict], list[str]]:
    west, south, east, north = bbox
    best: dict[tuple[str, str], dict] = {}
    errors: list[str] = []
    for entry in entries:
        for e in entry.errors:
            if e not in errors:
                errors.append(e)
        for s in entry.stations:
            key = (s["source"], s["id"])
            prev = best.get(key)
            if prev is None or s["obs_time"] > prev["obs_time"]:
                best[key] = s

    cutoff = now - STALE_OBS
    out: list[dict] = []
    for s in best.values():
        if s["obs_time"] < cutoff:
            continue
        if not (west <= s["lon"] <= east and south <= s["lat"] <= north):
            continue
        row = dict(s)
        row["obs_time"] = _iso_z(s["obs_time"])
        out.append(row)
    out.sort(key=lambda r: (r["source"], r["id"]))
    return out, errors


# --- point ----------------------------------------------------------------------------------


@dataclass
class _PointEntry:
    payload: dict | None
    err: str | None
    fetched_at: datetime


_point_cache: dict[Cell, _PointEntry] = {}
_point_locks: dict[Cell, asyncio.Lock] = {}


async def _fetch_point(lat: float, lon: float) -> _PointEntry:
    async with client() as c:
        payload, err = await openmeteo.fetch_current(c, lat, lon)
    return _PointEntry(payload=payload, err=err, fetched_at=_now())


async def _get_point(cell: Cell, lat: float, lon: float) -> _PointEntry:
    entry = _point_cache.get(cell)
    if entry is not None and (_now() - entry.fetched_at).total_seconds() < POINT_TTL_S:
        return entry
    lock = _point_locks.setdefault(cell, asyncio.Lock())
    async with lock:
        entry = _point_cache.get(cell)
        if entry is not None and (_now() - entry.fetched_at).total_seconds() < POINT_TTL_S:
            return entry
        entry = await _fetch_point(lat, lon)
        if entry.err is None:  # do not lock in a failure for 15 minutes; let the next call retry
            _point_cache[cell] = entry
            _prune(_point_cache)
            _prune_locks(_point_locks, _point_cache)
        return entry


def _current_time_z(raw) -> str | None:
    if not raw:
        return None
    s = str(raw)
    if len(s) == 16:  # "YYYY-MM-DDTHH:MM", no seconds
        s += ":00"
    return s + "Z"


def _point_row(payload: dict, lat: float, lon: float) -> dict:
    current = (payload or {}).get("current") or {}
    speed = _int_or_none(current.get("wind_speed_10m"))
    dir_deg = None if speed == 0 else _int_or_none(current.get("wind_direction_10m"))
    return {
        "lat": lat,
        "lon": lon,
        "dir_deg": dir_deg,
        "speed_kt": speed,
        "gust_kt": _int_or_none(current.get("wind_gusts_10m")),
        "time": _current_time_z(current.get("time")),
        "source": "model",
    }


# --- routes -----------------------------------------------------------------------------------


@router.get("/stations")
async def get_stations(bbox: str = Query(...)) -> dict:
    parsed = _parse_bbox(bbox)
    tiles = tiles_for_bbox(parsed)
    if len(tiles) > MAX_TILES:
        raise HTTPException(status_code=400, detail="bbox too large")
    if not tiles:
        return {"stations": [], "fetched_at": _iso_z(_now()), "errors": []}

    entries = await asyncio.gather(*(_get_tile(t) for t in tiles))
    now = _now()
    stations, errors = merge_stations(list(entries), parsed, now)
    fetched_at = min(e.fetched_at for e in entries)
    return {"stations": stations, "fetched_at": _iso_z(fetched_at), "errors": errors}


@router.get("/point")
async def get_point(lat: float = Query(...), lon: float = Query(...)) -> dict:
    cell = cell_of(lat, lon)
    c_lat, c_lon = cell_point(cell)
    entry = await _get_point(cell, c_lat, c_lon)
    if entry.err is not None or entry.payload is None:
        raise HTTPException(status_code=502, detail=entry.err or "open_meteo: empty response")
    return _point_row(entry.payload, c_lat, c_lon)
