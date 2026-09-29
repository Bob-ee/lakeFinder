"""`briefing.json` -> `timeline` (data contract, "Forecast timeline"): the axis, the window rules, and the
home-water arrays. Series are built by hand so the wind at each hour is known; nothing here opens a socket.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from itertools import pairwise

from seaplane_api.briefing import lakes as lakes_mod
from seaplane_api.briefing.generate import Feeds, build_briefing
from seaplane_api.briefing.series import HourlySeries
from seaplane_api.briefing.sun import local_sun_times
from seaplane_api.briefing.timeline import HourState, build_timeline, combine, find_windows, time_axis
from seaplane_api.fetch import ndbc
from seaplane_api.settings import HomeWater, Settings

from .conftest import DETROIT, make_wave_field, wave_point


def naive(*args) -> datetime:
    """A naive datetime: Open-Meteo's own labels are naive local strings."""
    return datetime(*args)  # noqa: DTZ001


def payload(start: datetime, n: int, wind, *, offset: int = -14400, models=None) -> dict:
    """An Open-Meteo-shaped point response. `start` is the naive first label; `wind(i)` -> (dir, kt, gust)."""
    times = [(start + timedelta(hours=i)).strftime("%Y-%m-%dT%H:%M") for i in range(n)]
    w = [wind(i) for i in range(n)]
    hourly = {
        "time": times,
        "wind_direction_10m": [x[0] for x in w],
        "wind_speed_10m": [x[1] for x in w],
        "wind_gusts_10m": [x[2] for x in w],
        "temperature_2m": [70.0] * n,
        "dew_point_2m": [50.0] * n,
        "precipitation": [0.0] * n,
        "precipitation_probability": [0] * n,
        "weather_code": [0] * n,
        "visibility": [16000.0] * n,
        "cape": [0.0] * n,
        "pressure_msl": [1015.0] * n,
    }
    if models is not None:
        hourly["wind_model"] = models
    return {"utc_offset_seconds": offset, "hourly": hourly}


CALM = lambda i: (270, 6, 8)


def series(start: datetime, n: int, wind=CALM, **kw) -> HourlySeries:
    return HourlySeries.from_open_meteo(payload(start, n, wind, **kw), DETROIT)


def feeds_with(airport: HourlySeries | None, **kw) -> Feeds:
    return Feeds(airport=airport, alerts=[], fetched_at=datetime(2026, 9, 29, 14, 0, tzinfo=UTC), **kw)


NOW = datetime(2026, 9, 29, 10, 30, tzinfo=DETROIT).astimezone(UTC)


# --- the axis --------------------------------------------------------------------------------------


def test_the_axis_is_seventy_nine_hours_with_six_past_and_offsets_in_the_strings():
    axis = time_axis(NOW, DETROIT, 6, 72)
    assert len(axis) == 79
    assert axis[6].isoformat() == "2026-09-29T10:00:00-04:00"  # the current hour
    assert axis[0].isoformat() == "2026-09-29T04:00:00-04:00"
    assert axis[-1].isoformat() == "2026-10-02T10:00:00-04:00"


def test_the_timeline_marks_exactly_the_hours_before_the_current_one_as_past():
    tl = build_timeline(
        Settings(), feeds_with(series(naive(2026, 9, 27), 24 * 8)), now_utc=NOW, tz=DETROIT, frozen=False, home_water=None
    )
    hours = tl["hours"]
    assert len(hours) == 79
    assert [h["past"] for h in hours] == [True] * 6 + [False] * 73
    assert hours[6]["t"] == "2026-09-29T10:00:00-04:00"


def test_the_axis_length_follows_settings():
    settings = Settings.model_validate({"forecast": {"horizon_h": 24, "past_h": 2}})
    tl = build_timeline(
        settings, feeds_with(series(naive(2026, 9, 27), 24 * 8)), now_utc=NOW, tz=DETROIT, frozen=False, home_water=None
    )
    assert len(tl["hours"]) == 27 and sum(h["past"] for h in tl["hours"]) == 2


def test_the_axis_across_the_fall_back_change_has_no_repeated_or_missing_instant():
    # 2026-11-01: 02:00 EDT becomes 01:00 EST, so the 01:00 hour happens twice.
    now = datetime(2026, 11, 1, 0, 30, tzinfo=DETROIT).astimezone(UTC)
    axis = time_axis(now, DETROIT, 6, 72)
    assert len(axis) == 79
    instants = [h.astimezone(UTC) for h in axis]
    assert all(b - a == timedelta(hours=1) for a, b in pairwise(instants))
    labels = [h.isoformat() for h in axis]
    assert len(set(labels)) == 79  # the repeated 01:00 is told apart by its offset
    assert "2026-11-01T01:00:00-04:00" in labels and "2026-11-01T01:00:00-05:00" in labels
    assert axis[6].isoformat() == "2026-11-01T00:00:00-04:00"


