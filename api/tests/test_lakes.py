"""Candidate filter, the 0.1 degree forecast grid, the fetch arc, regions, and lake ranking."""
from __future__ import annotations

from datetime import datetime

import pytest

from seaplane_api.briefing import lakes as lakes_mod
from seaplane_api.briefing.series import HourlySeries
from seaplane_api.settings import Limits

from .conftest import DETROIT, load, make_wave_field, wave_point

KPTK = (42.6655, -83.4187)
LIMITS = Limits()
NOON = [datetime(2026, 9, 19, 12, 0, tzinfo=DETROIT)]
CASS_LAKE = 1900195525


def _candidates(**kw):
    defaults = {
        "home_lat": KPTK[0], "home_lon": KPTK[1], "radius_nm": 40, "min_run_ft": 2000,
        "public_access_only": False, "extents": load("lake_extents_sample.json"),
    }
    defaults.update(kw)
    return lakes_mod.select_candidates(load("index_sample.json"), **defaults)


def test_candidate_filter_drops_restricted_lakes():
    names = {c.name for c in _candidates()}
    assert "Lake Angelus" not in names  # verdict restricted
    assert {"Cass Lake", "Orchard Lake", "Pontiac Lake", "Union Lake", "Big Lake"} == names


def test_a_river_without_a_wave_field_is_still_not_a_candidate():
    """A reach length says nothing about width: Otter Creek is not landable water."""
    index = load("index_sample.json")
    cass = next(lake for lake in index if lake["name"] == "Cass Lake")
    river = {**cass, "id": 999, "name": "Otter Creek", "kind": "river"}
    lake = {**cass, "id": 998, "name": "Tagged Lake", "kind": "lake"}
    got = lakes_mod.select_candidates(
        [*index, river, lake], home_lat=KPTK[0], home_lon=KPTK[1], radius_nm=40, min_run_ft=2000,
        public_access_only=False, extents=None,
    )
    names = {c.name for c in got}
    assert "Otter Creek" not in names
    assert {"Tagged Lake", "Cass Lake"} <= names  # a missing kind means lake


@pytest.mark.parametrize("kind", ["river", "connecting_water"])
def test_a_river_with_a_wave_field_is_a_candidate_because_its_width_is_measured(kind):
    index = load("index_sample.json")
    cass = next(lake for lake in index if lake["name"] == "Cass Lake")
    river = {**cass, "id": 999, "name": "Detroit River", "kind": kind}
    field = make_wave_field(
        {999: [wave_point(-83.36, 42.60, label=0, fetch_m=[900.0] * 16, run_ft=[6000.0] * 8, depth_m=6.0)]},
        ["Fighting Island reach"],
    )
    got = lakes_mod.select_candidates(
        [*index, river], home_lat=KPTK[0], home_lon=KPTK[1], radius_nm=40, min_run_ft=2000,
        public_access_only=False, extents=None, wave_field=field,
    )
    river_cand = next(c for c in got if c.name == "Detroit River")
    assert river_cand.kind == kind and river_cand.has_wave_field
    hour = lakes_mod.score_lake_hour(river_cand, 270, 12, 16, LIMITS)
    assert hour.region_label == "Fighting Island reach"
    assert hour.level == "favorable"


def test_candidate_filter_honours_min_run_and_radius_and_access():
    assert {c.name for c in _candidates(min_run_ft=9000)} == {"Cass Lake", "Pontiac Lake"}
    assert _candidates(radius_nm=2) == [c for c in _candidates() if c.distance_nm <= 2]
    public = {c.name for c in _candidates(public_access_only=True)}
    assert "Cass Lake" not in public  # access is null in the real index entry
    assert "Orchard Lake" in public


def test_candidates_come_back_nearest_first_with_distance_and_bearing():
    cands = _candidates()
    assert [round(c.distance_nm, 1) for c in cands] == sorted(round(c.distance_nm, 1) for c in cands)
    pontiac = next(c for c in cands if c.name == "Pontiac Lake")
    assert 1.0 < pontiac.distance_nm < 3.0
    assert 0 <= pontiac.bearing_deg < 360


