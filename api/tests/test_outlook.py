"""The evening outlook: target date, window, scoring, watch, trend, confidence, run carry-forward."""
from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from seaplane_api.briefing import outlook as ol
from seaplane_api.briefing.generate import build_briefing
from seaplane_api.briefing.scoring import Conditions, Score
from seaplane_api.briefing.sun import local_sun_times
from seaplane_api.settings import Settings

from .conftest import DETROIT, load, make_feeds

KPTK = (42.6655, -83.4187)


def hour(hhmm: str, level: str, limiting: str | None = None, fog: bool = False) -> ol.Hour:
    h, m = (int(x) for x in hhmm.split(":"))
    return ol.Hour(
        start=datetime(2026, 9, 20, h, m, tzinfo=DETROIT),
        conditions=Conditions(),
        score=Score(level=level, limiting=limiting, factors={}, skipped=(), fog_risk=fog),
    )


# --- target date and window ----------------------------------------------------------------

@pytest.mark.parametrize(
    ("now", "expected"),
    [
        (datetime(2026, 9, 19, 6, 0, tzinfo=DETROIT), date(2026, 9, 19)),
        (datetime(2026, 9, 19, 11, 59, tzinfo=DETROIT), date(2026, 9, 19)),
        (datetime(2026, 9, 19, 12, 0, tzinfo=DETROIT), date(2026, 9, 20)),
        (datetime(2026, 9, 19, 22, 0, tzinfo=DETROIT), date(2026, 9, 20)),
    ],
)
def test_target_date_flips_at_morning_end(now, expected):
    assert ol.target_date(now, "12:00") == expected


def test_window_bounds_from_sunrise_civil_twilight_and_a_fixed_time():
    day = date(2026, 9, 20)
    sun = local_sun_times(day, *KPTK, DETROIT)
    start, end = ol.window_bounds(day, sun, "sunrise", "12:00", DETROIT)
    assert start.strftime("%H:%M") == "07:18" and end.strftime("%H:%M") == "12:00"
    start, _ = ol.window_bounds(day, sun, "civil_twilight", "12:00", DETROIT)
    assert start.strftime("%H:%M") == "06:50"
    start, _ = ol.window_bounds(day, sun, "08:30", "12:00", DETROIT)
    assert start.strftime("%H:%M") == "08:30"


def test_window_hours_drop_hours_that_are_already_over():
    """A 09:00 run must not report a best window starting at 07:00."""
    day = date(2026, 9, 20)
    sun = local_sun_times(day, *KPTK, DETROIT)
    start, end = ol.window_bounds(day, sun, "sunrise", "12:00", DETROIT)
    now = datetime(2026, 9, 20, 9, 5, tzinfo=DETROIT)
    assert [h.strftime("%H:%M") for h in ol.window_hours(start, end, now)] == ["09:00", "10:00", "11:00"]
    # The hour in progress is kept until its end has passed.
    on_the_hour = datetime(2026, 9, 20, 9, 0, tzinfo=DETROIT)
    assert ol.window_hours(start, end, on_the_hour)[0].strftime("%H:%M") == "09:00"
    # Nothing is trimmed for a window that has not started.
    assert len(ol.window_hours(start, end, None)) == 5
    # Past the end of the window, nothing is left.
    assert ol.window_hours(start, end, datetime(2026, 9, 20, 13, 0, tzinfo=DETROIT)) == []


def test_window_hours_include_the_hour_the_window_opens_in():
    day = date(2026, 9, 20)
    sun = local_sun_times(day, *KPTK, DETROIT)
    start, end = ol.window_bounds(day, sun, "sunrise", "12:00", DETROIT)
    hours = ol.window_hours(start, end)
    assert [h.strftime("%H:%M") for h in hours] == ["07:00", "08:00", "09:00", "10:00", "11:00"]


# --- scoring the window --------------------------------------------------------------------

