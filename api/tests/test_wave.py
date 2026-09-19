"""SPM 1984 wave model and the region aggregation, against the design doc and the shared fixtures.

`rules/fixtures/waves.json` and `wave_points.sample.*` are the agreement between this service and the
JS in `rules/waves/` that the client runs. They are read from the repo, never copied: both sides have
to produce the same numbers from the same bytes or the map and the briefing will disagree.
"""
from __future__ import annotations

import math

import pytest

from seaplane_api.briefing import wave

from .conftest import sample_wave_field, shared

FIXTURE = shared("waves.json")


def test_worked_values_from_the_design_doc():
    # 20 kt over 5 km of fetch is about 0.45 m; over 40 km (Lake St. Clair) about 1.3 m.
    assert wave.significant_wave_height_m(20, 5_000) == pytest.approx(0.45, abs=0.02)
    assert wave.significant_wave_height_m(20, 40_000) == pytest.approx(1.28, abs=0.05)


def test_wind_stress_factor():
    # UA = 0.71 * U10^1.23 with U10 = 20 kt = 10.288 m/s
    assert wave.wind_stress_factor(20) == pytest.approx(0.71 * (20 * 0.5144444) ** 1.23, rel=1e-9)
    assert wave.wind_stress_factor(0) == 0.0


def test_fully_developed_cap_binds_only_at_absurd_fetch():
    ua = wave.wind_stress_factor(20)
    cap = 2.433e-1 * ua**2 / wave.G
    assert wave.significant_wave_height_m(20, 10_000_000) == pytest.approx(cap, rel=1e-9)
    # ...and it does not clip the doc's own worked example, which the doc's 2.482e-2 would have.
    assert wave.significant_wave_height_m(20, 5_000) < cap


def test_hs_scales_with_sqrt_fetch():
    a = wave.significant_wave_height_m(15, 2_000)
    b = wave.significant_wave_height_m(15, 8_000)
    assert b / a == pytest.approx(2.0, rel=1e-6)


def test_period_is_reasonable_and_capped():
    tp = wave.peak_period_s(20, 5_000)
    assert 2.0 < tp < 3.5
    ua = wave.wind_stress_factor(20)
    assert wave.peak_period_s(20, 10_000_000) == pytest.approx(8.134 * ua / wave.G, rel=1e-9)


def test_small_lake_stays_calm_in_wind_that_ruins_a_big_one():
    small = wave.wave_height_in(18, 2_500)  # 2,500 ft of fetch
    big = wave.wave_height_in(18, 25_000)
    assert small < 8 < big


def test_zero_wind_and_zero_fetch():
    assert wave.significant_wave_height_m(0, 5_000) == 0.0
    assert wave.significant_wave_height_m(20, 0) == 0.0
    assert wave.peak_period_s(0, 5_000) == 0.0


def test_nomogram_sanity_points():
    """Three SPM points, checked for order of magnitude rather than to the millimetre."""
    for wind_kt, fetch_m, low, high in ((10, 10_000, 0.25, 0.45), (30, 5_000, 0.6, 0.9), (40, 20_000, 1.8, 2.8)):
        hs = wave.significant_wave_height_m(wind_kt, fetch_m)
        assert low < hs < high, (wind_kt, fetch_m, hs)


def test_inches_helper_converts_from_feet_of_fetch():
    hs_m = wave.significant_wave_height_m(20, 5_000 / 0.3048 * 0.3048)
    assert wave.wave_height_in(20, 5_000 / 0.3048) == pytest.approx(hs_m * 39.3700787, rel=1e-9)


def test_steep_chop_helper_is_defined_but_not_wired_in():
    assert wave.steep_chop(hs_in=8, tp_s=1.5) is True
    assert wave.steep_chop(hs_in=4, tp_s=1.5) is False
    assert not math.isnan(wave.peak_period_s(12, 1_000))


# --- the shared fixtures ---------------------------------------------------------------------


@pytest.mark.parametrize("case", FIXTURE["wave"], ids=lambda c: f"{c['wind_kt']}kt/{c['fetch_m']}m/{c['depth_m']}")
def test_every_shared_wave_case_to_the_printed_precision(case):
    hs_m, tp_s = wave.spm_wave(case["wind_kt"], case["fetch_m"], case["depth_m"])
    assert round(hs_m, 4) == case["hs_m"]
    assert round(tp_s, 3) == case["tp_s"]


