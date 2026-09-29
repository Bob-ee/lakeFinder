"""Open-Meteo hourly point forecasts, batched 50 points per request.

Multi-point works by passing comma-joined `latitude`/`longitude`; the response is then a **list** of
per-point objects in request order (a single point still comes back as a bare object, which is
normalised here). Units are set on the request: knots, Fahrenheit. `visibility` comes back in metres
and `pressure_msl` in hPa regardless.

`pressure_msl` is requested on top of the design's variable list because density altitude needs an
altimeter setting, and `surface_pressure` is station pressure at the model's own grid elevation.

The service is keyless and free; the run is kept to a handful of calls by snapping lakes to a
0.1 degree grid (see `briefing/lakes.py`).

**Several wind models in one request** (verified live 2026-09-28). `models=a,b` makes the response
suffix *every* variable with the model id (`wind_speed_10m_ncep_hrrr_conus`, `visibility_best_match`);
a single model, or none, leaves the names bare. `merge_models` folds the suffixed shape back into the
bare one the rest of the package reads: per hour, speed + gust + direction **together** from the first
model where all three are non-null (the model used goes in `hourly["wind_model"]`), every other
variable from `best_match`. `past_days` / `forecast_days` work with `models`; `past_hours` and
`forecast_hours` do not combine with them (`past_days` is silently dropped next to `forecast_hours`,
and `forecast_days` next to `past_hours`), so the timeline is sized with the day counts and cut to the
hour on our side.
"""
from __future__ import annotations

from collections.abc import Sequence

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

WIND_VARS = ("wind_speed_10m", "wind_gusts_10m", "wind_direction_10m")
FALLBACK_MODEL = "best_match"

FORECAST_LINK = (
    "https://open-meteo.com/en/docs#latitude={lat}&longitude={lon}&hourly=wind_speed_10m,wind_gusts_10m"
)

MARINE_URL = "https://marine-api.open-meteo.com/v1/marine"

CURRENT_VARS = ("wind_speed_10m", "wind_direction_10m", "wind_gusts_10m")


async def fetch_current(c: httpx.AsyncClient, lat: float, lon: float) -> tuple[dict | None, str | None]:
    """Current wind at one point, for the wind proxy's `/api/wind/point` (design 7.9).

    `current=wind_speed_10m,wind_direction_10m,wind_gusts_10m` returns `{"current": {"time": "...",
    "wind_speed_10m": .., "wind_direction_10m": .., "wind_gusts_10m": ..}}`. No `timezone` param is
    sent, so `current.time` comes back a naive UTC ISO string (`"2026-09-28T16:00"`, no seconds).
    """
    return await get_json(
        c,
        URL,
        {
            "latitude": f"{lat:.4f}",
            "longitude": f"{lon:.4f}",
            "current": ",".join(CURRENT_VARS),
            "wind_speed_unit": "kn",
        },
        label="open_meteo_current",
    )


async def fetch_marine(
    c: httpx.AsyncClient, lat: float, lon: float, *, timezone: str, past_days: int = 1
) -> tuple[dict | None, str | None]:
    """Hourly `wave_height` (metres) from the marine model, for the second opinion on big water.

    Verified live on 2026-09-19. Two things the caller has to handle. **Nulls:** a point the marine
    model does not cover comes back 200 with `wave_height` a list of `null` (42.60,-83.35, inland
    Oakland County, returns 168 of them) rather than an error. **Snapping:** the response carries the
    grid point it actually used, and it can be a long way from the request -- 42.60,-83.05 came back
    as 42.625,-82.875, about 8 nm east, because that is the nearest wet cell. A caller that does not
    check `latitude`/`longitude` will quietly print Lake Huron's waves for an inland lake.
    """
    return await get_json(
        c,
        MARINE_URL,
        {
            "latitude": f"{lat:.4f}",
            "longitude": f"{lon:.4f}",
            "hourly": "wave_height",
            "timezone": timezone,
            "past_days": str(past_days),
        },
        label="open_meteo_marine",
    )


