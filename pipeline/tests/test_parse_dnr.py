"""The DNR parser, its helpers, the LLM second pass, and the `parse-dnr` stage."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import pytest

from seaplane_pipeline import dnr_fetch, llm_extract, parse_dnr
from seaplane_pipeline.config import Config
from seaplane_pipeline.restriction import Restriction, compute_restriction_id

COUNTIES = {
    "oakland": "Oakland",
    "cheboygan": "Cheboygan",
    "kent": "Kent",
    "livingston": "Livingston",
    "missaukee": "Missaukee",
}
SOURCE_URL = "https://example/{slug}"
FETCHED_AT = "2026-09-15T18:00:00Z"


def parse_fixture(fixtures_dir: Path, slug: str) -> list[Restriction]:
    html = (fixtures_dir / "dnr_pages" / f"{slug}.html").read_text(encoding="utf-8")
    return parse_dnr.parse_county_page(html, COUNTIES[slug], SOURCE_URL.format(slug=slug), FETCHED_AT)


@pytest.fixture(scope="module")
def parsed(request) -> dict[str, list[Restriction]]:
    fixtures = Path(request.config.rootdir) / "tests" / "fixtures"
    return {slug: parse_fixture(fixtures, slug) for slug in COUNTIES}


# ---------------------------------------------------------------------------
# Hand-checked expected records
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("slug", sorted(COUNTIES))
def test_expected_records(slug: str, parsed, fixtures_dir) -> None:
    path = fixtures_dir / "dnr_expected" / f"{slug}.json"
    cases = json.loads(path.read_text(encoding="utf-8"))
    records = parsed[slug]
    for case in cases:
        match, expect = case["match"], case["expect"]
        candidates = [
            r for r in records if all(getattr(r, key) == value for key, value in match.items())
        ]
        assert candidates, f"{slug}: no record matching {match}"
        problems = []
        for record in candidates:
            actual = record.to_jsonl_dict()
            diff = {k: (actual[k], v) for k, v in expect.items() if actual[k] != v}
            if not diff:
                break
            problems.append(diff)
        else:
            pytest.fail(f"{slug}: {match} fields differ (actual, expected): {problems}")


def test_oakland_expectations_cover_the_required_templates(fixtures_dir) -> None:
    cases = json.loads((fixtures_dir / "dnr_expected" / "oakland.json").read_text(encoding="utf-8"))
    assert len(cases) >= 12
    types = {case["expect"]["restriction_type"] for case in cases}
    assert {
        "no_high_speed",
        "high_speed_hours",
        "speed_limit",
        "no_motorboats",
        "slow_no_wake",
        "shore_buffer",
        "not_applicable",
        "other",
    } <= types


# ---------------------------------------------------------------------------
# Whole-page smoke tests
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("slug", sorted(COUNTIES))
def test_every_entry_with_a_rule_number_yields_a_record(slug: str, parsed, fixtures_dir) -> None:
    html = (fixtures_dir / "dnr_pages" / f"{slug}.html").read_text(encoding="utf-8")
    node = parse_dnr.field_content(html)
    entries = parse_dnr.segment_entries(parse_dnr.iter_blocks(node)) if node else []
    with_rule = [e for e in entries if e.rule_id]
    covered = {r.rule_id for r in parsed[slug]}
    missing = sorted(e.header[:70] for e in with_rule if e.rule_id not in covered)
    assert not missing, f"{slug}: entries produced no record: {missing}"


def test_missaukee_has_no_controls_and_no_error(parsed) -> None:
    assert parsed["missaukee"] == []


def test_every_record_is_contract_shaped(parsed) -> None:
    for slug, records in parsed.items():
        for record in records:
            row = record.to_jsonl_dict()
            assert len(row["restriction_id"]) == 12
            assert row["county"] == COUNTIES[slug]
            assert row["lake_name_raw"] and row["lake_name_norm"]
            assert row["source_url"] == SOURCE_URL.format(slug=slug)
            assert row["fetched_at"] == FETCHED_AT
            assert row["parser"] == "regex"
            assert row["scope"] in ("lakewide", "zone")
            assert (row["scope_description"] is not None) == (row["scope"] == "zone")
            if row["restriction_type"] == "speed_limit":
                assert row["speed_mph"] is not None
            if row["hours"]:
                assert row["hours"]["text"]
            assert "lake_name_group" not in row


def test_restriction_ids_are_unique_per_page(parsed) -> None:
    for slug, records in parsed.items():
        ids = [r.restriction_id for r in records]
        assert len(ids) == len(set(ids)), f"{slug}: duplicate restriction_id"


def test_needs_review_fraction_is_low(parsed) -> None:
    total = sum(len(v) for v in parsed.values())
    flagged = sum(1 for records in parsed.values() for r in records if r.needs_review)
    fraction = flagged / total
    print(f"\nneeds_review across fixture pages: {flagged}/{total} = {fraction:.1%}")
    assert total > 150
    assert fraction < 0.35, f"needs_review fraction too high: {fraction:.1%}"


def test_rescinded_entries_are_kept_but_inactive(parsed) -> None:
    rescinded = [r for records in parsed.values() for r in records if r.status == "rescinded"]
    assert rescinded, "fixtures include rescinded entries"
    for record in rescinded:
        assert record.restriction_type == "other"
        assert record.needs_review is False
        assert record.lake_name_raw not in ("", None)


def test_multi_lake_headers_become_one_record_each(parsed) -> None:
    stringy = [r for r in parsed["oakland"] if r.rule_id == "R 281.763.58"]
    assert {r.lake_name_raw for r in stringy} == {
        "Tan Lake",
        "Clear Lake",
        "Squaw Lake",
        "Second Lake",
        "Spring Lake",
        "Cedar Lake",
        "Long Lake",
    }
    assert all(r.lake_name_group.upper().startswith("STRINGY LAKES") for r in stringy)


def test_one_rule_can_decompose_into_several_clauses(parsed) -> None:
    cass = [r for r in parsed["oakland"] if r.rule_id == "R 281.763.36"]
    assert [r.clause for r in cass] == ["(a)", "(b)", "(c)"]
    assert [r.restriction_type for r in cass] == ["high_speed_hours", "speed_limit", "slow_no_wake"]
    assert all(r.raw_text == cass[0].raw_text for r in cass)


def test_high_speed_and_towing_clauses_merge_into_one_record(parsed) -> None:
    school_lot = [r for r in parsed["oakland"] if r.rule_id == "R 281.763.3"]
    assert len(school_lot) == 2  # two lakes, one restriction each
    assert {r.restriction_type for r in school_lot} == {"no_high_speed"}
    assert all(r.clause is None for r in school_lot)


def test_towing_only_clause_stays_no_towing(parsed) -> None:
    silver = [r for r in parsed["cheboygan"] if r.rule_id == "R 281.716.1"]
    assert [r.restriction_type for r in silver] == ["no_towing"]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("BIG LAKE - R281.763.3 - High-speed boating prohibited.", "R 281.763.3"),
        ("MIDDLE STRAITS LAKE - R 281.763.39 - Slow-no wake speed.", "R 281.763.39"),
        ("CEDAR ISLAND LAKE, CERTAIN BAYS - R281.763.18. - Slow-no wake speed.", "R 281.763.18"),
        ("PONTIAC LAKE - R281.763.12- Speed restriction.", "R 281.763.12"),
        ("COWAN LAKE - R281.741.6.- High-speed boating prohibited.", "R 281.741.6"),
        ("CHALMERS LAKE - WC-63-24-002 - Slow-no wake speed", "WC-63-24-002"),
        ("APPLETON LAKE - WC - 47 - 01 - 001 - Hours for high speed boating", "WC-47-01-001"),
        ("ST JOSEPH RIVER - WC 11-96-001 - Boating prohibited", "WC-11-96-001"),
        ("SOMETHING - WC - 80-00-001 - Slow-no wake", "WC-80-00-001"),
        ("NO RULE NUMBER HERE", None),
    ],
)
def test_parse_rule_id(text: str, expected: str | None) -> None:
    assert parse_dnr.parse_rule_id(text)[0] == expected


def test_split_clauses_letters() -> None:
    preamble, clauses = parse_dnr.split_clauses(
        [
            "36. On the waters of Cass lake, Oakland county, it is unlawful:",
            "(a) Between the hours of 9:00 p.m. and 6:30 a.m. of the following day to operate a vessel at high speed.",
            "(b) At any time to operate a vessel in excess of 50 miles per hour (80 kilometers per hour).",
            "(c) To operate a vessel in excess of a slow-no wake speed in the northerly tip of Coles bay.",
        ]
    )
    assert preamble.startswith("On the waters of Cass lake")
    assert [c.label for c in clauses] == ["(a)", "(b)", "(c)"]


def test_split_clauses_nested_romans_stay_with_their_parent() -> None:
    _preamble, clauses = parse_dnr.split_clauses(
        [
            "2. On the waters of the Indian river, Cheboygan county:",
            "(a) Between day beacons 25 and 40, an operator of a vessel shall not:",
            "(i) Operate such vessel at high speed.",
            "(ii) Have in tow a person on water skis.",
            "(b) Between day beacons 40 and 57, an operator shall not exceed a slow-no wake speed.",
        ]
    )
    assert [c.label for c in clauses] == ["(a)", "(b)"]
    assert "(i)" in clauses[0].text and "(ii)" in clauses[0].text


def test_split_clauses_numeric_subrules() -> None:
    _preamble, clauses = parse_dnr.split_clauses(
        [
            "9. (1) On the waters of Lake Orion, it is unlawful to exceed a slow--no wake speed within 100 feet of any shore.",
            "(2) On the waters of Lake Orion, it is unlawful, during the period of 1 hour after sunset to 1 hour before sunrise, to:",
            "(a) Operate a vessel at high speed.",
            "(3) On the waters of Lake Orion, it is unlawful at any time to operate a vessel at a speed in excess of 40 miles per hour.",
        ]
    )
    assert [c.label for c in clauses] == ["(1)", "(2)", "(3)"]
    assert "(a)" in clauses[1].text


def test_split_clauses_without_markers() -> None:
    preamble, clauses = parse_dnr.split_clauses(["Rule 62. On the waters of Bass Lake, it is unlawful."])
    assert preamble == ""
    assert len(clauses) == 1
    assert clauses[0].label is None
    assert clauses[0].text.startswith("On the waters of Bass Lake")


@pytest.mark.parametrize(
    ("text", "start", "end"),
    [
        ("between the hours of 6:30 p.m. and 10:00 a.m. of the following day", "18:30", "10:00"),
        ("during the period from 6:30 p.m. to 10:00 a.m. of the following day", "18:30", "10:00"),
        ("it is unlawful between the hours of 6:30 p.m. to 10:00 a.m. of the following day", "18:30", "10:00"),
        ("except between the hours of 10:00 a.m. and 6:30 p.m.", "10:00", "18:30"),
        ("between the hours of 10:00 a.m. and 7:00 p.m., on Saturdays and Holidays", "10:00", "19:00"),
        ("between 11:00 a.m. and 7:30 p.m. during June, July, and August", "11:00", "19:30"),
        ("between the hours of 12:00 a.m. and 12:00 p.m.", "00:00", "12:00"),
    ],
)
def test_extract_hours(text: str, start: str, end: str) -> None:
    hours = parse_dnr.extract_hours(text)
    assert hours is not None
    assert (hours.start, hours.end) == (start, end)


def test_extract_hours_sunset_window_has_text_only() -> None:
    hours = parse_dnr.extract_hours("during the period of 1 hour after sunset to 1 hour before sunrise")
    assert hours is not None
    assert hours.text == "1 hour after sunset to 1 hour before sunrise"
    assert hours.start is None and hours.end is None


def test_extract_hours_keeps_the_dst_sentence_in_text() -> None:
    dst = "The hours should be 7:30 p.m. to 11:00 a.m. of the following day when Eastern Daylight Savings Time is in effect."
    hours = parse_dnr.extract_hours("between the hours of 6:30 p.m. to 10:00 a.m. of the following day", dst)
    assert hours is not None
    assert hours.start == "18:30"
    assert "Daylight Savings Time" in hours.text


def test_extract_days() -> None:
    assert (
        parse_dnr.extract_days("unlawful on Sundays, Memorial Day, Independence Day, and Labor Day, except")
        == "Sundays, Memorial Day, Independence Day, and Labor Day"
    )
    assert parse_dnr.extract_days("on Saturdays and Holidays during the months of June") == "Saturdays and Holidays"
    assert parse_dnr.extract_days("no day qualifier here") is None


@pytest.mark.parametrize(
    ("text", "start", "end"),
    [
        ("it is unlawful to operate a motorboat during September, October and November", "09-01", "11-30"),
        ("during the months of June, July and August", "06-01", "08-31"),
        ("during June, July, and August", "06-01", "08-31"),
        ("from Memorial Day through Labor Day", "05-25", "09-07"),
    ],
)
def test_extract_season(text: str, start: str, end: str) -> None:
    season = parse_dnr.extract_season(text)
    assert season is not None
    assert (season.start, season.end) == (start, end)


def test_extract_season_ignores_time_windows() -> None:
    assert parse_dnr.extract_season("during the period from 6:30 p.m. to 10:00 a.m.") is None


@pytest.mark.parametrize(
    ("text", "speed"),
    [
        ("in excess of 40 miles per hour (64 kilometers per hour)", 40),
        ("at a speed in excess of 35 miles per hour (56 kilometers per hour)", 35),
        ("a rate of speed greater than 10 statute miles per hour", 10),
        ("which shall not exceed a speed limit of 10 miles per hour", 10),
        ("maintain a distance of 100 feet from the shoreline", None),
    ],
)
def test_extract_speed(text: str, speed: float | None) -> None:
    assert parse_dnr.extract_speed(text) == speed


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("section 16, town 4 north, range 7 east, township of Rose, county of Oakland", "Rose Township"),
        ("section 33, T3N, R8E, White Lake township, Oakland county", "White Lake Township"),
        ("Milford township, and Lyon township, Oakland county", "Milford Township, Lyon Township"),
        ("charter township of Oxford, Oakland county", "Oxford Township"),
        ("section 29, town 2 north, range 8 east, city of Wixom, Oakland county", "City of Wixom"),
        ("village of Lake Orion and Orion township, Oakland county", "Village of Lake Orion, Orion Township"),
        ("no place name at all", None),
    ],
)
def test_extract_townships(text: str, expected: str | None) -> None:
    assert parse_dnr.extract_townships(text) == expected


def test_detect_zone_lakewide_locus() -> None:
    scope, description = parse_dnr.detect_zone(
        "On the waters of Fish lake, section 32, town 4 north, range 7 east, Rose township, Oakland county, "
        "it shall be unlawful for an operator of a vessel to exceed a slow-no wake speed.",
        header="FISH LAKE",
    )
    assert (scope, description) == ("lakewide", None)


@pytest.mark.parametrize(
    "text",
    [
        "sections 21 and 22, town 4 north, range 7 east, Rose township, Oakland county, north of Demode road, it is unlawful",
        "To operate a vessel in excess of a slow-no wake speed in the northerly tip of Coles bay, Waterford township",
        "within the north 1/2 of the northwest 1/4 of the southwest 1/4 of section 35",
        "On that portion of the waters of the Indian River between west of the I-75 Bridge and Burt Lake",
        "Southerly and westerly of a line beginning where the east line of lot 42, Golden Shores subdivision no. 1",
        "Between day beacons 25 and 40 and between day beacons 57 and 63",
        "for a distance of 200 feet in any direction from the state public access site",
    ],
)
def test_detect_zone_finds_sub_areas(text: str) -> None:
    scope, description = parse_dnr.detect_zone(text, header="SOME LAKE")
    assert scope == "zone"
    assert description


def test_detect_zone_falls_back_to_the_header_descriptor() -> None:
    scope, description = parse_dnr.detect_zone(
        "On the waters of Middle Straits lake, in sections 12 and 13, town 2 north, range 8 east, "
        "Commerce township, Oakland county, it is unlawful for the operator of a vessel to exceed a slow-no wake speed.",
        header="MIDDLE STRAITS LAKE, COMMERCE TOWNSHIP",
    )
    assert (scope, description) == ("zone", "Commerce Township")


def test_detect_zone_ignores_cross_reference_parentheticals() -> None:
    scope, _ = parse_dnr.detect_zone(
        "On the waters of Dunham lake, sections 13 and 24, T3N, R6E, Hartland township, Livingston county, "
        "it is unlawful to operate a motorboat. (See R281.763.47 for the regulation covering the part of "
        "Dunham lake which lies in Oakland county.)",
        header="DUNHAM LAKE",
    )
    assert scope == "lakewide"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("it is unlawful to operate a motorboat.", "no_motorboats"),
        ("it is unlawful to operate a vessel powered by a motor except an electric motor.", "no_motorboats"),
        ("it is unlawful to operate a motorboat powered by any motor other that an electric trolling motor.", "no_motorboats"),
        ("motorboats are prohibited", "no_motorboats"),
        ("it shall be unlawful for an operator of a vessel to exceed a slow-no wake speed.", "slow_no_wake"),
        ("it is unlawful for the operator of a vessel to exceed a slow--no wake speed.", "slow_no_wake"),
        ("Operate a vessel at high speed.", "no_high_speed"),
        ("no operator of any motorboat shall operate such motorboat at high speed, which means a speed at or above which a motorboat reaches a planing condition.", "no_high_speed"),
        ("it is unlawful, between the hours of 6:30 p.m. and 10:00 a.m. of the following day, to operate a vessel at high speed.", "high_speed_hours"),
        ("it is unlawful at any time to operate a vessel in excess of 40 miles per hour (64 kilometers per hour).", "speed_limit"),
        ("no operator of any motorboat shall have in tow or shall otherwise be assisting in the propulsion of a person on water skis.", "no_towing"),
        ("it is unlawful for the operator of a vessel to exceed a slow--no wake speed when within 100 feet of any shore, dock, raft, buoyed or occupied bathing area, or vessel moored or at anchor.", "shore_buffer"),
        ("Persons navigating on water skis shall maintain a distance of 100 feet from the shoreline.", "shore_buffer"),
        ("it is unlawful to operate an airboat.", "not_applicable"),
        ("Rubber rafts and all floating devices, other than vessels, shall not be used except in swimming areas.", "not_applicable"),
        ("it is unlawful to tow more than 2 persons at 1 time on water skis.", "not_applicable"),
        ("no vessel shall be moored, docked, or anchored for a distance of 350 feet inland.", "not_applicable"),
        ("boating is prohibited", "no_vessels"),
        ("A completely novel sentence with no known template.", "other"),
    ],
)
def test_classify_clause(text: str, expected: str) -> None:
    assert parse_dnr.classify_clause(text)[0].restriction_type == expected


def test_classify_electric_only_with_speed_cap_emits_two_records() -> None:
    results = parse_dnr.classify_clause(
        "it is unlawful to operate a vessel powered by a motor, except an electric motor, "
        "which shall not exceed a speed limit of 10 miles per hour."
    )
    assert [r.restriction_type for r in results] == ["no_motorboats", "speed_limit"]
    assert results[1].speed_mph == 10


def test_unclassified_clause_is_flagged_for_review() -> None:
    result = parse_dnr.classify_clause("Something entirely unlike the known templates.")[0]
    assert result.restriction_type == "other"
    assert result.needs_review is True


def test_pwc_hits_are_flagged_even_though_the_corpus_has_none() -> None:
    result = parse_dnr.classify_clause("personal watercraft are prohibited")[0]
    assert result.restriction_type == "no_pwc"
    assert result.needs_review is True


def test_no_controls_page_is_detected_from_markup(fixtures_dir) -> None:
    html = (fixtures_dir / "dnr_pages" / "missaukee.html").read_text(encoding="utf-8")
    node = parse_dnr.field_content(html)
    assert node is not None
    assert parse_dnr.NO_CONTROLS_RE.search(node.get_text(" ", strip=True))


def test_bold_headers_are_found_under_strong_and_b(fixtures_dir) -> None:
    for slug, count in (("oakland", 86), ("cheboygan", 10), ("kent", 19), ("livingston", 30)):
        html = (fixtures_dir / "dnr_pages" / f"{slug}.html").read_text(encoding="utf-8")
        blocks = parse_dnr.iter_blocks(parse_dnr.field_content(html))
        assert sum(1 for b in blocks if b.is_header) == count, slug


def test_signage_boilerplate_sets_the_flag(parsed) -> None:
    chalmers = [r for r in parsed["oakland"] if r.rule_id == "WC-63-24-002"]
    assert chalmers and all(r.signage_required for r in chalmers)
    school_lot = [r for r in parsed["oakland"] if r.rule_id == "R 281.763.3"]
    assert not any(r.signage_required for r in school_lot)


# ---------------------------------------------------------------------------
# Restriction model
# ---------------------------------------------------------------------------


def test_restriction_id_follows_the_contract_formula() -> None:
    record = Restriction(
        county="Oakland",
        township="Rose Township",
        lake_name_raw="Big School Lot Lake",
        raw_text="some verbatim text",
    )
    record.assign_id()
    key = "Oakland|Big School Lot Lake|Rose Township|some verbatim text"
    expected = hashlib.sha1(key.encode("utf-8")).hexdigest()[:12]
    assert record.restriction_id == expected == compute_restriction_id(
        "Oakland", "Big School Lot Lake", "Rose Township", "some verbatim text"
    )


def test_restriction_id_discriminator_separates_clauses() -> None:
    args = ("Oakland", "Cass Lake", "Waterford Township", "entry text")
    assert compute_restriction_id(*args, "(b)") != compute_restriction_id(*args, "(c)")
    assert compute_restriction_id(*args) != compute_restriction_id(*args, "(b)")


def test_restriction_ids_are_stable_across_runs(fixtures_dir) -> None:
    first = parse_fixture(fixtures_dir, "cheboygan")
    second = parse_fixture(fixtures_dir, "cheboygan")
    assert [r.restriction_id for r in first] == [r.restriction_id for r in second]


# ---------------------------------------------------------------------------
# LLM second pass (no network; a fake client stands in)
# ---------------------------------------------------------------------------


class FakeResponse:
    def __init__(self, parsed_output):
        self.parsed_output = parsed_output


class FakeMessages:
    def __init__(self, entry):
        self.entry = entry
        self.calls: list[dict] = []

    def parse(self, **kwargs):
        self.calls.append(kwargs)
        return FakeResponse(self.entry)


class FakeClient:
    def __init__(self, entry):
        self.messages = FakeMessages(entry)


def _unclassified(raw_text: str = "A novel clause the regexes did not match.") -> Restriction:
    record = Restriction(
        county="Oakland",
        township="Rose Township",
        lake_name_raw="Mystery Lake",
        lake_name_norm="mystery",
        rule_id="R 281.763.99",
        restriction_type="other",
        needs_review=True,
        raw_text=raw_text,
        lake_name_group="MYSTERY LAKE",
    )
    record.assign_id()
    return record


@pytest.fixture
def cfg(tmp_path, monkeypatch) -> Config:
    monkeypatch.setenv("SEAPLANE_DATA_DIR", str(tmp_path))
    config = Config()
    config.ensure_dirs()
    return config


def test_llm_pass_replaces_unclassified_records(cfg) -> None:
    entry = llm_extract.ExtractedEntry(
        clauses=[
            llm_extract.ExtractedClause(
                clause="(a)",
                restriction_type="no_high_speed",
                scope="zone",
                scope_description="north of the bridge",
                hours="between the hours of 6:30 p.m. and 10:00 a.m. of the following day",
                season="during the months of June, July and August",
                confidence=0.9,
            ),
            llm_extract.ExtractedClause(clause="(b)", restriction_type="no_towing", confidence=0.8),
        ]
    )
    client = FakeClient(entry)
    records = [_unclassified()]
    out = llm_extract.upgrade_records(cfg, records, client=client)

    assert len(out) == 2
    assert [r.restriction_type for r in out] == ["no_high_speed", "no_towing"]
    assert all(r.parser == "llm" and r.needs_review for r in out)
    assert out[0].scope == "zone" and out[0].scope_description == "north of the bridge"
    assert out[0].hours is not None and (out[0].hours.start, out[0].hours.end) == ("18:30", "10:00")
    assert out[0].season is not None and out[0].season.start == "06-01"
    assert len({r.restriction_id for r in out}) == 2

    call = client.messages.calls[0]
    assert call["model"] == "claude-opus-5"
    assert call["output_format"] is llm_extract.ExtractedEntry
    assert "thinking" not in call
    assert [m["role"] for m in call["messages"]] == ["user"]
    assert "Mystery Lake" in call["messages"][0]["content"] or "MYSTERY LAKE" in call["messages"][0]["content"]
    assert "restriction_type" in call["system"]


def test_llm_pass_caches_responses(cfg) -> None:
    entry = llm_extract.ExtractedEntry(
        clauses=[llm_extract.ExtractedClause(restriction_type="no_vessels", confidence=0.7)]
    )
    client = FakeClient(entry)
    record = _unclassified()
    llm_extract.upgrade_records(cfg, [record], client=client)
    assert llm_extract.cache_path(cfg, record).exists()

    llm_extract.upgrade_records(cfg, [record], client=client)
    assert len(client.messages.calls) == 1, "second run should read the cache"


def test_llm_pass_skips_classified_and_rescinded_records(cfg) -> None:
    client = FakeClient(llm_extract.ExtractedEntry(clauses=[]))
    rescinded = _unclassified()
    rescinded.status = "rescinded"
    classified = _unclassified()
    classified.restriction_type = "slow_no_wake"
    classified.needs_review = False
    out = llm_extract.upgrade_records(cfg, [rescinded, classified], client=client)
    assert out == [rescinded, classified]
    assert client.messages.calls == []


def test_llm_pass_is_a_no_op_without_credentials(cfg, monkeypatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    records = [_unclassified()]
    assert llm_extract.upgrade_records(cfg, records) == records


def test_llm_pass_survives_an_api_error(cfg) -> None:
    class Boom:
        def parse(self, **_kwargs):
            raise RuntimeError("503")

    class BoomClient:
        messages = Boom()

    records = [_unclassified()]
    assert llm_extract.upgrade_records(cfg, records, client=BoomClient()) == records


# ---------------------------------------------------------------------------
# Stage entry point
# ---------------------------------------------------------------------------


def _seed_raw_pages(cfg: Config, fixtures_dir: Path, slugs: list[str]) -> None:
    dnr_fetch.dnr_dir(cfg).mkdir(parents=True, exist_ok=True)
    manifest = {}
    for slug in slugs:
        html = (fixtures_dir / "dnr_pages" / f"{slug}.html").read_text(encoding="utf-8")
        dnr_fetch.page_path(cfg, slug).write_text(html, encoding="utf-8")
        manifest[slug] = {
            "name": COUNTIES[slug],
            "url": SOURCE_URL.format(slug=slug),
            "fetched_at": FETCHED_AT,
            "sha256": "0" * 64,
        }
    dnr_fetch.manifest_path(cfg).write_text(json.dumps(manifest), encoding="utf-8")


def test_run_writes_jsonl_review_and_diff(tmp_path, monkeypatch, fixtures_dir) -> None:
    monkeypatch.setenv("SEAPLANE_DATA_DIR", str(tmp_path))
    cfg = Config(counties=["cheboygan", "missaukee"])
    cfg.ensure_dirs()
    _seed_raw_pages(cfg, fixtures_dir, ["cheboygan", "missaukee", "kent"])
    args = argparse.Namespace(llm=False)

    assert parse_dnr.run(cfg, args) == 0
    out = cfg.work_dir / "restrictions.jsonl"
    rows = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
    assert rows
    assert {row["county"] for row in rows} == {"Cheboygan"}  # kent not selected
    assert rows == sorted(
        rows, key=lambda r: (r["county"], r["lake_name_norm"], r["rule_id"] or "", r["clause"] or "")
    )
    assert (cfg.work_dir / "restrictions_review.jsonl").exists()
    assert (cfg.work_dir / "restrictions.prev.jsonl").exists()
    assert not (cfg.work_dir / "restrictions_diff.json").exists()  # no previous run yet

    # second run against the previous output, now with one more county
    cfg2 = Config(counties=["cheboygan", "kent"])
    assert parse_dnr.run(cfg2, args) == 0
    diff = json.loads((cfg.work_dir / "restrictions_diff.json").read_text(encoding="utf-8"))
    assert diff["new"] and not diff["removed"] and not diff["changed"]
    assert len(diff["new"]) == len([r for r in parse_fixture(fixtures_dir, "kent")])


def test_run_without_cached_pages_fails_cleanly(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("SEAPLANE_DATA_DIR", str(tmp_path))
    cfg = Config(counties=["oakland"])
    cfg.ensure_dirs()
    assert parse_dnr.run(cfg, argparse.Namespace(llm=False)) == 1


def test_add_args_exposes_the_llm_flag() -> None:
    parser = argparse.ArgumentParser()
    parse_dnr.add_args(parser)
    assert parser.parse_args([]).llm is False
    assert parser.parse_args(["--llm"]).llm is True
