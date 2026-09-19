"""The home water block: every region with its own score, real observations, the marine opinion."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from seaplane_api.briefing import homewater
from seaplane_api.briefing import lakes as lakes_mod
from seaplane_api.briefing.series import HourlySeries
from seaplane_api.fetch import ndbc
from seaplane_api.settings import Limits

from .conftest import DETROIT, load, make_wave_field, wave_point

KONZ = (42.0991, -83.1615)
LIMITS = Limits()
NOW = datetime(2026, 9, 19, 14, 0, tzinfo=UTC)
NOON = [datetime(2026, 9, 19, 12, 0, tzinfo=DETROIT)]


def _home_water(min_run_ft: float = 2000) -> lakes_mod.Candidate:
    """Three regions: a glassy bay, a choppier bay, and open water; one bay has almost no run."""
    field = make_wave_field(
        {
            42: [
                wave_point(-82.66, 42.55, label=0, fetch_m=[1200.0] * 16, run_ft=[3000.0] * 8, depth_m=0.9),
                wave_point(-82.65, 42.56, label=0, fetch_m=[1100.0] * 16, run_ft=[3200.0] * 8, depth_m=0.8),
                wave_point(-82.71, 42.65, label=1, fetch_m=[7500.0] * 16, run_ft=[900.0] * 8, depth_m=3.0),
                wave_point(-82.68, 42.43, label=2, fetch_m=[22000.0] * 16, run_ft=[40000.0] * 8, depth_m=5.5),
            ]
        },
        ["Big Muscamoot Bay", "Anchor Bay", "open lake"],
    )
    index = [
        {"id": 42, "name": "Lake St. Clair", "kind": "great_lake", "verdict": "conditional",
         "chord_ft": 100000, "chord_bearing_deg": 90, "lat": 42.45, "lon": -82.70}
    ]
    return lakes_mod.find_candidate(
        index, 42, home_lat=KONZ[0], home_lon=KONZ[1], extents=None, wave_field=field
    )


def _ranked(cand, wind_kt=14, gust_kt=18, wind_dir=270, frozen=False, limits=LIMITS):
    series = _uniform_series(wind_kt, gust_kt, wind_dir)
    return lakes_mod.score_over(cand, {cand.cell: series}, NOON, limits, frozen=frozen)


def test_every_region_is_listed_with_its_own_score():
    cand = _home_water()
    block = homewater.block(_ranked(cand), LIMITS)
    labels = [r["label"] for r in block["regions"]]
    assert labels == ["Big Muscamoot Bay", "open lake", "Anchor Bay"]  # calm to rough, unusable last
    assert len(labels) == 3  # not capped at the row's four
    scores = {r["label"]: r["score"] for r in block["regions"]}
    assert scores["Big Muscamoot Bay"] == "favorable"
    assert scores["open lake"] == "unfavorable"
    assert scores["Anchor Bay"] == "unfavorable"  # no usable run into this wind


def test_a_region_with_no_usable_run_keeps_null_numbers_and_a_position():
    cand = _home_water()
    block = homewater.block(_ranked(cand), LIMITS)
    anchor = next(r for r in block["regions"] if r["label"] == "Anchor Bay")
    assert anchor["hs_in"] is None and anchor["run_ft"] is None
    assert anchor["score"] == "unfavorable"
    assert anchor["lat"] and anchor["lon"]  # still drawable


def test_the_block_carries_the_contract_fields_and_the_open_water_figure():
    cand = _home_water()
    block = homewater.block(_ranked(cand), LIMITS, observed=[], marine_hs_in=None)
    assert block["id"] == 42 and block["name"] == "Lake St. Clair"
    assert block["kind"] == "great_lake"
    assert block["score"] == "favorable"  # the bay is landable even when the lake is not
    assert block["hs_open_in"] == max(r["hs_in"] for r in block["regions"] if r["hs_in"])
    assert block["observed"] == [] and block["marine_hs_in"] is None


def test_the_outlook_copy_leaves_observed_out_entirely():
    block = homewater.block(_ranked(_home_water()), LIMITS)
    assert "observed" not in block
    assert "marine_hs_in" in block


def test_a_frozen_day_marks_every_region_unfavorable():
    cand = _home_water()
    block = homewater.block(_ranked(cand, frozen=True), LIMITS)
    assert block["score"] == "unfavorable" and block["limiting"] == "ice"
    assert {r["score"] for r in block["regions"]} == {"unfavorable"}


def test_a_home_water_without_a_wave_field_still_briefs_at_lake_level():
    cand = lakes_mod.Candidate(
        id=5, name="Plain Lake", lat=42.5, lon=-83.3, verdict="clear", chord_ft=9000,
        chord_bearing_deg=90, distance_nm=12, bearing_deg=0, extents_ft=tuple([9000] * 16),
        home=KONZ,
    )
    block = homewater.block(_ranked(cand), LIMITS, observed=[])
    assert block["regions"] == [] and block["hs_open_in"] is None
    assert block["score"] in {"favorable", "marginal", "unfavorable"}


# --- observed --------------------------------------------------------------------------------


def _buoy(**kw) -> dict:
    row = {
        "id": "45147", "source": "ndbc", "lat": 42.43, "lon": -82.68,
        "at": NOW - timedelta(minutes=20), "dir_deg": 90.0, "speed_kt": 12.0,
        "gust_kt": 15.0, "wave_height_m": 0.3,
    }
    row.update(kw)
    return row


def _metar(**kw) -> dict:
    row = {
        "icaoId": "KMTC", "name": "Selfridge ANGB, MI, US", "lat": 42.6045, "lon": -82.8353,
        "obsTime": (NOW - timedelta(minutes=10)).timestamp(), "wdir": 90, "wspd": 8, "wgst": None,
    }
    row.update(kw)
    return row


def test_observed_keeps_nearby_fresh_stations_and_measures_to_the_nearest_point():
    cand = _home_water()
    rows = homewater.observed_rows(cand, buoys=[_buoy()], metars=[_metar()], now_utc=NOW)
    assert [r["kind"] for r in rows] == ["buoy", "metar"] or [r["kind"] for r in rows] == ["metar", "buoy"]
    buoy = next(r for r in rows if r["kind"] == "buoy")
    assert buoy["station"] == "45147"
    assert buoy["wind"] == {"dir": 90, "kt": 12, "gust": 15}
    assert buoy["wave_ft"] == 1.0
    assert buoy["at"] == "2026-09-19T13:40:00Z"
    # Distance is to the nearest sample point, not to the centroid 20 nm up the lake.
    assert buoy["distance_nm"] == round(homewater.nearest_nm(cand, 42.43, -82.68), 1)
    assert buoy["distance_nm"] < round(lakes_mod.aero.distance_nm(42.43, -82.68, cand.lat, cand.lon), 1)
    assert rows == sorted(rows, key=lambda r: r["distance_nm"])


def test_a_buoy_further_off_than_fifteen_miles_is_a_different_piece_of_water():
    cand = _home_water()
    far = _buoy(id="45005", lat=41.677, lon=-82.398)  # western Lake Erie
    assert homewater.observed_rows(cand, buoys=[far], metars=[], now_utc=NOW) == []


def test_a_stale_observation_is_dropped_rather_than_shown_as_agreement():
    cand = _home_water()
    old_buoy = _buoy(at=NOW - timedelta(minutes=91))
    old_metar = _metar(obsTime=(NOW - timedelta(minutes=120)).timestamp())
    assert homewater.observed_rows(cand, buoys=[old_buoy], metars=[old_metar], now_utc=NOW) == []
    fresh = homewater.observed_rows(
        cand, buoys=[_buoy(at=NOW - timedelta(minutes=89))], metars=[], now_utc=NOW
    )
    assert len(fresh) == 1


def test_a_station_with_neither_wind_nor_waves_says_nothing_worth_printing():
    cand = _home_water()
    silent = _buoy(id="AGCM4", speed_kt=None, gust_kt=None, dir_deg=None, wave_height_m=None)
    assert homewater.observed_rows(cand, buoys=[silent], metars=[], now_utc=NOW) == []
    waves_only = _buoy(id="WVHT", speed_kt=None, dir_deg=None, gust_kt=None)
    rows = homewater.observed_rows(cand, buoys=[waves_only], metars=[], now_utc=NOW)
    assert rows[0]["wind"] == {"dir": None, "kt": None, "gust": None} and rows[0]["wave_ft"] == 1.0


def test_a_field_whose_wind_sensor_is_out_is_not_one_of_the_three():
    """KARB on the live check: `METAR KARB 191753Z AUTO A3003 RMK AO2 SLPNO 57023 PWINO`."""
    cand = _home_water()
    blind = _metar(icaoId="KARB", lat=42.223, lon=-83.746, wdir=None, wspd=None)
    rows = homewater.observed_rows(cand, buoys=[], metars=[blind, _metar()], now_utc=NOW)
    assert [r["station"] for r in rows] == ["KMTC"]


def test_only_the_nearest_three_metars_are_kept_newest_per_station():
    cand = _home_water()
    metars = [
        _metar(icaoId="KMTC", lat=42.6045, lon=-82.8353),
        _metar(icaoId="KDET", lat=42.4072, lon=-83.009),
        _metar(icaoId="CYQG", lat=42.269, lon=-82.963),
        _metar(icaoId="KVLL", lat=42.543, lon=-83.178),
        _metar(icaoId="KPTK", lat=42.6655, lon=-83.4187),
        _metar(icaoId="KMTC", lat=42.6045, lon=-82.8353, obsTime=(NOW - timedelta(minutes=70)).timestamp()),
    ]
    rows = homewater.observed_rows(cand, buoys=[], metars=metars, now_utc=NOW)
    assert len(rows) == 3
    assert [r["station"] for r in rows] == ["KMTC", "KDET", "CYQG"]
    assert rows[0]["at"] == "2026-09-19T13:50:00Z"  # the newer of the two KMTC reports
    assert all(r["wave_ft"] is None for r in rows)


def test_observed_reads_the_real_ndbc_file_through_the_real_parser():
    """End to end from the recorded `latest_obs.txt`: the St. Clair buoy is 45147."""
    cand = _home_water()
    bbox = homewater.bbox_around(cand)
    rows = ndbc.parse_latest_obs(load("ndbc_latest_obs.txt"), (bbox[1], bbox[0], bbox[3], bbox[2]))
    at = datetime(2026, 9, 19, 13, 30, tzinfo=UTC)
    observed = homewater.observed_rows(cand, buoys=rows, metars=[], now_utc=at)
    assert [r["station"] for r in observed] == ["45147", "CLSM4"]
    assert observed[0]["wave_ft"] == 1.0 and observed[0]["wind"]["kt"] == 12
    # CLSM4 is a shore station: wind but no wave height, which is still worth showing.
    assert observed[1]["wave_ft"] is None and observed[1]["wind"]["kt"] == 7
    assert all(r["name"] is None for r in observed)  # latest_obs.txt has no station names


def test_without_a_wave_field_the_water_body_is_measured_by_its_bounding_box():
    """A home water with no points is still not a dot: a buoy on its far end is on it."""
    plain = lakes_mod.find_candidate(
        [{"id": 9, "name": "Long Lake", "kind": "lake", "verdict": "clear", "chord_ft": 30000,
          "chord_bearing_deg": 90, "lat": 42.50, "lon": -83.30,
          "bbox": [-83.40, 42.48, -83.20, 42.52]}],
        9, home_lat=KONZ[0], home_lon=KONZ[1], extents=None, wave_field=None,
    )
    on_the_east_end = homewater.nearest_nm(plain, 42.50, -83.20)
    assert on_the_east_end < 0.1
    assert lakes_mod.aero.distance_nm(42.50, -83.20, plain.lat, plain.lon) > 4
    box = homewater.bbox_around(plain)
    assert box[1] < -83.40 and box[3] > -83.20


def test_the_bbox_covers_the_whole_water_body_plus_a_margin():
    cand = _home_water()
    min_lat, min_lon, max_lat, max_lon = homewater.bbox_around(cand)
    assert min_lat < min(p.lat for p in cand.points)
    assert max_lat > max(p.lat for p in cand.points)
    assert min_lon < min(p.lon for p in cand.points)
    assert max_lon > max(p.lon for p in cand.points)


# --- the marine second opinion -----------------------------------------------------------------


def _marine(values, lat=42.45, lon=-82.70) -> dict:
    return {
        "latitude": lat,
        "longitude": lon,
        "hourly": {"time": [f"2026-09-19T{h:02d}:00" for h in range(24)], "wave_height": values},
    }


def test_marine_wave_height_is_read_for_the_hour_and_converted_to_inches():
    cand = _home_water()
    payload = _marine([0.3] * 12 + [0.61] * 12)
    assert homewater.marine_hs_in(payload, NOON[0], cand) == 24  # 0.61 m at noon


def test_a_narrow_bay_comes_back_as_nulls_and_stays_null():
    cand = _home_water()
    assert homewater.marine_hs_in(_marine([None] * 24), NOON[0], cand) is None


def test_no_marine_payload_at_all_is_null_not_an_error():
    cand = _home_water()
    assert homewater.marine_hs_in(None, NOON[0], cand) is None
    assert homewater.marine_hs_in({}, NOON[0], cand) is None
    assert homewater.marine_hs_in(_marine([0.3] * 24), datetime(2026, 9, 25, 9, 0, tzinfo=DETROIT), cand) is None


def test_a_grid_point_the_api_snapped_miles_away_is_not_this_water():
    """The marine API answers about its nearest wet cell without saying so (verified live)."""
    cand = _home_water()
    assert homewater.marine_hs_in(_marine([0.5] * 24, lat=43.4, lon=-82.1), NOON[0], cand) is None


def _uniform_series(wind_kt: float, gust_kt: float, wind_dir: float) -> HourlySeries:
    times = [f"2026-09-19T{h:02d}:00" for h in range(24)]
    payload = {
        "elevation": 175.0,
        "hourly": {
            "time": times,
            "wind_speed_10m": [wind_kt] * 24,
            "wind_gusts_10m": [gust_kt] * 24,
            "wind_direction_10m": [wind_dir] * 24,
        },
    }
    return HourlySeries.from_open_meteo(payload, DETROIT)
