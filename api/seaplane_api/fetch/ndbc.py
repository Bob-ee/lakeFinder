"""NDBC `latest_obs.txt`: buoy and shore-station observations. Optional, and fetched last.

Read by the home water's `observed` list (data contract, "Briefing"): the stations within 15 nm of
the water that report wind or wave height, newest observation only, stale ones dropped.

The file is a whitespace-aligned table of every station on earth (~900 rows, ~90 KB) with two header
lines and `MM` for missing values. Units: `WDIR` degrees true, `WSPD`/`GST` m/s, `WVHT` metres. The
`YYYY MM DD hh mm` columns are UTC and are the only timestamp the file carries, so `at` is built from
them; a row whose date does not parse gets `at: None` and the caller treats it as unusable rather
than as fresh.
"""
from __future__ import annotations

from datetime import UTC, datetime

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
                "at": _observed_at(row),
                "dir_deg": _num(row["wdir"]),
                "speed_kt": None if wspd is None else round(wspd * MS_TO_KT, 1),
                "gust_kt": None if gst is None else round(gst * MS_TO_KT, 1),
                "wave_height_m": _num(row["wvht"]),
            }
        )
    return out


def _observed_at(row: dict[str, str]) -> datetime | None:
    """The row's UTC observation time, or `None` when the date columns are missing or junk."""
    try:
        return datetime(
            int(row["yy"]), int(row["mm"]), int(row["dd"]), int(row["hh"]), int(row["mi"]), tzinfo=UTC
        )
    except (ValueError, KeyError):
        return None


def _num(value: str) -> float | None:
    try:
        return float(value)
    except ValueError:
        return None


HISTORY_URL = "https://www.ndbc.noaa.gov/data/realtime2/{station}.txt"
_HISTORY_COLUMNS = ("yy", "mm", "dd", "hh", "mi", "wdir", "wspd", "gst", "wvht")


async def fetch_history(c: httpx.AsyncClient, station: str) -> tuple[list[dict] | None, str | None]:
    """One station's last 45 days of observations, newest first (`realtime2/<station>.txt`).

    The same table as `latest_obs.txt` minus the station, latitude and longitude columns, and with
    the newest row first; `MM` is missing. Units are as there: `WSPD`/`GST` m/s, `WVHT` m, times UTC.
    Rows come back in knots and metres like `parse_latest_obs`.
    """
    text, err = await get_text(c, HISTORY_URL.format(station=station), label=f"ndbc_history[{station}]")
    if err or text is None:
        return None, err or "ndbc_history: empty response"
    return parse_history(text), None


def parse_history(text: str) -> list[dict]:
    out: list[dict] = []
    for line in text.splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) < len(_HISTORY_COLUMNS):
            continue
        row = dict(zip(_HISTORY_COLUMNS, parts, strict=False))
        at = _observed_at(row)
        if at is None:
            continue
        wspd, gst = _num(row["wspd"]), _num(row["gst"])
        out.append(
            {
                "at": at,
                "dir_deg": _num(row["wdir"]),
                "speed_kt": None if wspd is None else round(wspd * MS_TO_KT, 1),
                "gust_kt": None if gst is None else round(gst * MS_TO_KT, 1),
                "wave_height_m": _num(row["wvht"]),
            }
        )
    return out
