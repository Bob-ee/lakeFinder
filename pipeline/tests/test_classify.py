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

from seaplane_pipeline import classify

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
