"""Synoptic Data Mesonet API: ASOS/AWOS, RAWS, and CWOP/APRS amateur stations (design 7.9).

The wind proxy's third source, and the only one that needs a key. `SYNOPTIC_TOKEN` gates it entirely
-- unset, `enabled()` is false and the caller skips this source with no error (data contract, "Wind
proxy"). `docker-compose.yml` already passes the variable through when it is set in the environment.

Shape not verified live (no token in this environment): built from Synoptic's documented
`/v2/stations/latest` response, `{"STATION": [{"STID", "NAME", "LATITUDE", "LONGITUDE",
"OBSERVATIONS": {"wind_speed_value_1": {"value", "date_time"}, "wind_direction_value_1": {...},
"wind_gust_value_1": {...}}}]}`, `units=english` so speed/gust arrive in mph and are converted to
knots here to match every other source in the wind proxy.
"""
from __future__ import annotations

import os
from datetime import UTC, datetime
from typing import Any

import httpx

from .http import get_json

URL = "https://api.synopticdata.com/v2/stations/latest"
MPH_TO_KT = 0.868976
TOKEN_ENV = "SYNOPTIC_TOKEN"


def enabled() -> bool:
    return bool(os.environ.get(TOKEN_ENV))


async def fetch_stations(
    c: httpx.AsyncClient, bbox: tuple[float, float, float, float]
) -> tuple[list[dict], str | None]:
    """Stations inside `(west, south, east, north)`, mapped to the wind proxy's common row shape.

    Returns `([], None)` when `SYNOPTIC_TOKEN` is unset, so this is safe to call directly even
    though `wind.py` already checks `enabled()` first and does not reach here in that case.
    """
    token = os.environ.get(TOKEN_ENV)
    if not token:
        return [], None
    west, south, east, north = bbox
    data, err = await get_json(
        c,
        URL,
        {
            "token": token,
            "bbox": f"{west},{south},{east},{north}",
            "vars": "wind_speed,wind_direction,wind_gust",
            "units": "english",
        },
        label="synoptic",
    )
    if err:
        return [], err
    return _parse(data), None


def _parse(data: Any) -> list[dict]:
    rows: list[dict] = []
    for st in (data or {}).get("STATION") or []:
        lat, lon = st.get("LATITUDE"), st.get("LONGITUDE")
        if lat is None or lon is None:
            continue
        obs = st.get("OBSERVATIONS") or {}
        speed_kt = _mph_field(obs.get("wind_speed_value_1"))
        at = _date_time(obs.get("wind_speed_value_1")) or _date_time(obs.get("wind_direction_value_1"))
        if at is None:
            continue
        dir_raw = _value(obs.get("wind_direction_value_1"))
        dir_deg = None if (speed_kt == 0 or dir_raw is None) else round(dir_raw) % 360
        gust_kt = _mph_field(obs.get("wind_gust_value_1"))
        rows.append(
            {
                "id": str(st.get("STID") or ""),
                "source": "synoptic",
                "name": _clean(st.get("NAME")),
                "lat": round(float(lat), 4),
                "lon": round(float(lon), 4),
                "dir_deg": dir_deg,
                "speed_kt": None if speed_kt is None else round(speed_kt),
                "gust_kt": None if gust_kt is None else round(gust_kt),
                "obs_time": at,
            }
        )
    return rows


def _value(obj: Any) -> float | None:
    if not isinstance(obj, dict):
        return None
    v = obj.get("value")
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


def _mph_field(obj: Any) -> float | None:
    v = _value(obj)
    return None if v is None else v * MPH_TO_KT


def _date_time(obj: Any) -> datetime | None:
    if not isinstance(obj, dict):
        return None
    raw = obj.get("date_time")
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw)).astimezone(UTC)
    except ValueError:
        return None


def _clean(raw: Any) -> str | None:
    if not raw:
        return None
    return " ".join(str(raw).split()) or None