def test_the_axis_across_the_spring_forward_change_skips_the_missing_hour():
    now = datetime(2027, 3, 14, 0, 30, tzinfo=DETROIT).astimezone(UTC)  # 02:00 EST becomes 03:00 EDT
    axis = time_axis(now, DETROIT, 6, 72)
    labels = [h.isoformat() for h in axis]
    assert len(axis) == 79 and len(set(labels)) == 79
    assert not any("2027-03-14T02:" in x for x in labels)
    assert "2027-03-14T01:00:00-05:00" in labels and "2027-03-14T03:00:00-04:00" in labels


def test_the_timeline_over_a_dst_change_reads_each_hour_at_its_real_instant():
    """Open-Meteo's labels are a fixed 24-hour grid at one offset; after the change they are an hour off
    the wall clock, so the series is read at its own `utc_offset_seconds`, not as wall-clock."""
    # Labels 00:00..; the wind's kt is the label's hour so a misread shows up as the wrong number.
    start = naive(2026, 10, 31, 0, 0)
    pay = payload(start, 24 * 4, lambda i: (270, i, i + 2), offset=-14400)
    s = HourlySeries.from_open_meteo(pay, DETROIT)
    # Label index 50 is 2026-11-02T02:00 at -04:00 = 06:00Z = 01:00 EST.
    at = datetime(2026, 11, 2, 1, 0, tzinfo=DETROIT)
    assert at.utcoffset() == timedelta(hours=-5)
    assert s.at(at)["wind_speed_10m"] == 50
    # The first EDT 01:00 (05:00Z on 11-01) is label index 24 + 1 = 25.
    first = datetime(2026, 11, 1, 1, 0, tzinfo=DETROIT, fold=0)
    second = datetime(2026, 11, 1, 1, 0, tzinfo=DETROIT, fold=1)
    assert s.at(first)["wind_speed_10m"] == 25
    assert s.at(second)["wind_speed_10m"] == 26  # 01:00 EST is the next real hour, not a repeat of the same value


def test_a_series_whose_labels_repeat_an_hour_is_read_as_wall_clock_with_fold():
    naive = ["2026-11-01T00:00", "2026-11-01T01:00", "2026-11-01T01:00", "2026-11-01T02:00"]
    pay = {"hourly": {"time": naive, "wind_speed_10m": [1, 2, 3, 4]}}
    s = HourlySeries.from_open_meteo(pay, DETROIT)
    assert [t.utcoffset() for t in s.times] == [timedelta(hours=-4), timedelta(hours=-4), timedelta(hours=-5), timedelta(hours=-5)]
    assert s.at(datetime(2026, 11, 1, 1, 0, tzinfo=DETROIT, fold=1))["wind_speed_10m"] == 3


# --- airport hours -----------------------------------------------------------------------------------


def _timeline(wind=CALM, settings: Settings | None = None, **kw) -> dict:
    return build_timeline(
        settings or Settings(),
        feeds_with(series(naive(2026, 9, 27), 24 * 8, wind, **kw)),
        now_utc=NOW,
        tz=DETROIT,
        frozen=False,
        home_water=None,
    )


def test_an_airport_hour_carries_the_contract_fields():
    h = _timeline(models=["ncep_hrrr_conus"] * (24 * 8))["hours"][12]
    assert set(h) == {
        "t", "past", "daylight", "score", "limiting", "wind", "model", "ceiling_ft", "ceiling_known",
        "vis_sm", "fog_risk", "precip_prob", "temp_f",
    }
    assert h["wind"] == {"dir": 270, "kt": 6, "gust": 8}
    assert h["model"] == "ncep_hrrr_conus"
    assert h["score"] == "favorable" and h["limiting"] is None
    assert h["ceiling_known"] is False and h["vis_sm"] == 10 and h["temp_f"] == 70


