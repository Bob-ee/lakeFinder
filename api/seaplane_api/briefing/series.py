"""Turning an Open-Meteo point response into a lookup, and a lookup into `Conditions`.

Open-Meteo is asked for `timezone=<settings.timezone>`, so `hourly.time` comes back as naive local
ISO strings ("2026-09-19T14:00"). They are localised here once, which makes DST handling the
zoneinfo library's problem rather than ours.

Unit notes from the live API (checked 2026-09-19): `visibility` is metres, `surface_pressure` and
`pressure_msl` are hPa, `precipitation` is mm, `cape` is J/kg, winds are knots and temperatures
Fahrenheit because the request asks for those units. Density altitude needs an altimeter setting,
which is `pressure_msl`, not `surface_pressure`.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, tzinfo

from . import aero
from .scoring import Conditions
from .taf import TafHour

M_TO_SM = 1.0 / 1609.344


@dataclass
class HourlySeries:
    """One Open-Meteo point's hourly arrays, addressable by local datetime."""

    times: list[datetime]
    values: dict[str, list]
    index: dict[datetime, int]
    elevation_m: float = 0.0

    @classmethod
    def from_open_meteo(cls, payload: dict, tz: tzinfo) -> HourlySeries:
        hourly = payload.get("hourly") or {}
        raw_times = hourly.get("time") or []
        times = [datetime.fromisoformat(t).replace(tzinfo=tz) for t in raw_times]
        values = {k: v for k, v in hourly.items() if k != "time"}
        return cls(
            times=times,
            values=values,
            index={t: i for i, t in enumerate(times)},
            elevation_m=float(payload.get("elevation") or 0.0),
        )

    def at(self, when: datetime) -> dict[str, float | None]:
        """The hour containing `when` (the series is hourly, so truncate)."""
        key = when.astimezone(self.times[0].tzinfo).replace(minute=0, second=0, microsecond=0) if self.times else when
        i = self.index.get(key)
        if i is None:
            return {}
        return {k: (v[i] if i < len(v) else None) for k, v in self.values.items()}

    def covers(self, when: datetime) -> bool:
        if not self.times:
            return False
        key = when.astimezone(self.times[0].tzinfo).replace(minute=0, second=0, microsecond=0)
        return key in self.index


def daylight_fraction(start: datetime, end: datetime, dawn: datetime | None, dusk: datetime | None) -> float:
    """Fraction of `[start, end)` between civil dawn and civil dusk."""
    if dawn is None or dusk is None:
        return 1.0
    span = (end - start).total_seconds()
    if span <= 0:
        return 0.0
    lo = max(start, dawn)
    hi = min(end, dusk)
    return max(0.0, (hi - lo).total_seconds()) / span


