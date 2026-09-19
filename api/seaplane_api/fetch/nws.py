"""api.weather.gov: active alerts and the hourly gridpoint forecast (the second-opinion wind).

Requires a User-Agent (`fetch/http.py` sets it). Two calls:

- `/alerts/active?point=lat,lon` -> GeoJSON; `features[].properties` carries `event`, `areaDesc`,
  `ends` / `expires`.
- `/points/{lat},{lon}` -> `properties.forecastHourly`, then that URL -> `properties.periods`.
  Verified live 2026-09-19: periods carry `windSpeed` as the **string** `"7 mph"` (occasionally a
  range, `"5 to 10 mph"`) and `windDirection` as a compass point, `"E"`. There is **no sky cover**
  in `forecastHourly`, which is why the briefing has no model-derived ceiling.
"""
from __future__ import annotations

import re

import httpx

from .http import get_json

BASE = "https://api.weather.gov"
MPH_TO_KT = 0.868976
_SPEED = re.compile(r"(\d+)")
_COMPASS = {
    "N": 0, "NNE": 22.5, "NE": 45, "ENE": 67.5, "E": 90, "ESE": 112.5, "SE": 135, "SSE": 157.5,
    "S": 180, "SSW": 202.5, "SW": 225, "WSW": 247.5, "W": 270, "WNW": 292.5, "NW": 315, "NNW": 337.5,
}


async def fetch_alerts(c: httpx.AsyncClient, lat: float, lon: float) -> tuple[list[dict] | None, str | None]:
    data, err = await get_json(c, f"{BASE}/alerts/active", {"point": f"{lat:.4f},{lon:.4f}"}, label="nws_alerts")
    if err or not isinstance(data, dict):
        return None, err or "nws_alerts: unexpected payload"
    out = []
    for feat in data.get("features") or []:
        p = feat.get("properties") or {}
        out.append({"event": p.get("event") or "Alert", "area": p.get("areaDesc") or "", "ends": p.get("ends") or p.get("expires")})
    return out, None


async def fetch_hourly(c: httpx.AsyncClient, lat: float, lon: float) -> tuple[list[dict] | None, str | None]:
    """`properties.periods` of the gridpoint hourly forecast, via `/points`."""
    point, err = await get_json(c, f"{BASE}/points/{lat:.4f},{lon:.4f}", label="nws_points")
    if err or not isinstance(point, dict):
        return None, err or "nws_points: unexpected payload"
    url = (point.get("properties") or {}).get("forecastHourly")
    if not url:
        return None, "nws_points: no forecastHourly link"
    data, err = await get_json(c, url, label="nws_hourly")
    if err or not isinstance(data, dict):
        return None, err or "nws_hourly: unexpected payload"
    return (data.get("properties") or {}).get("periods") or [], None


def wind_kt(period: dict) -> float | None:
    """`"5 to 10 mph"` -> 10 kt-equivalent (the top of the range, the conservative end)."""
    matches = _SPEED.findall(str(period.get("windSpeed") or ""))
    if not matches:
        return None
    return max(int(m) for m in matches) * MPH_TO_KT


def wind_dir_deg(period: dict) -> float | None:
    return _COMPASS.get(str(period.get("windDirection") or "").upper())


def peak_wind_kt(periods: list[dict] | None, start_iso: str, end_iso: str) -> float | None:
    """Highest hourly wind (kt) whose period starts inside `[start_iso, end_iso)`.

    Both bounds are ISO strings with offsets, compared as `datetime`s by the caller's tz-aware
    parsing; string comparison would break across a DST offset change.
    """
    from datetime import datetime

    if not periods:
        return None
    lo, hi = datetime.fromisoformat(start_iso), datetime.fromisoformat(end_iso)
    best: float | None = None
    for p in periods:
        try:
            t = datetime.fromisoformat(p["startTime"])
        except (KeyError, ValueError):
            continue
        if lo <= t < hi:
            kt = wind_kt(p)
            if kt is not None and (best is None or kt > best):
                best = kt
    return best