def test_daylight_is_the_whole_hour_inside_civil_dawn_and_dusk():
    tl = _timeline()
    sun = local_sun_times(NOW.astimezone(DETROIT).date(), 42.6655, -83.4187, DETROIT)
    for h in tl["hours"]:
        t = datetime.fromisoformat(h["t"])
        if t.date() != sun.civil_dawn.date():
            continue
        assert h["daylight"] == (sun.civil_dawn <= t and t + timedelta(hours=1) <= sun.civil_dusk)
    day = [h for h in tl["hours"] if h["daylight"] and h["t"].startswith("2026-09-29")]
    assert day and day[0]["t"] >= "2026-09-29T07:00"


def test_hours_the_forecast_does_not_reach_are_null_not_dropped():
    tl = build_timeline(
        Settings(), feeds_with(series(naive(2026, 9, 29), 12)), now_utc=NOW, tz=DETROIT, frozen=False, home_water=None
    )
    assert len(tl["hours"]) == 79
    assert tl["hours"][6]["score"] is not None
    late = tl["hours"][-1]
    assert late["score"] is None and late["wind"] is None and late["model"] is None
    assert tl["windows"] and all(w["end"] <= "2026-09-29T23:00:00-04:00" for w in tl["windows"])


def test_no_airport_forecast_means_no_timeline(settings):
    assert (
        build_timeline(settings, feeds_with(None), now_utc=NOW, tz=DETROIT, frozen=False, home_water=None) is None
    )


def test_an_alert_applies_only_until_it_ends():
    ends = datetime(2026, 9, 29, 14, 0, tzinfo=DETROIT).isoformat()
    feeds = feeds_with(series(naive(2026, 9, 27), 24 * 8))
    feeds.alerts = [{"event": "Lake Wind Advisory", "area": "x", "ends": ends}]
    tl = build_timeline(Settings(), feeds, now_utc=NOW, tz=DETROIT, frozen=False, home_water=None)
    by_t = {h["t"][:16]: h for h in tl["hours"]}
    assert by_t["2026-09-29T12:00"]["limiting"] == "alert"
    assert by_t["2026-09-29T15:00"]["limiting"] is None


# --- windows ---------------------------------------------------------------------------------------------


def _states(spec: str, *, start=datetime(2026, 9, 29, 6, tzinfo=DETROIT), past: int = 0) -> list[HourState]:
    """One char per hour: F favorable, M marginal, U unfavorable, n night (unfavorable), . unknown."""
    out = []
    for i, ch in enumerate(spec):
        t = (start.astimezone(UTC) + timedelta(hours=i)).astimezone(DETROIT)
        level = {"F": "favorable", "M": "marginal", "U": "unfavorable", "n": "unfavorable", ".": None}[ch]
        out.append(HourState(t, i < past, ch != "n", level, None if level == "favorable" else "gusts"))
    return out


def test_a_window_is_a_run_of_favorable_daylight_hours_at_least_min_long():
    w = find_windows(_states("UFFFFMF"), 2)
    assert len(w) == 1
    assert (w[0]["start"], w[0]["end"], w[0]["score"]) == (
        "2026-09-29T07:00:00-04:00", "2026-09-29T11:00:00-04:00", "favorable",
    )
    assert w[0]["limiting_after"] == "gusts"  # the marginal hour that ended it


def test_a_single_favorable_hour_is_not_a_window_and_min_window_hours_is_respected():
    assert find_windows(_states("UFUFU"), 2) == []
    assert len(find_windows(_states("UFFU"), 2)) == 1
    assert find_windows(_states("UFFU"), 3) == []


def test_marginal_runs_count_only_when_the_day_has_no_favorable_window():
    w = find_windows(_states("MMMUFU"), 2)  # favorable never reaches 2 hours: the marginal run stands
    assert [(x["score"]) for x in w] == ["marginal"]
    assert w[0]["end"] == "2026-09-29T09:00:00-04:00"
    w = find_windows(_states("MMMUFFU"), 2)  # a favorable window exists, so the marginal run is dropped
    assert [x["score"] for x in w] == ["favorable"]


def test_a_marginal_window_may_contain_favorable_hours_and_scores_marginal():
    w = find_windows(_states("FMFU"), 2)
    assert len(w) == 1 and w[0]["score"] == "marginal" and w[0]["end"] == "2026-09-29T09:00:00-04:00"


def test_each_day_is_judged_on_its_own():
    # Day 1 has a favorable window; day 2 only marginal hours. Night (n) between them breaks the run.
    spec = "FFF" + "n" * 18 + "MMM"  # 06:00-08:59, night through 03:00, then 09:00 tomorrow...
    states = _states(spec)
    w = find_windows(states, 2)
    assert [x["score"] for x in w] == ["favorable", "marginal"]
    assert w[0]["start"].startswith("2026-09-29") and w[1]["start"].startswith("2026-09-30")