def conditions_from_model(
    model: dict[str, float | None],
    *,
    field_elev_ft: float,
    taf_hour: TafHour | None,
    alerts: list[str],
    daylight: float,
    runways: list[dict],
    metar: dict | None = None,
) -> Conditions:
    """Build one `Conditions` from the model hour, letting the TAF and (optionally) a METAR win.

    Precedence, most trusted first: METAR (only when the caller passes one -- design 3.1 allows it
    for the first block when it is under 90 minutes old), then TAF for ceiling/visibility/fog, then
    the model. Wind always comes from the model unless a METAR is supplied, because the TAF's
    prevailing wind is a 3-6 hour smear while the model is hourly.
    """
    wind_dir = _f(model.get("wind_direction_10m"))
    wind_kt = _f(model.get("wind_speed_10m"))
    gust_kt = _f(model.get("wind_gusts_10m"))
    temp_f = _f(model.get("temperature_2m"))
    dew_f = _f(model.get("dew_point_2m"))
    vis_m = _f(model.get("visibility"))
    vis_sm = None if vis_m is None else vis_m * M_TO_SM
    altim_inhg = None
    msl = _f(model.get("pressure_msl"))
    if msl is not None:
        altim_inhg = aero.hpa_to_inhg(msl)

    ceiling_ft: float | None = None
    ceiling_known = False
    taf_fog = False
    if taf_hour is not None:
        ceiling_known = True
        ceiling_ft = taf_hour.ceiling_ft
        taf_fog = taf_hour.fog
        if taf_hour.vis_sm is not None:
            vis_sm = min(vis_sm, taf_hour.vis_sm) if vis_sm is not None else taf_hour.vis_sm

    if metar:
        ceiling_known = True
        ceiling_ft = _metar_ceiling(metar)
        m_vis = _f(metar.get("visib") if not isinstance(metar.get("visib"), str) else None)
        if m_vis is None:
            from .taf import parse_visibility

            m_vis = parse_visibility(metar.get("visib"))
        if m_vis is not None:
            vis_sm = m_vis
        if metar.get("wdir") is not None and not isinstance(metar.get("wdir"), str):
            wind_dir = _f(metar.get("wdir"))
        if metar.get("wspd") is not None:
            wind_kt = _f(metar.get("wspd"))
        if metar.get("wgst") is not None:
            gust_kt = _f(metar.get("wgst"))
        if metar.get("temp") is not None:
            temp_f = aero.c_to_f(_f(metar["temp"]))
        if metar.get("dewp") is not None:
            dew_f = aero.c_to_f(_f(metar["dewp"]))
        if metar.get("altim") is not None:  # hPa on this feed, despite the METAR carrying inHg
            altim_inhg = aero.hpa_to_inhg(_f(metar["altim"]))
        wx = (metar.get("wxString") or "").upper()
        taf_fog = taf_fog or "FG" in wx

    da_ft = None
    if altim_inhg is not None and temp_f is not None:
        da_ft = aero.density_altitude_ft(field_elev_ft, altim_inhg, aero.f_to_c(temp_f))

    xw = xwg = None
    runway_id = None
    if wind_dir is not None and wind_kt is not None:
        end = aero.best_runway(runways, wind_dir, wind_kt)
        if end is not None:
            runway_id = end.id
            xw = aero.crosswind_kt(wind_dir, wind_kt, end.heading)
            xwg = aero.crosswind_kt(wind_dir, gust_kt if gust_kt is not None else wind_kt, end.heading)

    return Conditions(
        wind_dir_deg=wind_dir or 0.0,
        wind_kt=wind_kt or 0.0,
        gust_kt=gust_kt,
        ceiling_ft=ceiling_ft,
        ceiling_known=ceiling_known,
        vis_sm=vis_sm,
        temp_f=temp_f,
        dewpoint_f=dew_f,
        precip_mm=_f(model.get("precipitation")),
        precip_prob=_f(model.get("precipitation_probability")),
        weather_code=None if model.get("weather_code") is None else int(model["weather_code"]),
        cape=_f(model.get("cape")),
        da_ft=da_ft,
        taf_fog=taf_fog,
        alerts=alerts,
        daylight_fraction=daylight,
        xwind_kt=xw,
        xwind_gust_kt=xwg,
        runway=runway_id,
    )


def _metar_ceiling(metar: dict) -> float | None:
    bases = [
        c["base"]
        for c in (metar.get("clouds") or [])
        if c.get("cover") in ("BKN", "OVC", "OVX") and c.get("base") is not None
    ]
    return float(min(bases)) if bases else None


def _f(v) -> float | None:
    if v is None or isinstance(v, str):
        return None
    return float(v)


def metar_is_fresh(metar: dict | None, now: datetime, max_age_min: int = 90) -> bool:
    """Design 3.1: the METAR overrides the first block only when it is under 90 minutes old."""
    if not metar or metar.get("obsTime") is None:
        return False
    age = now.timestamp() - float(metar["obsTime"])
    return 0 <= age <= max_age_min * 60


# --- display helpers, shared by the block and hour row shapes --------------------------------

VIS_DISPLAY_MAX_SM = 10


def display_vis(vis_sm: float | None) -> int | None:
    """Visibility for display, capped at 10 SM.

    Aviation reports visibility as "10" (or P6SM) once it is unrestricted; Open-Meteo happily
    returns 14 or 15 SM, which reads as a false precision next to a METAR. Scoring uses the
    uncapped value, so nothing about the verdict changes.
    """
    if vis_sm is None:
        return None
    return min(round(vis_sm), VIS_DISPLAY_MAX_SM)


def round100(value: float | None) -> int | None:
    """Ceilings and density altitudes are reported to the nearest 100 ft (data contract)."""
    return None if value is None else round(value / 100.0) * 100
