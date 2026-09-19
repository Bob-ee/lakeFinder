"""SPM 1984 wave model against the design doc's worked values and the fully developed cap."""
from __future__ import annotations

import math

import pytest

from seaplane_api.briefing import wave


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