def test_a_window_never_crosses_dusk_and_ends_with_no_limiting_factor_there():
    w = find_windows(_states("FFFnF"), 2)
    assert len(w) == 1 and w[0]["end"] == "2026-09-29T09:00:00-04:00"
    assert w[0]["limiting_after"] is None  # dusk, not weather


def test_past_hours_are_never_in_a_window():
    w = find_windows(_states("FFFFF", past=3), 2)
    assert len(w) == 1 and w[0]["start"] == "2026-09-29T09:00:00-04:00"


def test_a_window_that_runs_to_the_end_of_the_axis_has_no_limiting_factor():
    w = find_windows(_states("UFFF"), 2)
    assert w[0]["end"] == "2026-09-29T10:00:00-04:00" and w[0]["limiting_after"] is None


def test_unknown_hours_break_a_window():
    assert find_windows(_states("FF.FF"), 3) == []


def test_the_combined_score_is_the_worse_of_airport_and_water():
    assert combine(("favorable", None), ("marginal", "waves")) == ("marginal", "waves")
    assert combine(("unfavorable", "gusts"), ("marginal", "waves")) == ("unfavorable", "gusts")
    assert combine(("marginal", "fog"), ("marginal", "waves")) == ("marginal", "fog")  # tie: airport first
    assert combine(("favorable", None), (None, None)) == ("favorable", None)  # no water forecast: airport decides
    assert combine((None, None), ("favorable", None)) == (None, None)


def test_windows_in_a_full_timeline_stop_at_a_gusty_hour_and_name_it():
    def wind(i: int):
        # Local label index -> hour of day: series starts 2026-09-27 00:00.
        return (270, 8, 34) if i % 24 == 14 else (270, 6, 8)

    tl = _timeline(wind)
    day = [w for w in tl["windows"] if w["start"].startswith("2026-09-29")]
    assert len(day) == 2
    first, second = day
    assert first["start"] == "2026-09-29T10:00:00-04:00" and first["end"] == "2026-09-29T14:00:00-04:00"
    assert first["limiting_after"] == "gusts"
    assert second["start"] == "2026-09-29T15:00:00-04:00"
    sun = local_sun_times(datetime(2026, 9, 29, tzinfo=DETROIT).date(), 42.6655, -83.4187, DETROIT)
    assert datetime.fromisoformat(second["end"]) <= sun.civil_dusk
    assert second["limiting_after"] is None


# --- home water ---------------------------------------------------------------------------------------------

LAKE_ID = 4242


def _home(tz=DETROIT):
    """A three-region water body, each region in its own 0.1 degree cell."""
    field = make_wave_field(
        {
            LAKE_ID: [
                wave_point(-82.90, 42.40, label=0, fetch_m=[2000.0] * 16, run_ft=[9000.0] * 8, depth_m=5.0),
                wave_point(-82.91, 42.41, label=0, fetch_m=[2200.0] * 16, run_ft=[9000.0] * 8, depth_m=5.0),
                wave_point(-83.30, 42.60, label=2, fetch_m=[9000.0] * 16, run_ft=[20000.0] * 8, depth_m=5.0),
                wave_point(-83.60, 42.20, label=1, fetch_m=[500.0] * 16, run_ft=[500.0] * 8, depth_m=1.0),
            ]
        },
        ["Anchor Bay", "Duck Bay", "open middle"],
    )
    index = [{"id": LAKE_ID, "name": "Test Water", "lat": 42.4, "lon": -83.2, "verdict": "clear", "chord_ft": 20000}]
    return lakes_mod.find_candidate(index, LAKE_ID, home_lat=42.6655, home_lon=-83.4187, extents=None, wave_field=field)


def _home_feeds(cand, *, buoy_rows=None, marine=None) -> Feeds:
    start = naive(2026, 9, 27)
    cells = {}
    for label, cell in cand.region_cells.items():
        # Anchor Bay (0) light, Duck Bay (1) strong, open middle (2) moderate.
        kt = {0: (4, 6), 1: (18, 26), 2: (10, 15)}[label]
        cells[cell] = series(start, 24 * 8, lambda i, kt=kt, label=label: (200 + 10 * label, kt[0], kt[1]))
    f = feeds_with(series(start, 24 * 8), lake_series=cells, marine=marine)
    if buoy_rows is not None:
        f.buoy_history = {"station": "45147", "rows": buoy_rows}
    return f


