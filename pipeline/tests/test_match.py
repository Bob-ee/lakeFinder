"""match.py: PLSS resolution, name scoring, duplicate names, overrides.

All geometry here is synthetic: lakes and PLSS sections are small boxes laid out on a grid near
Oakland County, sized so that "1 km buffer" and "inside the section" are meaningfully different.
"""
from __future__ import annotations

import argparse
import json

import geopandas as gpd
import pytest
from shapely.geometry import box

from seaplane_pipeline import ids, match

# ~0.012 deg longitude is about 1 km at this latitude; sections are laid out 0.05 deg apart.
LAT0, LON0 = 42.70, -83.60
SECTION = 0.02


def section_box(col: int, row: int):
    return box(LON0 + col * 0.05, LAT0 + row * 0.05, LON0 + col * 0.05 + SECTION, LAT0 + row * 0.05 + SECTION)


def lake_box(col: int, row: int, dx: float = 0.004, dy: float = 0.004, size: float = 0.006):
    x0 = LON0 + col * 0.05 + dx
    y0 = LAT0 + row * 0.05 + dy
    return box(x0, y0, x0 + size, y0 + size)


def make_lakes(rows: list[dict]) -> gpd.GeoDataFrame:
    frame = gpd.GeoDataFrame(
        {
            "id": [r["id"] for r in rows],
            "name": [r["name"] for r in rows],
            "name_norm": [ids.normalize_name(r["name"]) for r in rows],
            "county": [r.get("county", "Oakland") for r in rows],
            "township": [r.get("township") for r in rows],
        },
        geometry=[r["geometry"] for r in rows],
        crs="EPSG:4326",
    )
    return frame


def make_plss(entries: list[tuple[str, str, str, int, int]]) -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        {
            "TOWN": [e[0] for e in entries],
            "RANGE": [e[1] for e in entries],
            "SECTION": [e[2] for e in entries],
        },
        geometry=[section_box(e[3], e[4]) for e in entries],
        crs="EPSG:4326",
    )


def restriction(rid, name, county="Oakland", plss=None, **kw):
    return {
        "restriction_id": rid,
        "lake_name_raw": name,
        "lake_name_norm": ids.normalize_name(name),
        "county": county,
        "township": kw.get("township"),
        "plss": plss or [],
        "restriction_type": kw.get("restriction_type", "no_high_speed"),
        "scope": "lakewide",
        "status": kw.get("status", "active"),
    }


# --- scoring ---------------------------------------------------------------


def test_score_name_tiers():
    assert match.score_name("long", "long") == (1.0, "exact")
    assert match.score_name("big school lot", "little school lot") == (0.6, "qualifier")
    score, method = match.score_name("mud", "mud creek")
    assert method == "tokens" and 0 < score < 0.6
    assert match.score_name("silver", "round") == (0.0, "none")
    assert match.score_name("", "round") == (0.0, "none")


def test_score_name_fuzzy_tier_handles_spelling_variants():
    """Real Oakland case: the DNR writes "Stoney Creek Lake", hydrography has "Stony Creek Lake"."""
    score, method = match.score_name("stoney creek", "stony creek")
    assert method == "fuzzy"
    assert 0.4 < score < 0.5           # always under 0.5, so it can never auto-accept
    assert match.score_name("darb", "darby")[1] == "fuzzy"
    assert match.score_name("heron", "huron") == (0.0, "none")   # 0.80 ratio is below the gate
    assert match.score_name("clear", "pickerel") == (0.0, "none")


def test_fuzzy_match_lands_in_the_review_band():
    rows = [{"id": 5101, "name": "Stony Creek Lake", "geometry": lake_box(0, 0)}]
    matcher = match.Matcher(make_lakes(rows), make_plss([("04N", "07E", "16", 0, 0)]))
    m, miss = matcher.match_one(
        restriction("r", "Stoney Creek Lake", plss=[{"township": "4N", "range": "7E", "sections": [16]}])
    )
    assert miss is None
    assert m.lake_id == 5101
    assert m.method == "fuzzy+plss"
    assert 0.5 <= m.score < match.ACCEPT and m.needs_review

    # Even with no PLSS at all (county fallback only) it still reaches the review band.
    county_only, _ = matcher.match_one(restriction("r2", "Stoney Creek Lake"))
    assert county_only.lake_id == 5101 and county_only.needs_review


def test_pad_tr_and_plss_keys():
    assert match.pad_tr("4N") == "04N"
    assert match.pad_tr("04n") == "04N"
    assert match.pad_tr("12W") == "12W"
    assert match.pad_tr(None) == ""
    assert match.plss_keys([{"township": "4N", "range": "7E", "sections": [3, 16]}]) == {"04N07E03", "04N07E16"}
    assert match.plss_keys([{"township": "4N", "range": None, "sections": [3]}]) == set()
    assert match.plss_keys(None) == set()


# --- duplicate names -------------------------------------------------------


