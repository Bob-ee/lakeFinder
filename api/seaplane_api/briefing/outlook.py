"""The evening "tomorrow morning" outlook (design 3.4).

Hourly rather than three-hourly, because the thing that kills a morning -- radiation fog over the
lakes at sunrise -- burns off inside one block. Target date is tomorrow once local time has reached
`morning_end_local`, otherwise today, so the 06:00 run refines *this* morning against last night's
22:00 run.

Window hours are every whole hour that *overlaps* the window, so a 07:18 sunrise puts 07:00 in the
list. `outlook.score` is the best level L for which at least `min_window_hours` consecutive hours all
score L or better, and `best_window` is the earliest longest such run.

`runs` is carried forward from the previous `briefing.json` while `target_date` is unchanged; an
entry with the same `at` is replaced rather than duplicated, so re-running 20:00 by hand does not
invent a second data point.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta

from .scoring import FAVORABLE, LEVELS, Conditions, Score, rank
from .series import display_vis, round100
from .sun import SunTimes


@dataclass
class Hour:
    start: datetime
    conditions: Conditions
    score: Score

    def to_row(self) -> dict:
        c, s = self.conditions, self.score
        return {
            "time": self.start.strftime("%H:%M"),
            "score": s.level,
            "limiting": s.limiting,
            "wind": {
                "dir": round(c.wind_dir_deg),
                "kt": round(c.wind_kt),
                "gust": None if c.gust_kt is None else round(c.gust_kt),
            },
            "xwind_kt": None if c.xwind_kt is None else round(c.xwind_kt),
            "runway": c.runway,
            "ceiling_ft": round100(c.ceiling_ft),
            "ceiling_known": c.ceiling_known,
            "vis_sm": display_vis(c.vis_sm),
            "temp_f": None if c.temp_f is None else round(c.temp_f),
            "dewpoint_f": None if c.dewpoint_f is None else round(c.dewpoint_f),
            "fog_risk": s.fog_risk,
            "precip_prob": None if c.precip_prob is None else round(c.precip_prob),
            "da_ft": round100(c.da_ft),
        }


def target_date(now_local: datetime, morning_end_local: str) -> date:
    """Tomorrow once local time is at or past `morning_end_local`, otherwise today."""
    h, m = (int(x) for x in morning_end_local.split(":"))
    end_today = now_local.replace(hour=h, minute=m, second=0, microsecond=0)
    return now_local.date() + timedelta(days=1) if now_local >= end_today else now_local.date()


def window_bounds(day: date, sun: SunTimes, morning_start: str, morning_end_local: str, tz) -> tuple[datetime, datetime]:
    """`(start, end)` local aware datetimes for the morning window on `day`."""
    h, m = (int(x) for x in morning_end_local.split(":"))
    end = datetime(day.year, day.month, day.day, h, m, tzinfo=tz)
    if morning_start == "sunrise" and sun.sunrise is not None:
        start = sun.sunrise
    elif morning_start == "civil_twilight" and sun.civil_dawn is not None:
        start = sun.civil_dawn
    elif ":" in morning_start:
        sh, sm = (int(x) for x in morning_start.split(":"))
        start = datetime(day.year, day.month, day.day, sh, sm, tzinfo=tz)
    else:  # sun never rose and no explicit time: fall back to the whole morning
        start = datetime(day.year, day.month, day.day, 0, 0, tzinfo=tz)
    if start >= end:
        start = end - timedelta(hours=1)
    return start.astimezone(tz), end


def window_hours(start: datetime, end: datetime, now: datetime | None = None) -> list[datetime]:
    """Every whole hour overlapping `[start, end)`, dropping any that is already over.

    When `target_date` is today the window has usually started, and an hour whose end has passed is
    not a forecast -- it is history. A 09:00 run that reported "favorable 07:00-12:00" would be
    offering the pilot two hours he cannot fly. `now` is `None` for a window in the future, where
    nothing is trimmed.
    """
    first = start.replace(minute=0, second=0, microsecond=0)
    hours: list[datetime] = []
    t = first
    while t < end:
        if now is None or t + timedelta(hours=1) > now:
            hours.append(t)
        t += timedelta(hours=1)
    return hours


def score_window(hours: list[Hour], min_window_hours: int) -> tuple[str, list[str] | None, int | None, int | None]:
    """`(score, best_window, first_index, last_index_exclusive)` per design 3.4.

    The score is the best level for which a run of at least `min_window_hours` consecutive hours all
    score that level or better; `best_window` is the earliest longest such run. With no qualifying
    run at any level the score is the worst level present and `best_window` is `None`.
    """
    for level in LEVELS:
        span = _longest_run(hours, rank(level), min_window_hours)
        if span is not None:
            lo, hi = span
            return (
                level,
                [hours[lo].start.strftime("%H:%M"), (hours[hi - 1].start + timedelta(hours=1)).strftime("%H:%M")],
                lo,
                hi,
            )
    worst = max((rank(h.score.level) for h in hours), default=0)
    return LEVELS[worst], None, None, None


def _longest_run(hours: list[Hour], max_rank: int, min_len: int) -> tuple[int, int] | None:
    best: tuple[int, int] | None = None
    start: int | None = None
    for i in range(len(hours) + 1):
        ok = i < len(hours) and rank(hours[i].score.level) <= max_rank
        if ok and start is None:
            start = i
        elif not ok and start is not None:
            if i - start >= min_len and (best is None or (i - start) > (best[1] - best[0])):
                best = (start, i)
            start = None
    return best


def limiting_in_window(hours: list[Hour], lo: int | None, hi: int | None) -> str | None:
    """The limiting factor of the worst hour inside the best window."""
    scope = hours[lo:hi] if lo is not None and hi is not None else hours
    if not scope:
        return None
    worst = max(scope, key=lambda h: rank(h.score.level))
    return worst.score.limiting


def watch_after(hours: list[Hour], hi: int | None) -> str | None:
    """What ends a favorable window: the limiting factor of the first non-favorable hour after it."""
    if hi is None or hi >= len(hours):
        return None
    nxt = hours[hi]
    if nxt.score.level == FAVORABLE or not nxt.score.limiting:
        return None
    return f"{factor_phrase(nxt.score.limiting)} after {nxt.start.strftime('%H:%M')}"


_FACTOR_PHRASES = {
    "wind": "wind",
    "gusts": "gusts",
    "xwind_runway": "crosswind",
    "ceiling": "ceiling",
    "visibility": "visibility",
    "precip": "rain",
    "convection": "storms",
    "density_altitude": "density altitude",
    "fog": "fog",
    "temperature": "cold",
    "alert": "an advisory",
    "daylight": "daylight",
    "waves": "waves",
    "run": "run length",
    "xwind_water": "water crosswind",
    "ice": "ice",
}


def factor_phrase(factor: str) -> str:
    return _FACTOR_PHRASES.get(factor, factor)


def carry_runs(previous: dict | None, target: date) -> list[dict]:
    """Previous `outlook.runs`, kept only while `target_date` is unchanged."""
    if not previous:
        return []
    prev_outlook = previous.get("outlook") or {}
    if prev_outlook.get("target_date") != target.isoformat():
        return []
    return [dict(r) for r in (prev_outlook.get("runs") or [])]


def append_run(runs: list[dict], entry: dict) -> list[dict]:
    """Append, replacing any existing entry with the same `at`."""
    out = [r for r in runs if r.get("at") != entry["at"]]
    out.append(entry)
    out.sort(key=lambda r: r.get("generated_at") or "")
    return out


def trend(runs: list[dict]) -> str | None:
    """Design 3.4: score rank first, then `max_gust_kt` moving by 3 kt or more. `None` on the first."""
    if len(runs) < 2:
        return None
    now, prev = runs[-1], runs[-2]
    a, b = rank(now.get("score", FAVORABLE)), rank(prev.get("score", FAVORABLE))
    if a < b:
        return "improving"
    if a > b:
        return "worsening"
    g_now, g_prev = now.get("max_gust_kt"), prev.get("max_gust_kt")
    if g_now is not None and g_prev is not None:
        if g_now - g_prev >= 3:
            return "worsening"
        if g_prev - g_now >= 3:
            return "improving"
    return "steady"


def confidence(
    *,
    taf_covers: bool,
    taf_agrees: bool,
    taf_any_coverage: bool = True,
    wind_diff_kt: float | None,
    score_move: int | None,
    extra_reasons: list[str],
) -> tuple[str, list[str]]:
    """Deterministic confidence per design 3.4, with plain-language reasons.

    `wind_diff_kt` is |NWS peak morning wind - Open-Meteo peak morning wind|; `score_move` is how
    many levels the outlook score shifted since the previous run (`None` on the first run).

    `taf_covers` is "the TAF spans the whole window", `taf_any_coverage` is "it covers at least one
    hour". Only a window with *no* coverage at all says "no TAF coverage for the window"; a partly
    covered one is already described by the "no ceiling data for N of M hours" reason the caller
    passes in, and saying both would contradict itself.
    """
    reasons: list[str] = list(extra_reasons)
    level = "medium"

    if wind_diff_kt is not None and wind_diff_kt > 8:
        level = "low"
        reasons.append(f"NWS and model wind differ by {round(wind_diff_kt)} kt")
    elif score_move is not None and score_move >= 2:
        level = "low"
        reasons.append("the outlook moved two levels since the last run")
    elif taf_covers and taf_agrees and (wind_diff_kt is not None and wind_diff_kt <= 4) and score_move == 0:
        level = "high"
        reasons.append("TAF covers the window and agrees with the model")
    else:
        if not taf_any_coverage:
            reasons.append("no TAF coverage for the window")
        elif taf_covers and not taf_agrees:
            reasons.append("TAF and model disagree on ceiling or visibility")
        if wind_diff_kt is None:
            reasons.append("no NWS wind to compare")
        elif wind_diff_kt > 4:
            reasons.append(f"NWS and model wind differ by {round(wind_diff_kt)} kt")
        if score_move is None:
            reasons.append("first run for this morning")
        elif score_move == 1:
            reasons.append("the outlook moved a level since the last run")
    return level, reasons
