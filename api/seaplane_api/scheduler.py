"""In-process scheduler (design 4).

The loop wakes every 30 s and asks a pure question: *has a scheduled wall-clock time passed since the
last run?* It does not sleep until the next one, because the deploy target is a MacBook that sleeps;
a laptop that wakes at 21:40 must still notice that 20:00 went by, and must not fire 06:00, 09:00,
12:00, 15:00 and 18:00 in a burst when it wakes the next morning. Hence the 90 minute staleness cut:
a scheduled time older than that is skipped, not run late.

`due_times` is that question as a pure function of `(now, last_run, times, tz)`, so DST is a test and
not a hope. Times are local wall-clock strings in `settings.timezone`; `zoneinfo` resolves them,
including the spring-forward hour that does not exist and the fall-back hour that happens twice
(`fold=0`, i.e. the first occurrence -- running once is the point).

It also runs at startup when `briefing.json` is missing or older than 3 hours, and immediately when
settings change.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta, tzinfo

from .paths import briefing_path, read_json
from .service import run_briefing
from .settings import Settings, load_settings

log = logging.getLogger(__name__)

TICK_S = 30
MAX_MISS_MIN = 90
STARTUP_STALE_H = 3


def due_times(
    now: datetime,
    last_run: datetime | None,
    times: list[str],
    tz: tzinfo,
    *,
    max_miss_min: int = MAX_MISS_MIN,
) -> list[datetime]:
    """Scheduled instants that have passed since `last_run` and are still fresh enough to run.

    `now` and `last_run` may be in any timezone; both are converted. The returned instants are
    tz-aware in `tz`, oldest first. An empty list means "nothing to do".
    """
    if not times:
        return []
    # Every comparison happens on the UTC timeline. Python compares two aware datetimes that share a
    # tzinfo as if they were naive, which is exactly wrong on a DST boundary: 22:00 EST to 06:00 EDT
    # is seven real hours, not eight, and 01:30 EST and 01:30 EDT are different instants.
    now_utc = now.astimezone(UTC)
    last_utc = last_run.astimezone(UTC) if last_run else None
    horizon_utc = now_utc - timedelta(minutes=max_miss_min)
    now_local = now.astimezone(tz)

    out: list[datetime] = []
    # Yesterday, today and tomorrow cover every offset shift a scheduled time can sit across.
    for offset in (-1, 0, 1):
        day = (now_local.date() + timedelta(days=offset))
        for t in times:
            hour, minute = (int(x) for x in t.split(":"))
            when = datetime(day.year, day.month, day.day, hour, minute, tzinfo=tz)
            when_utc = when.astimezone(UTC)
            if when_utc > now_utc or when_utc < horizon_utc:
                continue
            if last_utc is not None and when_utc <= last_utc:
                continue
            out.append(when)
    out.sort(key=lambda d: d.astimezone(UTC))
    return out


def next_run_local(now: datetime, times: list[str], tz: tzinfo) -> str | None:
    """The next scheduled `HH:MM` at or after `now`, for `/api/health`."""
    if not times:
        return None
    now_local = now.astimezone(tz)
    today = sorted(t for t in times if t > now_local.strftime("%H:%M"))
    return today[0] if today else min(times)


def startup_due(now: datetime, briefing: dict | None, *, stale_hours: int = STARTUP_STALE_H) -> bool:
    """True when there is no briefing, or the one on disk is older than `stale_hours`."""
    if not briefing or not briefing.get("generated_at"):
        return True
    try:
        generated = datetime.fromisoformat(str(briefing["generated_at"]))
    except ValueError:
        return True
    return (now - generated) > timedelta(hours=stale_hours)


class Scheduler:
    """Owns the asyncio task, the last-run clock, and the settings the app is serving."""

    def __init__(self) -> None:
        self.settings: Settings = load_settings()
        self.last_run: datetime | None = None
        self._task: asyncio.Task | None = None
        self._lock = asyncio.Lock()
        self._stop = asyncio.Event()

    @property
    def tz(self):
        from zoneinfo import ZoneInfo

        return ZoneInfo(self.settings.timezone)

    def next_run_local(self, now: datetime) -> str | None:
        return next_run_local(now, self.settings.all_run_times(), self.tz)

    async def run(self, *, run_kind: str, run_at: str | None = None) -> dict:
        """One run, serialised: a scheduled tick and a manual refresh never overlap."""
        async with self._lock:
            briefing = await run_briefing(self.settings, run_kind=run_kind, run_at=run_at)
            self.last_run = datetime.fromisoformat(briefing["generated_at"])
            return briefing

    async def start(self) -> None:
        now = datetime.now(UTC)
        if startup_due(now, read_json(briefing_path())):
            log.info("startup: briefing missing or stale, running now")
            try:
                await self.run(run_kind="startup")
            except Exception:  # a bad startup run must not stop the server coming up
                log.exception("startup briefing failed")
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass  # the cancellation we just asked for
            except Exception:
                log.exception("scheduler task failed on shutdown")

    async def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=TICK_S)
                return
            except TimeoutError:
                pass
            try:
                now = datetime.now(UTC)
                due = due_times(now, self.last_run, self.settings.all_run_times(), self.tz)
                if due:
                    fired = due[-1]  # catch up to the most recent missed time, once
                    at = fired.strftime("%H:%M")
                    kind = "outlook" if at in self.settings.outlook.times_local else "scheduled"
                    log.info("scheduled run for %s (%s)", at, kind)
                    await self.run(run_kind=kind, run_at=at)
            except Exception:  # keep ticking whatever one run did
                log.exception("scheduled briefing failed")