def test_score_window_takes_the_best_level_with_a_long_enough_run():
    hours = [
        hour("07:00", "marginal", "fog"),
        hour("08:00", "favorable"),
        hour("09:00", "favorable"),
        hour("10:00", "favorable"),
        hour("11:00", "marginal", "gusts"),
    ]
    score, window, lo, hi = ol.score_window(hours, 2)
    assert score == "favorable"
    assert window == ["08:00", "11:00"]
    assert (lo, hi) == (1, 4)
    assert ol.watch_after(hours, hi) == "gusts after 11:00"


def test_score_window_falls_back_to_marginal_when_no_favorable_run_is_long_enough():
    hours = [
        hour("07:00", "marginal", "fog"),
        hour("08:00", "favorable"),
        hour("09:00", "marginal", "gusts"),
        hour("10:00", "marginal", "gusts"),
        hour("11:00", "unfavorable", "wind"),
    ]
    score, window, lo, hi = ol.score_window(hours, 2)
    assert score == "marginal"
    assert window == ["07:00", "11:00"]
    assert ol.limiting_in_window(hours, lo, hi) == "fog"  # the worst hour inside the window
    assert ol.watch_after(hours, hi) == "wind after 11:00"


def test_score_window_with_nothing_long_enough_anywhere():
    hours = [hour("07:00", "favorable"), hour("08:00", "unfavorable", "wind")]
    score, window, lo, hi = ol.score_window(hours, 3)
    assert score == "unfavorable" and window is None and lo is None and hi is None
    assert ol.limiting_in_window(hours, lo, hi) == "wind"


def test_watch_is_none_when_the_window_runs_to_the_end():
    hours = [hour("09:00", "favorable"), hour("10:00", "favorable")]
    _, _, _, hi = ol.score_window(hours, 2)
    assert ol.watch_after(hours, hi) is None


def test_earliest_longest_run_wins_a_tie():
    hours = [
        hour("06:00", "favorable"), hour("07:00", "favorable"), hour("08:00", "marginal", "fog"),
        hour("09:00", "favorable"), hour("10:00", "favorable"),
    ]
    _, window, _, _ = ol.score_window(hours, 2)
    assert window == ["06:00", "08:00"]


# --- runs, trend, confidence ----------------------------------------------------------------

def test_runs_carry_forward_only_while_the_target_date_is_unchanged():
    previous = {"outlook": {"target_date": "2026-09-20", "runs": [{"at": "18:00", "score": "favorable"}]}}
    assert len(ol.carry_runs(previous, date(2026, 9, 20))) == 1
    assert ol.carry_runs(previous, date(2026, 9, 21)) == []
    assert ol.carry_runs(None, date(2026, 9, 20)) == []


def test_append_run_replaces_an_entry_with_the_same_at():
    runs = [{"at": "18:00", "generated_at": "2026-09-19T22:00:00Z", "score": "favorable"}]
    runs = ol.append_run(runs, {"at": "20:00", "generated_at": "2026-09-20T00:00:00Z", "score": "marginal"})
    assert [r["at"] for r in runs] == ["18:00", "20:00"]
    runs = ol.append_run(runs, {"at": "20:00", "generated_at": "2026-09-20T00:05:00Z", "score": "favorable"})
    assert [r["at"] for r in runs] == ["18:00", "20:00"]
    assert runs[-1]["score"] == "favorable"


@pytest.mark.parametrize(
    ("runs", "expected"),
    [
        ([{"at": "18:00", "score": "favorable", "max_gust_kt": 10}], None),
        ([{"score": "marginal", "max_gust_kt": 10}, {"score": "favorable", "max_gust_kt": 10}], "improving"),
        ([{"score": "favorable", "max_gust_kt": 10}, {"score": "marginal", "max_gust_kt": 10}], "worsening"),
        ([{"score": "favorable", "max_gust_kt": 10}, {"score": "favorable", "max_gust_kt": 11}], "steady"),
        ([{"score": "favorable", "max_gust_kt": 10}, {"score": "favorable", "max_gust_kt": 14}], "worsening"),
        ([{"score": "favorable", "max_gust_kt": 14}, {"score": "favorable", "max_gust_kt": 10}], "improving"),
        ([{"score": "favorable"}, {"score": "favorable"}], "steady"),
    ],
)
def test_trend(runs, expected):
    assert ol.trend(runs) == expected


