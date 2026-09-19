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


def test_load_lakes_filters_type_names_and_small_unnamed(fixtures_dir):
    """The GIS sample has 2 named lakes, 1 tiny unnamed lake, a river and a swamp."""
    sample = fixtures_dir / "gis" / "hydrography_polygons.sample.json"
    kept = geometry.load_lakes(sample, min_unnamed_acres=20.0)
    assert sorted(n for n in kept["name"] if n) == ["Hidden Lake", "Lake Ahmik"]
    assert len(kept) == 2  # the 1.9-acre unnamed lake, the river and the swamp are all dropped
    assert kept["name"].notna().all()

    with_small = geometry.load_lakes(sample, min_unnamed_acres=0.5)
    assert len(with_small) == 3
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
