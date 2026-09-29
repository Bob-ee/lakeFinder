"""`GET /api/forecast/wind?lake=<id>` (data contract, "Forecast timeline and waves over time").

The forecast wind for every cell a water body's wave points fall in (its centroid without a wave
field), on the briefing timeline's axis, so the map's time bar can give each point its own region's
wind for the hour being looked at.

**Cells.** 0.1 degrees, a point belonging to `(round(lat / d) * d, round(lon / d) * d)`. When that is
more than `MAX_CELLS` (120) the size doubles until it is not, so Lake Michigan is a handful of cells
and two requests rather than thousands. The doubling is on the reply (`cell_deg`), and the cache key
carries it, because a 0.2 degree cell is a different forecast point from a 0.1 one.

**Fetch and cache.** Cells are cached for 30 minutes each, keyed `(cell, cell_deg)`; a request fetches
only the cells it is missing, in Open-Meteo batches of 50, asking for wind alone and the settings'
`forecast.wind_models` (same per-hour rule as the briefing, `fetch/openmeteo.merge_models`). A failed
batch is not cached and its cells come back with all-null arrays plus an entry in `errors`; when
nothing at all could be fetched or cached the answer is `502`. An unknown lake is `404`.

**Regions.** The reply also carries `regions`: one entry per wave-field label (none without a wave field),
each at the **0.1 degree** cell of that region's centroid (mean lat / lon of the label's points, the
briefing's own definition in `briefing/lakes.py`) whatever `cell_deg` is, so the map's per-region wind is
exactly the wind the briefing scored that region with. Region cells are fetched with the rest (and share
their cache entries when `cell_deg` is 0.1) but do not count toward the 120-cell limit.

`SEAPLANE_WIND_FIXTURES=1` answers with a deterministic synthetic wind and never touches the network:
direction veering through 360 degrees across the axis, speed 4 -> 22 kt at mid-axis and back, gust
speed + 30% (at least 3 kt), the same on every cell, model `"fixture"`.
"""
from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Query, Request

from . import wavefield
from .briefing import lakes as lakes_mod
from .briefing.series import HourlySeries
from .briefing.timeline import time_axis
from .fetch import openmeteo
from .fetch.http import client
from .paths import (
    index_path,
    read_json,
    wave_points_bin_path,
    wave_points_index_path,
)
from .settings import Settings

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/forecast", tags=["forecast"])

BASE_CELL_DEG = 0.1
MAX_CELLS = 120
TTL_S = 30 * 60
MAX_CACHE_ENTRIES = 2048

CellKey = tuple[int, int, float]  # (round(lat / d), round(lon / d), d)


def _now() -> datetime:
    """`datetime.now(UTC)`, indirected so tests can move the clock."""
    return datetime.now(UTC)


def _iso_z(dt: datetime) -> str:
    return dt.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


# --- cells -------------------------------------------------------------------------------------


def cells_for(points: list[tuple[float, float]], *, max_cells: int = MAX_CELLS) -> tuple[float, list[tuple[int, int]]]:
    """`(cell_deg, cell indices)`: 0.1 degrees, doubled until at most `max_cells` distinct cells hold `points`.

    Indices are `(round(lat / cell_deg), round(lon / cell_deg))`, sorted south-to-north then west-to-east
    so the reply's order (and "the first cell") never depends on the pack's point order.
    """
    deg = BASE_CELL_DEG
    while True:
        cells = sorted({(round(lat / deg), round(lon / deg)) for lat, lon in points})
        if len(cells) <= max_cells or deg > 90:
            return round(deg, 4), cells
        deg *= 2


def cell_center(cell: tuple[int, int], deg: float) -> tuple[float, float]:
    return (round(cell[0] * deg, 4), round(cell[1] * deg, 4))


# --- the pack, read once per file version -----------------------------------------------------------


_index_cache: tuple[tuple[int, int], dict[str, tuple[float, float]]] | None = None
_wave_cache: tuple[tuple, wavefield.WaveField | None] | None = None


def _stamp(*paths) -> tuple:
    out = []
    for p in paths:
        try:
            st = p.stat()
            out.append((st.st_mtime_ns, st.st_size))
        except OSError:
            out.append(None)
    return tuple(out)


