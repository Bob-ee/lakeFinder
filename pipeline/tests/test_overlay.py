"""overlay.py: public access, federal unit, and airspace class from synthetic geometry."""
from __future__ import annotations

import argparse
import json

import geopandas as gpd
from shapely.geometry import Point, box

from seaplane_pipeline import overlay

LAT0, LON0 = 42.70, -83.60


def lakes_frame():
    """Two 1 km-ish lakes plus one far away."""
    return gpd.GeoDataFrame(
        {"id": [101, 102, 103], "name": ["Cass Lake", "Private Lake", "Remote Lake"]},
        geometry=[
            box(LON0, LAT0, LON0 + 0.012, LAT0 + 0.009),
            box(LON0 + 0.05, LAT0, LON0 + 0.062, LAT0 + 0.009),
            box(LON0 + 0.50, LAT0, LON0 + 0.512, LAT0 + 0.009),
        ],
        crs="EPSG:4326",
    )


def test_public_access_within_50_m_only():
    # ~0.00025 deg lon is about 20 m here; 0.005 deg is about 400 m.
    bas = gpd.GeoDataFrame(
        {
            "name": ["Cass Lake BAS", "Too Far Launch", "River Launch"],
            "waterbodytype": ["Inland Lake", "Inland Lake", "River/Stream"],
        },
        geometry=[
            Point(LON0 + 0.012 + 0.00025, LAT0 + 0.004),  # ~20 m off the east shore
            Point(LON0 + 0.05 - 0.005, LAT0 + 0.004),      # ~400 m from Private Lake
            Point(LON0 + 0.0001, LAT0 + 0.004),            # on Cass Lake but a river site
        ],
        crs="EPSG:4326",
    )
    out = overlay.compute_overlays(lakes_frame(), bas=bas)
    assert out["101"] == {
        "public_access": True, "access": "Cass Lake BAS", "federal_unit": None, "airspace_class": None,
    }
    assert out["102"]["public_access"] is False and out["102"]["access"] is None
    assert out["103"]["public_access"] is False


def test_federal_unit_containment_uses_the_centroid():
    federal = gpd.GeoDataFrame(
        {"name": ["Sleeping Bear Dunes National Lakeshore"], "kind": ["nps"]},
        geometry=[box(LON0 - 0.01, LAT0 - 0.01, LON0 + 0.02, LAT0 + 0.02)],
        crs="EPSG:4326",
    )
    out = overlay.compute_overlays(lakes_frame(), federal=federal)
    assert out["101"]["federal_unit"] == "Sleeping Bear Dunes National Lakeshore"
    assert out["102"]["federal_unit"] is None


def test_airspace_prefers_surface_floors_and_the_most_restrictive_class():
    airspace = gpd.GeoDataFrame(
        {
            "NAME": ["DETROIT CLASS B SHELF", "PONTIAC", "SURFACE E"],
            "CLASS": ["B", "D", "E"],
            "LOWER_VAL": [6000, 0, 0],
        },
        geometry=[
            box(LON0 - 1, LAT0 - 1, LON0 + 1, LAT0 + 1),          # covers everything, 6000 ft floor
            box(LON0 - 0.01, LAT0 - 0.01, LON0 + 0.02, LAT0 + 0.02),
            box(LON0 - 0.01, LAT0 - 0.01, LON0 + 0.10, LAT0 + 0.02),
        ],
        crs="EPSG:4326",
    )
    out = overlay.compute_overlays(lakes_frame(), airspace=airspace)
    assert out["101"]["airspace_class"] == "D"   # D beats the overlapping surface E
    assert out["102"]["airspace_class"] == "E"
    assert out["103"]["airspace_class"] is None  # outside everything with a surface floor

    literal = overlay.compute_overlays(lakes_frame(), airspace=airspace, airspace_any_floor=True)
    assert literal["103"]["airspace_class"] == "B"  # the 6000 ft shelf now counts


def test_lower_code_sfc_is_not_treated_as_a_surface_floor():
    """The FAA layer says LOWER_CODE='SFC' for a 700 ft Class E floor; only LOWER_VAL==0 counts."""
    airspace = gpd.GeoDataFrame(
        {"NAME": ["E700"], "CLASS": ["E"], "LOWER_VAL": [700], "LOWER_CODE": ["SFC"]},
        geometry=[box(LON0 - 1, LAT0 - 1, LON0 + 1, LAT0 + 1)],
        crs="EPSG:4326",
    )
    out = overlay.compute_overlays(lakes_frame(), airspace=airspace)
    assert all(v["airspace_class"] is None for v in out.values())


def test_empty_inputs_still_produce_an_entry_per_lake():
    out = overlay.compute_overlays(lakes_frame())
    assert set(out) == {"101", "102", "103"}
    assert all(
        v == {"public_access": False, "access": None, "federal_unit": None, "airspace_class": None}
        for v in out.values()
    )


def test_load_federal_units_filters_fws_to_michigan(fixtures_dir, tmp_path, monkeypatch):
    from seaplane_pipeline.config import Config

    monkeypatch.setenv("SEAPLANE_DATA_DIR", str(tmp_path / "data"))
    cfg = Config()
    cfg.ensure_dirs()
    import shutil

    shutil.copyfile(fixtures_dir / "gis" / "nps_boundary.sample.json", cfg.raw_dir / "nps_boundary.geojson")
    shutil.copyfile(
        fixtures_dir / "gis" / "fws_refuge_boundary.sample.json", cfg.raw_dir / "fws_refuge_boundary.geojson"
    )
    units = overlay.load_federal_units(cfg)
    names = set(units["name"])
    assert "Keweenaw National Historical Park" in names
    assert "Harbor Island National Wildlife Refuge" in names  # ORGNAME upper-cased in source
    assert set(units["kind"]) == {"nps", "fws"}