def test_confidence_high_needs_everything_to_line_up():
    level, reasons = ol.confidence(
        taf_covers=True, taf_agrees=True, wind_diff_kt=2.0, score_move=0, extra_reasons=[]
    )
    assert level == "high"
    assert reasons == ["TAF covers the window and agrees with the model"]


def test_confidence_low_on_a_big_wind_disagreement_or_a_two_level_move():
    level, reasons = ol.confidence(
        taf_covers=True, taf_agrees=True, wind_diff_kt=11.0, score_move=0, extra_reasons=[]
    )
    assert level == "low" and "differ by 11 kt" in reasons[0]
    level, reasons = ol.confidence(
        taf_covers=True, taf_agrees=True, wind_diff_kt=1.0, score_move=2, extra_reasons=[]
    )
    assert level == "low" and "two levels" in reasons[0]


def test_confidence_medium_explains_itself():
    level, reasons = ol.confidence(
        taf_covers=False, taf_agrees=False, taf_any_coverage=False, wind_diff_kt=6.0,
        score_move=None, extra_reasons=[],
    )
    assert level == "medium"
    assert "no TAF coverage for the window" in reasons
    assert "NWS and model wind differ by 6 kt" in reasons
    assert "first run for this morning" in reasons


def test_partial_taf_coverage_does_not_also_claim_there_is_none():
    """The two reasons would contradict each other; the per-hour count is the informative one."""
    _, reasons = ol.confidence(
        taf_covers=False, taf_agrees=False, taf_any_coverage=True, wind_diff_kt=2.0,
        score_move=0, extra_reasons=["no ceiling data for 1 of 5 hours (no TAF coverage)"],
    )
    assert "no ceiling data for 1 of 5 hours (no TAF coverage)" in reasons
    assert "no TAF coverage for the window" not in reasons
    assert "TAF and model disagree on ceiling or visibility" not in reasons


# --- the whole outlook, end to end ----------------------------------------------------------

def _outlook(om: str, taf: str | None, now: datetime, previous=None, run_kind="outlook", run_at="18:00"):
    feeds = make_feeds(om, taf_name=taf, nws_hourly=load("nws_hourly.json")["properties"]["periods"])
    b = build_briefing(
        Settings(), feeds, [], now_utc=now.astimezone(UTC), run_kind=run_kind, run_at=run_at, previous=previous
    )
    return b["outlook"]


def test_evening_run_builds_tomorrow_morning_and_records_itself():
    now = datetime(2026, 9, 19, 18, 0, tzinfo=DETROIT)
    o = _outlook("om_calm.json", "taf_vfr.json", now)
    assert o["target_date"] == "2026-09-20"
    assert o["window"] == {"start": "07:18", "end": "12:00"}
    assert o["sun"]["sunrise"] == "07:18"
    assert o["score"] == "favorable"
    assert o["best_window"] == ["07:00", "12:00"]
    assert [h["time"] for h in o["hours"]] == ["07:00", "08:00", "09:00", "10:00", "11:00"]
    assert o["runs"] == [
        {
            "at": "18:00",
            "generated_at": "2026-09-19T22:00:00Z",
            "score": "favorable",
            "best_window": ["07:00", "12:00"],
            "limiting": None,
            "max_gust_kt": 6,
        }
    ]
    assert o["trend"] is None  # first run for this morning
    assert o["summary"].startswith("Tomorrow morning (Sun): favorable 07:00–12:00.")


def test_a_later_evening_run_carries_the_history_forward_and_sets_a_trend():
    first = _outlook("om_calm.json", "taf_vfr.json", datetime(2026, 9, 19, 18, 0, tzinfo=DETROIT))
    second = _outlook(
        "om_gusty.json", "taf_vfr.json", datetime(2026, 9, 19, 20, 0, tzinfo=DETROIT),
        previous={"outlook": first}, run_at="20:00",
    )
    assert [r["at"] for r in second["runs"]] == ["18:00", "20:00"]
    assert second["score"] == "unfavorable"
    assert second["trend"] == "worsening"
    assert "Worsening since 18:00." in second["summary"]


