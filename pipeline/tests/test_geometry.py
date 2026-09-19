"""geometry.py: area, longest chord, bearing, filtering, and the parquet outputs.

The three fixtures in `fixtures/lakes/` are hand-built shapes with independently computed
expectations (`expected.json`): a circle, a long thin rectangle on a 030 bearing, and an L-shape
whose longest convex-hull chord is *not* inside the polygon. Tolerance is 2%.
"""
from __future__ import annotations

import argparse
import json
import math

import geopandas as gpd
import numpy as np
import pytest
import shapely
from shapely.geometry import LineString, Polygon

from seaplane_pipeline import geometry

TOL = 0.02
FT_PER_M = geometry.FT_PER_M


@pytest.fixture
def lake_fixtures(fixtures_dir):
    expected = {e["name"]: e for e in json.loads((fixtures_dir / "lakes" / "expected.json").read_text())}
    return fixtures_dir / "lakes", expected


def _load_projected(path):
    gdf = gpd.read_file(path).to_crs(geometry.MEASURE_CRS)
    return gdf.geometry.iloc[0]


def _bearing(poly, a, b):
    from pyproj import Transformer

    to_wgs = Transformer.from_crs(geometry.MEASURE_CRS, geometry.WGS84, always_xy=True)
    lon1, lat1 = to_wgs.transform(a[0], a[1])
    lon2, lat2 = to_wgs.transform(b[0], b[1])
    az, _, _ = geometry._GEOD.inv(lon1, lat1, lon2, lat2)
    return az % 180.0


@pytest.mark.parametrize("name", ["circle", "rect30", "lshape"])
def test_area_and_chord_match_hand_computed_values(lake_fixtures, name):
    directory, expected = lake_fixtures
    exp = expected[name]
    poly = _load_projected(directory / f"{name}.geojson")

    acres = poly.area / geometry.M2_PER_ACRE
    assert acres == pytest.approx(exp["area_acres"], rel=TOL)

    length_m, a, b = geometry.longest_chord(poly)
    assert length_m * FT_PER_M == pytest.approx(exp["chord_ft"], rel=TOL)
    assert poly.covers(LineString([a, b]))

    if exp["chord_bearing_deg"] is not None:
        assert _bearing(poly, a, b) == pytest.approx(exp["chord_bearing_deg"], rel=TOL)


def test_lshape_chord_is_not_the_convex_hull_diameter(lake_fixtures):
    """The tip-to-tip hull chord crosses the notch, so it must be rejected."""
    directory, expected = lake_fixtures
    exp = expected["lshape"]
    poly = _load_projected(directory / "lshape.geojson")
    length_ft = geometry.longest_chord(poly)[0] * FT_PER_M
    assert length_ft < exp["hull_diameter_ft"] * 0.9
    assert length_ft == pytest.approx(exp["chord_ft"], rel=TOL)


# --- extent by bearing --------------------------------------------------------


def test_extent_by_bearing_rect30_matches_analytic_rectangle_chord(lake_fixtures):
    """22.5 deg bins mean the rectangle's true-030 long axis falls in bin 1 (centered 22.5 deg), 7.5
    deg off axis, so the bin is NOT close to the full chord/long side -- verify against the
    closed-form chord-in-a-rectangle formula instead of assuming near-full-length.
    """
    directory, _ = lake_fixtures
    raw = gpd.read_file(directory / "rect30.geojson")
    centroid = raw.geometry.iloc[0].centroid
    convergence = geometry.meridian_convergence_deg(centroid.x, centroid.y)
    poly = raw.to_crs(geometry.MEASURE_CRS).geometry.iloc[0]

    bins = geometry.extent_by_bearing(poly, convergence)
    assert len(bins) == 16

    # 6000 x 40 m rectangle, long axis on a true 030 bearing (half-extents a=3000, b=20 m; see
    # rect30.geojson's `_description` and expected.json). Longest chord inside a centered a x b
    # rectangle along a direction `theta` off the long axis: 2a/cos(theta) while shallow, or
    # 2b/sin(theta) once theta exceeds atan(b/a) (the rectangle's own diagonal half-angle).
    def rect_chord_m(theta_deg: float, a: float = 3000.0, b: float = 20.0) -> float:
        theta = math.radians(abs(theta_deg))
        if math.tan(theta) <= b / a:
            return 2 * a / math.cos(theta)
        return 2 * b / math.sin(theta)

    axis_bearing = 30.0  # theta below is always measured from this long axis, not the short one
    expected_bin1_ft = rect_chord_m(22.5 - axis_bearing) * geometry.FT_PER_M
    expected_bin5_ft = rect_chord_m(112.5 - axis_bearing) * geometry.FT_PER_M

    assert bins[1] == pytest.approx(expected_bin1_ft, rel=0.03)
    assert bins[5] == pytest.approx(expected_bin5_ft, rel=0.03)
    # Bin 5 (nearest the short, 120 deg axis) is close to the 40 m width; bin 1 (nearest the long,
    # 30 deg axis) is not close to the long side (19685 ft) precisely because of the 7.5 deg offset.
    assert bins[5] == pytest.approx(40 * geometry.FT_PER_M, rel=0.05)
    assert bins[1] < 6561 * 0.5  # nowhere near the long side, unlike a naive "nearest bin" guess


