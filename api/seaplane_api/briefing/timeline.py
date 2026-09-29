"""`briefing.json` -> `timeline`: the hourly forecast picture (data contract, "Forecast timeline").

One 79-hour axis (6 h before the current hour through 72 h after, from `settings.forecast`), and on it:
the airport's score, the home water's per-region waves, the buoy's observed history for the hours that
are already over, and the windows worth looking at. Pure: the feeds are fetched elsewhere.

Decisions the contract leaves open, and how they were settled:

- **Airport score** is the same `score_conditions` the outlook hours use, from the model hour plus the
  TAF where it covers. The METAR override the outlook applies to the current hour is *not* applied: the
  timeline's wind is the model's (the contract says "airport cell"), and mixing one observed hour into a
  model row would make that column disagree with its neighbours.
- **Alerts** apply to the hours before their `ends` only (an alert with no end applies to every future
  hour); the outlook applies them to all its hours, but a 72 h axis would otherwise carry tonight's
  advisory to Thursday.
- **Daylight** is the whole hour inside civil dawn..civil dusk. An hour that is only partly light is
  night for windows, so a window never crosses dusk and never starts before dawn.
- **Combined score** (airport with home water) is the worse of the two; when the water has no forecast
  for the hour the airport alone decides it (an unknown is not a worse score). Ties go to the airport's
  limiting factor.
- **Region order** in `home_water.labels` is the pack's label order (ascending label index), fixed for
  the whole axis so an array column always means the same water.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from ..settings import Settings
from . import homewater as homewater_mod
from . import lakes as lakes_mod
from . import taf as taf_mod
from . import wave
from .scoring import FAVORABLE, MARGINAL, UNFAVORABLE, rank, score_conditions
from .series import HourlySeries, conditions_from_model, daylight_fraction, display_vis, round100
from .sun import SunTimes, local_sun_times

OBS_MATCH = timedelta(minutes=30)  # a buoy row this close to the top of the hour is that hour's


def time_axis(now_utc: datetime, tz, past_h: int, horizon_h: int) -> list[datetime]:
    """Aware local hours from `past_h` before the current hour through `horizon_h` after it.

    Stepped on the UTC timeline, so a DST change inside the range yields the real 23 or 25 local hours
    (the repeated hour appears twice, with the two offsets) rather than a wall-clock 24 a day.
    """
    cur = now_utc.astimezone(tz).replace(minute=0, second=0, microsecond=0).astimezone(UTC)
    return [(cur + timedelta(hours=k)).astimezone(tz) for k in range(-past_h, horizon_h + 1)]


@dataclass
class HourState:
    """What the window search needs to know about one axis hour."""

    start: datetime
    past: bool
    daylight: bool
    level: str | None  # the combined score; `None` when the airport has no forecast for the hour
    limiting: str | None


def combine(air: tuple[str | None, str | None], water: tuple[str | None, str | None]) -> tuple[str | None, str | None]:
    """The worse of airport and water; the airport's own answer when the water has none."""
    (a_level, a_lim), (w_level, w_lim) = air, water
    if a_level is None:
        return None, None
    if w_level is not None and rank(w_level) > rank(a_level):
        return w_level, w_lim
    return a_level, a_lim


def find_windows(hours: list[HourState], min_hours: int) -> list[dict]:
    """Windows per local day: runs of >= `min_hours` daylight, non-past hours, best level first.

    A day's windows are its `favorable` runs; when it has none, its `marginal`-or-better runs (whose
    score is then `marginal`). `end` is exclusive; `limiting_after` is what stopped the run, `None` when
    it stopped at dusk or at the end of the axis.
    """
    by_day: dict[date, list[int]] = {}
    for i, h in enumerate(hours):
        if not h.past and h.daylight and h.level is not None:
            by_day.setdefault(h.start.date(), []).append(i)

    windows: list[dict] = []
    for day in sorted(by_day):
        idx = by_day[day]
        for ok_rank in (rank(FAVORABLE), rank(MARGINAL)):
            runs = [r for r in _runs(idx, lambda i, r=ok_rank: rank(hours[i].level) <= r) if len(r) >= min_hours]
            if runs:
                for run in runs:
                    windows.append(_window(hours, run))
                break
    windows.sort(key=lambda w: w["start"])
    return windows


def _runs(idx: list[int], ok: Callable[[int], bool]) -> list[list[int]]:
    out: list[list[int]] = []
    cur: list[int] = []
    for i in idx:
        if ok(i) and (not cur or i == cur[-1] + 1):
            cur.append(i)
            continue
        if cur:
            out.append(cur)
        cur = [i] if ok(i) else []
    if cur:
        out.append(cur)
    return out


