"""Name normalization (shared with rules/engine/index.js) and multi-lake splitting."""

from __future__ import annotations

import json

import pytest

from seaplane_pipeline.names import descriptor_segments, normalize_name, split_multi_lake_names

# Cases taken from docs/data-contract.md "Name normalization". Kept here so the
# suite is meaningful even if the shared JS fixture is unavailable.
CONTRACT_CASES = [
    ("Lake Angelus", "angelus"),
    ("Big School Lot Lake", "big school lot"),
    ("Mud Lake", "mud"),
    ("Lake", "lake"),
    ("Pond", "pond"),
    ("St. Clair", "saint clair"),
    ("Lk. St. Clair", "saint clair"),
    ("N. Long Lake", "north long"),
    ("S. Long Lake", "south long"),
    ("Upr Herring Lake", "upper herring"),
    ("Lwr Herring Lake", "lower herring"),
    ("Mt. Clemens Pond", "mount clemens"),
    ("Twp Line Lake", "township line"),
    ("Crooked Lake (Big)", "crooked big"),
    ("Café Lake", "cafe"),
    ("O'Brien Lake", "o brien"),
    ("  Extra   Spaces   Lake  ", "extra spaces"),
    ("Lake of the Clouds", "of the clouds"),
    ("Middle Lake", "middle"),
    ("Upper Straits Lake", "upper straits"),
]


@pytest.mark.parametrize(("raw", "norm"), CONTRACT_CASES)
def test_normalize_name_contract_cases(raw: str, norm: str) -> None:
    assert normalize_name(raw) == norm


def test_normalize_name_handles_none_and_empty() -> None:
    assert normalize_name(None) == ""
    assert normalize_name("") == ""
    assert normalize_name("   ") == ""


def test_shared_fixture_matches_the_js_engine(repo_root) -> None:
    """rules/fixtures/names.json is the contract between Python and JS."""
    path = repo_root / "rules" / "fixtures" / "names.json"
    if not path.exists():  # written by the rules-engine agent
        pytest.skip("rules/fixtures/names.json not present yet")
    cases = json.loads(path.read_text(encoding="utf-8"))
    assert cases, "shared name fixture is empty"
    mismatches = [
        (case["raw"], normalize_name(case["raw"]), case["norm"])
        for case in cases
        if normalize_name(case["raw"]) != case["norm"]
    ]
    assert not mismatches, f"normalize_name disagrees with the shared fixture: {mismatches}"


SPLIT_CASES = [
    (
        "BIG AND LITTLE SCHOOL LOT LAKES AND CONNECTING CHANNEL",
        ["Big School Lot Lake", "Little School Lot Lake"],
    ),
    ("UPPER AND LOWER PETTIBONE LAKES", ["Upper Pettibone Lake", "Lower Pettibone Lake"]),
    (
        "STRINGY LAKES (TAN, CLEAR, SQUAW, SECOND, SPRING, CEDAR, AND LONG)",
        [
            "Tan Lake",
            "Clear Lake",
            "Squaw Lake",
            "Second Lake",
            "Spring Lake",
            "Cedar Lake",
            "Long Lake",
        ],
    ),
    ("INDIAN, PATTERSON AND BAIN LAKES", ["Indian Lake", "Patterson Lake", "Bain Lake"]),
    ("MASTON AND MUSKELLONGE LAKES, CHANNEL CONNECTING", ["Maston Lake", "Muskellonge Lake"]),
    (
        "EAST CROOKED LAKE, WEST CROOKED LAKE, AND CLIFFORD LAKE; CHANNELS AND CANALS",
        ["East Crooked Lake", "West Crooked Lake", "Clifford Lake"],
    ),
    ("FISH LAKE AND PINE LAKE", ["Fish Lake", "Pine Lake"]),
    ("HUFF LAKE & LAKE LOUISE, CHANNEL CONNECTING", ["Huff Lake", "Lake Louise"]),
    ("CANAL CONNECTED TO MARL LAKE", ["Marl Lake"]),
    ("CHANNEL CONNECTING TAMARACK LAKE TO HURON RIVER", ["Tamarack Lake"]),
    ("CEDAR ISLAND LAKE, CERTAIN BAYS", ["Cedar Island Lake"]),
    ("BUCKHORN LAKE, NORTH BASIN", ["Buckhorn Lake"]),
    ("MIDDLE STRAITS LAKE, WEST BLOOMFIELD TOWNSHIP", ["Middle Straits Lake"]),
    ("ORCHARD LAKE, PART ADJOINING PUBLIC ACCESS SITE", ["Orchard Lake"]),
    ("WOLVERINE LAKE, ALL ARTIFICIAL CHANNELS AND CANALS CONNECTED TO", ["Wolverine Lake"]),
    ("HI-LAND LAKE AND CONNECTING CANALS AND CHANNELS", ["Hi-Land Lake"]),
    ("MUD BAY, PORTAGE LAKE", ["Portage Lake"]),
    ("BLACK RIVER FROM MEYERS CREEK TO BLACK LAKE", ["Black River"]),
    ("LAKE OAKLAND CANAL", ["Lake Oakland"]),
    ("TWIN LAKES", ["Twin Lake"]),
    ("SQUARE LAKE", ["Square Lake"]),
    ("LAKE SHAN-GRI-LA", ["Lake Shan-Gri-La"]),
    ("DOG LAKE FLOODING", ["Dog Lake Flooding"]),
]


@pytest.mark.parametrize(("header", "expected"), SPLIT_CASES)
def test_split_multi_lake_names(header: str, expected: list[str]) -> None:
    assert split_multi_lake_names(header) == expected


def test_split_keeps_every_name_normalizable() -> None:
    for header, _ in SPLIT_CASES:
        for name in split_multi_lake_names(header):
            assert normalize_name(name), f"{header} -> {name!r} normalizes to nothing"


def test_descriptor_segments_feed_zone_descriptions() -> None:
    assert descriptor_segments("CEDAR ISLAND LAKE, CERTAIN BAYS") == ["Certain Bays"]
    assert descriptor_segments("BUCKHORN LAKE, NORTH BASIN") == ["North Basin"]
    assert descriptor_segments("ORCHARD LAKE") == []
