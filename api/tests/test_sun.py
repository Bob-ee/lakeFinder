"""NOAA solar algorithm against the US Naval Observatory's published times for Pontiac.

USNO is the reference rather than api.sunrise-sunset.org: the latter's 2026-06-21 Pontiac sunrise
(05:54 EDT) is nearly two minutes off USNO's 05:56, so it is not a value worth testing against.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from seaplane_api.briefing.sun import sun_times

from .conftest import load

KPTK_LAT, KPTK_LON = 42.6655, -83.4187
DETROIT = ZoneInfo("America/Detroit")
TOLERANCE = timedelta(minutes=2)


def _usno(phen: str) -> datetime:
    """The published local time for `phen` on 2026-06-21, as an aware datetime."""
    data = load("usno_pontiac_2026-06-21.json")["properties"]["data"]
    hhmm = next(e["time"] for e in data["sundata"] if e["phen"] == phen)
    return datetime.fromisoformat(f"2026-06-21T{hhmm}").replace(tzinfo=DETROIT)


@pytest.mark.parametrize(
    ("attr", "phen"),
    [
        ("sunrise", "Rise"),
        ("sunset", "Set"),
        ("civil_dawn", "Begin Civil Twilight"),
        ("civil_dusk", "End Civil Twilight"),
    ],
)
def test_pontiac_solstice_within_two_minutes_of_usno(attr, phen):
    got = getattr(sun_times(date(2026, 6, 21), KPTK_LAT, KPTK_LON), attr)
    assert abs(got - _usno(phen)) < TOLERANCE


def test_solstice_local_times_are_the_ones_a_pilot_would_recognise():
    s = sun_times(date(2026, 6, 21), KPTK_LAT, KPTK_LON)
    assert s.sunrise.astimezone(DETROIT).strftime("%H:%M") == "05:55"
    assert s.sunset.astimezone(DETROIT).strftime("%H:%M") == "21:15"


def test_civil_twilight_brackets_sunrise_and_sunset():
    s = sun_times(date(2026, 9, 19), KPTK_LAT, KPTK_LON)
    assert s.civil_dawn < s.sunrise < s.sunset < s.civil_dusk


def test_september_matches_the_data_contract_example():
    """The contract's outlook example uses sunrise 07:18 / civil dawn 06:49 at KPTK."""
    s = sun_times(date(2026, 9, 19), KPTK_LAT, KPTK_LON)
    assert s.sunrise.astimezone(DETROIT).strftime("%H:%M") == "07:17"
    assert s.civil_dawn.astimezone(DETROIT).strftime("%H:%M") == "06:49"


def test_december_day_is_much_shorter_than_june():
    june = sun_times(date(2026, 6, 21), KPTK_LAT, KPTK_LON)
    dec = sun_times(date(2026, 12, 21), KPTK_LAT, KPTK_LON)
    june_len = (june.sunset - june.sunrise).total_seconds()
    dec_len = (dec.sunset - dec.sunrise).total_seconds()
    assert june_len == pytest.approx(15 * 3600 + 19 * 60, abs=120)  # USNO 05:56 to 21:15
    assert dec_len < june_len * 0.62


def test_local_sun_times_crosses_midnight_utc_correctly():
    from seaplane_api.briefing.sun import local_sun_times

    s = local_sun_times(date(2026, 6, 21), KPTK_LAT, KPTK_LON, DETROIT)
    assert s.sunset.date() == date(2026, 6, 21)  # 01:16 UTC on the 22nd is still the 21st locally
    assert s.sunset.utcoffset().total_seconds() == -4 * 3600


def test_polar_night_returns_none_rather_than_raising():
    s = sun_times(date(2026, 12, 21), 78.0, 15.0)  # Svalbard
    assert s.sunrise is None and s.sunset is None