@pytest.fixture
def duplicate_world():
    """Long, Mud, Round and Silver lakes, each duplicated in a different PLSS section."""
    rows = []
    lake_id = 1000
    layout = [("Long Lake", 0, 0), ("Mud Lake", 1, 0), ("Round Lake", 2, 0), ("Silver Lake", 3, 0),
              ("Long Lake", 0, 1), ("Mud Lake", 1, 1), ("Round Lake", 2, 1), ("Silver Lake", 3, 1)]
    for name, col, row in layout:
        lake_id += 1
        rows.append({"id": lake_id, "name": name, "geometry": lake_box(col, row)})
    sections = [("04N", "07E", f"{1 + col:02d}", col, 0) for col in range(4)]
    sections += [("05N", "07E", f"{1 + col:02d}", col, 1) for col in range(4)]
    return match.Matcher(make_lakes(rows), make_plss(sections))


def test_plss_resolves_duplicate_names(duplicate_world):
    for col, name in enumerate(["Long Lake", "Mud Lake", "Round Lake", "Silver Lake"]):
        south, _ = duplicate_world.match_one(
            restriction("r1", name, plss=[{"township": "4N", "range": "7E", "sections": [1 + col]}])
        )
        north, _ = duplicate_world.match_one(
            restriction("r2", name, plss=[{"township": "5N", "range": "7E", "sections": [1 + col]}])
        )
        assert south is not None and north is not None
        assert south.lake_id != north.lake_id
        assert south.lake_id == 1001 + col
        assert north.lake_id == 1005 + col
        assert south.score == 1.0 and not south.needs_review
        assert "exact+plss" == south.method


def test_unpadded_township_range_still_joins(duplicate_world):
    """restrictions.jsonl stores "4N"/"7E"; the PLSS layer stores "04N"/"07E"."""
    m, _ = duplicate_world.match_one(
        restriction("r", "Long Lake", plss=[{"township": "04N", "range": "07E", "sections": [1]}])
    )
    assert m.lake_id == 1001


def test_big_little_disambiguation():
    rows = [
        {"id": 2001, "name": "Big School Lot Lake", "geometry": lake_box(0, 0, dx=0.004, size=0.005)},
        {"id": 2002, "name": "Little School Lot Lake", "geometry": lake_box(0, 0, dx=0.011, size=0.005)},
    ]
    matcher = match.Matcher(make_lakes(rows), make_plss([("04N", "07E", "16", 0, 0)]))
    plss = [{"township": "4N", "range": "7E", "sections": [16]}]
    big, _ = matcher.match_one(restriction("r-big", "Big School Lot Lake", plss=plss))
    little, _ = matcher.match_one(restriction("r-little", "Little School Lot Lake", plss=plss))
    assert big.lake_id == 2001
    assert little.lake_id == 2002
    assert big.score == 1.0 and little.score == 1.0  # exact beats the 0.6 qualifier match


def test_county_fallback_when_no_plss():
    rows = [
        {"id": 3001, "name": "Silver Lake", "county": "Oakland", "geometry": lake_box(0, 0)},
        {"id": 3002, "name": "Silver Lake", "county": "Livingston", "geometry": lake_box(5, 0)},
    ]
    matcher = match.Matcher(make_lakes(rows), None)
    oakland, _ = matcher.match_one(restriction("r", "Silver Lake", county="Oakland"))
    livingston, _ = matcher.match_one(restriction("r", "Silver Lake", county="Livingston"))
    assert oakland.lake_id == 3001
    assert livingston.lake_id == 3002
    assert oakland.method.endswith("+county")


def test_section_bonus_beats_the_buffer_only_candidate():
    """Same name in and just outside the section: the one inside wins on the +0.2 bonus."""
    rows = [
        {"id": 4001, "name": "Round Lake", "geometry": lake_box(0, 0)},          # inside section
        {"id": 4002, "name": "Round Lake", "geometry": lake_box(0, 0, dx=0.025)},  # outside, inside 1 km
    ]
    matcher = match.Matcher(make_lakes(rows), make_plss([("04N", "07E", "16", 0, 0)]))
    m, _ = matcher.match_one(
        restriction("r", "Round Lake", plss=[{"township": "4N", "range": "7E", "sections": [16]}])
    )
    assert m.lake_id == 4001


def test_low_confidence_match_is_applied_but_flagged():
    rows = [{"id": 5001, "name": "Mud Creek Lake", "geometry": lake_box(0, 0)}]
    matcher = match.Matcher(make_lakes(rows), make_plss([("04N", "07E", "16", 0, 0)]))
    m, miss = matcher.match_one(
        restriction("r", "Mud Lake", plss=[{"township": "4N", "range": "7E", "sections": [16]}])
    )
    # tokens 0.4 * 0.5 + inside 0.2 + county 0.1 = 0.5
    assert miss is None
    assert m.lake_id == 5001
    assert 0.5 <= m.score < 0.8
    assert m.needs_review


def test_unmatched_when_nothing_scores():
    rows = [{"id": 6001, "name": "Round Lake", "geometry": lake_box(0, 0)}]
    matcher = match.Matcher(make_lakes(rows), make_plss([("04N", "07E", "16", 0, 0)]))
    m, miss = matcher.match_one(
        restriction("r", "Pickerel Lake", plss=[{"township": "4N", "range": "7E", "sections": [16]}])
    )
    assert m is None
    assert miss["restriction_id"] == "r"
    assert miss["best_score"] == 0.0
    assert miss["candidates"] == 1