def _centroids() -> dict[str, tuple[float, float]]:
    """`{lake id: (lat, lon)}` from `index.json`, re-read only when the file changes."""
    global _index_cache
    path = index_path()
    stamp = _stamp(path)
    if _index_cache is not None and _index_cache[0] == stamp:
        return _index_cache[1]
    raw = read_json(path)
    out: dict[str, tuple[float, float]] = {}
    if isinstance(raw, list):
        for lake in raw:
            if lake.get("id") is not None and lake.get("lat") is not None and lake.get("lon") is not None:
                out[str(lake["id"])] = (float(lake["lat"]), float(lake["lon"]))
    _index_cache = (stamp, out)
    return out


def _wave_field() -> wavefield.WaveField | None:
    global _wave_cache
    stamp = _stamp(wave_points_index_path(), wave_points_bin_path())
    if _wave_cache is not None and _wave_cache[0] == stamp:
        return _wave_cache[1]
    field = wavefield.load()
    _wave_cache = (stamp, field)
    return field


@dataclass
class LakeGeometry:
    """Where a water body is forecast: its points (or centroid) and its regions' centroids."""

    points: list[tuple[float, float]]
    regions: list[tuple[str, tuple[int, int]]]  # (label, 0.1 degree cell index), in label order


def lake_geometry(lake_id: int) -> LakeGeometry | None:
    """The lat/lon a water body is forecast at, and one centroid cell per region; `None` if unknown."""
    centroid = _centroids().get(str(lake_id))
    if centroid is None:
        return None
    field = _wave_field()
    if field is None or not field.has_points(lake_id):
        return LakeGeometry([centroid], [])
    pts = field.points(lake_id)
    by_label: dict[int, list] = {}
    for p in pts:
        by_label.setdefault(p.label, []).append(p)
    regions = [
        (
            field.label(i),
            lakes_mod.cell_of(sum(p.lat for p in g) / len(g), sum(p.lon for p in g) / len(g)),
        )
        for i, g in sorted(by_label.items())
    ]
    return LakeGeometry([(p.lat, p.lon) for p in pts], regions)


# --- the cache ---------------------------------------------------------------------------------------


@dataclass
class _Entry:
    series: HourlySeries
    fetched_at: datetime


_cache: dict[CellKey, _Entry] = {}
_fetch_lock = asyncio.Lock()


def _fresh(entry: _Entry | None) -> bool:
    return entry is not None and (_now() - entry.fetched_at).total_seconds() < TTL_S


def _prune() -> None:
    if len(_cache) <= MAX_CACHE_ENTRIES:
        return
    oldest = sorted(_cache, key=lambda k: _cache[k].fetched_at)[: len(_cache) - MAX_CACHE_ENTRIES]
    for key in oldest:
        del _cache[key]


async def _ensure(keys: list[CellKey], settings: Settings) -> list[str]:
    """Fetch every key that is not fresh in the cache. Returns the error strings."""
    if all(_fresh(_cache.get(k)) for k in keys):
        return []
    errors: list[str] = []
    async with _fetch_lock:
        missing = [k for k in keys if not _fresh(_cache.get(k))]  # another request may have filled them
        if not missing:
            return []
        fc = settings.forecast
        points = [cell_center((k[0], k[1]), k[2]) for k in missing]
        tz = ZoneInfo(settings.timezone)
        async with client() as c:
            payloads, errs = await openmeteo.fetch_points(
                c,
                points,
                timezone=settings.timezone,
                forecast_days=max(2, fc.forecast_days),
                past_days=fc.past_days,
                models=fc.wind_models,
                hourly=openmeteo.WIND_VARS,
            )
        errors.extend(errs)
        now = _now()
        for key, payload in zip(missing, payloads, strict=False):
            if payload and (payload.get("hourly") or {}).get("time"):
                _cache[key] = _Entry(HourlySeries.from_open_meteo(payload, tz), now)
        _prune()
    return errors


# --- the answer ---------------------------------------------------------------------------------------


def _wind_arrays(series: HourlySeries | None, axis: list[datetime]) -> tuple[list, list, list, list]:
    dirs: list[int | None] = []
    kts: list[int | None] = []
    gusts: list[int | None] = []
    models: list[str | None] = []
    for h in axis:
        m = series.at(h) if series is not None else {}
        s, g, d = (m.get("wind_speed_10m"), m.get("wind_gusts_10m"), m.get("wind_direction_10m")) if m else (None,) * 3
        if s is None or g is None or d is None:
            dirs.append(None)
            kts.append(None)
            gusts.append(None)
            models.append(None)
            continue
        dirs.append(round(float(d)))  # as the briefing prints it: a model's 360 stays 360
        kts.append(round(float(s)))
        gusts.append(round(float(g)))
        models.append(m.get("wind_model"))
    return dirs, kts, gusts, models