def test_grid_points_collapse_nearby_lakes_onto_one_forecast():
    cands = _candidates()
    points = lakes_mod.grid_points(cands)
    assert len(points) < len(cands)
    assert all(abs(round(lat / 0.1) * 0.1 - lat) < 1e-6 for lat, _ in points)


def test_run_uses_the_wind_bin_and_fetch_uses_the_three_bin_arc():
    """Orchard Lake: bin 4 reads 6,616 ft, but bin 3 next door threads 6,343 and bin 5 6,572.

    The run is what the bin says; the fetch is the widest of the arc, because a bin that undershoots
    the shoreline would otherwise predict calmer water than the lake gives.
    """
    orchard = next(c for c in _candidates() if c.name == "Orchard Lake")
    assert orchard.extents_ft[4] == 6616
    assert orchard.run_ft(90, 15) == 6616
    assert orchard.fetch_ft(90, 15) == max(orchard.extents_ft[3], orchard.extents_ft[4], orchard.extents_ft[5])
    assert orchard.fetch_ft(90, 15) >= orchard.run_ft(90, 15)


def test_fetch_arc_wraps_around_the_bin_array():
    cand = lakes_mod.Candidate(
        id=1, name="Test", lat=42.0, lon=-83.0, verdict="clear", chord_ft=5000,
        chord_bearing_deg=0, distance_nm=1, bearing_deg=0,
        extents_ft=(1000, 2000, 500, 500, 500, 500, 500, 500, 1000, 2000, 500, 500, 500, 500, 500, 9000),
    )
    assert cand.run_ft(0, 15) == 1000
    assert cand.fetch_ft(0, 15) == 9000  # bin 15 is the neighbour below bin 0


def test_light_wind_falls_back_to_the_longest_chord():
    orchard = next(c for c in _candidates() if c.name == "Orchard Lake")
    assert orchard.run_ft(90, 3) == pytest.approx(orchard.chord_ft)
    assert orchard.fetch_ft(90, 3) == pytest.approx(orchard.chord_ft)


def test_missing_extents_fall_back_to_the_chord():
    cands = lakes_mod.select_candidates(
        load("index_sample.json"), home_lat=KPTK[0], home_lon=KPTK[1], radius_nm=40,
        min_run_ft=2000, public_access_only=False, extents=None,
    )
    assert all(c.extents_ft is None for c in cands)
    assert cands[0].run_ft(200, 15) == pytest.approx(cands[0].chord_ft)


def test_small_lake_beats_the_big_one_in_the_same_eighteen_knot_wind():
    """Design 7: a big lake and a small lake in 18 kt; the small one must win."""
    big = lakes_mod.Candidate(
        id=1, name="Big Water", lat=42.7, lon=-83.4, verdict="clear", chord_ft=25000,
        chord_bearing_deg=90, distance_nm=5, bearing_deg=90, extents_ft=tuple([25000] * 16),
    )
    small = lakes_mod.Candidate(
        id=2, name="Little Pond", lat=42.7, lon=-83.4, verdict="clear", chord_ft=2600,
        chord_bearing_deg=90, distance_nm=6, bearing_deg=90, extents_ft=tuple([2600] * 16),
    )
    series = _uniform_series(wind_kt=18, gust_kt=18, wind_dir=270)
    ranked = lakes_mod.rank_lakes(
        [big, small], {big.cell: series}, [datetime(2026, 9, 19, 12, 0, tzinfo=DETROIT)],
        LIMITS, frozen=False, n_lakes=8,
    )
    # The small lake wins on *score*, not on the tie-break: it is the further of the two.
    assert [r.cand.name for r in ranked] == ["Little Pond", "Big Water"]
    assert ranked[0].level == "favorable" and ranked[1].level == "unfavorable"
    assert ranked[1].limiting == "waves"
    assert ranked[0].hour.hs_in < ranked[1].hour.hs_in
    assert ranked[0].cand.distance_nm > ranked[1].cand.distance_nm


