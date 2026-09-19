"""`due_times` as a pure function, including DST days and the MacBook-woke-up case."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from seaplane_api.scheduler import due_times, next_run_local, startup_due

TZ = ZoneInfo("America/Detroit")
TIMES = ["06:00", "09:00", "12:00", "15:00", "18:00", "20:00", "22:00"]


def L(y, m, d, hh, mm=0, ss=0) -> datetime:
    return datetime(y, m, d, hh, mm, ss, tzinfo=TZ)


def hhmm(dts) -> list[str]:
    return [d.strftime("%H:%M") for d in dts]


def test_nothing_is_due_between_scheduled_times():
    assert due_times(L(2026, 9, 19, 10, 30), L(2026, 9, 19, 9, 0, 30), TIMES, TZ) == []


def test_the_scheduled_time_that_just_passed_is_due():
    due = due_times(L(2026, 9, 19, 9, 0, 20), L(2026, 9, 19, 6, 0, 10), TIMES, TZ)
    assert hhmm(due) == ["09:00"]


def test_a_time_missed_by_less_than_ninety_minutes_still_runs():
    due = due_times(L(2026, 9, 19, 10, 20), L(2026, 9, 19, 6, 1), TIMES, TZ)
    assert hhmm(due) == ["09:00"]


def test_a_time_missed_by_more_than_ninety_minutes_is_skipped():
    assert due_times(L(2026, 9, 19, 10, 40), L(2026, 9, 19, 6, 1), TIMES, TZ) == []


def test_the_macbook_woke_up_after_a_day_asleep():
    """Only the most recent scheduled time runs; the eight it slept through are skipped."""
    due = due_times(L(2026, 9, 19, 9, 5), L(2026, 9, 18, 12, 0, 30), TIMES, TZ)
    assert hhmm(due) == ["09:00"]


def test_a_wake_up_in_the_middle_of_the_night_runs_nothing():
    assert due_times(L(2026, 9, 19, 3, 0), L(2026, 9, 18, 12, 0), TIMES, TZ) == []


def test_two_times_inside_the_window_come_back_oldest_first():
    """20:00 and 22:00 are 95 and 35 minutes apart; a 22:35 wake sees only 22:00."""
    assert hhmm(due_times(L(2026, 9, 19, 22, 35), L(2026, 9, 19, 18, 1), TIMES, TZ)) == ["22:00"]
    # Widening the staleness cut shows the ordering.
    due = due_times(L(2026, 9, 19, 22, 35), L(2026, 9, 19, 17, 50), TIMES, TZ, max_miss_min=300)
    assert hhmm(due) == ["18:00", "20:00", "22:00"]


def test_the_first_ever_run_has_no_last_run():
    due = due_times(L(2026, 9, 19, 9, 10), None, TIMES, TZ)
    assert hhmm(due) == ["09:00"]


def test_a_time_already_run_is_not_run_twice():
    assert due_times(L(2026, 9, 19, 9, 5), L(2026, 9, 19, 9, 0), TIMES, TZ) == []
    assert due_times(L(2026, 9, 19, 9, 5), L(2026, 9, 19, 9, 0, 1), TIMES, TZ) == []


def test_it_crosses_local_midnight():
    """00:30 on the 20th, last run 22:00 on the 19th: nothing new, and no crash on the date roll."""
    assert due_times(L(2026, 9, 20, 0, 30), L(2026, 9, 19, 22, 0, 30), TIMES, TZ) == []
    due = due_times(L(2026, 9, 20, 6, 5), L(2026, 9, 19, 22, 0, 30), TIMES, TZ)
    assert hhmm(due) == ["06:00"] and due[0].date().day == 20


# --- DST ------------------------------------------------------------------------------------

def test_spring_forward_day_fires_06_00_once_on_the_new_offset():
    """2026-03-08: clocks jump 02:00 -> 03:00, so the night is 23 hours long and 06:00 must still
    fire exactly once."""
    last = L(2026, 3, 7, 22, 0, 30)
    due = due_times(L(2026, 3, 8, 6, 5), last, TIMES, TZ)
    assert hhmm(due) == ["06:00"]
    assert due[0].utcoffset() == timedelta(hours=-4)  # EDT
    # Eight hours on the wall, seven in real time: the arithmetic has to happen on the UTC timeline.
    assert (due[0].astimezone(UTC) - last.astimezone(UTC)).total_seconds() == 7 * 3600 - 30


def test_a_scheduled_time_inside_the_spring_forward_gap_does_not_crash_or_repeat():
    times = ["02:30"]
    first = due_times(L(2026, 3, 8, 4, 0), L(2026, 3, 8, 1, 0), times, TZ, max_miss_min=600)
    assert len(first) == 1
    assert due_times(L(2026, 3, 8, 4, 0), first[0], times, TZ, max_miss_min=600) == []


def test_fall_back_day_fires_06_00_once_on_the_old_offset():
    """2026-11-01: clocks fall back 02:00 -> 01:00, so the night is 25 hours long."""
    last = L(2026, 10, 31, 22, 0, 30)
    due = due_times(L(2026, 11, 1, 6, 5), last, TIMES, TZ)
    assert hhmm(due) == ["06:00"]
    assert due[0].utcoffset() == timedelta(hours=-5)  # EST
    # Eight hours on the wall, nine in real time: the extra 01:00-02:00 happens twice.
    assert (due[0].astimezone(UTC) - last.astimezone(UTC)).total_seconds() == 9 * 3600 - 30


def test_an_ambiguous_fall_back_time_runs_only_once():
    times = ["01:30"]
    first = due_times(L(2026, 11, 1, 3, 0), L(2026, 11, 1, 0, 30), times, TZ, max_miss_min=600)
    assert len(first) == 1
    assert due_times(L(2026, 11, 1, 3, 0), first[0], times, TZ, max_miss_min=600) == []


def test_utc_input_is_converted_before_comparison():
    due = due_times(datetime(2026, 9, 19, 13, 5, tzinfo=UTC), None, TIMES, TZ)  # 09:05 EDT
    assert hhmm(due) == ["09:00"]


def test_no_times_configured():
    assert due_times(L(2026, 9, 19, 9, 5), None, [], TZ) == []


# --- helpers ----------------------------------------------------------------------------------

def test_next_run_local_wraps_to_tomorrow():
    assert next_run_local(L(2026, 9, 19, 9, 1), TIMES, TZ) == "12:00"
    assert next_run_local(L(2026, 9, 19, 23, 0), TIMES, TZ) == "06:00"
    assert next_run_local(L(2026, 9, 19, 9, 1), [], TZ) is None


def test_startup_runs_when_the_briefing_is_missing_or_stale():
    now = datetime(2026, 9, 19, 12, 0, tzinfo=UTC)
    assert startup_due(now, None) is True
    assert startup_due(now, {}) is True
    assert startup_due(now, {"generated_at": "not a date"}) is True
    assert startup_due(now, {"generated_at": "2026-09-19T11:00:00Z"}) is False
    assert startup_due(now, {"generated_at": "2026-09-19T08:00:00Z"}) is True
