"""classify.py: assembly for the shared rules engine, run against the real Node CLI.

`test_shared_lake_fixtures_match` is the cross-check docs/design.md asks for: the same
`rules/fixtures/lakes.json` cases the JS unit tests use, pushed through this pipeline's assembly
code and `rules/engine/cli.js`, compared to the fixtures' expected verdict and flags.
"""
from __future__ import annotations

import argparse
import json
import shutil

import geopandas as gpd
import pytest
from shapely.geometry import box

from seaplane_pipeline import classify, manual, match

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node not on PATH")

LAT0, LON0 = 42.70, -83.60


def lakes_frame(rows):
    return gpd.GeoDataFrame(
        {
            "id": [r["id"] for r in rows],
            "name": [r.get("name") for r in rows],
            "name_norm": [r.get("name_norm", "") for r in rows],
            "county": ["Oakland"] * len(rows),
            "township": [None] * len(rows),
            "chord_ft": [r.get("chord_ft") for r in rows],
            "area_acres": [r.get("area_acres", 50.0) for r in rows],
        },
        geometry=[box(LON0 + i * 0.05, LAT0, LON0 + i * 0.05 + 0.01, LAT0 + 0.01) for i in range(len(rows))],
        crs="EPSG:4326",
    )


def test_shared_lake_fixtures_match(repo_root):
    """Every case in rules/fixtures/lakes.json, through run_engine()."""
    cases = json.loads((repo_root / "rules" / "fixtures" / "lakes.json").read_text())
    rules_path = repo_root / "rules" / "rules.json"

    # Cases differ in mac_record_loaded, so group by that option and run one engine call per group.
    for mac_loaded in (True, False):
        group = [c for c in cases if c.get("options", {}).get("mac_record_loaded", True) is mac_loaded]
        if not group:
            continue
        lakes = [c["lake"] for c in group]
        restrictions = {str(c["lake"]["id"]): c["restrictions"] for c in group}
        results = {r["id"]: r for r in classify.run_engine(rules_path, lakes, restrictions, mac_loaded=mac_loaded)}
        for case in group:
            got = results[case["lake"]["id"]]
            assert got["verdict"] == case["expect"]["verdict"], case["name"]
            assert got["flags"] == case["expect"]["flags"], case["name"]
            assert len(got["reasons"]) == len(case["restrictions"]), case["name"]


def test_lake_inputs_shape():
    lakes = lakes_frame([
        {"id": 1, "name": "Cass Lake", "chord_ft": 3000.0, "area_acres": 120.0},
        {"id": 2, "name": None, "chord_ft": float("nan")},
    ])
    overlays = {"1": {"public_access": True, "access": "Cass BAS", "federal_unit": "Isle Royale"}}
    got = classify.lake_inputs(lakes, overlays)
    assert got[0] == {
        "id": 1, "name": "Cass Lake", "chord_ft": 3000.0, "area_acres": 120.0,
        "public_access": True, "federal_unit": "Isle Royale",
    }
    assert got[1]["name"] is None and got[1]["chord_ft"] is None
    assert got[1]["public_access"] is False and got[1]["federal_unit"] is None


def test_restrictions_by_lake_groups_and_flags_low_confidence():
    records = [
        {"restriction_id": "a", "restriction_type": "no_high_speed", "status": "active", "needs_review": False},
        {"restriction_id": "b", "restriction_type": "no_towing", "status": "rescinded"},
    ]
    matches = [
        {"restriction_id": "a", "lake_id": 1, "score": 0.65, "method": "tokens+plss", "needs_review": True},
        {"restriction_id": "b", "lake_id": 1, "score": 1.0, "method": "exact+plss", "needs_review": False},
        {"restriction_id": "zz", "lake_id": 1, "score": 1.0, "method": "exact", "needs_review": False},
    ]
    synthetic = [{"restriction_id": "mac1", "restriction_type": "mac_ordinance", "lake_ids": [1, 2]}]
    grouped = classify.restrictions_by_lake(records, matches, synthetic)
    assert [r["restriction_id"] for r in grouped["1"]] == ["a", "mac1"]  # rescinded and unknown dropped
    assert grouped["1"][0]["needs_review"] is True
    assert grouped["1"][0]["match_confidence"] == 0.65
    assert [r["restriction_id"] for r in grouped["2"]] == ["mac1"]


def test_apply_verdict_overrides():
    results = [{"id": 7, "verdict": "clear", "reasons": [], "flags": []}, {"id": 8, "verdict": "clear"}]
    applied = classify.apply_verdict_overrides(results, {7: {"verdict": "conditional", "note": "clerk said so"}})
    assert applied == 1
    assert results[0]["verdict"] == "conditional"
    assert results[0]["reasons"][-1]["matched_rule"] == "manual-override"
    assert results[0]["reasons"][-1]["note"] == "clerk said so"
    assert results[1]["verdict"] == "clear"