def merge_models(payload: dict, models: Sequence[str]) -> dict:
    """One point's multi-model response as the bare-named shape, plus `hourly["wind_model"]`.

    Wind (speed, gust, direction) is taken per hour from the first model in `models` where all three
    are non-null; an hour no model covers is null in all three and `None` in `wind_model`. Every other
    variable comes from `best_match` (the last model when `best_match` is not listed). A payload that
    already has bare names (a one-model request) keeps them and just gains `wind_model`.
    """
    hourly = payload.get("hourly") or {}
    times = hourly.get("time") or []
    if not hourly or not models:
        return payload
    n = len(times)
    single = len(models) == 1

    def col(var: str, model: str) -> list | None:
        v = hourly.get(f"{var}_{model}")
        if v is None and single:
            v = hourly.get(var)
        return v

    cols = {m: [col(v, m) for v in WIND_VARS] for m in models}
    merged: dict[str, list] = {v: [None] * n for v in WIND_VARS}
    wind_model: list[str | None] = [None] * n
    for i in range(n):
        for m in models:
            vals = [None if c is None or i >= len(c) else c[i] for c in cols[m]]
            if all(x is not None for x in vals):
                for v, x in zip(WIND_VARS, vals, strict=True):
                    merged[v][i] = x
                wind_model[i] = m
                break

    fallback = FALLBACK_MODEL if FALLBACK_MODEL in models else models[-1]
    out: dict = {"time": times, **merged}
    by_len = sorted(models, key=len, reverse=True)  # longest suffix first
    for key, values in hourly.items():
        if key == "time" or key in merged:
            continue
        base, model = key, None
        for m in by_len:
            if key.endswith(f"_{m}"):
                base, model = key[: -len(m) - 1], m
                break
        if base in WIND_VARS:
            continue
        if model is None or model == fallback:
            out.setdefault(base, values)
    out["wind_model"] = wind_model
    return {**payload, "hourly": out}


async def fetch_points(
    c: httpx.AsyncClient,
    points: list[tuple[float, float]],
    *,
    timezone: str,
    forecast_days: int = 2,
    past_days: int = 0,
    daily: tuple[str, ...] = (),
    models: Sequence[str] | None = None,
    hourly: Sequence[str] | None = None,
) -> tuple[list[dict], list[str]]:
    """Hourly forecasts for `points` in request order. Returns `(payloads, errors)`.

    A batch that fails contributes its error and yields `{}` placeholders, so one bad batch does not
    lose the others and indexes still line up with `points`.

    `models` is the wind model order (see `merge_models`); every payload comes back merged. `hourly`
    narrows the variables (the grid cells only need wind). If a multi-model request fails, the batch is
    retried once with `best_match` alone and the failure is still reported in `errors`, so a bad model
    id in settings degrades to the old behaviour rather than losing the forecast.
    """
    out: list[dict] = []
    errors: list[str] = []
    variables = tuple(hourly) if hourly else HOURLY_VARS
    for i in range(0, len(points), BATCH):
        chunk = points[i : i + BATCH]
        params = {
            "latitude": ",".join(f"{lat:.4f}" for lat, _ in chunk),
            "longitude": ",".join(f"{lon:.4f}" for _, lon in chunk),
            "hourly": ",".join(variables),
            "wind_speed_unit": "kn",
            "temperature_unit": "fahrenheit",
            "forecast_days": str(forecast_days),
            "timezone": timezone,
        }
        if past_days:
            params["past_days"] = str(past_days)
        if daily:
            params["daily"] = ",".join(daily)
        used = list(models) if models else []
        if used:
            params["models"] = ",".join(used)
        label = f"open_meteo[{i // BATCH}]"
        data, err = await get_json(c, URL, params, label=label)
        if (err or data is None) and len(used) > 1:
            errors.append(f"{err or 'open_meteo: empty response'} (models {','.join(used)}; retrying best_match)")
            used = [FALLBACK_MODEL]
            params["models"] = FALLBACK_MODEL
            data, err = await get_json(c, URL, params, label=label)
        if err or data is None:
            errors.append(err or "open_meteo: empty response")
            out.extend({} for _ in chunk)
            continue
        payloads = data if isinstance(data, list) else [data]
        if len(payloads) != len(chunk):  # pragma: no cover - defensive
            errors.append(f"open_meteo: expected {len(chunk)} points, got {len(payloads)}")
        if used:
            payloads = [merge_models(p, used) for p in payloads]
        out.extend(payloads[: len(chunk)])
        out.extend({} for _ in range(max(0, len(chunk) - len(payloads))))
    return out, errors