def test_run_writes_overlays_json(tmp_path, monkeypatch):
    from seaplane_pipeline.config import Config

    monkeypatch.setenv("SEAPLANE_DATA_DIR", str(tmp_path / "data"))
    cfg = Config()
    cfg.ensure_dirs()
    lakes_frame().to_parquet(cfg.work_dir / "lakes.parquet", index=False)
    assert overlay.run(cfg, argparse.Namespace(access_distance_m=50.0, airspace_any_floor=False)) == 0
    out = json.loads((cfg.work_dir / "overlays.json").read_text())
    assert set(out) == {"101", "102", "103"}


def test_run_without_geometry_fails_cleanly(tmp_path, monkeypatch):
    from seaplane_pipeline.config import Config

    monkeypatch.setenv("SEAPLANE_DATA_DIR", str(tmp_path / "data"))
    cfg = Config()
    cfg.ensure_dirs()
    assert overlay.run(cfg, argparse.Namespace()) == 2


# --- big water -------------------------------------------------------------


def big_water_frame():
    """One Great Lake, one connecting water, one ordinary inland lake, all 1 km-ish boxes."""
    return gpd.GeoDataFrame(
        {
            "id": [201, 202, 203],
            "name": ["Lake St. Clair", "Detroit River", "Cass Lake"],
            "kind": ["great_lake", "connecting_water", "lake"],
        },
        geometry=[
            box(LON0, LAT0, LON0 + 0.12, LAT0 + 0.09),
            box(LON0 + 0.20, LAT0, LON0 + 0.32, LAT0 + 0.09),
            box(LON0 + 0.50, LAT0, LON0 + 0.512, LAT0 + 0.009),
        ],
        crs="EPSG:4326",
    )


def test_big_water_takes_access_from_its_own_site_type():
    bas = gpd.GeoDataFrame(
        {
            "name": ["Great Lake Launch", "River Launch", "Inland Launch"],
            "waterbodytype": ["Great Lake", "River/Stream", "Inland Lake"],
        },
        geometry=[
            Point(LON0 + 0.06, LAT0 + 0.04),        # inside the Great Lake
            Point(LON0 + 0.26, LAT0 + 0.04),        # inside the connecting water
            Point(LON0 + 0.50, LAT0 + 0.004),       # inside the inland lake
        ],
        crs="EPSG:4326",
    )
    out = overlay.compute_overlays(big_water_frame(), bas=bas)
    assert out["201"]["access"] == "Great Lake Launch"
    assert out["202"]["access"] == "River Launch"
    assert out["203"]["access"] == "Inland Launch"


def test_a_great_lake_site_never_reaches_an_inland_lake():
    """A "Great Lake" site at a river mouth must not flag the inland lake it sits on."""
    bas = gpd.GeoDataFrame(
        {"name": ["Great Lake Launch"], "waterbodytype": ["Great Lake"]},
        geometry=[Point(LON0 + 0.50, LAT0 + 0.004)],
        crs="EPSG:4326",
    )
    out = overlay.compute_overlays(big_water_frame(), bas=bas)
    assert out["203"]["public_access"] is False


def test_big_water_flags_are_computed_by_intersection_not_by_centroid():
    """Selfridge's Class D covers a corner of Lake St. Clair; the centroid is open water."""
    corner = box(LON0 - 0.01, LAT0 - 0.01, LON0 + 0.02, LAT0 + 0.02)
    airspace = gpd.GeoDataFrame(
        {"CLASS": ["D"], "LOWER_VAL": [0]}, geometry=[corner], crs="EPSG:4326"
    )
    federal = gpd.GeoDataFrame({"name": ["Some Refuge"], "kind": ["fws"]}, geometry=[corner], crs="EPSG:4326")
    out = overlay.compute_overlays(big_water_frame(), federal=federal, airspace=airspace)
    assert out["201"]["airspace_class"] == "D"
    assert out["201"]["federal_unit"] == "Some Refuge"
    # The inland lake keeps the centroid rule, so a corner touch does not flag it.
    inland_corner = box(LON0 + 0.499, LAT0 - 0.001, LON0 + 0.5005, LAT0 + 0.001)
    out2 = overlay.compute_overlays(
        big_water_frame(),
        airspace=gpd.GeoDataFrame({"CLASS": ["D"], "LOWER_VAL": [0]}, geometry=[inland_corner], crs="EPSG:4326"),
    )
    assert out2["203"]["airspace_class"] is None


def test_big_water_federal_unit_is_the_largest_overlap():
    """"Part of Lake Superior is Isle Royale", not the refuge that clips 0.16 km2 of it."""
    big = box(LON0 - 0.01, LAT0 - 0.01, LON0 + 0.10, LAT0 + 0.08)
    tiny = box(LON0 - 0.001, LAT0 - 0.001, LON0 + 0.001, LAT0 + 0.001)
    federal = gpd.GeoDataFrame(
        {"name": ["Tiny Sliver Refuge", "Big National Park"], "kind": ["fws", "nps"]},
        geometry=[tiny, big],
        crs="EPSG:4326",
    )
    out = overlay.compute_overlays(big_water_frame(), federal=federal)
    assert out["201"]["federal_unit"] == "Big National Park"