def test_shallow_water_is_lower_than_deep_water_at_the_same_fetch():
    """The whole reason depth is in the contract: 25 km of fetch over 1 m is not open-lake chop."""
    deep, _ = wave.spm_wave(15, 25_000)
    shallow, _ = wave.spm_wave(15, 25_000, 1.0)
    assert shallow < deep


def test_the_depth_free_form_is_the_wave_model_the_service_already_had():
    """`spm_wave(..., None)` has to be the old deep-water call, or today's numbers would move."""
    for wind_kt in (1, 5, 12, 18, 25, 40):
        for fetch_m in (100, 2_500, 12_000, 40_000, 370_000, 10_000_000):
            hs, tp = wave.spm_wave(wind_kt, fetch_m)
            assert hs == pytest.approx(wave.significant_wave_height_m(wind_kt, fetch_m), rel=1e-12)
            assert tp == pytest.approx(wave.peak_period_s(wind_kt, fetch_m), rel=1e-12)


def test_zero_wind_or_fetch_is_flat_calm_with_a_depth_too():
    assert wave.spm_wave(0, 5_000, 2.0) == (0.0, 0.0)
    assert wave.spm_wave(12, 0, 2.0) == (0.0, 0.0)


@pytest.mark.parametrize("case", FIXTURE["bins"], ids=lambda c: str(c["deg"]))
def test_wind_bins_round_half_up_not_to_even(case):
    assert wave.wind_bin(case["deg"]) == case["bin"]


def test_wind_bin_wraps_and_ignores_negative_or_oversized_bearings():
    assert wave.wind_bin(360) == 0
    assert wave.wind_bin(-10) == wave.wind_bin(350)
    assert wave.wind_bin(722.5) == wave.wind_bin(2.5)


@pytest.mark.parametrize(
    "case", FIXTURE["regions"], ids=lambda c: f"lake{c['lake']}/{c['wind_dir']}@{c['wind_kt']}/{c['min_run_ft']}"
)
def test_every_shared_region_case_including_order(case):
    field = sample_wave_field()
    points = field.points(case["lake"])
    assert points, case["lake"]
    got = wave.regions(
        points,
        field.labels,
        wind_dir_deg=case["wind_dir"],
        wind_kt=case["wind_kt"],
        min_run_ft=case["min_run_ft"],
    )
    assert wave.wind_bin(case["wind_dir"]) == case["bin"]
    assert [r.label for r in got] == [r["label"] for r in case["regions"]]
    for mine, theirs in zip(got, case["regions"], strict=True):
        assert (mine.hs_in, mine.run_ft, mine.point) == (theirs["hs_in"], theirs["run_ft"], theirs["point"])
        assert (mine.hs_all_in, mine.n_points, mine.n_usable) == (
            theirs["hs_all_in"], theirs["n_points"], theirs["n_usable"],
        )


def test_the_calmest_usable_region_is_the_best_one_and_the_roughest_is_the_open_water():
    field = sample_wave_field()
    rows = wave.regions(
        field.points("111"), field.labels, wind_dir_deg=225, wind_kt=12, min_run_ft=2000
    )
    assert wave.best_region(rows).label == "Big Muscamoot Bay"
    assert wave.open_water_in(rows) == max(r.hs_all_in for r in rows) == 16


def test_a_region_with_no_usable_run_keeps_its_place_and_its_position():
    """min_run_ft above every run: both regions survive with null numbers, and still have a point."""
    field = sample_wave_field()
    rows = wave.regions(field.points("222"), field.labels, wind_dir_deg=270, wind_kt=14, min_run_ft=5000)
    assert [r.hs_in for r in rows] == [None, None]
    assert wave.best_region(rows) is None
    assert all(r.lat and r.lon for r in rows)
    assert all(r.run_all_ft > 0 for r in rows)  # the ungated run is still there for the row


def test_regions_survive_a_label_index_the_pack_does_not_name():
    class P:
        lat, lon, depth_m, label = 42.0, -83.0, None, 9
        fetch_m = tuple([1000.0] * 16)
        run_ft = tuple([3000.0] * 8)

    rows = wave.regions([P()], ["only"], wind_dir_deg=0, wind_kt=10, min_run_ft=2000)
    assert rows[0].label == "9"


def test_inches_round_half_up():
    assert wave.hs_inches(0.0254 * 2.5) == 3  # round() would give 2
    assert wave.hs_inches(0.0) == 0
