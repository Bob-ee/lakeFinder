"""Three-hour blocks for today and tomorrow (design 3.1) and the `days[]` array.

Blocks start at the current hour and step three hours to the end of tomorrow's civil dusk. Only
blocks that overlap civil twilight are kept, and the forecast hour nearest the block centre
represents the block. The METAR overrides the first block at the airport when it is under 90 minutes
old.

Block boundaries deliberately float with "now" rather than snapping to 00:00/03:00/...: the contract's
own example starts a block at 07:00, and a briefing generated at 07:20 should describe the three
hours in front of the pilot, not the two that are left of an 06:00-09:00 box.

A day's `score` is its best block; `best_window` is the earliest longest run of consecutive blocks at
that level or better, reported as `[start, end]` local HH:MM.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta

from .scoring import Conditions, Score, rank
from .series import daylight_fraction, display_vis, round100
from .sun import SunTimes

BLOCK_HOURS = 3


@dataclass
class Block:
    start: datetime  # local, aware
    end: datetime
    center: datetime
    conditions: Conditions
    score: Score

    def to_row(self) -> dict:
        c, s = self.conditions, self.score
        return {
            "start": self.start.strftime("%H:%M"),
            "end": self.end.strftime("%H:%M"),
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
            "da_ft": round100(c.da_ft),
            "temp_f": None if c.temp_f is None else round(c.temp_f),
            "precip_prob": None if c.precip_prob is None else round(c.precip_prob),
        }


def block_spans(now_local: datetime, suns: dict[date, SunTimes], horizon_end: datetime) -> list[tuple[datetime, datetime]]:
    """Three-hour spans from the current hour to `horizon_end`, keeping only daylight ones."""
    spans: list[tuple[datetime, datetime]] = []
    start = now_local.replace(minute=0, second=0, microsecond=0)
    while start < horizon_end:
        end = start + timedelta(hours=BLOCK_HOURS)
        sun = suns.get(start.date())
        dawn = sun.civil_dawn if sun else None
        dusk = sun.civil_dusk if sun else None
        if daylight_fraction(start, end, dawn, dusk) > 0:
            spans.append((start, end))
        start = end
    return spans


def group_days(blocks: list[Block], suns: dict[date, SunTimes] | None = None) -> list[dict]:
    """The contract's `days[]`: one entry per local date, in order.

    `best_window` is clamped to civil dusk. The last block of the day often runs past it -- a
    18:00-21:00 block on a September evening is two hours of daylight and one of dark -- and the
    summary line quotes this window, so it must not promise flying time that is not there.
    """
    by_date: dict[date, list[Block]] = {}
    for b in blocks:
        by_date.setdefault(b.start.date(), []).append(b)
    days: list[dict] = []
    for d in sorted(by_date):
        day_blocks = by_date[d]
        best = min(rank(b.score.level) for b in day_blocks)
        sun = (suns or {}).get(d)
        window = best_window(day_blocks, best, sun.civil_dusk if sun else None)
        days.append(
            {
                "date": d.isoformat(),
                "score": _level_at(day_blocks, best),
                "best_window": window,
                "blocks": [b.to_row() for b in day_blocks],
            }
        )
    return days


def best_window(day_blocks: list[Block], best_rank: int, civil_dusk: datetime | None = None) -> list[str] | None:
    """Earliest longest run of consecutive blocks scoring `best_rank` or better, ending by dusk."""
    best: tuple[int, int] | None = None
    run_start: int | None = None
    for i, b in enumerate(day_blocks + [None]):  # type: ignore[list-item]
        ok = b is not None and rank(b.score.level) <= best_rank
        if ok and run_start is None:
            run_start = i
        elif not ok and run_start is not None:
            length = i - run_start
            if best is None or length > best[1] - best[0]:
                best = (run_start, i)
            run_start = None
    if best is None:
        return None
    start = day_blocks[best[0]].start
    end = day_blocks[best[1] - 1].end
    if civil_dusk is not None and end > civil_dusk > start:
        end = civil_dusk
    return [start.strftime("%H:%M"), end.strftime("%H:%M")]


def _level_at(day_blocks: list[Block], best_rank: int) -> str:
    return next(b.score.level for b in day_blocks if rank(b.score.level) == best_rank)
