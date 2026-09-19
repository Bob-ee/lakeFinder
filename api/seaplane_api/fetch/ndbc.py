"""NDBC `latest_obs.txt`: Great Lakes buoy observations. Optional, and fetched last.

Design 2 lists this as "only for lakes flagged shoreline". `index.json` carries no shoreline flag
today, so nothing in the briefing consumes it yet; the parser is here (and tested) so that wiring it
up is a one-line change once the pipeline tags Great Lakes shoreline water.

The file is a whitespace-aligned table of every buoy on earth (~900 rows, ~90 KB) with two header
lines and `MM` for missing values. Units: `WDIR` degrees true, `WSPD`/`GST` m/s, `WVHT` metres.
"""
from __future__ import annotations

import httpx

from .http import get_text

URL = "https://www.ndbc.noaa.gov/data/latest_obs/latest_obs.txt"
MS_TO_KT = 1.943844

_COLUMNS = (
    "stn", "lat", "lon", "yy", "mm", "dd", "hh", "mi", "wdir", "wspd", "gst",
    "wvht", "dpd", "apd", "mwd", "pres", "ptdy", "atmp", "wtmp", "dewp", "vis", "tide",
)


async def fetch_latest_obs(
    c: httpx.AsyncClient, bbox: tuple[float, float, float, float]
) -> tuple[list[dict] | None, str | None]:
    """Buoys inside `(west, south, east, north)`, in knots."""
    text, err = await get_text(c, URL, label="ndbc")
    if err or text is None:
        return None, err or "ndbc: empty response"
    return parse_latest_obs(text, bbox), None


def parse_latest_obs(text: str, bbox: tuple[float, float, float, float]) -> list[dict]:
    west, south, east, north = bbox
    out: list[dict] = []
    for line in text.splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) < len(_COLUMNS):
            continue
        row = dict(zip(_COLUMNS, parts, strict=False))
        lat, lon = _num(row["lat"]), _num(row["lon"])
        if lat is None or lon is None or not (south <= lat <= north and west <= lon <= east):
            continue
        wspd, gst = _num(row["wspd"]), _num(row["gst"])
        out.append(
            {
                "id": row["stn"],
                "source": "ndbc",
                "lat": lat,
                "lon": lon,
                "dir_deg": _num(row["wdir"]),
                "speed_kt": None if wspd is None else round(wspd * MS_TO_KT, 1),
                "gust_kt": None if gst is None else round(gst * MS_TO_KT, 1),
                "wave_height_m": _num(row["wvht"]),
            }
        )
    return out


def _num(value: str) -> float | None:
    try:
        return float(value)
    except ValueError:
        return None
