"""The factor table (design 3.2) as a pure function over one block's or hour's conditions.

Design 3.2 says "compute, in order, and record the first factor that fails", and separately "block
score = worst factor". Both are honoured: every factor is evaluated, the score is the worst level,
and `limiting` is the *first* factor in table order that reached that worst level. Order is the
design's table order, with `daylight` appended (it only bites at the edges of the window).

A factor whose input is unknown is **not evaluated** rather than assumed good or bad, and the caller
is told which ones were skipped so it can say so in `confidence_reasons`. In practice that is
`ceiling` and `visibility` whenever no METAR or TAF covers the hour: Open-Meteo has no ceiling
product and NWS's `forecastHourly` carries no sky cover, so there is no defensible way to invent a
cloud base. (`ceiling_ft: null` in the output then means "no ceiling reported"; `skipped` says
whether that is "clear above" or "unknown".)

Everything the app prints is a level plus a limiting factor. The words are only ever
favorable / marginal / unfavorable -- never legal, safe, or go.
"""
from __future__ import annotations

from dataclasses import dataclass, field

FAVORABLE = "favorable"
MARGINAL = "marginal"
UNFAVORABLE = "unfavorable"
LEVELS = (FAVORABLE, MARGINAL, UNFAVORABLE)

# Design 3.2 table order; `daylight` is appended. Lake factors live in lakes.py.
FACTOR_ORDER = (
    "wind",
    "gusts",
    "xwind_runway",
    "ceiling",
    "visibility",
    "precip",
    "convection",
    "density_altitude",
    "fog",
    "temperature",
    "alert",
    "daylight",
)

TS_WEATHER_CODES = frozenset({95, 96, 99})
_ALERT_UNFAVORABLE = (
    "lake wind",
    "small craft",
    "thunderstorm",
    "tornado",
    "winter",
    "blizzard",
    "ice storm",
    "freezing rain",
    "snow squall",
    "hurricane",
    "tropical storm",
)


def rank(level: str) -> int:
    return LEVELS.index(level)


def worse(a: str, b: str) -> str:
    return a if rank(a) >= rank(b) else b


def band(value: float, ok: float, limit: float) -> str:
    """Lower is better: `<= ok` favorable, `<= limit` marginal, above unfavorable."""
    if value <= ok:
        return FAVORABLE
    if value <= limit:
        return MARGINAL
    return UNFAVORABLE


def band_high(value: float, ok: float, floor: float) -> str:
    """Higher is better: `>= ok` favorable, `>= floor` marginal, below unfavorable."""
    if value >= ok:
        return FAVORABLE
    if value >= floor:
        return MARGINAL
    return UNFAVORABLE


@dataclass
class Conditions:
    """One block or hour at the airport. Units are the ones the output uses, already converted."""

    wind_dir_deg: float = 0.0
    wind_kt: float = 0.0
    gust_kt: float | None = None
    ceiling_ft: float | None = None
    ceiling_known: bool = False  # False = nobody told us; True + None = genuinely no ceiling
    vis_sm: float | None = None
    temp_f: float | None = None
    dewpoint_f: float | None = None
    precip_mm: float | None = None
    precip_prob: float | None = None
    weather_code: int | None = None
    cape: float | None = None
    da_ft: float | None = None
    taf_fog: bool = False  # TAF carries FG/FZFG for this hour
    alerts: list[str] = field(default_factory=list)
    daylight_fraction: float = 1.0
    xwind_kt: float | None = None  # sustained crosswind on the best runway
    xwind_gust_kt: float | None = None
    runway: str | None = None


@dataclass
class Score:
    level: str
    limiting: str | None
    factors: dict[str, str]  # factor id -> level, only the ones actually evaluated
    skipped: tuple[str, ...]  # factor ids with no input
    fog_risk: bool


def alert_level(events: list[str]) -> str:
    """Worst level implied by a list of active NWS alert event names."""
    level = FAVORABLE
    for event in events:
        s = event.lower()
        if any(k in s for k in _ALERT_UNFAVORABLE):
            level = UNFAVORABLE
        elif "warning" in s:
            level = worse(level, UNFAVORABLE)
        else:
            level = worse(level, MARGINAL)
    return level