def test_run_shorter_than_the_minimum_is_the_limiting_factor():
    lake = lakes_mod.Candidate(
        id=3, name="Narrow", lat=42.7, lon=-83.4, verdict="clear", chord_ft=6000,
        chord_bearing_deg=0, distance_nm=4, bearing_deg=0,
        extents_ft=tuple([6000 if i in (0, 8) else 1200 for i in range(16)]),
    )
    hour = lakes_mod.score_lake_hour(lake, 90, 12, 14, LIMITS)
    assert hour.run_ft == 1200
    assert hour.level == "unfavorable" and hour.limiting == "run"


def test_lake_score_is_water_only_and_ignores_the_airport_weather():
    """A low ceiling belongs in the header and the block strip, not on every lake row."""
    lake = lakes_mod.Candidate(
        id=4, name="Fine", lat=42.7, lon=-83.4, verdict="clear", chord_ft=4000,
        chord_bearing_deg=0, distance_nm=4, bearing_deg=0, extents_ft=tuple([4000] * 16),
    )
    series = _uniform_series(wind_kt=6, gust_kt=8, wind_dir=270)
    ranked = lakes_mod.rank_lakes(
        [lake], {lake.cell: series}, [datetime(2026, 9, 19, 12, 0, tzinfo=DETROIT)],
        LIMITS, frozen=False, n_lakes=8,
    )
    assert ranked[0].level == "favorable" and ranked[0].limiting is None


def test_among_equal_score_lakes_the_nearer_one_ranks_first():
    """Ranking on Hs before distance buried Cass and Orchard under 2,000 ft ponds 36 nm out."""
    near = lakes_mod.Candidate(
        id=10, name="Near Big", lat=42.7, lon=-83.4, verdict="clear", chord_ft=9000,
        chord_bearing_deg=0, distance_nm=4.2, bearing_deg=0, extents_ft=tuple([9000] * 16),
    )
    far = lakes_mod.Candidate(
        id=11, name="Far Pond", lat=42.7, lon=-83.4, verdict="clear", chord_ft=2100,
        chord_bearing_deg=0, distance_nm=36.4, bearing_deg=0, extents_ft=tuple([2100] * 16),
    )
    series = _uniform_series(wind_kt=6, gust_kt=9, wind_dir=270)
    ranked = lakes_mod.rank_lakes(
        [far, near], {near.cell: series}, [datetime(2026, 9, 19, 12, 0, tzinfo=DETROIT)],
        LIMITS, frozen=False, n_lakes=8,
    )
    assert [r.level for r in ranked] == ["favorable", "favorable"]
    assert ranked[0].hour.hs_in > ranked[1].hour.hs_in  # the nearer lake is the choppier one
    assert [r.cand.name for r in ranked] == ["Near Big", "Far Pond"]


def test_hs_is_the_final_tie_break_at_equal_distance():
    calm = lakes_mod.Candidate(
        id=12, name="Calm", lat=42.7, lon=-83.4, verdict="clear", chord_ft=2500,
        chord_bearing_deg=0, distance_nm=5.0, bearing_deg=0, extents_ft=tuple([2500] * 16),
    )
    choppy = lakes_mod.Candidate(
        id=13, name="Choppy", lat=42.7, lon=-83.4, verdict="clear", chord_ft=7000,
        chord_bearing_deg=0, distance_nm=5.0, bearing_deg=0, extents_ft=tuple([7000] * 16),
    )
    series = _uniform_series(wind_kt=6, gust_kt=9, wind_dir=270)
    ranked = lakes_mod.rank_lakes(
        [choppy, calm], {calm.cell: series}, [datetime(2026, 9, 19, 12, 0, tzinfo=DETROIT)],
        LIMITS, frozen=False, n_lakes=8,
    )
    assert [r.cand.name for r in ranked] == ["Calm", "Choppy"]