@pytest.mark.parametrize("name", ["circle", "rect30", "lshape"])
def test_extent_by_bearing_opposite_bins_match(lake_fixtures, name):
    """A segment has two ends: bin i always equals bin (i + 8) % 16."""
    directory, _ = lake_fixtures
    poly = _load_projected(directory / f"{name}.geojson")
    bins = geometry.extent_by_bearing(poly, convergence_deg=0.0)
    for i in range(8):
        assert bins[i] == bins[i + 8]


def test_extent_by_bearing_max_is_near_the_longest_chord_for_a_convex_shape(lake_fixtures):
    directory, expected = lake_fixtures
    poly = _load_projected(directory / "circle.geojson")
    chord_ft = expected["circle"]["chord_ft"]
    bins = geometry.extent_by_bearing(poly, convergence_deg=0.0)
    assert max(bins) <= chord_ft * 1.02          # small tolerance over the true longest chord
    assert max(bins) >= chord_ft * 0.90          # within ~10% for a convex, near-isotropic shape


def test_extent_by_bearing_does_not_span_an_island():
    """A hole through the middle must split the east-west run: the answer is the longer single
    piece, never the sum of both sides and never the un-holed full width.
    """
    outer = Polygon(
        [(0, 0), (1000, 0), (1000, 1000), (0, 1000)],
        holes=[[(400, 1), (600, 1), (600, 999), (400, 999)]],
    )
    bins = geometry.extent_by_bearing(outer, convergence_deg=0.0)
    east_ft = bins[4]  # bearing 90 = east; every sampled row is blocked by the hole at x in [400,600]
    assert east_ft == pytest.approx(400 * geometry.FT_PER_M, rel=0.02)
    assert east_ft < 800 * geometry.FT_PER_M * 0.9   # not the sum of the two sides
    assert east_ft < 1000 * geometry.FT_PER_M * 0.9  # not the un-holed width


def test_longest_chord_falls_back_to_the_longest_interior_piece():
    """A ring so coarse that no simplified vertex pair is inside still yields a real chord."""
    # A C-shape: every long vertex pair crosses the opening.
    outer = shapely.geometry.Point(0, 0).buffer(1000, quad_segs=64)
    inner = shapely.geometry.Point(0, 0).buffer(600, quad_segs=64)
    notch = Polygon([(0, -1200), (1200, -1200), (1200, 1200), (0, 1200)])
    c_shape = outer.difference(inner).difference(notch)
    length, a, b = geometry.longest_chord(c_shape, max_vertices=40, max_candidates=50)
    assert length > 0
    assert c_shape.buffer(1e-6).covers(LineString([a, b]))


def test_reduce_ring_caps_vertex_count():
    circle = shapely.geometry.Point(0, 0).buffer(1000, quad_segs=256)
    coords = shapely.get_coordinates(circle.exterior)
    assert len(coords) > 200
    assert len(geometry.reduce_ring(coords, 200)) <= 200
    small = coords[:10]
    assert len(geometry.reduce_ring(small, 200)) == 10


def test_largest_part_picks_the_biggest_polygon():
    a = Polygon([(0, 0), (0, 10), (10, 10), (10, 0)])
    b = Polygon([(100, 100), (100, 101), (101, 101), (101, 100)])
    multi = shapely.geometry.MultiPolygon([b, a])
    assert geometry.largest_part(multi).equals(a)


def test_load_lakes_keeps_lakes_and_rivers_and_drops_swamps(fixtures_dir):
    """The GIS sample has 2 named lakes, 1 tiny unnamed lake, a named river and a swamp."""
    sample = fixtures_dir / "gis" / "hydrography_polygons.sample.json"
    kept = geometry.load_lakes(sample, min_unnamed_acres=20.0)
    assert sorted(n for n in kept["name"] if n) == ["Hidden Lake", "Lake Ahmik", "Siskiwit River"]
    assert len(kept) == 3  # the 1.9-acre unnamed lake and the swamp are dropped
    assert kept["name"].notna().all()
    assert dict(zip(kept["name"], kept["kind"], strict=True)) == {
        "Hidden Lake": "lake",
        "Lake Ahmik": "lake",
        "Siskiwit River": "river",  # a 3-acre named river polygon is kept, like a named lake
    }

    with_small = geometry.load_lakes(sample, min_unnamed_acres=0.5)
    assert len(with_small) == 4
    assert with_small["name"].isna().sum() == 1  # padded " " became None, not the string " "