def test_run_end_to_end(tmp_path, monkeypatch, repo_root):
    from seaplane_pipeline.config import Config

    monkeypatch.setenv("SEAPLANE_DATA_DIR", str(tmp_path / "data"))
    cfg = Config()
    cfg.ensure_dirs()
    cfg.manual_dir.mkdir(parents=True, exist_ok=True)
    (cfg.manual_dir / "overrides.yaml").write_text(
        "matches: []\nunmatch: []\nverdicts:\n  - lake_id: 3\n    verdict: conditional\n    note: forced\n"
    )
    (cfg.manual_dir / "mac_record.yaml").write_text(
        "loaded: true\nentries:\n  - lake_name: Angelus Lake\n    county: Oakland\n"
        "    lake_id: null\n    kind: ordinance\n    status: approved\n"
        "    citation: test\n    note: banned\n    source_url: http://example.test\n"
    )
    lakes = lakes_frame([
        {"id": 1, "name": "Long Lake", "name_norm": "long", "chord_ft": 3000.0},
        {"id": 2, "name": "Angelus Lake", "name_norm": "angelus", "chord_ft": 3000.0},
        {"id": 3, "name": "Round Lake", "name_norm": "round", "chord_ft": 1000.0},
    ])
    lakes.to_parquet(cfg.work_dir / "lakes.parquet", index=False)
    (cfg.work_dir / "overlays.json").write_text(json.dumps({
        "1": {"public_access": True, "access": "A", "federal_unit": None, "airspace_class": None},
        "2": {"public_access": False, "access": None, "federal_unit": None, "airspace_class": "D"},
        "3": {"public_access": True, "access": None, "federal_unit": "Isle Royale National Park",
              "airspace_class": None},
    }))
    (cfg.work_dir / "restrictions.jsonl").write_text(json.dumps({
        "restriction_id": "r1", "rule_id": "R 281.763.3", "restriction_type": "no_high_speed",
        "scope": "lakewide", "hours": None, "season": None, "status": "active", "needs_review": False,
    }))
    (cfg.work_dir / "matches.json").write_text(json.dumps(
        [{"restriction_id": "r1", "lake_id": 1, "score": 1.0, "method": "exact+plss", "needs_review": False}]
    ))

    assert classify.run(cfg, argparse.Namespace(rules=None, node="node")) == 0
    verdicts = json.loads((cfg.work_dir / "verdicts.json").read_text())
    assert verdicts["1"]["verdict"] == "restricted"           # lakewide no_high_speed
    assert verdicts["2"]["verdict"] == "restricted"           # MAC ordinance, matched by name+county
    assert "no_public_access" in verdicts["2"]["flags"]
    assert verdicts["3"]["verdict"] == "conditional"          # federal restricted, then overridden
    assert verdicts["3"]["reasons"][-1]["matched_rule"] == "manual-override"
    assert "federal_overlay" in verdicts["3"]["flags"]
    assert "chord_below_minimum" in verdicts["3"]["flags"]
    assert "mac_pending" not in verdicts["1"]["flags"]        # mac_record.yaml has loaded: true

    synthetic = json.loads((cfg.work_dir / "synthetic_restrictions.json").read_text())
    kinds = {s["restriction_type"] for s in synthetic}
    assert kinds == {"mac_ordinance", "federal_no_landing"}
    assert all(s["parser"] == "manual" for s in synthetic)
    mac = next(s for s in synthetic if s["restriction_type"] == "mac_ordinance")
    assert mac["lake_ids"] == [2] and mac["source_url"] == "http://example.test"


# --- synthetic federal records ---------------------------------------------


def refuge_frame():
    """Four ponds in one refuge: two unnamed in one township, one unnamed elsewhere, one named."""
    frame = lakes_frame([
        {"id": 401, "name": None},
        {"id": 402, "name": None},
        {"id": 403, "name": None},
        {"id": 404, "name": "Lower Goose Pen Pool"},
    ])
    frame["township"] = ["Doyle Township", "Doyle Township", "Germfask Township", "Doyle Township"]
    return frame


SENEY = {"federal_unit": "Seney National Wildlife Refuge"}


def test_federal_restrictions_merge_the_lakes_that_hash_to_one_id():
    """Unnamed ponds sharing county, township and unit share one record (contract: one per id)."""
    overlays = {"402": SENEY, "401": SENEY, "403": SENEY, "404": SENEY}
    records = manual.federal_restrictions(refuge_frame(), overlays)

    assert len({r["restriction_id"] for r in records}) == len(records)
    by_ids = {tuple(r["lake_ids"]): r for r in records}
    assert sorted(by_ids) == [(401, 402), (403,), (404,)]
    assert all(r["restriction_type"] == "federal_no_landing" for r in records)
    assert all(r["parser"] == "manual" for r in records)


def test_federal_restriction_ids_do_not_depend_on_how_many_lakes_share_them():
    """Keeping the id formula means an existing pack's federal ids survive the merge."""
    one = manual.federal_restrictions(refuge_frame(), {"401": SENEY})
    both = manual.federal_restrictions(refuge_frame(), {"401": SENEY, "402": SENEY})
    assert [r["restriction_id"] for r in one] == [r["restriction_id"] for r in both]