def test_missing_plss_section_falls_back_to_county():
    rows = [{"id": 7001, "name": "Round Lake", "geometry": lake_box(0, 0)}]
    matcher = match.Matcher(make_lakes(rows), make_plss([("04N", "07E", "16", 0, 0)]))
    m, _ = matcher.match_one(
        restriction("r", "Round Lake", plss=[{"township": "99N", "range": "99E", "sections": [1]}])
    )
    assert m.lake_id == 7001
    assert m.method.endswith("+county-plss-miss")


def test_two_restrictions_can_share_one_lake():
    """A lake straddling a county line gets one record per county, both on the same polygon."""
    rows = [{"id": 8001, "name": "Bishop Lake", "county": "Livingston", "geometry": lake_box(0, 0)}]
    matcher = match.Matcher(make_lakes(rows), make_plss([("04N", "07E", "16", 0, 0)]))
    plss = [{"township": "4N", "range": "7E", "sections": [16]}]
    a, _ = matcher.match_one(restriction("r-liv", "Bishop Lake", county="Livingston", plss=plss))
    b, _ = matcher.match_one(restriction("r-oak", "Bishop Lake", county="Oakland", plss=plss))
    assert a.lake_id == b.lake_id == 8001
    assert a.restriction_id != b.restriction_id
    assert match.matches_by_lake([a.as_dict(), b.as_dict()])[8001].__len__() == 2


def test_match_name_county_for_mac_entries():
    rows = [
        {"id": 9001, "name": "Lake Angelus", "county": "Oakland", "geometry": lake_box(0, 0)},
        {"id": 9002, "name": "Angelus Lake", "county": "Kent", "geometry": lake_box(5, 0)},
    ]
    matcher = match.Matcher(make_lakes(rows), None)
    lake_id, score = matcher.match_name_county("Lake Angelus", "Oakland")
    assert lake_id == 9001 and score == 1.0
    assert matcher.match_name_county("Nonexistent Lake", "Oakland") == (None, 0.0)


# --- overrides -------------------------------------------------------------


def test_overrides_force_and_unmatch():
    auto = [
        match.Match("r1", 1111, 0.9, "exact+plss", False),
        match.Match("r2", 2222, 0.6, "tokens+county", True),
    ]
    overrides = {
        "matches": [{"restriction_id": "r1", "lake_id": 3333, "note": "east basin"}],
        "unmatch": [{"restriction_id": "r2", "lake_id": 2222}],
    }
    result = {(m.restriction_id, m.lake_id): m for m in match.apply_overrides(auto, overrides)}
    assert set(result) == {("r1", 3333)}
    forced = result[("r1", 3333)]
    assert forced.method == "override" and forced.score == 1.0 and not forced.needs_review


# --- stage -----------------------------------------------------------------


def test_run_writes_matches_and_unmatched(tmp_path, monkeypatch):
    from seaplane_pipeline.config import Config

    monkeypatch.setenv("SEAPLANE_DATA_DIR", str(tmp_path / "data"))
    cfg = Config()
    cfg.ensure_dirs()
    (cfg.manual_dir).mkdir(parents=True, exist_ok=True)
    (cfg.manual_dir / "overrides.yaml").write_text("matches: []\nunmatch: []\nverdicts: []\n")

    rows = [
        {"id": 1001, "name": "Long Lake", "geometry": lake_box(0, 0)},
        {"id": 1002, "name": "Round Lake", "geometry": lake_box(1, 0)},
    ]
    make_lakes(rows).to_parquet(cfg.work_dir / "lakes.parquet", index=False)
    make_plss([("04N", "07E", "01", 0, 0), ("04N", "07E", "02", 1, 0)]).to_parquet(
        cfg.cache_dir / "plss_sections.parquet", index=False
    )
    records = [
        restriction("aaa", "Long Lake", plss=[{"township": "4N", "range": "7E", "sections": [1]}]),
        restriction("bbb", "Pickerel Lake", plss=[{"township": "4N", "range": "7E", "sections": [2]}]),
        restriction("ccc", "Round Lake", status="rescinded"),
    ]
    (cfg.work_dir / "restrictions.jsonl").write_text("\n".join(json.dumps(r) for r in records))

    assert match.run(cfg, argparse.Namespace(restrictions=None, buffer_km=1.0)) == 0
    matches = json.loads((cfg.work_dir / "matches.json").read_text())
    unmatched = json.loads((cfg.work_dir / "unmatched.json").read_text())
    assert [m["restriction_id"] for m in matches] == ["aaa"]
    assert matches[0] == {
        "restriction_id": "aaa", "lake_id": 1001, "score": 1.0,
        "method": "exact+plss", "needs_review": False,
    }
    assert [u["restriction_id"] for u in unmatched] == ["bbb"]  # rescinded "ccc" is never considered