def _combined_fixture(directory, tmp_path):
    features = []
    for name in ("circle", "rect30", "lshape"):
        fc = json.loads((directory / f"{name}.geojson").read_text())
        features.extend(fc["features"])
    features.append({**features[0], "properties": {"NAME": "  ", "TYPE": "lake"}})
    path = tmp_path / "hydro.geojson"
    path.write_text(json.dumps({"type": "FeatureCollection", "features": features}))
    return path


def test_run_writes_both_parquets(lake_fixtures, tmp_path, monkeypatch):
    from seaplane_pipeline.config import Config

    directory, expected = lake_fixtures
    monkeypatch.setenv("SEAPLANE_DATA_DIR", str(tmp_path / "data"))
    cfg = Config()
    cfg.ensure_dirs()
    args = argparse.Namespace(input=str(_combined_fixture(directory, tmp_path)), min_unnamed_acres=20.0)

    assert geometry.run(cfg, args) == 0

    lakes = gpd.read_parquet(cfg.work_dir / "lakes.parquet")
    assert len(lakes) == 4  # 3 named + the unnamed copy of the 776-acre circle
    assert lakes["id"].is_unique
    assert (lakes["id"] > 0).all()
    assert lakes.crs.to_epsg() == 4326
    assert lakes["county"].isna().all()  # no counties.geojson in this temp data dir

    rect = lakes[lakes["name"] == "rect30"].iloc[0]
    assert rect["chord_ft"] == pytest.approx(expected["rect30"]["chord_ft"], rel=TOL)
    assert rect["chord_bearing_deg"] == pytest.approx(expected["rect30"]["chord_bearing_deg"], rel=0.05)
    assert 0 <= rect["chord_bearing_deg"] < 180
    assert rect["minx"] < rect["lon"] < rect["maxx"]
    assert rect["miny"] < rect["lat"] < rect["maxy"]

    # The 40 m wide rectangle vanishes under a 100 ft erosion; the circle survives.
    usable = gpd.read_parquet(cfg.work_dir / "usable_water.parquet")
    assert set(usable["id"]) <= set(lakes["id"])
    circle_id = int(lakes[lakes["name"] == "circle"].iloc[0]["id"])
    assert circle_id in set(usable["id"])
    assert int(rect["id"]) not in set(usable["id"])


# --- rivers -------------------------------------------------------------------
#
# `river_meander.geojson` is a hand-built 80 m wide channel: a 600 m amplitude sine meander, then a
# dead-straight 5,000 m east-west limb, then a quarter turn into a 4,000 m north-south limb, with
# bank noise on both sides so no stretch of boundary is a straight edge. 1,500 vertices.


@pytest.fixture
def meander_river(fixtures_dir):
    gdf = gpd.read_file(fixtures_dir / "lakes" / "river_meander.geojson").to_crs(geometry.MEASURE_CRS)
    return gdf.geometry.iloc[0]


def test_sweep_finds_the_straight_reach_of_a_sinuous_river(meander_river):
    """The answer a pilot needs is the 5,000 m straight limb, plus a little into the bends."""
    length_m, a, b = geometry.sweep_longest_reach(meander_river)
    assert 5000 <= length_m <= 6000
    assert geometry._covers_segment(meander_river, np.asarray(a), np.asarray(b))


def test_vertex_pair_chord_collapses_on_a_river_but_the_sweep_does_not(meander_river):
    """Why rivers need their own measurement.

    `longest_chord` ranks pairs of *simplified boundary vertices*. On a meander nothing survives
    along a reach, so the answer is a cliff: it depends on which handful of vertices Douglas-Peucker
    happened to keep, and one step of thinning takes it from 5,525 m to 275 m. The scan-line sweep
    does not use boundary vertices at all, so it is flat across the same range -- which is what makes
    it safe on the real 14,000-vertex Muskegon River polygon at the production budget of 200.
    """
    sweep = geometry.sweep_longest_reach(meander_river)[0]
    chords = [geometry.longest_chord(meander_river, mv, 1000)[0] for mv in (200, 120, 80, 40)]
    sweeps = [geometry.sweep_longest_reach(meander_river)[0] for _ in range(2)]

    assert min(chords) < 0.1 * sweep  # at least one budget collapses to under a tenth
    assert max(chords) <= sweep * 1.01
    assert sweeps[0] == sweeps[1]
    for mv in (200, 120, 80, 40):
        _, a, b = geometry.longest_chord(meander_river, mv, 1000)
        assert meander_river.covers(LineString([a, b]))  # never over land, only short