def test_federal_restrictions_reach_every_lake_through_the_rules_engine():
    """The end the collision broke: each merged lake is still handed the record to classify."""
    overlays = {"401": SENEY, "402": SENEY}
    records = manual.federal_restrictions(refuge_frame(), overlays)
    grouped = classify.restrictions_by_lake([], [], records)
    assert sorted(grouped) == ["401", "402"]
    assert grouped["401"][0]["restriction_id"] == grouped["402"][0]["restriction_id"]


def test_a_structure_zone_is_conditional_and_names_the_structure(repo_root):
    """End to end through the real engine: the same rule reads restricted lakewide."""
    phrase = "the channel connecting Intermediate Lake to Hanley Lake"
    lakes = [{"id": 1, "name": "Intermediate Lake", "chord_ft": 6000.0, "area_acres": 1569.0},
             {"id": 2, "name": "Intermediate Lake", "chord_ft": 6000.0, "area_acres": 1569.0}]
    restrictions = {
        "1": [{"restriction_id": "z", "restriction_type": "slow_no_wake", "scope": "zone",
               "scope_description": phrase, "status": "active", "hours": None, "season": None}],
        "2": [{"restriction_id": "w", "restriction_type": "slow_no_wake", "scope": "lakewide",
               "scope_description": None, "status": "active", "hours": None, "season": None}],
    }
    results = {r["id"]: r for r in classify.run_engine(
        repo_root / "rules" / "rules.json", lakes, restrictions, mac_loaded=True)}
    assert results[1]["verdict"] == "conditional"
    assert phrase in results[1]["reasons"][0]["note"]
    assert results[2]["verdict"] == "restricted"


# --- big water -------------------------------------------------------------


def big_water_frame():
    return lakes_frame([
        {"id": 301, "name": "Lake St. Clair"},
        {"id": 302, "name": "Cass Lake"},
    ]).assign(kind=["great_lake", "lake"])


def test_drop_big_water_federal_withholds_only_the_big_water_records():
    records = [
        {"restriction_id": "f1", "restriction_type": "federal_no_landing", "lake_ids": [301]},
        {"restriction_id": "f2", "restriction_type": "federal_no_landing", "lake_ids": [302]},
    ]
    kept = classify.drop_big_water_federal(records, big_water_frame())
    assert [r["restriction_id"] for r in kept] == ["f2"]


def test_drop_big_water_federal_keeps_the_inland_half_of_a_merged_record():
    """A merged federal record covering both kinds keeps its inland lakes and loses the big water."""
    records = [
        {"restriction_id": "f1", "restriction_type": "federal_no_landing", "lake_ids": [301, 302]},
    ]
    kept = classify.drop_big_water_federal(records, big_water_frame())
    assert [r["lake_ids"] for r in kept] == [[302]]
    assert records[0]["lake_ids"] == [301, 302]  # the input record is not mutated


def test_restrictions_by_lake_stamps_big_water_partial():
    records = [
        {"restriction_id": "a", "restriction_type": "slow_no_wake", "scope": "lakewide", "status": "active"},
        {"restriction_id": "b", "restriction_type": "slow_no_wake", "scope": "lakewide", "status": "active"},
    ]
    matches = [
        {"restriction_id": "a", "lake_id": 301, "score": 1.0, "method": "exact+river-township"},
        {"restriction_id": "b", "lake_id": 302, "score": 1.0, "method": "exact+plss"},
    ]
    partial = match.big_water_partial_ids(matches, match.kinds_by_lake(big_water_frame()))
    grouped = classify.restrictions_by_lake(records, matches, [], partial)
    assert grouped["301"][0]["big_water_partial"] is True
    assert "big_water_partial" not in grouped["302"][0]


def test_the_cap_is_what_the_engine_applies_to_the_pipeline_verdict(repo_root):
    """End to end through the real rules engine: the same rule is conditional on big water only."""
    records = [
        {"restriction_id": "a", "restriction_type": "slow_no_wake", "scope": "lakewide", "status": "active",
         "hours": None, "season": None},
        {"restriction_id": "b", "restriction_type": "slow_no_wake", "scope": "lakewide", "status": "active",
         "hours": None, "season": None},
    ]
    matches = [
        {"restriction_id": "a", "lake_id": 301, "score": 1.0, "method": "exact+river-township"},
        {"restriction_id": "b", "lake_id": 302, "score": 1.0, "method": "exact+plss"},
    ]
    lakes = big_water_frame()
    partial = match.big_water_partial_ids(matches, match.kinds_by_lake(lakes))
    grouped = classify.restrictions_by_lake(records, matches, [], partial)
    results = {
        r["id"]: r
        for r in classify.run_engine(repo_root / "rules" / "rules.json", classify.lake_inputs(lakes, {}), grouped)
    }
    assert results[301]["verdict"] == "conditional"
    assert results[302]["verdict"] == "restricted"