def test_home_water_arrays_are_aligned_with_hours_and_labels():
    cand = _home()
    feeds = _home_feeds(cand)
    tl = build_timeline(Settings(), feeds, now_utc=NOW, tz=DETROIT, frozen=False, home_water=cand)
    hw = tl["home_water"]
    n = len(tl["hours"])
    assert n == 79
    assert hw["id"] == LAKE_ID and hw["name"] == "Test Water"
    assert hw["labels"] == ["Anchor Bay", "Duck Bay", "open middle"]  # the pack's order, fixed
    for key in ("hs_in", "wind", "best", "open_in", "score", "limiting", "marine_in"):
        assert len(hw[key]) == n, key
    assert all(len(row) == 3 for row in hw["hs_in"]) and all(len(row) == 3 for row in hw["wind"])
    # Each region reads its own cell's wind, in label order.
    assert hw["wind"][10] == [
        {"dir": 200, "kt": 4, "gust": 6},
        {"dir": 210, "kt": 18, "gust": 26},
        {"dir": 220, "kt": 10, "gust": 15},
    ]
    # Duck Bay's 500 ft run is unusable: null, never zero. The calmer usable region is the best one.
    assert hw["hs_in"][10][1] is None
    assert hw["hs_in"][10][0] is not None and hw["hs_in"][10][2] is not None
    assert hw["best"][10]["label"] == "Anchor Bay" and hw["best"][10]["hs_in"] == hw["hs_in"][10][0]
    assert hw["open_in"][10] >= hw["hs_in"][10][2]
    assert hw["score"][10] in {"favorable", "marginal", "unfavorable"}


def test_home_water_score_is_water_only_and_ice_overrides_it():
    cand = _home()
    feeds = _home_feeds(cand)
    calm = build_timeline(Settings(), feeds, now_utc=NOW, tz=DETROIT, frozen=False, home_water=cand)["home_water"]
    assert calm["score"][10] == "favorable" and calm["limiting"][10] is None
    frozen = build_timeline(Settings(), feeds, now_utc=NOW, tz=DETROIT, frozen=True, home_water=cand)["home_water"]
    assert set(frozen["score"]) == {"unfavorable"} and set(frozen["limiting"]) == {"ice"}


def test_a_region_without_a_forecast_cell_is_null_and_a_water_with_none_scores_null():
    cand = _home()
    feeds = _home_feeds(cand)
    dropped = cand.region_cells[1]
    del feeds.lake_series[dropped]
    hw = build_timeline(Settings(), feeds, now_utc=NOW, tz=DETROIT, frozen=False, home_water=cand)["home_water"]
    assert hw["wind"][10][1] is None and hw["hs_in"][10][1] is None
    assert hw["wind"][10][0] is not None
    feeds.lake_series.clear()
    hw = build_timeline(Settings(), feeds, now_utc=NOW, tz=DETROIT, frozen=False, home_water=cand)["home_water"]
    assert hw["score"][10] is None and hw["best"][10] is None and hw["wind"][10] == [None, None, None]
    assert len(hw["score"]) == 79


def test_a_worse_home_water_pulls_a_window_down():
    cand = _home()
    feeds = _home_feeds(cand)
    # Blow every region hard from 13:00 on the 29th: airport stays calm, the water does not.
    for cell in cand.cells:
        feeds.lake_series[cell] = series(
            naive(2026, 9, 27), 24 * 8, lambda i: (200, 22, 32) if i % 24 >= 13 else (200, 4, 6)
        )
    with_water = build_timeline(Settings(), feeds, now_utc=NOW, tz=DETROIT, frozen=False, home_water=cand)
    without = build_timeline(Settings(), feeds, now_utc=NOW, tz=DETROIT, frozen=False, home_water=None)
    day = lambda tl: [w for w in tl["windows"] if w["start"].startswith("2026-09-29")]
    assert day(without)[0]["end"] > "2026-09-29T13:00:00-04:00"
    assert day(with_water)[0]["end"] == "2026-09-29T13:00:00-04:00"
    assert day(with_water)[0]["limiting_after"] in {"waves", "run"}


