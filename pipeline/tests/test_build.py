"""build.py: the served data pack, cut with the real tippecanoe.

A three-lake, two-restriction synthetic work dir is built end to end; the tiles are inspected with
`pmtiles show` and every pack.json digest is re-verified against the file on disk.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess

import geopandas as gpd
import pytest
from shapely.geometry import box

from seaplane_pipeline import build

pytestmark = pytest.mark.skipif(
    shutil.which("tippecanoe") is None or shutil.which("pmtiles") is None,
    reason="tippecanoe and pmtiles are required",
)

LAT0, LON0 = 42.70, -83.60
INDEX_FIELDS = {
    "id", "name", "name_norm", "county", "township", "lat", "lon", "bbox",
    "area_acres", "chord_ft", "chord_bearing_deg", "verdict", "flags", "restriction_ids", "access",
}


def _lake(i, name, name_norm):
    geom = box(LON0 + i * 0.05, LAT0, LON0 + i * 0.05 + 0.01, LAT0 + 0.01)
    return {
        "id": 1000 + i, "name": name, "name_norm": name_norm, "county": "Oakland",
        "township": "Rose Township", "lat": LAT0 + 0.005, "lon": LON0 + i * 0.05 + 0.005,
        "minx": LON0 + i * 0.05, "miny": LAT0, "maxx": LON0 + i * 0.05 + 0.01, "maxy": LAT0 + 0.01,
        "area_acres": 120.0 + i, "chord_ft": 2500.0 + i, "chord_bearing_deg": 47.0,
        "geometry": geom,
    }


@pytest.fixture
def work_dir(tmp_path, monkeypatch, fixtures_dir):
    from seaplane_pipeline.config import Config

    monkeypatch.setenv("SEAPLANE_DATA_DIR", str(tmp_path / "data"))
    cfg = Config()
    cfg.ensure_dirs()
    for src, dest in (
        ("nps_boundary.sample.json", "nps_boundary.geojson"),
        ("fws_refuge_boundary.sample.json", "fws_refuge_boundary.geojson"),
        ("boating_access_sites.sample.json", "boating_access_sites.geojson"),
        ("faa_class_airspace.sample.json", "faa_class_airspace.geojson"),
        ("faa_airports.sample.json", "faa_airports.geojson"),
    ):
        shutil.copyfile(fixtures_dir / "gis" / src, cfg.raw_dir / dest)
    cfg.manual_dir.mkdir(parents=True, exist_ok=True)
    (cfg.manual_dir / "mac_record.yaml").write_text("loaded: false\nentries: []\n")
    (cfg.manual_dir / "overrides.yaml").write_text("matches: []\nunmatch: []\nverdicts: []\n")

    rows = [_lake(0, "Big School Lot Lake", "big school lot"), _lake(1, "Mud Lake", "mud"), _lake(2, None, "")]
    lakes = gpd.GeoDataFrame(rows, geometry="geometry", crs="EPSG:4326")
    lakes.to_parquet(cfg.work_dir / "lakes.parquet", index=False)
    gpd.GeoDataFrame(
        {"id": [1000, 1001]},
        geometry=[r["geometry"].buffer(-0.001) for r in rows[:2]],
        crs="EPSG:4326",
    ).to_parquet(cfg.work_dir / "usable_water.parquet", index=False)

    records = [
        {"restriction_id": "aaaaaaaaaaaa", "rule_id": "R 281.763.3", "county": "Oakland",
         "township": "Rose Township", "lake_name_raw": "Big School Lot Lake",
         "lake_name_norm": "big school lot", "plss": [], "restriction_type": "no_high_speed",
         "scope": "lakewide", "hours": None, "season": None, "status": "active",
         "needs_review": False, "raw_text": "…", "source_url": "https://example.test", "parser": "regex"},
        {"restriction_id": "bbbbbbbbbbbb", "rule_id": None, "county": "Oakland", "township": None,
         "lake_name_raw": "Mud Lake", "lake_name_norm": "mud", "plss": [],
         "restriction_type": "no_towing", "scope": "lakewide", "hours": None, "season": None,
         "status": "rescinded", "needs_review": False, "raw_text": "…",
         "source_url": "https://example.test", "parser": "regex"},
    ]
    (cfg.work_dir / "restrictions.jsonl").write_text("\n".join(json.dumps(r) for r in records))
    (cfg.work_dir / "matches.json").write_text(json.dumps([
        {"restriction_id": "aaaaaaaaaaaa", "lake_id": 1000, "score": 0.9, "method": "exact+plss",
         "needs_review": False},
        {"restriction_id": "bbbbbbbbbbbb", "lake_id": 1001, "score": 1.0, "method": "exact+plss",
         "needs_review": False},
    ]))
    (cfg.work_dir / "verdicts.json").write_text(json.dumps({
        "1000": {"id": 1000, "verdict": "restricted", "reasons": [], "flags": ["no_public_access"]},
        "1001": {"id": 1001, "verdict": "clear", "reasons": [], "flags": []},
        "1002": {"id": 1002, "verdict": "unknown", "reasons": [], "flags": []},
    }))
    (cfg.work_dir / "overlays.json").write_text(json.dumps({
        "1000": {"public_access": True, "access": "School Lot Lake BAS", "federal_unit": None,
                 "airspace_class": "D"},
        "1001": {"public_access": False, "access": None, "federal_unit": None, "airspace_class": None},
        "1002": {"public_access": False, "access": None, "federal_unit": None, "airspace_class": None},
    }))
    (cfg.work_dir / "synthetic_restrictions.json").write_text(json.dumps([
        {"restriction_id": "cccccccccccc", "restriction_type": "federal_no_landing", "county": "Oakland",
         "township": None, "lake_name_raw": None, "lake_name_norm": "", "plss": [], "scope": "lakewide",
         "status": "active", "needs_review": False, "raw_text": "36 CFR 2.17", "parser": "manual",
         "source_url": "https://example.test", "lake_ids": [1002]},
    ]))
    return cfg


def test_build_writes_the_whole_pack(work_dir):
    cfg = work_dir
    assert build.run(cfg, argparse.Namespace(skip_basemap=True, skip_tiles=False)) == 0
    out = cfg.out_dir
    for name in ("index.json", "restrictions.json", "rules.json", "pack.json",
                 "lakes.pmtiles", "usable_water.pmtiles", "overlays.pmtiles"):
        assert (out / name).exists(), name

    pack = json.loads((out / "pack.json").read_text())
    assert pack["version"] == cfg.run_date
    assert pack["rules_version"] == json.loads((cfg.rules_dir / "rules.json").read_text())["version"]
    assert pack["mac_record_loaded"] is False
    assert pack["counts"] == {"lakes": 3, "restrictions": 2, "needs_review": 0}
    assert "basemap.pmtiles" not in {f["name"] for f in pack["files"]}

    for entry in pack["files"]:
        path = out / entry["name"]
        assert path.stat().st_size == entry["bytes"]
        assert hashlib.sha256(path.read_bytes()).hexdigest() == entry["sha256"], entry["name"]


def test_index_entries_carry_every_contract_field(work_dir):
    cfg = work_dir
    build.run(cfg, argparse.Namespace(skip_basemap=True, skip_tiles=True))
    index = json.loads((cfg.out_dir / "index.json").read_text())
    assert len(index) == 3
    for entry in index:
        assert set(entry) == INDEX_FIELDS

    by_id = {e["id"]: e for e in index}
    assert by_id[1000]["name"] == "Big School Lot Lake"
    assert by_id[1000]["verdict"] == "restricted"
    assert by_id[1000]["flags"] == ["no_public_access"]
    assert by_id[1000]["restriction_ids"] == ["aaaaaaaaaaaa"]
    assert by_id[1000]["access"] == "School Lot Lake BAS"
    assert by_id[1000]["bbox"] == [pytest.approx(LON0), pytest.approx(LAT0),
                                   pytest.approx(LON0 + 0.01), pytest.approx(LAT0 + 0.01)]
    assert by_id[1000]["chord_bearing_deg"] == 47
    assert by_id[1002]["name"] is None                    # unnamed lakes get null, not ""
    assert by_id[1002]["restriction_ids"] == ["cccccccccccc"]  # synthetic federal record
    assert by_id[1001]["restriction_ids"] == []           # its only rule is rescinded
    assert by_id[1001]["access"] is None


def test_restrictions_json_drops_rescinded_and_fills_lake_ids(work_dir):
    cfg = work_dir
    build.run(cfg, argparse.Namespace(skip_basemap=True, skip_tiles=True))
    restrictions = json.loads((cfg.out_dir / "restrictions.json").read_text())
    assert set(restrictions) == {"aaaaaaaaaaaa", "cccccccccccc"}
    assert restrictions["aaaaaaaaaaaa"]["lake_ids"] == [1000]
    assert restrictions["aaaaaaaaaaaa"]["match_confidence"] == 0.9
    assert restrictions["cccccccccccc"]["lake_ids"] == [1002]
    assert restrictions["aaaaaaaaaaaa"]["raw_text"] == "…"
    assert restrictions["aaaaaaaaaaaa"]["rule_id"] == "R 281.763.3"


def test_pmtiles_layers_and_properties(work_dir):
    cfg = work_dir
    build.run(cfg, argparse.Namespace(skip_basemap=True, skip_tiles=False))

    def layers(path):
        out = subprocess.run(
            ["pmtiles", "show", "--metadata", str(path)], capture_output=True, text=True, check=True
        )
        meta = json.loads(out.stdout)
        raw = meta.get("vector_layers")
        return json.loads(raw) if isinstance(raw, str) else raw

    lakes_layers = layers(cfg.out_dir / "lakes.pmtiles")
    assert [layer["id"] for layer in lakes_layers] == ["lakes"]
    fields = lakes_layers[0]["fields"]
    assert set(fields) == {"id", "name", "verdict", "flags", "county"}
    assert fields["id"] == "Number"
    assert lakes_layers[0]["minzoom"] == 6 and lakes_layers[0]["maxzoom"] == 14

    usable = layers(cfg.out_dir / "usable_water.pmtiles")
    assert [layer["id"] for layer in usable] == ["usable_water"]
    assert usable[0]["minzoom"] == 12

    show = subprocess.run(
        ["pmtiles", "show", str(cfg.out_dir / "lakes.pmtiles")], capture_output=True, text=True, check=True
    )
    assert "min zoom: 6" in show.stdout and "max zoom: 14" in show.stdout


def test_overlays_pmtiles_has_the_four_layers(work_dir):
    cfg = work_dir
    build.run(cfg, argparse.Namespace(skip_basemap=True, skip_tiles=False))
    out = subprocess.run(
        ["pmtiles", "show", "--metadata", str(cfg.out_dir / "overlays.pmtiles")],
        capture_output=True, text=True, check=True,
    )
    raw = json.loads(out.stdout)["vector_layers"]
    layers = json.loads(raw) if isinstance(raw, str) else raw
    assert sorted(layer["id"] for layer in layers) == ["airports", "airspace", "bas", "federal"]
    for layer in layers:
        assert "name" in layer["fields"] and "kind" in layer["fields"]


def test_tippecanoe_version_detection():
    assert build.tippecanoe_writes_pmtiles() in (True, False)
    with pytest.raises(RuntimeError):
        build.tippecanoe_writes_pmtiles("definitely-not-tippecanoe")


def test_sha256_file_matches_hashlib(tmp_path):
    path = tmp_path / "x.bin"
    path.write_bytes(b"seaplane" * 1000)
    assert build.sha256_file(path) == hashlib.sha256(path.read_bytes()).hexdigest()


def test_build_without_geometry_fails_cleanly(tmp_path, monkeypatch):
    from seaplane_pipeline.config import Config

    monkeypatch.setenv("SEAPLANE_DATA_DIR", str(tmp_path / "data"))
    cfg = Config()
    cfg.ensure_dirs()
    assert build.run(cfg, argparse.Namespace(skip_basemap=True, skip_tiles=True)) == 2
