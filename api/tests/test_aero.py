"""Crosswind, runway selection, density altitude, and geometry against hand calculations."""
from __future__ import annotations

import math

import pytest

from seaplane_api.briefing import aero

KPTK_RUNWAYS = [{"id": "09R/27L", "heading": 91}, {"id": "18/36", "heading": 179}]

# The real bug: two parallel runways, same heading, different lengths.
KPTK_PARALLEL_RUNWAYS = [
    {"id": "09L/27R", "heading": 88, "length_ft": 5676},
    {"id": "09R/27L", "heading": 88, "length_ft": 6521},
    {"id": "18/36", "heading": 172, "length_ft": 2582},
]


def test_crosswind_hand_calcs():
    # 30 degrees off: half the wind is crosswind, sqrt(3)/2 is headwind.
    xw, hw = aero.wind_components(120, 20, 90)
    assert xw == pytest.approx(10.0, abs=0.01)
    assert hw == pytest.approx(20 * math.sqrt(3) / 2, abs=0.01)

    # Straight down the runway: no crosswind.
    assert aero.crosswind_kt(90, 20, 90) == pytest.approx(0.0, abs=1e-9)
    # Straight across: all of it.
    assert aero.crosswind_kt(180, 20, 90) == pytest.approx(20.0, abs=1e-9)
    # 45 degrees: 0.7071 of the wind, either side of the nose.
    assert aero.crosswind_kt(45, 14, 90) == pytest.approx(14 * math.sqrt(2) / 2, abs=0.01)
    assert aero.crosswind_kt(135, 14, 90) == pytest.approx(14 * math.sqrt(2) / 2, abs=0.01)


def test_runway_ends_expand_both_directions():
    ends = aero.runway_ends(KPTK_RUNWAYS)
    assert [(e.id, e.heading) for e in ends] == [
        ("09R", 91), ("27L", 271), ("18", 179), ("36", 359),
    ]
    assert [e.length_ft for e in ends] == [None, None, None, None]  # older files carry no length_ft


def test_runway_ends_carries_length_ft_to_both_ends_of_a_runway():
    ends = aero.runway_ends(KPTK_PARALLEL_RUNWAYS)
    assert [(e.id, e.length_ft) for e in ends] == [
        ("09L", 5676), ("27R", 5676), ("09R", 6521), ("27L", 6521), ("18", 2582), ("36", 2582),
    ]


def test_best_runway_picks_least_crosswind_then_headwind():
    # Wind from 250: 27L (271 true) has 21 degrees of crosswind and a headwind.
    assert aero.best_runway(KPTK_RUNWAYS, 250, 12).id == "27L"
    # Wind from 090 favours 09R, not its reciprocal.
    assert aero.best_runway(KPTK_RUNWAYS, 90, 12).id == "09R"
    # Wind from 180 is straight down 18.
    assert aero.best_runway(KPTK_RUNWAYS, 180, 12).id == "18"
    # A direct tailwind on 09R must not be chosen over its reciprocal.
    assert aero.best_runway(KPTK_RUNWAYS, 271, 12).id == "27L"


def test_best_runway_with_no_runways_or_no_wind():
    assert aero.best_runway([], 250, 12) is None
    # Calm wind, no length data at all (an older settings file): the first end listed wins, exactly
    # as before `length_ft` existed.
    assert aero.best_runway(KPTK_RUNWAYS, 250, 0).id == "09R"


def test_best_runway_breaks_a_parallel_tie_toward_the_longer_runway():
    """The bug this feature fixes: 09L/27R and 09R/27L share a heading, so wind straight down it
    gives every end zero crosswind and the same headwind/tailwind. The longer physical runway
    (09R/27L, 6521 ft) must win over the one listed first (09L/27R, 5676 ft)."""
    assert aero.best_runway(KPTK_PARALLEL_RUNWAYS, 88, 10).id == "09R"
    assert aero.best_runway(KPTK_PARALLEL_RUNWAYS, 268, 10).id == "27L"


def test_best_runway_with_all_lengths_unknown_keeps_first_listed_on_a_parallel_tie():
    unknown = [{"id": "09L/27R", "heading": 88}, {"id": "09R/27L", "heading": 88}]
    assert aero.best_runway(unknown, 88, 10).id == "09L"


def test_best_runway_a_known_length_beats_an_unknown_one_on_a_tie():
    mixed = [{"id": "09L/27R", "heading": 88}, {"id": "09R/27L", "heading": 88, "length_ft": 6521}]
    assert aero.best_runway(mixed, 88, 10).id == "09R"


def test_best_runway_calm_wind_prefers_the_longest_known_runway():
    assert aero.best_runway(KPTK_PARALLEL_RUNWAYS, 0, 0).id == "09R"
    # A length tie between a runway's own two ends goes to the first-named one.
    same_length = [{"id": "09L/27R", "heading": 88, "length_ft": 5000}, {"id": "18/36", "heading": 172, "length_ft": 5000}]
    assert aero.best_runway(same_length, 999, 0).id == "09L"


def test_pressure_and_density_altitude_hand_calcs():
    # Standard day at 981 ft: PA = field elevation, ISA = 15 - 2*0.981 = 13.04 C, DA = PA.
    assert aero.pressure_altitude_ft(981, 29.92) == pytest.approx(981.0)
    assert aero.density_altitude_ft(981, 29.92, 15 - 2 * 0.981) == pytest.approx(981.0, abs=0.01)

    # 30.13 inHg lowers PA by 210 ft; 30 C then adds 120 ft per degree above ISA.
    pa = aero.pressure_altitude_ft(981, 30.13)
    assert pa == pytest.approx(771.0, abs=0.01)
    isa = 15 - 2 * pa / 1000
    assert aero.density_altitude_ft(981, 30.13, 30) == pytest.approx(pa + 120 * (30 - isa), abs=0.01)

    # A hot, low-pressure day at Pontiac is a few thousand feet of density altitude.
    da = aero.density_altitude_ft(981, 29.70, 32)
    assert 3200 < da < 3800


def test_unit_helpers():
    assert aero.f_to_c(32) == pytest.approx(0.0)
    assert aero.c_to_f(100) == pytest.approx(212.0)
    assert aero.hpa_to_inhg(1013.25) == pytest.approx(29.921, abs=0.001)
    assert aero.hpa_to_inhg(1020.4) == pytest.approx(30.13, abs=0.005)


def test_distance_and_bearing():
    # One minute of latitude is one nautical mile.
    assert aero.distance_nm(42.0, -83.0, 42.0 + 1 / 60, -83.0) == pytest.approx(1.0, abs=0.001)
    assert aero.bearing_deg(42.0, -83.0, 43.0, -83.0) == pytest.approx(0.0, abs=0.01)
    assert aero.bearing_deg(42.0, -83.0, 42.0, -82.0) == pytest.approx(89.7, abs=0.5)
    # KPTK to Cass Lake, from the real index entry: a few miles to the southeast.
    d = aero.distance_nm(42.6655, -83.4187, 42.5977, -83.3423)
    assert 4.0 < d < 6.0


def test_extent_bin_matches_the_data_contract():
    assert aero.extent_bin(0) == 0
    assert aero.extent_bin(90) == 4
    assert aero.extent_bin(22.5) == 1
    assert aero.extent_bin(11.2) == 0
    assert aero.extent_bin(11.3) == 1
    assert aero.extent_bin(350) == 0  # wraps
    assert aero.extent_bin(359.9) == 0


def test_compass_point():
    assert aero.compass_point(0) == "N"
    assert aero.compass_point(135) == "SE"
    assert aero.compass_point(271) == "W"