def test_the_history_is_dropped_when_the_target_morning_changes():
    """Last night's run was for the 19th; tonight's 18:00 run is for the 20th, so it starts over."""
    stale = {"outlook": {"target_date": "2026-09-19", "runs": [{"at": "22:00", "score": "marginal"}]}}
    o = _outlook("om_calm.json", "taf_vfr.json", datetime(2026, 9, 19, 18, 0, tzinfo=DETROIT), previous=stale)
    assert o["target_date"] == "2026-09-20"
    assert [r["at"] for r in o["runs"]] == ["18:00"]
    assert o["trend"] is None


def test_the_morning_run_refines_today_and_records_itself_once():
    now = datetime(2026, 9, 19, 6, 0, tzinfo=DETROIT)
    evening = {
        "outlook": {
            "target_date": "2026-09-19",
            "runs": [{"at": "22:00", "generated_at": "2026-09-19T02:00:00Z", "score": "marginal", "max_gust_kt": 14}],
        }
    }
    o = _outlook("om_calm.json", "taf_vfr.json", now, previous=evening, run_kind="scheduled", run_at="06:00")
    assert o["target_date"] == "2026-09-19"
    assert [r["at"] for r in o["runs"]] == ["22:00", "06:00"]
    assert o["trend"] == "improving"


def test_no_ceiling_coverage_shows_up_in_the_confidence_reasons():
    """With no TAF there is no ceiling anywhere, and the outlook says so instead of guessing."""
    now = datetime(2026, 9, 19, 18, 0, tzinfo=DETROIT)
    o = _outlook("om_calm.json", None, now)
    assert o["confidence"] in ("low", "medium")
    assert "no TAF coverage for the window" in o["confidence_reasons"]
    assert not any("no ceiling data" in r for r in o["confidence_reasons"])
    assert all(h["ceiling_ft"] is None and h["ceiling_known"] is False for h in o["hours"])


def test_fog_morning_is_called_out():
    now = datetime(2026, 9, 19, 18, 0, tzinfo=DETROIT)
    o = _outlook("om_ifr.json", "taf_ifr.json", now)
    assert o["score"] == "unfavorable"
    assert o["limiting"] in ("ceiling", "visibility", "fog")
    assert "Confidence" in o["summary"]


def test_outlook_is_null_when_the_forecast_input_failed_entirely():
    from seaplane_api.briefing.generate import Feeds

    feeds = Feeds(errors=["open_meteo[0]: ConnectError"])
    b = build_briefing(
        Settings(), feeds, [], now_utc=datetime(2026, 9, 19, 22, 0, tzinfo=UTC), run_kind="scheduled"
    )
    assert b["outlook"] is None
    assert b["days"] == []
    assert b["lakes"] == []
    assert b["errors"] == ["open_meteo[0]: ConnectError"]
    assert b["sources"]["open_meteo"] is None


def test_hour_row_shape_matches_the_data_contract():
    o = _outlook("om_calm.json", "taf_vfr.json", datetime(2026, 9, 19, 18, 0, tzinfo=DETROIT))
    assert set(o["hours"][0]) == {
        "time", "score", "limiting", "wind", "xwind_kt", "runway", "ceiling_ft", "ceiling_known",
        "vis_sm", "temp_f", "dewpoint_f", "fog_risk", "precip_prob", "da_ft",
    }


def test_max_gust_is_taken_over_the_best_window_only():
    o = _outlook("om_gusty.json", "taf_vfr.json", datetime(2026, 9, 19, 18, 0, tzinfo=DETROIT))
    assert o["runs"][-1]["max_gust_kt"] == 26


