"""PLSS parsing. Every example is verbatim from docs/dnr-pages.md or a fixture page."""

from __future__ import annotations

import pytest

from seaplane_pipeline.plss import format_plss, parse_plss

CASES: list[tuple[str, list[dict]]] = [
    # docs/dnr-pages.md section 3, abbreviated form
    ("section 33, T3N, R8E, White Lake township, Oakland county", [{"township": "3N", "range": "8E", "sections": [33]}]),
    ("Section 16, T4N, R7E", [{"township": "4N", "range": "7E", "sections": [16]}]),
    # spelled-out form
    (
        "section 16, town 2 north, range 8 east, Commerce township, Oakland county",
        [{"township": "2N", "range": "8E", "sections": [16]}],
    ),
    (
        "section 8, town 2 north, range 6 east, Brighton township, Livingston county",
        [{"township": "2N", "range": "6E", "sections": [8]}],
    ),
    # multiple sections, both list spellings
    (
        "sections 13 and 14, town 2 north, range 9 east, West Bloomfield township",
        [{"township": "2N", "range": "9E", "sections": [13, 14]}],
    ),
    (
        "sections 4, 5, 8, and 9, town 2 north, range 9 east, West Bloomfield township",
        [{"township": "2N", "range": "9E", "sections": [4, 5, 8, 9]}],
    ),
    (
        "sections 22, 26 and 27, T33N, R1W, Nunda township, Cheboygan county",
        [{"township": "33N", "range": "1W", "sections": [22, 26, 27]}],
    ),
    # singular "section" with a list (Kent, Cedar Lake)
    (
        "section 10, 11, 14, and 15, T10N, R9W, Spencer township, Kent county",
        [{"township": "10N", "range": "9W", "sections": [10, 11, 14, 15]}],
    ),
    # quarter-section fragments are ignored, the section is kept
    (
        "in the west 1/4 of the northwest 1/4 of section 4, town 4 north, range 6 east, Tyrone township",
        [{"township": "4N", "range": "6E", "sections": [4]}],
    ),
    (
        "within the north 1/2 of the northwest 1/4 of the southwest 1/4 of section 35, T3N, R9E",
        [{"township": "3N", "range": "9E", "sections": [35]}],
    ),
    # two T/R groups in one entry
    (
        "section 6, T8N, R9W, Grattan township, and section 31, T9N, R9W, Oakfield township",
        [
            {"township": "8N", "range": "9W", "sections": [6]},
            {"township": "9N", "range": "9W", "sections": [31]},
        ],
    ),
    (
        "sections 1, 2, 3, and 11, T33N, R1W, Nunda township, and sections 34 and 35, T34N, R1W, Walker township",
        [
            {"township": "33N", "range": "1W", "sections": [1, 2, 3, 11]},
            {"township": "34N", "range": "1W", "sections": [34, 35]},
        ],
    ),
    (
        "section 13, town 1 north, range 5 east, Hamburg township, and section 18, town 1 north, range 6 east, Green Oak township",
        [
            {"township": "1N", "range": "5E", "sections": [13]},
            {"township": "1N", "range": "6E", "sections": [18]},
        ],
    ),
    # range before town (Cheboygan, Weber Lake)
    (
        "all within section 31, range 3 west, town 34 north, Mentor township",
        [{"township": "34N", "range": "3W", "sections": [31]}],
    ),
    # section stated after the T/R (Oakland, Squaw lake lagoon)
    (
        "town 5 north, range 10 east, section 29, Oxford township, Oakland county",
        [{"township": "5N", "range": "10E", "sections": [29]}],
    ),
    # parentheticals between sections (Kent, Flat River)
    (
        (
            'in section 24 (upstream limit is the "covered bridge"), sections 25 and 26 '
            "(downstream limit is the STS power dam), town 7 north, range 9 west, Vergennes township"
        ),
        [{"township": "7N", "range": "9W", "sections": [24, 25, 26]}],
    ),
    # T/R with no section at all (Kent, Big Wabasis)
    (
        "On the waters of Big Wabasis lake, T9N, R9W, Oakfield township, Kent county",
        [{"township": "9N", "range": "9W", "sections": []}],
    ),
    # several T/R groups, none with sections (Cheboygan, Black River)
    (
        (
            "town 36 north, range 1 east, Grant township; town 36 north, range 1 east, Aloha township; "
            "town 27 north, range 1 west, Benton township"
        ),
        [
            {"township": "36N", "range": "1E", "sections": []},
            {"township": "36N", "range": "1E", "sections": []},
            {"township": "27N", "range": "1W", "sections": []},
        ],
    ),
    # title-case spelling (Oakland, Sears Lake WC-63-12-001)
    (
        "Section 5 and 8, Town 2 North, Range 7 East, charter township of Milford",
        [{"township": "2N", "range": "7E", "sections": [5, 8]}],
    ),
    # south/west directions (Berrien, St. Joseph river)
    ("within section 25, T7S, R18W, downstream from the dam", [{"township": "7S", "range": "18W", "sections": [25]}]),
    # source typo kept verbatim (Oakland, Seymour Lake: R9W in an east-range county)
    ("sections 27 and 34, T5N, R9W, Brandon township", [{"township": "5N", "range": "9W", "sections": [27, 34]}]),
    # township only, no PLSS
    ("township of Orion, county of Oakland, state of Michigan", []),
    ("sections 1 and 2, Deerfield township, Livingston county", []),
    # quarter-section boundary with no T/R of its own
    ("north of the south line of the northwest 1/4 of the southeast 1/4, section 19", []),
]


@pytest.mark.parametrize(("text", "expected"), CASES)
def test_parse_plss(text: str, expected: list[dict]) -> None:
    assert parse_plss(text) == expected


def test_parse_plss_empty_input() -> None:
    assert parse_plss("") == []
    assert parse_plss("no survey reference here") == []


def test_no_leading_zeros_or_lowercase_directions() -> None:
    entries = parse_plss("section 07, town 04 north, range 09 east")
    assert entries == [{"township": "4N", "range": "9E", "sections": [7]}]


def test_rule_numbers_are_not_mistaken_for_survey_tokens() -> None:
    assert parse_plss("R 281.763.3 - High-speed boating prohibited.") == []


def test_format_plss() -> None:
    assert format_plss({"township": "4N", "range": "7E", "sections": [16, 21]}) == "T4N R7E sections 16, 21"
    assert format_plss({"township": "9N", "range": "9W", "sections": []}) == "T9N R9W"