def test_observed_history_covers_past_hours_only_and_uses_the_nearest_row():
    cand = _home()
    rows = []
    for k in range(24 * 3):
        at = datetime(2026, 9, 29, 12, 0, tzinfo=UTC) - timedelta(hours=k)
        rows.append({"at": at, "dir_deg": 60.0, "speed_kt": 6.2, "gust_kt": None, "wave_height_m": 0.1016})
    feeds = _home_feeds(cand, buoy_rows=rows)
    hw = build_timeline(Settings(), feeds, now_utc=NOW, tz=DETROIT, frozen=False, home_water=cand)["home_water"]
    obs = hw["observed"]
    assert [o["t"] for o in obs] == [f"2026-09-29T{h:02d}:00:00-04:00" for h in range(4, 9)]  # rows stop at 12:00Z = 08:00 local
    assert obs[0] == {"t": obs[0]["t"], "station": "45147", "wave_in": 4, "wind": {"dir": 60, "kt": 6, "gust": None}}


def test_observed_history_skips_hours_the_buoy_has_no_row_near():
    cand = _home()
    rows = [{"at": datetime(2026, 9, 29, 10, 0, tzinfo=UTC), "dir_deg": 90.0, "speed_kt": 5.0, "gust_kt": 8.0, "wave_height_m": 0.2}]
    hw = build_timeline(Settings(), _home_feeds(cand, buoy_rows=rows), now_utc=NOW, tz=DETROIT, frozen=False, home_water=cand)["home_water"]
    assert [o["t"] for o in hw["observed"]] == ["2026-09-29T06:00:00-04:00"]  # 10:00Z is 06:00 EDT
    assert hw["observed"][0]["wave_in"] == 8 and hw["observed"][0]["wind"]["gust"] == 8


def test_no_buoy_means_an_empty_observed_list_and_marine_is_per_hour():
    cand = _home()
    marine = {
        "latitude": 42.4,
        "longitude": -83.2,
        "hourly": {
            "time": [(naive(2026, 9, 27) + timedelta(hours=i)).strftime("%Y-%m-%dT%H:00") for i in range(24 * 8)],
            "wave_height": [0.0254 * (i % 24) for i in range(24 * 8)],
        },
    }
    hw = build_timeline(Settings(), _home_feeds(cand, marine=marine), now_utc=NOW, tz=DETROIT, frozen=False, home_water=cand)["home_water"]
    assert hw["observed"] == []
    assert hw["marine_in"][6] == 10 and hw["marine_in"][7] == 11  # 10:00 and 11:00 local, in inches


def test_home_water_is_null_when_none_is_set():
    assert _timeline()["home_water"] is None


def test_the_briefing_carries_the_timeline_and_leaves_the_rest_alone(settings):
    feeds = feeds_with(series(naive(2026, 9, 27), 24 * 8))
    b = build_briefing(settings, feeds, [], now_utc=NOW, run_kind="manual")
    assert set(b["timeline"]) == {"hours", "windows", "home_water"}
    assert {"days", "outlook", "summary"} <= set(b)
    feeds.airport = None
    assert build_briefing(settings, feeds, [], now_utc=NOW, run_kind="manual")["timeline"] is None


def test_home_water_settings_round_trip_into_the_briefing():
    cand = _home()
    settings = Settings(home_water=HomeWater(id=LAKE_ID, name="Test Water"))
    b = build_briefing(settings, _home_feeds(cand), [], now_utc=NOW, run_kind="manual", home_water=cand)
    assert b["timeline"]["home_water"]["id"] == LAKE_ID


# --- NDBC realtime2 -------------------------------------------------------------------------------------------


REALTIME2 = """#YY  MM DD hh mm WDIR WSPD GST  WVHT   DPD   APD MWD   PRES  ATMP  WTMP  DEWP  VIS PTDY  TIDE
#yr  mo dy hr mn degT m/s  m/s     m   sec   sec degT   hPa  degC  degC  degC  nmi  hPa    ft
2026 09 29 02 00  60  3.0   MM   0.1     2    MM  MM 1016.6  17.8  17.7    MM   MM +0.8    MM
2026 09 29 01 00  MM  MM   5.0   MM     2    MM  MM 1016.6  18.5  17.9    MM   MM +1.2    MM
"""


def test_the_realtime2_table_parses_to_knots_and_metres():
    rows = ndbc.parse_history(REALTIME2)
    assert len(rows) == 2
    assert rows[0] == {
        "at": datetime(2026, 9, 29, 2, 0, tzinfo=UTC),
        "dir_deg": 60.0,
        "speed_kt": 5.8,
        "gust_kt": None,
        "wave_height_m": 0.1,
    }
    assert rows[1]["dir_deg"] is None and rows[1]["speed_kt"] is None and rows[1]["gust_kt"] == 9.7