def is_convective_alert(events: list[str]) -> bool:
    return any(k in e.lower() for e in events for k in ("thunderstorm", "tornado", "severe"))


def score_conditions(c: Conditions, limits) -> Score:
    """Evaluate the design 3.2 factor table. `limits` is a `settings.Limits`."""
    f: dict[str, str] = {}
    skipped: list[str] = []

    f["wind"] = band(c.wind_kt, limits.wind_ok, limits.wind_max)

    spread = max(0.0, (c.gust_kt or c.wind_kt) - c.wind_kt)
    f["gusts"] = band(spread, limits.gust_spread_ok, limits.gust_spread_max)

    if c.xwind_kt is None:
        skipped.append("xwind_runway")
    else:
        # Design 3.2: sustained for the favorable check, gusts for marginal/unfavorable.
        xg = c.xwind_gust_kt if c.xwind_gust_kt is not None else c.xwind_kt
        f["xwind_runway"] = worse(
            band(c.xwind_kt, limits.xwind_runway_ok, limits.xwind_runway_max),
            band(xg, limits.xwind_runway_max, limits.xwind_runway_max),
        )

    if not c.ceiling_known:
        skipped.append("ceiling")
    elif c.ceiling_ft is None:
        f["ceiling"] = FAVORABLE  # reported, and there is no ceiling
    else:
        f["ceiling"] = band_high(c.ceiling_ft, limits.ceiling_ok, limits.ceiling_min)

    if c.vis_sm is None:
        skipped.append("visibility")
    else:
        f["visibility"] = band_high(c.vis_sm, limits.vis_ok, limits.vis_min)

    if c.precip_mm is None and c.precip_prob is None:
        skipped.append("precip")
    else:
        mm = c.precip_mm or 0.0
        prob = c.precip_prob or 0.0
        if mm <= 0.0 and prob < 30:
            f["precip"] = FAVORABLE
        elif mm <= 2.5 and prob < 60:
            f["precip"] = MARGINAL
        else:
            f["precip"] = UNFAVORABLE

    ts = c.weather_code in TS_WEATHER_CODES
    if c.cape is None and c.weather_code is None and not c.alerts:
        skipped.append("convection")
    elif ts or is_convective_alert(c.alerts):
        f["convection"] = UNFAVORABLE
    else:
        cape = c.cape or 0.0
        f["convection"] = FAVORABLE if cape < 500 else (MARGINAL if cape < 1000 else UNFAVORABLE)

    if c.da_ft is None:
        skipped.append("density_altitude")
    else:
        f["density_altitude"] = band(c.da_ft, limits.da_ok, limits.da_max)

    fog_risk = False
    if c.temp_f is None or c.dewpoint_f is None:
        skipped.append("fog")
    else:
        fog_risk = (c.temp_f - c.dewpoint_f) <= limits.fog_spread_f and c.wind_kt <= 5
        if c.taf_fog or (c.vis_sm is not None and c.vis_sm < limits.vis_min):
            f["fog"] = UNFAVORABLE
        elif fog_risk:
            f["fog"] = MARGINAL
        else:
            f["fog"] = FAVORABLE

    if c.temp_f is None:
        skipped.append("temperature")
    else:
        f["temperature"] = band_high(c.temp_f, limits.temp_water_min_f, 32.0)

    f["alert"] = alert_level(c.alerts)

    if c.daylight_fraction >= 0.9:
        f["daylight"] = FAVORABLE
    elif c.daylight_fraction >= 1.0 / 3.0:  # at least one usable hour of a three-hour block
        f["daylight"] = MARGINAL
    else:
        f["daylight"] = UNFAVORABLE

    level = FAVORABLE
    for fid in FACTOR_ORDER:
        if fid in f:
            level = worse(level, f[fid])
    limiting = None
    if level != FAVORABLE:
        limiting = next(fid for fid in FACTOR_ORDER if f.get(fid) == level)
    return Score(level=level, limiting=limiting, factors=f, skipped=tuple(skipped), fog_risk=fog_risk)
