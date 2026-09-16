"""suggest.py: candidate generation for the review queue, on the synthetic world from test_match."""
from __future__ import annotations

from seaplane_pipeline import match, suggest
from tests.test_match import lake_box, make_lakes, make_plss, restriction


def world():
    rows = [
        {"id": 1, "name": "Seymour Lake", "geometry": lake_box(0, 0)},          # in 05N 09E 27
        {"id": 2, "name": "Big Fish Lake", "geometry": lake_box(1, 0)},         # in 05N 09E 28
        {"id": 3, "name": None, "geometry": lake_box(2, 0)},                     # unnamed, in 05N 09E 29
        {"id": 4, "name": "Fish Lake", "county": "Oakland", "geometry": lake_box(3, 0)},  # 05N 09E 30
    ]
    plss = make_plss([("05N", "09E", "27", 0, 0), ("05N", "09E", "28", 1, 0), ("05N", "09E", "29", 2, 0),
                      ("05N", "09E", "30", 3, 0), ("05N", "09W", "27", 0, 3)])
    lakes = make_lakes(rows)
    lakes["area_acres"] = [40.0, 80.0, 25.0, 30.0]
    return suggest.Suggester(match.Matcher(lakes, plss), top=3)


def test_flipped_range_direction_finds_the_typo_lake():
    s = world().suggest(restriction("r1", "Seymour Lake", plss=[{"township": "5N", "range": "9W", "sections": [27]}]))
    assert s.candidates and s.candidates[0].lake_id == 1
    assert s.candidates[0].how.startswith("PLSS range direction flipped")
    assert any("typo" in h for h in s.hints)
    assert s.paste is not None and "lake_id: 1" in s.paste


def test_unnamed_lake_lists_what_the_section_holds():
    s = world().suggest(restriction("r2", "Unnamed Lake", plss=[{"township": "5N", "range": "9E", "sections": [29]}]))
    assert s.candidates == []
    assert any("unnamed polygons" in h for h in s.hints)
    assert s.paste is None


def test_same_name_elsewhere_in_county_is_offered():
    s = world().suggest(restriction("r3", "Fish Lake", plss=[{"township": "5N", "range": "9E", "sections": [27]}]))
    ids = {c.lake_id: c for c in s.candidates}
    assert 4 in ids and ids[4].how.startswith("same or similar name")
    assert 2 in ids  # Big Fish Lake via the qualifier tier, near the section


def test_build_skips_waterways_and_lists_low_confidence(tmp_path, monkeypatch):
    import json

    from seaplane_pipeline.config import Config

    cfg = Config()
    monkeypatch.setenv("SEAPLANE_DATA_DIR", str(tmp_path))
    cfg.ensure_dirs()
    lakes = make_lakes([{"id": 1, "name": "Stony Creek Lake", "geometry": lake_box(0, 0)}])
    lakes["area_acres"] = [40.0]
    lakes.to_parquet(cfg.work_dir / "lakes.parquet")
    recs = [restriction("a", "Stoney Creek Lake"), restriction("b", "Grand River")]
    (cfg.work_dir / "restrictions.jsonl").write_text("\n".join(json.dumps(r) for r in recs) + "\n")
    (cfg.work_dir / "unmatched.json").write_text(json.dumps([
        {"restriction_id": "b", "lake_name_raw": "Grand River", "kind": "waterway", "county": "Oakland"},
    ]))
    (cfg.work_dir / "matches.json").write_text(json.dumps([
        {"restriction_id": "a", "lake_id": 1, "score": 0.58, "method": "fuzzy+county", "needs_review": True},
    ]))
    data = suggest.build(cfg)
    assert data["suggested_matches"] == []
    assert len(data["confirm_low_confidence"]) == 1
    assert data["confirm_low_confidence"][0]["matched"]["name"] == "Stony Creek Lake"
    assert data["confirm_low_confidence"][0]["reject"].startswith("- {restriction_id: a, lake_id: 1}")