def test_longest_reach_takes_whichever_measurement_is_longer(meander_river, lake_fixtures):
    directory, expected = lake_fixtures
    assert geometry.longest_reach(meander_river)[0] == pytest.approx(
        geometry.sweep_longest_reach(meander_river)[0]
    )
    # On a convex lake the vertex-pair chord is exact and wins; the sweep must not drag it down.
    circle = _load_projected(directory / "circle.geojson")
    assert geometry.longest_reach(circle)[0] * FT_PER_M == pytest.approx(
        expected["circle"]["chord_ft"], rel=TOL
    )


def test_scan_longest_run_matches_the_shapely_sampler_on_a_rectangle(lake_fixtures):
    """The scan-line primitive and the shapely intersection must measure the same thing."""
    directory, _ = lake_fixtures
    rect = geometry.largest_part(_load_projected(directory / "rect30.geojson"))
    edges = geometry.part_edges(rect)
    minx, miny, maxx, maxy = rect.bounds
    run = geometry.scan_longest_run(edges, spacing=5.0)[0]
    assert run == pytest.approx(geometry._longest_segment(rect.intersection(
        LineString([(minx - 1, (miny + maxy) / 2), (maxx + 1, (miny + maxy) / 2)])
    )), rel=0.05)


def test_extent_by_bearing_needs_river_spacing_on_a_river(meander_river):
    """40 lines across a 17 km bounding box is one line per 425 m; an 80 m channel falls between them."""
    convergence = float(geometry.meridian_convergence_deg(-84.659, 43.235))
    default = geometry.extent_by_bearing(meander_river, convergence)
    adaptive = geometry.extent_by_bearing(
        meander_river, convergence, spacing_m=geometry.scan_spacing(geometry.largest_part(meander_river))
    )
    assert len(adaptive) == 16 and adaptive[:8] == adaptive[8:]
    # Both limbs show up only with river spacing; the fixed-count sampler reads them as nearly dry.
    assert adaptive[0] > 3000 and adaptive[4] > 3000        # north-south limb, east-west limb
    assert max(adaptive) > 3 * max(default[0], default[4])
    # It is the same measurement, so a denser grid can only find longer runs, never shorter ones.
    assert all(a >= d - 1 for a, d in zip(adaptive, default, strict=True))


# --- big water -------------------------------------------------------------


def _write_fc(path, features):
    """A tiny GeoJSON FeatureCollection, WGS84, the shape the `big_water_*` datasets arrive in."""
    path.write_text(json.dumps({
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "properties": props, "geometry": shapely.geometry.mapping(geom)}
            for geom, props in features
        ],
    }), encoding="utf-8")
    return path


@pytest.fixture
def big_water_cfg(tmp_path, monkeypatch):
    """A `Config` whose cache holds stand-ins for the four big-water sources.

    Geometry (degrees, near Lake St. Clair so EPSG:3078 is sane): the "Great Lake" is a 0.2 deg
    square; the "connecting water" is a strip overlapping its east edge; one inland lake sits inside
    the Great Lake and one 200 m sliver is left over to be dropped.
    """
    from shapely.geometry import box

    from seaplane_pipeline import config as config_mod

    monkeypatch.setenv("SEAPLANE_DATA_DIR", str(tmp_path))
    cfg = config_mod.Config()
    cfg.cache_dir.mkdir(parents=True, exist_ok=True)
    _write_fc(cfg.cache_dir / "big_water_erie.geojson", [(box(-82.9, 42.3, -82.7, 42.5), {})])
    _write_fc(cfg.cache_dir / "big_water_michigan.geojson", [
        (box(-86.5, 43.0, -86.3, 43.2), {"LAKE_NAME": "Lake Michigan"}),
        (box(-88.6, 43.8, -88.5, 43.9), {"LAKE_NAME": "Lake Oskosh"}),
    ])
    _write_fc(cfg.cache_dir / "big_water_st_clair.geojson", [(box(-82.6, 42.3, -82.5, 42.4), {})])
    _write_fc(cfg.cache_dir / "big_water_ifr.geojson", [
        (box(-82.75, 42.3, -82.65, 42.5), {"WBID": "dr"}),
        (box(-84.2, 46.4, -84.1, 46.5), {"WBID": "scr"}),
        (box(-84.4, 46.4, -84.3, 46.5), {"WBID": "smr"}),
        (box(-87.0, 45.0, -86.9, 45.1), {"WBID": "lh"}),
        (box(-89.0, 47.0, -88.9, 47.1), {"WBID": "ls"}),
        (box(-83.4, 41.8, -83.3, 41.9), {"WBID": "le"}),     # ignored: the MapServer layer wins
    ])
    return cfg


