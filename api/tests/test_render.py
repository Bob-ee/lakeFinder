"""The summary lines, which is where the wave field becomes a sentence a pilot reads."""
from __future__ import annotations

from seaplane_api.briefing import render


def _row(name, hs_in, run_ft=4100, region=None, hs_open_in=None, frozen=False) -> dict:
    return {
        "name": name, "hs_in": hs_in, "run_ft": run_ft, "region": region,
        "hs_open_in": hs_open_in, "frozen": frozen,
    }


def test_a_row_with_regions_names_the_place_and_the_open_water():
    rows = [
        _row("Lake St. Clair", 2, region="Big Muscamoot Bay", hs_open_in=14),
        _row("Cass Lake", 3, region="west end", hs_open_in=7),
        _row("Union Lake", 5, region="north end", hs_open_in=5),
    ]
    assert render.lakes_phrase(rows) == (
        "Best water: Lake St. Clair, Big Muscamoot Bay 2 in (open lake 14 in); "
        "Cass Lake west end 3 in; Union Lake north end 5 in."
    )


def test_a_row_without_a_wave_field_reads_exactly_as_it_always_has():
    rows = [_row("Cass Lake", 6), _row("Orchard Lake", 5)]
    assert render.lakes_phrase(rows) == (
        "Best water: Cass Lake 6 in chop with 4,100 ft run into the wind; Orchard Lake 5 in."
    )


def test_the_open_water_figure_is_left_off_when_it_says_nothing_new():
    rows = [_row("Round Lake", 4, region="middle", hs_open_in=4)]
    assert render.lakes_phrase(rows) == "Best water: Round Lake, middle 4 in."


def test_frozen_water_still_wins_over_the_region_text():
    rows = [_row("Cass Lake", 2, region="west end", hs_open_in=9, frozen=True)]
    assert render.lakes_phrase(rows) == "Best water: Cass Lake likely frozen, verify."


def test_the_home_water_sentence_lists_the_calm_ends_then_the_open_lake():
    home = {
        "name": "Lake St. Clair",
        "hs_open_in": 14,
        "regions": [
            {"label": "Big Muscamoot Bay", "hs_in": 2},
            {"label": "Anchor Bay", "hs_in": 5},
            {"label": "middle", "hs_in": 11},
            {"label": "North Channel", "hs_in": None},
        ],
    }
    assert render.home_water_phrase(home, limit=2) == "Lake St. Clair: Big Muscamoot Bay 2 in, Anchor Bay 5 in, open lake 14 in."
    assert render.home_water_phrase(home).startswith("Lake St. Clair: Big Muscamoot Bay 2 in, Anchor Bay 5 in, middle 11 in")


def test_no_home_water_and_no_regions_produce_no_sentence():
    assert render.home_water_phrase(None) is None
    assert render.home_water_phrase({"name": "Plain Lake", "regions": [], "hs_open_in": None}) is None


def test_a_home_water_with_nothing_usable_still_reports_the_open_water():
    home = {"name": "Lake St. Clair", "hs_open_in": 20, "regions": [{"label": "Anchor Bay", "hs_in": None}]}
    assert render.home_water_phrase(home) == "Lake St. Clair: open lake 20 in."


def test_the_summary_never_says_legal_safe_or_go():
    text = render.lakes_phrase([_row("Lake St. Clair", 2, region="Anchor Bay", hs_open_in=14)]).lower()
    assert not any(word in text for word in ("legal", "safe", "go/no-go"))
