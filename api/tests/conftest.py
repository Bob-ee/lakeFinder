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

FIXTURES = Path(__file__).parent / "fixtures"
DETROIT = ZoneInfo("America/Detroit")


def load(name: str):
    path = FIXTURES / name
    if path.suffix == ".txt":
        return path.read_text()
    return json.loads(path.read_text())


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
        fetched_at=datetime(2026, 9, 19, 12, 0, tzinfo=UTC),
    )