def _by_name(gdf):
    return {row["name"]: row for _, row in gdf.iterrows()}


def test_load_big_water_reads_every_source_with_its_published_name(big_water_cfg):
    out = geometry.load_big_water(big_water_cfg)
    got = _by_name(out)
    assert set(got) == {
        "Lake Superior", "Lake Michigan", "Lake Huron", "Lake Erie", "Lake St. Clair",
        "St. Marys River", "St. Clair River", "Detroit River",
    }
    assert got["Lake Erie"]["kind"] == "great_lake"
    assert got["Detroit River"]["kind"] == "connecting_water"
    # "Lake Oskosh" shares the Lake Michigan layer and is filtered out by LAKE_NAME.
    assert got["Lake Michigan"]["geometry"].bounds[0] > -87.0


def test_load_big_water_subtracts_the_connecting_water_from_the_great_lake(big_water_cfg):
    got = _by_name(geometry.load_big_water(big_water_cfg))
    erie, dr = got["Lake Erie"]["geometry"], got["Detroit River"]["geometry"]
    assert erie.intersection(dr).area == pytest.approx(0.0, abs=1e-12)
    assert dr.bounds == pytest.approx((-82.75, 42.3, -82.65, 42.5))   # the river keeps its mouth


def test_load_big_water_subtracts_the_inland_polygons(big_water_cfg):
    from shapely.geometry import box

    inland = gpd.GeoDataFrame(
        {"name": ["Torch Lake", "A Sliver"], "kind": ["lake", "lake"]},
        geometry=[box(-82.85, 42.35, -82.80, 42.40), box(-82.88, 42.32, -82.8799, 42.3201)],
        crs=geometry.WGS84,
    )
    got = _by_name(geometry.load_big_water(big_water_cfg, inland))
    erie = got["Lake Erie"]["geometry"]
    assert erie.intersection(inland.geometry.iloc[0]).area == pytest.approx(0.0, abs=1e-12)
    # The hole punched by the 10 m sliver is under MIN_BIG_WATER_PART_M2, but it is a hole, not a
    # part, so what the threshold must not do is drop the lake itself.
    assert erie.area > 0


def test_load_big_water_drops_parts_under_the_minimum(big_water_cfg):
    from shapely.geometry import box

    # A cut clean across the lake leaves a 0.005 deg strip: ~0.4 km2, kept; and a 2 m strip: dropped.
    inland = gpd.GeoDataFrame(
        {"name": ["Cut", "Thin Cut"], "kind": ["river", "river"]},
        geometry=[box(-82.9, 42.395, -82.7, 42.4), box(-82.9, 42.49998, -82.7, 42.4999999)],
        crs=geometry.WGS84,
    )
    erie = _by_name(geometry.load_big_water(big_water_cfg, inland))["Lake Erie"]["geometry"]
    parts = shapely.get_parts(shapely.geometry.shape(erie))
    areas = sorted(
        gpd.GeoSeries(list(parts), crs=geometry.WGS84).to_crs(geometry.MEASURE_CRS).area, reverse=True
    )
    assert all(a >= geometry.MIN_BIG_WATER_PART_M2 for a in areas), areas


def test_load_big_water_is_empty_without_the_sources(tmp_path, monkeypatch):
    from seaplane_pipeline import config as config_mod

    monkeypatch.setenv("SEAPLANE_DATA_DIR", str(tmp_path))
    out = geometry.load_big_water(config_mod.Config())
    assert len(out) == 0
    assert list(out.columns) == ["name", "kind", "area_acres", "geometry"]


def test_kind_order_is_append_only():
    """Ids hash in `KIND_ORDER` order; reordering it would renumber every published water body."""
    assert geometry.KIND_ORDER[:2] == geometry.KINDS == ("lake", "river")
    assert geometry.KIND_ORDER == ("lake", "river", "great_lake", "connecting_water")
    assert set(geometry.BIG_WATER_KINDS) == {"great_lake", "connecting_water"}
    assert "great_lake" in geometry.CHORD_SWEEP_KINDS and "great_lake" not in geometry.REACH_KINDS