def fixture_wind(n: int) -> tuple[list[int], list[int], list[int]]:
    """Direction veering 0 -> 360, speed 4 -> 22 -> 4 kt, gust = speed + 30% (>= 3 kt), over `n` hours."""
    dirs, kts, gusts = [], [], []
    for i in range(n):
        f = i / (n - 1) if n > 1 else 0.0
        kt = round(4 + 18 * (1 - abs(2 * f - 1)))
        dirs.append(round(360 * f) % 360)
        kts.append(kt)
        gusts.append(kt + max(3, round(kt * 0.3)))
    return dirs, kts, gusts


def _settings_of(request: Request) -> Settings:
    sched = getattr(request.app.state, "scheduler", None)
    settings = getattr(sched, "settings", None)
    return settings if isinstance(settings, Settings) else Settings()


@router.get("/wind")
async def get_forecast_wind(request: Request, lake: int = Query(...)) -> dict:
    geo = lake_geometry(lake)
    if geo is None:
        raise HTTPException(status_code=404, detail=f"lake {lake} is not in index.json")
    settings = _settings_of(request)
    fc = settings.forecast
    tz = ZoneInfo(settings.timezone)
    axis = time_axis(_now(), tz, fc.past_h, fc.horizon_h)
    deg, cells = cells_for(geo.points)
    times = [h.isoformat() for h in axis]
    region_cells = [(label, cell) for label, cell in geo.regions]

    if os.environ.get("SEAPLANE_WIND_FIXTURES") == "1":
        d, k, g = fixture_wind(len(axis))

        def wind_of(lat: float, lon: float) -> dict:
            return {"lat": lat, "lon": lon, "dir": d, "kt": k, "gust": g}

        return {
            "lake_id": lake,
            "cell_deg": deg,
            "times": times,
            "past": fc.past_h,
            "models": ["fixture"] * len(axis),
            "cells": [wind_of(*cell_center(c, deg)) for c in cells],
            "regions": [{"label": label, **wind_of(*cell_center(c, BASE_CELL_DEG))} for label, c in region_cells],
            "fetched_at": _iso_z(_now()),
            "errors": [],
        }

    keys: list[CellKey] = [(c[0], c[1], deg) for c in cells]
    region_keys: list[CellKey] = [(c[0], c[1], BASE_CELL_DEG) for _, c in region_cells]
    errors = await _ensure(list(dict.fromkeys(keys + region_keys)), settings)
    entries = [_cache.get(k) for k in keys]
    if not any(entries) and not any(_cache.get(k) for k in region_keys):
        raise HTTPException(status_code=502, detail=errors[0] if errors else "open_meteo: empty response")

    out_cells = []
    models: list[str | None] | None = None
    for key, entry in zip(keys, entries, strict=True):
        dirs, kts, gusts, ms = _wind_arrays(entry.series if entry else None, axis)
        if models is None and entry is not None:
            models = ms
        lat, lon = cell_center((key[0], key[1]), deg)
        out_cells.append({"lat": lat, "lon": lon, "dir": dirs, "kt": kts, "gust": gusts})
    out_regions = []
    for (label, cell), key in zip(region_cells, region_keys, strict=True):
        entry = _cache.get(key)
        dirs, kts, gusts, _ = _wind_arrays(entry.series if entry else None, axis)
        lat, lon = cell_center(cell, BASE_CELL_DEG)
        out_regions.append({"label": label, "lat": lat, "lon": lon, "dir": dirs, "kt": kts, "gust": gusts})
    fetched = min(e.fetched_at for e in [*entries, *(_cache.get(k) for k in region_keys)] if e is not None)
    return {
        "lake_id": lake,
        "cell_deg": deg,
        "times": times,
        "past": fc.past_h,
        "models": models or [None] * len(axis),
        "cells": out_cells,
        "regions": out_regions,
        "fetched_at": _iso_z(fetched),
        "errors": errors,
    }