def test_frozen_lakes_are_kept_but_marked():
    lake = lakes_mod.Candidate(
        id=5, name="Icebound", lat=42.7, lon=-83.4, verdict="clear", chord_ft=4000,
        chord_bearing_deg=0, distance_nm=4, bearing_deg=0, extents_ft=tuple([4000] * 16),
    )
    ranked = lakes_mod.rank_lakes(
        [lake], {lake.cell: _uniform_series(4, 6, 270)}, [datetime(2026, 9, 19, 12, 0, tzinfo=DETROIT)],
        LIMITS, frozen=True, n_lakes=8,
    )
    row = ranked[0].to_row()
    assert row["frozen"] is True and row["score"] == "unfavorable" and row["limiting"] == "ice"


def test_row_shape_matches_the_data_contract():
    lake = lakes_mod.Candidate(
        id=1234567, name="Cass Lake", lat=42.6, lon=-83.34, verdict="conditional", chord_ft=13880,
        chord_bearing_deg=68, distance_nm=6.12, bearing_deg=118.4, extents_ft=tuple([13880] * 16),
    )
    ranked = lakes_mod.rank_lakes(
        [lake], {lake.cell: _uniform_series(10, 15, 250)}, [datetime(2026, 9, 19, 12, 0, tzinfo=DETROIT)],
        LIMITS, frozen=False, n_lakes=8,
    )
    row = ranked[0].to_row()
    assert set(row) == {
        "id", "name", "kind", "score", "limiting", "hs_in", "run_ft", "region", "hs_open_in",
        "regions", "wind", "distance_nm", "bearing_deg", "verdict", "frozen",
    }
    assert set(row["wind"]) == {"dir", "kt", "gust"}
    assert isinstance(row["hs_in"], int) and isinstance(row["run_ft"], int)
    assert row["distance_nm"] == 6.1
    # Without a wave field the region columns are empty and the distance is still the centroid's.
    assert (row["kind"], row["region"], row["hs_open_in"], row["regions"]) == ("lake", None, None, [])


# --- the wave field ---------------------------------------------------------------------------


def _big_water(points=None, **kw) -> lakes_mod.Candidate:
    """A water body 20 nm across whose centroid is 12 nm from home, with two regions."""
    field = make_wave_field(
        {
            7: points
            or [
                # A sheltered bay 6 nm from home...
                wave_point(-83.30, 42.55, label=0, fetch_m=[600.0] * 16, run_ft=[4000.0] * 8, depth_m=3.0),
                wave_point(-83.29, 42.54, label=0, fetch_m=[700.0] * 16, run_ft=[4200.0] * 8, depth_m=3.0),
                # ...and open water 25 km of fetch across, further away.
                wave_point(-83.05, 42.40, label=1, fetch_m=[25000.0] * 16, run_ft=[20000.0] * 8),
                wave_point(-83.02, 42.38, label=1, fetch_m=[26000.0] * 16, run_ft=[21000.0] * 8),
            ]
        },
        ["Sheltered Bay", "open lake"],
    )
    index = [
        {
            "id": 7, "name": "Big Water", "kind": "great_lake", "verdict": "clear",
            "chord_ft": 80000, "chord_bearing_deg": 90, "lat": 42.47, "lon": -83.17, **kw,
        }
    ]
    return lakes_mod.find_candidate(
        index, 7, home_lat=KPTK[0], home_lon=KPTK[1], extents=None, wave_field=field
    )


def test_a_water_body_with_wave_points_is_scored_by_its_best_region():
    cand = _big_water()
    hour = lakes_mod.score_lake_hour(cand, 270, 18, 18, LIMITS)
    assert hour.region_label == "Sheltered Bay"
    assert hour.level == "favorable"  # the open lake in the same 18 kt is not
    assert hour.hs_in < hour.hs_open_in
    assert [r.label for r in hour.regions] == ["Sheltered Bay", "open lake"]
    assert hour.run_ft == 4000  # lower median of the bay's usable runs