def _window(hours: list[HourState], run: list[int]) -> dict:
    score = max((hours[i].level for i in run), key=rank)
    after = run[-1] + 1
    limiting = None
    if after < len(hours) and hours[after].daylight and hours[after].level is not None:
        limiting = hours[after].limiting
    return {
        "start": hours[run[0]].start.isoformat(),
        "end": hours[after].start.isoformat()
        if after < len(hours)
        else (hours[run[-1]].start + timedelta(hours=1)).isoformat(),
        "score": score,
        "limiting_after": limiting,
    }


def build_timeline(
    settings: Settings,
    feeds,
    *,
    now_utc: datetime,
    tz,
    frozen: bool,
    home_water: lakes_mod.Candidate | None,
) -> dict | None:
    """The contract's `timeline`, or `None` when the airport forecast failed."""
    if feeds.airport is None:
        return None
    fc = settings.forecast
    axis = time_axis(now_utc, tz, fc.past_h, fc.horizon_h)
    now_hour = axis[fc.past_h]
    home = settings.home_airport
    limits = settings.limits
    runways = [r.model_dump() for r in home.runways]

    suns: dict[date, SunTimes] = {}

    def sun(d: date) -> SunTimes:
        if d not in suns:
            suns[d] = local_sun_times(d, home.lat, home.lon, tz)
        return suns[d]

    alerts = feeds.alerts or []

    def alert_events(h: datetime) -> list[str]:
        return [a["event"] for a in alerts if _alert_active(a, h)]

    hours: list[dict] = []
    air_scores: list[tuple[str | None, str | None]] = []
    daylight: list[bool] = []
    for h in axis:
        past = h < now_hour
        s = sun(h.date())
        is_day = bool(s.civil_dawn and s.civil_dusk and s.civil_dawn <= h and h + timedelta(hours=1) <= s.civil_dusk)
        daylight.append(is_day)
        row, score = _airport_hour(
            h, past, is_day, feeds.airport, feeds.taf, home.elev_ft, runways, limits,
            [] if past else alert_events(h),
            s,
        )
        hours.append(row)
        air_scores.append(score)

    water = None
    water_scores: list[tuple[str | None, str | None]] = [(None, None)] * len(axis)
    if home_water is not None:
        water, water_scores = _home_water(
            home_water, feeds, limits, axis, fc.past_h, frozen=frozen, now_utc=now_utc
        )

    states = []
    for i, h in enumerate(axis):
        level, limiting = combine(air_scores[i], water_scores[i])
        states.append(HourState(h, i < fc.past_h, daylight[i], level, limiting))

    return {
        "hours": hours,
        "windows": find_windows(states, settings.outlook.min_window_hours),
        "home_water": water,
    }


def _alert_active(alert: dict, h: datetime) -> bool:
    ends = alert.get("ends")
    if not ends:
        return True
    try:
        return datetime.fromisoformat(str(ends)) > h
    except ValueError:
        return True


def _airport_hour(
    h: datetime,
    past: bool,
    is_day: bool,
    series: HourlySeries,
    taf: dict | None,
    elev_ft: float,
    runways: list[dict],
    limits,
    alert_events: list[str],
    s: SunTimes,
) -> tuple[dict, tuple[str | None, str | None]]:
    model = series.at(h)
    row: dict = {
        "t": h.isoformat(),
        "past": past,
        "daylight": is_day,
        "score": None,
        "limiting": None,
        "wind": None,
        "model": None,
        "ceiling_ft": None,
        "ceiling_known": False,
        "vis_sm": None,
        "fog_risk": False,
        "precip_prob": None,
        "temp_f": None,
    }
    if not model or model.get("wind_speed_10m") is None:
        return row, (None, None)
    conditions = conditions_from_model(
        model,
        field_elev_ft=elev_ft,
        taf_hour=taf_mod.taf_for_hour(taf, h),
        alerts=alert_events,
        daylight=daylight_fraction(h, h + timedelta(hours=1), s.civil_dawn, s.civil_dusk),
        runways=runways,
    )
    score = score_conditions(conditions, limits)
    c = conditions
    row.update(
        {
            "score": score.level,
            "limiting": score.limiting,
            "wind": {
                "dir": round(c.wind_dir_deg),
                "kt": round(c.wind_kt),
                "gust": None if c.gust_kt is None else round(c.gust_kt),
            },
            "model": model.get("wind_model"),
            "ceiling_ft": round100(c.ceiling_ft),
            "ceiling_known": c.ceiling_known,
            "vis_sm": display_vis(c.vis_sm),
            "fog_risk": score.fog_risk,
            "precip_prob": None if c.precip_prob is None else round(c.precip_prob),
            "temp_f": None if c.temp_f is None else round(c.temp_f),
        }
    )
    return row, (score.level, score.limiting)


