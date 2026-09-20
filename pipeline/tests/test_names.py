"""Name normalization (shared with rules/engine/index.js) and multi-lake splitting."""

from __future__ import annotations

import json

import pytest

from seaplane_pipeline.names import (
    connecting_structure,
    descriptor_segments,
    is_generic_name,
    normalize_name,
    split_multi_lake_names,
)

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
    # A segment headed by a part word is a part of the lake before it, even when a waterbody word
    # sits inside it ("creek", "river"): it is Lake Macatawa's bay, not a waterbody of its own.
    ("LAKE MACATAWA, PINE CREEK BAY", ["Lake Macatawa"]),
    ("CASS LAKE, GERUNDEGUT BAY AND CANALS AND CHANNELS", ["Cass Lake"]),
    ("TORCH RIVER AND TORCH LAKE ADJACENT TO ITS MOUTH", ["Torch River", "Torch Lake Adjacent to Its Mouth"]),
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
    assert descriptor_segments("LAKE MACATAWA, PINE CREEK BAY") == ["Pine Creek Bay"]
    assert descriptor_segments("ORCHARD LAKE") == []


#: (header or clause text, the structure phrase it names *only*, or None)
CONNECTING_STRUCTURE_CASES = [
    # names only the structure: the rule covers the channel, not the lakes it joins
    ("CHANNEL CONNECTING BLACK LAKE AND RAWSON LAKE", "the channel connecting Black Lake and Rawson Lake"),
    ("CHANNEL CONNECTING PAINTER AND JUNO LAKES", "the channel connecting Painter and Juno Lakes"),
    ("CANAL CONNECTED TO DEWEY LAKE", "the canal connected to Dewey Lake"),
    ("CANALS CONNECTED TO JUNO LAKE", "the canals connected to Juno Lake"),
    ("CHANNEL CONNECTED TO PATTERSON LAKE", "the channel connected to Patterson Lake"),
    ("CHANNEL CONNECTING TAMARACK LAKE TO HURON RIVER", "the channel connecting Tamarack Lake to Huron River"),
    ("Channel connecting Intermediate lake to Hanley lake.",
     "the channel connecting Intermediate Lake to Hanley Lake"),
    ("Channel connecting Ellsworth lake to St. Clair lake.",
     "the channel connecting Ellsworth Lake to St. Clair Lake"),
    # names the lake itself as well as its channels: stays a rule about the lake
    ("BIG AND LITTLE SCHOOL LOT LAKES AND CONNECTING CHANNEL", None),
    ("MASTON AND MUSKELLONGE LAKES, CHANNEL CONNECTING", None),
    ("WOLVERINE LAKE, ALL ARTIFICIAL CHANNELS AND CANALS CONNECTED TO", None),
    ("HI-LAND LAKE AND CONNECTING CANALS AND CHANNELS", None),
    ("HUFF LAKE & LAKE LOUISE, CHANNEL CONNECTING", None),
    ("LAKE OAKLAND CANAL", None),
    # a river or stream named outright is the water body, not a structure
    ("Clam river from Torch lake to Clam lake.", None),
    ("BLACK RIVER FROM MEYERS CREEK TO BLACK LAKE", None),
    # the structure never resolved onto its lakes, so the name is still the structure
    ("CHANNEL BETWEEN SYLVAN AND EMERALD LAKES", None),
    ("CHANNEL FROM BEAR LAKE TO MUSKEGON LAKE", None),
    ("CHANNELS", None),
    ("SQUARE LAKE", None),
    ("", None),
]


@pytest.mark.parametrize(("raw", "phrase"), CONNECTING_STRUCTURE_CASES)
def test_connecting_structure(raw: str, phrase: str | None) -> None:
    assert connecting_structure(raw) == phrase


def test_connecting_structure_reads_as_a_noun_phrase() -> None:
    """It is substituted into the engine's "... only in {scope_description}; ..." notes."""
    for _raw, phrase in CONNECTING_STRUCTURE_CASES:
        if phrase is None:
            continue
        assert phrase[0].islower(), phrase          # mid-sentence, so no leading capital
        assert not phrase.endswith("."), phrase     # the note supplies its own punctuation
        assert "  " not in phrase, phrase
        assert phrase.startswith(("the ", "all ")), phrase


def test_connecting_structure_does_not_fire_on_a_name_it_could_not_resolve() -> None:
    """The guard: if the structure word survives into the name, nothing was resolved onto a lake."""
    assert split_multi_lake_names("CHANNEL BETWEEN SYLVAN AND EMERALD LAKES") == [
        "Channel Between Sylvan and Emerald Lake"
    ]
    assert connecting_structure("CHANNEL BETWEEN SYLVAN AND EMERALD LAKES") is None


@pytest.mark.parametrize(
    ("name", "generic"),
    [
        ("Rivers", True),
        ("Channels", True),
        ("Connecting", True),
        ("RIVERS AND CHANNELS", True),
        ("Waters", True),
        ("", True),
        ("Clam River", False),
        ("Lake 16", False),            # a number identifies it
        ("Unnamed Lake", False),       # the DNR's own name for it
        ("Outlet Lake", False),        # "outlet" is part of a real name, not a category
        ("Grand Traverse Bay", False),
    ],
)
def test_is_generic_name(name: str, generic: bool) -> None:
    assert is_generic_name(name) is generic
