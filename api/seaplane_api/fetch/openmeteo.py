"""Open-Meteo hourly point forecasts, batched 50 points per request.

Multi-point works by passing comma-joined `latitude`/`longitude`; the response is then a **list** of
per-point objects in request order (a single point still comes back as a bare object, which is
normalised here). Units are set on the request: knots, Fahrenheit. `visibility` comes back in metres
and `pressure_msl` in hPa regardless.

`pressure_msl` is requested on top of the design's variable list because density altitude needs an
altimeter setting, and `surface_pressure` is station pressure at the model's own grid elevation.

The service is keyless and free; the run is kept to a handful of calls by snapping lakes to a
0.1 degree grid (see `briefing/lakes.py`).
"""
from __future__ import annotations

import httpx

from .http import get_json

URL = "https://api.open-meteo.com/v1/forecast"
BATCH = 50

HOURLY_VARS = (
    "wind_speed_10m",
    "wind_gusts_10m",
    "wind_direction_10m",
    "temperature_2m",
    "dew_point_2m",
    "precipitation",
    "precipitation_probability",
    "weather_code",
    "cloud_cover",
    "visibility",
    "cape",
    "surface_pressure",
    "pressure_msl",
)

FORECAST_LINK = (
    "https://open-meteo.com/en/docs#latitude={lat}&longitude={lon}&hourly=wind_speed_10m,wind_gusts_10m"
)


async def fetch_points(
    c: httpx.AsyncClient,
    points: list[tuple[float, float]],
    *,
    timezone: str,
    forecast_days: int = 2,
    past_days: int = 0,
    daily: tuple[str, ...] = (),
) -> tuple[list[dict], list[str]]:
    """Hourly forecasts for `points` in request order. Returns `(payloads, errors)`.

    A batch that fails contributes its error and yields `{}` placeholders, so one bad batch does not
    lose the others and indexes still line up with `points`.
    """
    out: list[dict] = []
    errors: list[str] = []
    for i in range(0, len(points), BATCH):
        chunk = points[i : i + BATCH]
        params = {
            "latitude": ",".join(f"{lat:.4f}" for lat, _ in chunk),
            "longitude": ",".join(f"{lon:.4f}" for _, lon in chunk),
            "hourly": ",".join(HOURLY_VARS),
            "wind_speed_unit": "kn",
            "temperature_unit": "fahrenheit",
            "forecast_days": str(forecast_days),
            "timezone": timezone,
        }
        if past_days:
            params["past_days"] = str(past_days)
        if daily:
            params["daily"] = ",".join(daily)
        data, err = await get_json(c, URL, params, label=f"open_meteo[{i // BATCH}]")
        if err or data is None:
            errors.append(err or "open_meteo: empty response")
            out.extend({} for _ in chunk)
            continue
        payloads = data if isinstance(data, list) else [data]
        if len(payloads) != len(chunk):  # pragma: no cover - defensive
            errors.append(f"open_meteo: expected {len(chunk)} points, got {len(payloads)}")
        out.extend(payloads[: len(chunk)])
        out.extend({} for _ in range(max(0, len(chunk) - len(payloads))))
    return out, errors