def test_the_same_water_body_without_its_points_is_the_old_lake_level_score():
    """The wave field is what changes the answer, not the new code path."""
    cand = _big_water()
    bare = lakes_mod.Candidate(
        id=cand.id, name=cand.name, lat=cand.lat, lon=cand.lon, verdict=cand.verdict,
        chord_ft=cand.chord_ft, chord_bearing_deg=cand.chord_bearing_deg,
        distance_nm=cand.distance_nm, bearing_deg=cand.bearing_deg,
        extents_ft=tuple([25000] * 16), kind=cand.kind,
    )
    hour = lakes_mod.score_lake_hour(bare, 270, 18, 18, LIMITS)
    assert hour.regions == () and hour.region_label is None and hour.hs_open_in is None
    assert hour.level == "unfavorable" and hour.limiting == "waves"
    assert hour.hs_in == pytest.approx(
        lakes_mod.wave.wave_height_in(18, bare.fetch_ft(270, 18)), rel=1e-9
    )


def test_a_row_carries_the_region_columns_and_caps_the_list_at_four():
    points = [
        wave_point(-83.30, 42.55, label=i, fetch_m=[(i + 1) * 800.0] * 16, run_ft=[4000.0] * 8)
        for i in range(6)
    ]
    field = make_wave_field({7: points}, [f"region {i}" for i in range(6)])
    cand = lakes_mod.find_candidate(
        [{"id": 7, "name": "Six Regions", "kind": "lake", "verdict": "clear", "chord_ft": 9000,
          "chord_bearing_deg": 0, "lat": 42.55, "lon": -83.30}],
        7, home_lat=KPTK[0], home_lon=KPTK[1], extents=None, wave_field=field,
    )
    ranked = lakes_mod.rank_lakes(
        [cand], {cand.cell: _uniform_series(10, 12, 270)}, NOON, LIMITS, frozen=False, n_lakes=8
    )
    row = ranked[0].to_row()
    assert row["region"] == "region 0"
    assert [r["label"] for r in row["regions"]] == ["region 0", "region 1", "region 2", "region 3"]
    assert row["hs_open_in"] >= row["hs_in"]
    assert set(row["regions"][0]) == {"label", "hs_in", "run_ft", "lat", "lon"}


def test_with_no_usable_run_anywhere_the_row_still_names_the_calmest_water():
    cand = _big_water()
    hour = lakes_mod.score_lake_hour(cand, 270, 12, 14, Limits(min_run_ft=30000))
    assert hour.level == "unfavorable" and hour.limiting == "run"
    assert hour.region_label == "Sheltered Bay"  # calmest by its own open-water figure
    assert all(r.hs_in is None for r in hour.regions)
    assert hour.run_ft == 4000  # the run it does have, not a zero


def test_distance_is_measured_to_the_best_region_not_to_the_centroid():
    """Lake St. Clair's centroid is 20+ nm from half its bays; the row has to say where to go."""
    cand = _big_water()
    ranked = lakes_mod.rank_lakes(
        [cand], {cand.cell: _uniform_series(18, 18, 270)}, NOON, LIMITS, frozen=False, n_lakes=8
    )
    row = ranked[0].to_row()
    bay = ranked[0].hour.chosen
    assert row["distance_nm"] == round(
        lakes_mod.aero.distance_nm(KPTK[0], KPTK[1], bay.lat, bay.lon), 1
    )
    assert row["distance_nm"] < round(cand.distance_nm, 1)  # the bay is nearer than the middle
    assert row["bearing_deg"] == round(lakes_mod.aero.bearing_deg(KPTK[0], KPTK[1], bay.lat, bay.lon))