def test_window_shrinks_to_an_hour_when_the_start_is_after_the_end():
    day = date(2026, 12, 21)
    sun = local_sun_times(day, *KPTK, DETROIT)
    start, end = ol.window_bounds(day, sun, "sunrise", "07:00", DETROIT)
    assert end - start == timedelta(hours=1)


def test_a_mid_morning_run_reports_only_the_morning_that_is_left():
    """The real 09:38 run used to say "marginal 07:00-11:00"; two of those hours were gone."""
    now = datetime(2026, 9, 19, 9, 30, tzinfo=DETROIT)
    o = _outlook("om_calm.json", "taf_vfr.json", now, run_kind="scheduled", run_at="09:00")
    assert o["target_date"] == "2026-09-19"
    assert [h["time"] for h in o["hours"]] == ["09:00", "10:00", "11:00"]
    assert o["best_window"] == ["09:00", "12:00"]
    assert "07:00" not in o["summary"]


def test_the_outlook_rolls_to_tomorrow_when_no_morning_hours_are_left():
    """Defensive: the window is empty (here because the model series stops), so target moves on."""
    from seaplane_api.briefing.generate import Feeds
    from seaplane_api.briefing.series import HourlySeries

    payload = load("om_calm.json")
    hourly = payload["hourly"]
    keep = [i for i, t in enumerate(hourly["time"]) if not t.startswith("2026-09-19T1")]
    trimmed = {k: ([v[i] for i in keep] if isinstance(v, list) else v) for k, v in hourly.items()}
    payload = {**payload, "hourly": trimmed}

    feeds = Feeds(airport=HourlySeries.from_open_meteo(payload, DETROIT), alerts=[])
    now = datetime(2026, 9, 19, 10, 30, tzinfo=DETROIT)
    b = build_briefing(Settings(), feeds, [], now_utc=now.astimezone(UTC), run_kind="scheduled")
    assert b["outlook"]["target_date"] == "2026-09-20"
    assert b["outlook"]["hours"][0]["time"] == "07:00"


def test_hours_carry_ceiling_known_so_null_is_not_mistaken_for_clear():
    covered = _outlook("om_calm.json", "taf_vfr.json", datetime(2026, 9, 19, 18, 0, tzinfo=DETROIT))
    assert all(h["ceiling_known"] is True for h in covered["hours"])
    assert covered["hours"][0]["ceiling_ft"] is None  # FEW120 is not a ceiling

    uncovered = _outlook("om_calm.json", None, datetime(2026, 9, 19, 18, 0, tzinfo=DETROIT))
    assert all(h["ceiling_known"] is False for h in uncovered["hours"])
    assert "ceiling unknown" in uncovered["summary"]


def test_displayed_visibility_is_capped_at_ten():
    """Open-Meteo returns 14 SM; a pilot reads 10. Scoring uses the uncapped value."""
    o = _outlook("om_calm.json", None, datetime(2026, 9, 19, 18, 0, tzinfo=DETROIT))
    assert all(h["vis_sm"] == 10 for h in o["hours"])
    assert "vis 10" in o["summary"]


def test_confidence_is_measured_over_the_hours_actually_scored():
    """At 09:30 the question is whether the TAF covers 09:00-12:00, not the 07:17 sunrise."""
    taf = load("taf_vfr.json")
    # A TAF that starts at 08:00 local (12:00Z) misses the nominal window but covers what is left.
    late = {**taf, "validTimeFrom": int(datetime(2026, 9, 19, 12, 0, tzinfo=UTC).timestamp())}
    feeds = make_feeds("om_calm.json", nws_hourly=load("nws_hourly.json")["properties"]["periods"])
    feeds.taf = late
    now = datetime(2026, 9, 19, 9, 30, tzinfo=DETROIT)
    b = build_briefing(Settings(), feeds, [], now_utc=now.astimezone(UTC), run_kind="scheduled", run_at="09:00")
    o = b["outlook"]
    assert [h["time"] for h in o["hours"]] == ["09:00", "10:00", "11:00"]
    assert all(h["ceiling_known"] for h in o["hours"])
    assert "no TAF coverage for the window" not in o["confidence_reasons"]
