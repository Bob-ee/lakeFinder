"""aviationweather.gov: METAR, TAF, and airport info. Keyless.

Shapes verified live on 2026-09-19:

- `/api/data/metar?ids=KPTK&format=json` -> list of objects with `obsTime` (unix seconds),
  `temp`/`dewp` in degrees C, `wdir`/`wspd`/`wgst` in knots (direction TRUE), `visib` (statute miles,
  but a *string* `"10+"` when unlimited), **`altim` in hectopascals, not inHg** (the raw METAR says
  A3013 and the field says 1020.4), `clouds: [{cover, base}]` with `base` in feet AGL, `rawOb`.
- `/api/data/taf?ids=KPTK&format=json` -> list with `validTimeFrom`/`validTimeTo` (unix) and `fcsts`,
  parsed by `briefing/taf.py`.
- `/api/data/airport?ids=KPTK&format=json` -> list with `lat`, `lon`, **`elev` in metres** (299 for
  KPTK, whose field elevation is 981 ft), `magdec` as a string like `"07W"`, and
  `runways: [{id, dimension, surface, alignment}]`.

`alignment` is degrees **TRUE**, which is what the contract's `home_airport.runways[].heading` wants,
so nothing is converted. Checked against two airports with large, opposite variation: KSEA 16L/34R
comes back as 180 (true alignment 180.6, magnetic 164 with 16E variation) and KBOS 04L/22R as 20
(magnetic 35 with 15W variation). A magnetic value would have read 164 and 35.

`dimension` is a string like `"6521x150"` (length x width, feet); the length is parsed into
`length_ft`. Anything that does not parse as `<int>x<int>` (missing, malformed, non-numeric) becomes
`None` rather than raising, per the contract's "optional" note on `home_airport.runways[].length_ft`.
"""
from __future__ import annotations

from typing import Any

import httpx

from .http import get_json

BASE = "https://aviationweather.gov/api/data"
METAR_LINK = "https://aviationweather.gov/api/data/metar?ids={ids}&format=raw"
TAF_LINK = "https://aviationweather.gov/api/data/taf?ids={ids}&format=raw"

M_TO_FT = 3.280839895


async def fetch_metar(c: httpx.AsyncClient, ids: str) -> tuple[list[dict] | None, str | None]:
    data, err = await get_json(c, f"{BASE}/metar", {"ids": ids, "format": "json"}, label="metar")
    if err:
        return None, err
    return (data if isinstance(data, list) else []), None


async def fetch_metar_bbox(
    c: httpx.AsyncClient, bbox: tuple[float, float, float, float]
) -> tuple[list[dict] | None, str | None]:
    """Every current METAR inside `(min_lat, min_lon, max_lat, max_lon)`.

    Verified live on 2026-09-19: `?bbox=42.2,-83.2,42.8,-82.2` returns KMTC, KDET, KVLL and CYQG,
    each with `lat`, `lon`, `name` and `obsTime`, so the caller can rank them by distance to the
    water and label them. The order the feed returns them in is not distance order.
    """
    data, err = await get_json(
        c,
        f"{BASE}/metar",
        {"bbox": ",".join(f"{v:.3f}" for v in bbox), "format": "json"},
        label="metar_bbox",
    )
    if err:
        return None, err
    return (data if isinstance(data, list) else []), None


async def fetch_taf(c: httpx.AsyncClient, ids: str) -> tuple[list[dict] | None, str | None]:
    data, err = await get_json(c, f"{BASE}/taf", {"ids": ids, "format": "json"}, label="taf")
    if err:
        return None, err
    return (data if isinstance(data, list) else []), None


async def fetch_airport(c: httpx.AsyncClient, ident: str) -> tuple[dict | None, str | None]:
    data, err = await get_json(c, f"{BASE}/airport", {"ids": ident, "format": "json"}, label="airport")
    if err:
        return None, err
    if not isinstance(data, list) or not data:
        return None, f"airport: {ident} not found"
    return data[0], None


def _parse_length_ft(dimension: Any) -> int | None:
    """`"6521x150"` (length x width, feet) -> `6521`. Anything odd or missing -> `None`."""
    if not isinstance(dimension, str):
        return None
    length, sep, _width = dimension.partition("x")
    if not sep:  # no "x": not the documented shape, can't tell length from width
        return None
    try:
        return int(float(length.strip()))
    except (ValueError, TypeError):
        return None


def to_home_airport(raw: dict[str, Any]) -> dict:
    """Map the airport-info object onto the contract's `home_airport` shape.

    `elev` is metres and the contract wants feet. `alignment` is already true (see module docstring),
    so `heading` is taken straight across for the first-named runway end.
    """
    runways = []
    for r in raw.get("runways") or []:
        alignment = r.get("alignment")
        if alignment is None:
            continue
        runways.append({
            "id": str(r.get("id") or "").strip(),
            "heading": round(float(alignment)) % 360,
            "length_ft": _parse_length_ft(r.get("dimension")),
        })
    return {
        "id": raw.get("icaoId") or raw.get("faaId") or raw.get("iataId") or "",
        "name": " ".join(str(raw.get("name") or "").split()).title(),
        "lat": round(float(raw.get("lat") or 0.0), 5),
        "lon": round(float(raw.get("lon") or 0.0), 5),
        "elev_ft": round(float(raw.get("elev") or 0.0) * M_TO_FT),
        "runways": runways,
    }


def pick_station(reports: list[dict] | None, ident: str) -> dict | None:
    """The report for `ident`, newest first; the feed can return several for one request."""
    if not reports:
        return None
    mine = [r for r in reports if str(r.get("icaoId", "")).upper() == ident.upper()] or reports
    return max(mine, key=lambda r: r.get("obsTime") or r.get("issueTime") or 0)