def test_a_water_body_whose_bbox_is_out_of_range_is_never_unpacked():
    """The nationwide pack must not be parsed for water in the next state."""
    index = [
        {"id": 7, "name": "Far Water", "kind": "lake", "verdict": "clear", "chord_ft": 9000,
         "chord_bearing_deg": 0, "lat": 45.0, "lon": -85.0, "bbox": [-85.1, 44.9, -84.9, 45.1]}
    ]
    field = make_wave_field(
        {7: [wave_point(-85.0, 45.0, label=0, fetch_m=[600.0] * 16, run_ft=[4000.0] * 8)]}, ["middle"]
    )
    got = lakes_mod.select_candidates(
        index, home_lat=KPTK[0], home_lon=KPTK[1], radius_nm=40, min_run_ft=2000,
        public_access_only=False, extents=None, wave_field=field,
    )
    assert got == []
    assert field._cache == {}  # its points were never read


def test_the_radius_gate_takes_a_water_body_with_any_water_inside_the_circle():
    """A water body that spans the radius is a candidate on its nearest point, not its centroid."""
    index = [
        {"id": 7, "name": "Big Water", "kind": "great_lake", "verdict": "clear", "chord_ft": 80000,
         "chord_bearing_deg": 45, "lat": 42.47, "lon": -83.17}
    ]
    field = make_wave_field(
        {7: [
            wave_point(-83.40, 42.63, label=0, fetch_m=[600.0] * 16, run_ft=[4000.0] * 8),  # 2 nm out
            wave_point(-82.40, 42.10, label=1, fetch_m=[30000.0] * 16, run_ft=[30000.0] * 8),  # 55 nm
        ]},
        ["near end", "far end"],
    )
    kw = {"home_lat": KPTK[0], "home_lon": KPTK[1], "min_run_ft": 2000,
          "public_access_only": False, "extents": None}
    inside = lakes_mod.select_candidates(index, radius_nm=10, wave_field=field, **kw)
    assert [c.name for c in inside] == ["Big Water"]
    assert inside[0].nearest_nm < 10 < inside[0].distance_nm
    # Without the wave field the same water body is out of range on its centroid, as before.
    assert lakes_mod.select_candidates(index, radius_nm=10, **kw) == []


def test_ranking_uses_the_region_distance_too():
    near_bay = _big_water()
    pond = lakes_mod.Candidate(
        id=8, name="Near Pond", lat=42.62, lon=-83.40, verdict="clear", chord_ft=4000,
        chord_bearing_deg=0, distance_nm=3.0, bearing_deg=180, extents_ft=tuple([4000] * 16),
        home=KPTK,
    )
    series = _uniform_series(6, 8, 270)
    ranked = lakes_mod.rank_lakes(
        [near_bay, pond], {near_bay.cell: series, pond.cell: series}, NOON, LIMITS,
        frozen=False, n_lakes=8,
    )
    assert [r.level for r in ranked] == ["favorable", "favorable"]
    assert [r.cand.name for r in ranked] == ["Near Pond", "Big Water"]
    assert ranked[1].distance_nm < ranked[1].cand.distance_nm


def test_a_frozen_home_water_is_still_frozen_with_a_wave_field():
    cand = _big_water()
    ranked = lakes_mod.rank_lakes(
        [cand], {cand.cell: _uniform_series(6, 8, 270)}, NOON, LIMITS, frozen=True, n_lakes=8
    )
    row = ranked[0].to_row()
    assert row["frozen"] is True and row["score"] == "unfavorable" and row["limiting"] == "ice"
    assert row["region"] == "Sheltered Bay"  # the water is still described


def _uniform_series(wind_kt: float, gust_kt: float, wind_dir: float) -> HourlySeries:
    times = [f"2026-09-19T{h:02d}:00" for h in range(24)]
    payload = {
        "elevation": 300.0,
        "hourly": {
            "time": times,
            "wind_speed_10m": [wind_kt] * 24,
            "wind_gusts_10m": [gust_kt] * 24,
            "wind_direction_10m": [wind_dir] * 24,
        },
    }
    return HourlySeries.from_open_meteo(payload, DETROIT)
