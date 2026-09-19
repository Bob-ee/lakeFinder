"""Sunrise, sunset and civil twilight from the NOAA solar position algorithm. No network.

Straight transcription of the NOAA Solar Calculator spreadsheet (Astronomical Algorithms, Meeus,
low-precision form): Julian century -> geometric mean longitude and anomaly -> equation of centre ->
apparent longitude -> obliquity -> declination and equation of time -> hour angle for a given zenith.

Everything is evaluated first at local solar noon and then re-evaluated once at the estimated event
time, which is the usual refinement and keeps the error under a few seconds at Michigan latitudes.
Zeniths: 90.833 deg for sunrise/sunset (refraction plus the solar semi-diameter), 96 deg for civil
twilight -- the two the briefing cares about.

Polar edge cases (`acos` out of range) return `None` rather than raising; Michigan never hits them,
but the home airport is user-settable.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta, tzinfo

SUNRISE_ZENITH = 90.833
CIVIL_ZENITH = 96.0


@dataclass(frozen=True)
class SunTimes:
    """UTC instants; `None` when the sun never reaches that zenith on that date."""

    civil_dawn: datetime | None
    sunrise: datetime | None
    sunset: datetime | None
    civil_dusk: datetime | None


def _julian_day(d: date) -> float:
    """Julian day at 00:00 UTC of `d` (Gregorian)."""
    y, m = d.year, d.month
    if m <= 2:
        y -= 1
        m += 12
    a = y // 100
    b = 2 - a + a // 4
    return math.floor(365.25 * (y + 4716)) + math.floor(30.6001 * (m + 1)) + d.day + b - 1524.5


def _century(jd: float) -> float:
    return (jd - 2451545.0) / 36525.0


def _declination_and_eqtime(t: float) -> tuple[float, float]:
    """`(solar declination in degrees, equation of time in minutes)` for Julian century `t`."""
    l0 = (280.46646 + t * (36000.76983 + t * 0.0003032)) % 360.0
    m = 357.52911 + t * (35999.05029 - 0.0001537 * t)
    e = 0.016708634 - t * (0.000042037 + 0.0000001267 * t)
    mrad = math.radians(m)
    c = (
        math.sin(mrad) * (1.914602 - t * (0.004817 + 0.000014 * t))
        + math.sin(2 * mrad) * (0.019993 - 0.000101 * t)
        + math.sin(3 * mrad) * 0.000289
    )
    true_long = l0 + c
    omega = 125.04 - 1934.136 * t
    app_long = true_long - 0.00569 - 0.00478 * math.sin(math.radians(omega))
    seconds = 21.448 - t * (46.815 + t * (0.00059 - t * 0.001813))
    e0 = 23.0 + (26.0 + seconds / 60.0) / 60.0
    obliq = e0 + 0.00256 * math.cos(math.radians(omega))

    decl = math.degrees(math.asin(math.sin(math.radians(obliq)) * math.sin(math.radians(app_long))))

    y = math.tan(math.radians(obliq / 2.0)) ** 2
    l0rad = math.radians(l0)
    eqtime = 4.0 * math.degrees(
        y * math.sin(2 * l0rad)
        - 2.0 * e * math.sin(mrad)
        + 4.0 * e * y * math.sin(mrad) * math.cos(2 * l0rad)
        - 0.5 * y * y * math.sin(4 * l0rad)
        - 1.25 * e * e * math.sin(2 * mrad)
    )
    return decl, eqtime


def _hour_angle(lat: float, decl: float, zenith: float) -> float | None:
    """Hour angle in degrees between the event and solar noon, or `None` above/below the horizon."""
    latr, dr = math.radians(lat), math.radians(decl)
    denom = math.cos(latr) * math.cos(dr)
    if abs(denom) < 1e-12:
        return None
    cos_ha = math.cos(math.radians(zenith)) / denom - math.tan(latr) * math.tan(dr)
    if cos_ha > 1.0 or cos_ha < -1.0:
        return None
    return math.degrees(math.acos(cos_ha))


def _event_utc(d: date, lat: float, lon: float, zenith: float, rising: bool) -> datetime | None:
    """UTC instant of the event, refined once from the local-noon estimate."""
    jd = _julian_day(d)
    t = _century(jd + 0.5 - lon / 360.0)  # local solar noon, near enough for the first pass
    minutes: float | None = None
    for _ in range(2):
        decl, eqtime = _declination_and_eqtime(t)
        ha = _hour_angle(lat, decl, zenith)
        if ha is None:
            return None
        signed = ha if rising else -ha
        minutes = 720.0 - 4.0 * (lon + signed) - eqtime
        t = _century(jd + minutes / 1440.0)
    assert minutes is not None
    return datetime(d.year, d.month, d.day, tzinfo=UTC) + timedelta(minutes=minutes)


def sun_times(d: date, lat: float, lon: float) -> SunTimes:
    """Civil dawn, sunrise, sunset and civil dusk (UTC) for the civil date `d` at `lat`/`lon`."""
    return SunTimes(
        civil_dawn=_event_utc(d, lat, lon, CIVIL_ZENITH, rising=True),
        sunrise=_event_utc(d, lat, lon, SUNRISE_ZENITH, rising=True),
        sunset=_event_utc(d, lat, lon, SUNRISE_ZENITH, rising=False),
        civil_dusk=_event_utc(d, lat, lon, CIVIL_ZENITH, rising=False),
    )


def local_sun_times(d: date, lat: float, lon: float, tz: tzinfo) -> SunTimes:
    """`sun_times` for the *local* date `d`, converted into `tz`.

    The UTC date of a Michigan sunrise is the same civil date, so no rollover correction is needed
    for the deploy target; converting after the fact keeps it correct for other longitudes anyway.
    """
    s = sun_times(d, lat, lon)
    return SunTimes(*[None if v is None else v.astimezone(tz) for v in (s.civil_dawn, s.sunrise, s.sunset, s.civil_dusk)])