def _home_water(
    cand: lakes_mod.Candidate,
    feeds,
    limits,
    axis: list[datetime],
    past_h: int,
    *,
    frozen: bool,
    now_utc: datetime,
) -> tuple[dict, list[tuple[str | None, str | None]]]:
    """The `home_water` object and each hour's water-only `(score, limiting)`."""
    label_ids = sorted({p.label for p in cand.points}) if cand.has_wave_field else []
    labels = [cand.labels[i] if 0 <= i < len(cand.labels) else str(i) for i in label_ids]
    per_region = cand.has_wave_field and bool(cand.region_cells)

    hs_in: list[list[int | None]] = []
    winds: list[list[dict | None]] = []
    best: list[dict | None] = []
    open_in: list[int | None] = []
    score: list[str | None] = []
    limiting: list[str | None] = []
    marine: list[int | None] = []
    scores: list[tuple[str | None, str | None]] = []

    for h in axis:
        region_winds: dict[int, lakes_mod.Wind] = {}
        if per_region:
            by_cell = {cell: lakes_mod.wind_at(feeds.lake_series.get(cell), h) for cell in cand.cells}
            region_winds = {
                label: w for label, cell in cand.region_cells.items() if (w := by_cell.get(cell)) is not None
            }
            first = next(iter(region_winds.values()), None)
        else:
            first = lakes_mod.wind_at(feeds.lake_series.get(cand.cell), h)
        marine.append(homewater_mod.marine_hs_in(feeds.marine, h, cand))
        if first is None:
            hs_in.append([None] * len(label_ids))
            winds.append([None] * len(label_ids))
            best.append(None)
            open_in.append(None)
            score.append(None)
            limiting.append(None)
            scores.append((None, None))
            continue
        hour = lakes_mod.score_lake_hour(
            cand, first.dir_deg, first.kt, first.gust_kt, limits, region_winds=region_winds if per_region else None
        )
        by_label = {r.label_index: r for r in hour.regions}
        hs_in.append([None if (r := by_label.get(i)) is None else r.hs_in for i in label_ids])
        winds.append([None if (w := region_winds.get(i)) is None else w.to_row() for i in label_ids])
        top = wave.best_region(hour.regions)
        best.append(None if top is None else {"label": top.label, "hs_in": top.hs_in})
        open_in.append(hour.hs_open_in)
        level, lim = (UNFAVORABLE, "ice") if frozen else (hour.level, hour.limiting)
        score.append(level)
        limiting.append(lim)
        scores.append((level, lim))

    return (
        {
            "id": cand.id,
            "name": cand.name,
            "labels": labels,
            "hs_in": hs_in,
            "wind": winds,
            "best": best,
            "open_in": open_in,
            "score": score,
            "limiting": limiting,
            "observed": _observed(feeds.buoy_history, axis, past_h),
            "marine_in": marine,
        },
        scores,
    )


def _observed(history: dict | None, axis: list[datetime], past_h: int) -> list[dict]:
    """The buoy's hourly rows for the past hours of the axis, one per hour that has a reading."""
    if not history or not history.get("rows"):
        return []
    rows = history["rows"]
    station = str(history.get("station") or "")
    out: list[dict] = []
    for h in axis[:past_h]:
        near = min(rows, key=lambda r: abs(r["at"] - h), default=None)
        if near is None or abs(near["at"] - h) > OBS_MATCH:
            continue
        wave_m = near.get("wave_height_m")
        kt, gust, direction = near.get("speed_kt"), near.get("gust_kt"), near.get("dir_deg")
        if wave_m is None and kt is None and direction is None:
            continue
        out.append(
            {
                "t": h.isoformat(),
                "station": station,
                "wave_in": None if wave_m is None else round(float(wave_m) / wave.M_PER_IN),
                "wind": {
                    "dir": None if direction is None else round(float(direction)),
                    "kt": None if kt is None else round(float(kt)),
                    "gust": None if gust is None else round(float(gust)),
                },
            }
        )
    return out
