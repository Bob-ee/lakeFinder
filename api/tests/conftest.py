"""Fixture loading. Everything in `tests/fixtures/` is a recorded live response or an edited copy.

Recorded 2026-09-19 from the real endpoints:
`metar_kptk.json`, `taf_kptk.json`, `airport_kptk.json`, `open_meteo_kptk.json`, `nws_alerts.json`,
`nws_points.json`, `nws_hourly.json`, `ndbc_latest_obs.txt` (trimmed to the Great Lakes bbox),
`sunrise_sunset_pontiac_2026-06-21.json` (the independent value the sun tests check against), and
`index_sample.json` (six real lakes from `data/out/index.json`).

Edited copies, for the day types in design 7: `om_calm`, `om_gusty`, `om_ifr`, `om_convective`,
`om_january`, `taf_vfr`, `taf_ifr`.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from seaplane_api.briefing.generate import Feeds
from seaplane_api.briefing.series import HourlySeries
from seaplane_api.settings import Settings
from seaplane_api.wavefield import RECORD, WaveField

FIXTURES = Path(__file__).parent / "fixtures"
# The wave fixtures are the agreement between this service and `rules/waves/` in JS, so they are read
# from the repo rather than copied here: a copy would drift the day one side is regenerated.
SHARED_FIXTURES = Path(__file__).resolve().parents[2] / "rules" / "fixtures"
DETROIT = ZoneInfo("America/Detroit")


def load(name: str):
    path = FIXTURES / name
    if path.suffix == ".txt":
        return path.read_text()
    return json.loads(path.read_text())


def shared(name: str):
    path = SHARED_FIXTURES / name
    if path.suffix == ".bin":
        return path.read_bytes()
    return json.loads(path.read_text())


def sample_wave_field() -> WaveField:
    """`rules/fixtures/wave_points.sample.*` through the real reader."""
    return WaveField(shared("wave_points.sample.json"), shared("wave_points.sample.bin"))


def wave_point(
    lon: float,
    lat: float,
    *,
    label: int,
    fetch_m: list[float],
    run_ft: list[float],
    depth_m: float | None = None,
) -> dict:
    """One record in the pack's own units (10 m of fetch, 10 ft of run, decimetres of depth)."""
    return {
        "lon": lon,
        "lat": lat,
        "depth_dm": 0xFFFF if depth_m is None else min(0xFFFE, round(depth_m * 10)),
        "label": label,
        "fetch": [min(0xFFFF, round(f / 10)) for f in fetch_m],
        "run": [min(0xFFFF, round(r / 10)) for r in run_ft],
    }


def pack_wave_points(lakes: dict[int | str, list[dict]], labels: list[str]) -> tuple[dict, bytes]:
    """`(index, blob)` in the contract's format for a handful of hand-made water bodies."""
    index = {
        "version": 1,
        "record_bytes": 60,
        "fetch_unit_m": 10,
        "run_unit_ft": 10,
        "labels": labels,
        "lakes": {},
    }
    blob = b""
    first = 0
    for lake_id, points in lakes.items():
        index["lakes"][str(lake_id)] = [first, len(points)]
        for p in points:
            blob += RECORD.pack(p["lon"], p["lat"], p["depth_dm"], p["label"], *p["fetch"], *p["run"])
        first += len(points)
    return index, blob


def make_wave_field(lakes: dict[int | str, list[dict]], labels: list[str]) -> WaveField:
    return WaveField(*pack_wave_points(lakes, labels))


@pytest.fixture
def settings() -> Settings:
    return Settings()


@pytest.fixture
def index_sample() -> list[dict]:
    return load("index_sample.json")


def make_feeds(
    om_name: str,
    *,
    taf_name: str | None = None,
    metar: dict | None = None,
    alerts: list[dict] | None = None,
    nws_hourly: list[dict] | None = None,
    lake_cells: dict | None = None,
    buoys: list[dict] | None = None,
    home_water_metars: list[dict] | None = None,
    marine: dict | None = None,
    tz=DETROIT,
) -> Feeds:
    """A `Feeds` built straight from fixtures, with no network anywhere."""
    payload = load(om_name)
    series = HourlySeries.from_open_meteo(payload, tz)
    return Feeds(
        metar=metar,
        taf=load(taf_name) if taf_name else None,
        airport=series,
        airport_daily=payload.get("daily"),
        lake_series=dict(lake_cells or {}),
        alerts=alerts if alerts is not None else [],
        nws_hourly=nws_hourly,
        buoys=buoys,
        home_water_metars=home_water_metars,
        marine=marine,
        fetched_at=datetime(2026, 9, 19, 12, 0, tzinfo=UTC),
    )
