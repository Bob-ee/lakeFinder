"""wavefield.py: sample points, fetch rays, run, labels, depth, and the binary pack.

The synthetic shapes have independently known answers (a circle's fetch is its radius from the
centre, a rectangle's run is its own width), and `tests/fixtures/waves/lake_st_clair.geojson.gz` is
the verified Lake St. Clair polygon from `docs/gis-sources.md` -- unioned, simplified to 5 m and
rounded to 5 decimals to fit in the repo -- which must still reproduce that document's trial fetch
table. `rules/fixtures/wave_points.sample.*` is the cross-language pack fixture: the writer has to
reproduce it byte for byte from what the reader decodes.

Note `WaterMask`'s artificial-edge rule when reading these shapes: a straight run of boundary over
2 km is taken for a clip line and the ray through it reads as the cap, and a synthetic rectangle is
nothing but straight runs. `mask_of` therefore turns the rule off, and the tests that are *about* it
turn it back on.
"""
from __future__ import annotations

import argparse
import gzip
import math
import struct
import zipfile

import geopandas as gpd
import numpy as np
import pytest
import shapely
from shapely.geometry import Point, Polygon, box

from seaplane_pipeline import bathymetry, gnis
from seaplane_pipeline import wavefield as wf

KM = 1000.0


def mask_of(*geoms, **kw):
    """A mask with the clip-line rule off, so a synthetic rectangle reads as real shore."""
    kw.setdefault("min_artificial_m", 1e12)
    return wf.WaterMask(list(geoms), **kw)


def grid_angles(arc: bool = False):
    """Rotation angles for the 16 true bearings with no convergence correction."""
    return wf.ray_angles(0.0, arc=arc).reshape(-1)


def densify(geom, step: float = 250.0):
    """Digitize a shape's edges at a realistic vertex spacing."""
    return shapely.segmentize(geom, step)


# --- the binary pack -----------------------------------------------------------


def test_sample_file_round_trips_byte_for_byte(repo_root, tmp_path):
    fx = repo_root / "rules" / "fixtures"
    points, labels, meta = wf.read_wave_points(fx / "wave_points.sample.bin", fx / "wave_points.sample.json")
    assert meta["record_bytes"] == wf.RECORD_BYTES
    assert labels == ["Anchor Bay", "Big Muscamoot Bay", "middle", "north end"]
    assert [len(points[k]) for k in (111, 222)] == [6, 2]
    first = points[111][0]
    assert first.fetch[8] == 3920 and first.run[4] == 1200 and first.depth_dm == 30
    assert points[222][0].depth_dm == wf.DEPTH_UNKNOWN

    order = [(int(k), points[int(k)]) for k in meta["lakes"]]
    wf.write_wave_points(tmp_path / "wave_points.bin", tmp_path / "wave_points.json", order, labels)
    assert (tmp_path / "wave_points.bin").read_bytes() == (fx / "wave_points.sample.bin").read_bytes()
    assert (tmp_path / "wave_points.json").read_text() == (fx / "wave_points.sample.json").read_text()


def test_records_are_contiguous_per_water_body(tmp_path):
    def pts(n, tag):
        return [wf.pack_point(-83.0, 42.0 + i / 1000, [tag * KM] * 16, [1000.0] * 8) for i in range(n)]

    meta = wf.write_wave_points(
        tmp_path / "p.bin", tmp_path / "p.json", [(7, pts(3, 1)), (9, pts(2, 2))], ["middle"]
    )
    assert meta["lakes"] == {"7": [0, 3], "9": [3, 2]}
    raw = (tmp_path / "p.bin").read_bytes()
    assert len(raw) == 5 * wf.RECORD_BYTES
    for r in range(3):
        assert struct.unpack_from(wf.RECORD_FORMAT, raw, r * wf.RECORD_BYTES)[4] == 100
    for r in (3, 4):
        assert struct.unpack_from(wf.RECORD_FORMAT, raw, r * wf.RECORD_BYTES)[4] == 200
    # Deterministic: the same input writes the same bytes.
    wf.write_wave_points(tmp_path / "q.bin", tmp_path / "q.json", [(7, pts(3, 1)), (9, pts(2, 2))], ["middle"])
    assert (tmp_path / "q.bin").read_bytes() == raw


def test_u16_clamps_and_units():
    p = wf.pack_point(-83.0, 42.0, [1_000_000.0] * 16, [9_000_000.0] * 8, depth_dm=65535, label=3)
    assert p.fetch == [65535] * 16  # 1,000 km would be 100,000 units
    assert p.run == [65535] * 8
    q = wf.pack_point(-83.0, 42.0, [155.0] * 16, [104.0] * 8)
    assert q.fetch[0] == 16 and q.run[0] == 10  # 155 m -> 15.5 -> 16; 104 ft -> 10.4 -> 10
    assert q.depth_dm == wf.DEPTH_UNKNOWN


# --- sample points -------------------------------------------------------------


def test_spacing_clamp():
    acre = wf.M2_PER_ACRE
    assert wf.point_spacing(100 * acre) == wf.SPACING_MIN_M  # sqrt(404686/60) = 82 m -> floor
    assert wf.point_spacing(10_000 * acre) == pytest.approx(math.sqrt(10_000 * acre / 400), rel=1e-9)
    assert wf.point_spacing(2_000_000 * acre) == wf.SPACING_MAX_M
    mid = wf.point_spacing(1_000 * acre)
    assert mid == pytest.approx(math.sqrt(1_000 * acre / 60), rel=1e-9)
    assert wf.SPACING_MIN_M < mid < wf.SPACING_MAX_M


def test_grid_is_anchored_to_the_crs_not_the_polygon():
    """The same water keeps the same points when its bounding box moves."""
    a = box(0, 0, 4000, 3000)
    b = box(0, 0, 4000, 3000).union(box(-10, -10, -5, -5))  # a speck that moves the bbox
    pa = wf.grid_points(a, 500.0)
    pb = wf.grid_points(b, 500.0)
    assert len(pa) == 8 * 6
    assert np.allclose(pa, pb[: len(pa)])
    # Nodes sit at (k + 0.5) * spacing, ordered south to north then west to east.
    assert np.allclose(pa[0], [250.0, 250.0])
    assert np.allclose(pa[1], [750.0, 250.0])
    assert np.allclose(pa[8], [250.0, 750.0])
    assert wf.grid_points(a, 500.0).tolist() == pa.tolist()


def test_grid_falls_back_to_one_point_when_nothing_fits():
    sliver = box(10, 10, 60, 12000)  # 50 m wide: no 500 m node lands inside
    pts = wf.grid_points(sliver, 500.0)
    assert len(pts) == 1
    assert sliver.contains(Point(pts[0]))


# --- fetch ---------------------------------------------------------------------


def test_circle_fetch_is_the_radius_from_the_centre():
    m = mask_of(Point(0, 0).buffer(5 * KM, quad_segs=256))
    d = m.fetch_rays([0.0], [0.0], grid_angles())[0]
    assert np.allclose(d, 5 * KM, rtol=0.001)
    off = m.fetch_rays([2 * KM], [0.0], grid_angles())[0]
    assert off[4] == pytest.approx(3 * KM, rel=0.002)  # east
    assert off[12] == pytest.approx(7 * KM, rel=0.002)  # west


def test_rectangle_fetch():
    m = mask_of(densify(box(-2 * KM, -1 * KM, 2 * KM, 1 * KM)))
    d = m.fetch_rays([0.0], [0.0], grid_angles())[0]
    assert d[0] == pytest.approx(1 * KM, rel=0.01)  # north: half the height
    assert d[4] == pytest.approx(2 * KM, rel=0.01)  # east: half the width
    assert d[2] == pytest.approx(math.hypot(1, 1) * KM, rel=0.01)  # northeast corner


def test_an_island_blocks_the_ray():
    lake = Point(0, 0).buffer(5 * KM, quad_segs=128)
    island = Point(0, 2 * KM).buffer(500, quad_segs=64)
    m = mask_of(lake.difference(island))
    d = m.fetch_rays([0.0], [0.0], grid_angles())[0]
    assert d[0] == pytest.approx(1.5 * KM, rel=0.01)  # stopped at the island
    assert d[8] == pytest.approx(5 * KM, rel=0.01)  # south is still open


def test_a_ray_crosses_between_touching_water_bodies():
    a = densify(box(0, 0, 1 * KM, 1 * KM))
    b = densify(box(1 * KM, 0, 4 * KM, 1 * KM))
    both = mask_of(a, b).fetch_rays([500.0], [500.0], grid_angles())[0]
    alone = mask_of(a).fetch_rays([500.0], [500.0], grid_angles())[0]
    assert both[4] == pytest.approx(3.5 * KM, rel=0.01)  # east, all the way across b
    assert alone[4] == pytest.approx(0.5 * KM, rel=0.01)
    assert both[12] == pytest.approx(0.5 * KM, rel=0.01)  # west is unaffected


def test_a_ray_crosses_between_overlapping_water_bodies():
    a = densify(box(0, 0, 1 * KM, 1 * KM))
    b = densify(box(0.9 * KM, 0.2 * KM, 4 * KM, 0.8 * KM))
    d = mask_of(a, b).fetch_rays([500.0], [500.0], grid_angles())[0]
    assert d[4] == pytest.approx(3.5 * KM, rel=0.01)


def _straight_clip_polygon(step: float):
    """Water clipped on the east by a 5 km straight line, digitized every `step` metres."""
    ring = [(-2 * KM, -2.5 * KM), (3 * KM, -2.5 * KM), (3 * KM, 2.5 * KM), (-2 * KM, 2.5 * KM)]
    return shapely.segmentize(Polygon(ring), step)


def test_a_densified_clip_line_is_still_a_clip_line():
    """Vertices every 300 m along a straight 5 km edge: length alone misses it, straightness does not."""
    poly = _straight_clip_polygon(300.0)
    assert (shapely.get_coordinates(poly.exterior).shape[0]) > 60
    segs = wf.Segments.from_geoms([poly])
    assert segs.length.max() < wf.ARTIFICIAL_MIN_LENGTH_M  # no single long segment to find
    assert wf.artificial_runs(segs).any()
    d = wf.WaterMask([poly]).fetch_rays([0.0], [0.0], grid_angles())[0]
    assert d[4] == wf.FETCH_CAP_M  # east, through the clip
    assert d[0] == wf.FETCH_CAP_M  # north, through another straight edge


def test_a_curving_natural_shore_is_not_a_clip_line():
    """The same vertex spacing on a shore that bends: a 3 km radius wanders off its chord at once."""
    radius = 3 * KM
    angles = np.linspace(0.0, 8 * KM / radius, 120)
    shore = [(radius * math.sin(a), radius * math.cos(a) - radius) for a in angles]
    poly = shapely.segmentize(
        Polygon([*shore, (shore[-1][0], -8 * KM), (0.0, -8 * KM)]), 300.0
    )
    runs = wf.artificial_runs(wf.Segments.from_geoms([poly]))
    n_shore = len(shore) - 1
    assert not runs[:n_shore].any()  # the curved part is left alone
    # A shore has to bend to stay a shore: the rule reads anything straighter than a ~8 km radius
    # over 2 km as artificial, which is the conservative direction (it adds waves, never removes).
    gentle = shapely.segmentize(
        Polygon([(x, (x / 1000.0) ** 2 * 0.05) for x in np.arange(0, 10001, 300)]
                + [(10 * KM, -5 * KM), (0.0, -5 * KM)]),
        300.0,
    )
    assert wf.artificial_runs(wf.Segments.from_geoms([gentle])).any()


def test_open_water_is_capped_at_100_km():
    m = mask_of(Point(0, 0).buffer(300 * KM, quad_segs=512))
    d = m.fetch_rays([0.0], [0.0], grid_angles())[0]
    assert np.allclose(d, wf.FETCH_CAP_M)


def test_arc_mean_smooths_a_narrow_opening():
    """A 5-ray arc through a gap reads less than the single ray that goes straight through it."""
    wall = box(-6 * KM, 1.9 * KM, 6 * KM, 2.1 * KM).difference(box(-200, 1.9 * KM, 200, 2.1 * KM))
    water = densify(box(-6 * KM, -2 * KM, 6 * KM, 30 * KM).difference(wall))
    m = mask_of(water)
    single = wf.fetch_by_bearing(m, [0.0], [0.0], 0.0, arc=False)[0]
    arc = wf.fetch_by_bearing(m, [0.0], [0.0], 0.0, arc=True)[0]
    assert single[0] > 20 * KM  # straight through the gap
    assert arc[0] < single[0] / 2  # four of the five rays hit the wall 2 km out
    assert arc[0] > 2 * KM  # but one of them did go through


def test_fetch_is_deterministic():
    m = mask_of(Point(0, 0).buffer(5 * KM, quad_segs=128))
    a = wf.fetch_by_bearing(m, [0.0, 1000.0], [0.0, 500.0], 1.5)
    b = wf.fetch_by_bearing(m, [0.0, 1000.0], [0.0, 500.0], 1.5)
    assert np.array_equal(a, b)


# --- run -----------------------------------------------------------------------


def test_run_through_a_point_is_both_directions_summed():
    core = box(-2 * KM, -1 * KM, 2 * KM, 1 * KM)
    r = wf.run_by_bearing(core, [0.0], [0.0], 0.0)[0]
    assert r[0] == pytest.approx(2 * KM, rel=0.01)  # north-south
    assert r[4] == pytest.approx(4 * KM, rel=0.01)  # east-west
    assert r[2] == pytest.approx(2 * math.hypot(1, 1) * KM, rel=0.01)
    off = wf.run_by_bearing(core, [1 * KM], [0.0], 0.0)[0]
    assert off[4] == pytest.approx(4 * KM, rel=0.01)  # still the full width


def test_run_stops_at_the_usable_water_edge_not_the_shore():
    lake = box(-2 * KM, -1 * KM, 2 * KM, 1 * KM)
    core = lake.buffer(-200.0)
    r = wf.run_by_bearing(core, [0.0], [0.0], 0.0)[0]
    assert r[0] == pytest.approx(1.6 * KM, rel=0.01)


def test_run_uses_the_piece_the_point_is_in():
    """A dumbbell: the run along the axis is the whole shape, across it only the local lobe."""
    core = shapely.union_all([
        Point(-3 * KM, 0).buffer(1 * KM, quad_segs=64),
        box(-3 * KM, -100, 3 * KM, 100),
        Point(3 * KM, 0).buffer(1 * KM, quad_segs=64),
    ])
    r = wf.run_by_bearing(core, [-3 * KM], [0.0], 0.0)[0]
    assert r[4] == pytest.approx(8 * KM, rel=0.02)  # east-west, out to the far lobe
    assert r[0] == pytest.approx(2 * KM, rel=0.02)  # north-south, across this lobe only


# --- labels --------------------------------------------------------------------


def test_descriptor_sectors_and_middle():
    lake = box(-10 * KM, -10 * KM, 10 * KM, 10 * KM)
    xs = [0.0, 0.0, 0.0, 9 * KM, 0.0, -9 * KM, 7 * KM]
    ys = [0.0, 9 * KM, -9 * KM, 0.0, 3 * KM, 0.0, 7 * KM]
    s = wf.descriptor_sectors(xs, ys, lake, 0.0)
    assert [wf.DESCRIPTORS[i] for i in s[:4]] == ["middle", "north end", "south end", "east end"]
    assert wf.DESCRIPTORS[s[4]] == "middle"  # inside MIDDLE_RADIUS
    assert wf.DESCRIPTORS[s[5]] == "west end"
    assert wf.DESCRIPTORS[s[6]] == "northeast side"


def test_descriptor_sectors_follow_true_north():
    lake = box(-10 * KM, -10 * KM, 10 * KM, 10 * KM)
    # Convergence is the angle from grid north to true north: at +45 deg, grid north is 45 deg
    # east of true north, so a point due grid-north of the centre is true north-east of it.
    assert wf.DESCRIPTORS[wf.descriptor_sectors([0.0], [9 * KM], lake, 45.0)[0]] == "northeast side"
    assert wf.DESCRIPTORS[wf.descriptor_sectors([0.0], [9 * KM], lake, -45.0)[0]] == "northwest side"


def test_small_sectors_merge_into_a_neighbour_or_the_middle():
    # 1 = north end, 2 = northeast side, 8 = northwest side, 0 = middle
    sectors = np.array([1, 1, 1, 1, 2, 8, 8, 8, 0, 0, 0])
    merged = wf.merge_small_sectors(sectors, min_points=3)
    assert set(merged.tolist()) == {0, 1, 8}
    assert (merged == 2).sum() == 0  # the singleton went to its bigger neighbour
    assert (merged == 1).sum() == 5

    # Nothing adjacent and nothing in the middle: the leftovers become `middle`.
    lonely = wf.merge_small_sectors(np.array([1, 1, 5, 5]), min_points=3)
    assert set(lonely.tolist()) == {0}

    # A sector that is big enough is left alone, and merging is idempotent.
    big = np.array([1, 1, 1, 5, 5, 5])
    assert np.array_equal(wf.merge_small_sectors(big, 3), big)
    assert np.array_equal(wf.merge_small_sectors(merged, 3), merged)


def test_a_bay_name_claims_its_own_water_and_not_the_open_lake():
    """A 1.2 km bay on the north shore of a 20 km lake: the bay's points, and nothing else."""
    lake = densify(
        shapely.union_all([box(-10 * KM, -10 * KM, 10 * KM, 0.0), box(-600.0, 0.0, 600.0, 1.2 * KM)])
    )
    names = [{"name": "Test Bay", "feature_class": "Bay", "x": 0.0, "y": 600.0}]
    xs = [0.0, 0.0, 0.0, 0.0, 3 * KM]
    ys = [1.0 * KM, 600.0, 200.0, -3 * KM, -3 * KM]
    widths = wf.run_by_bearing(lake, xs, ys, 0.0).min(axis=1)
    labels = wf.assign_labels(xs, ys, lake, lake, names, widths, 0.0)
    assert labels[:3] == ["Test Bay"] * 3
    assert labels[3] not in ("Test Bay",)  # 3 km out in the open lake
    assert labels[4] not in ("Test Bay",)


def test_a_name_does_not_reach_around_a_headland():
    """Two coves either side of a spit: each keeps its own points though they are 600 m apart."""
    lake = densify(
        shapely.union_all([
            box(-5 * KM, -5 * KM, 5 * KM, 0.0),
            box(-1.4 * KM, 0.0, -200.0, 1.2 * KM),
            box(200.0, 0.0, 1.4 * KM, 1.2 * KM),
        ])
    )
    names = [{"name": "West Cove", "feature_class": "Bay", "x": -800.0, "y": 600.0}]
    xs, ys = [-800.0, 800.0], [600.0, 600.0]
    widths = wf.run_by_bearing(lake, xs, ys, 0.0).min(axis=1)
    labels = wf.assign_labels(xs, ys, lake, lake, names, widths, 0.0, min_named_points=1)
    assert labels[0] == "West Cove"
    assert labels[1] != "West Cove"  # the line between them crosses the spit


def test_a_channel_only_labels_points_inside_it():
    channel = densify(box(-5 * KM, -150.0, 5 * KM, 150.0))
    basin = Point(6 * KM, 0).buffer(3 * KM, quad_segs=128)
    water = shapely.union_all([channel, basin])
    names = [{"name": "Test Channel", "feature_class": "Channel", "x": 0.0, "y": 0.0}]
    xs, ys = [0.0, 600.0, 6 * KM], [0.0, 0.0, 0.0]
    widths = wf.run_by_bearing(water, xs, ys, 0.0).min(axis=1)
    labels = wf.assign_labels(xs, ys, water, water, names, widths, 0.0, min_named_points=1)
    assert labels[0] == "Test Channel"
    assert labels[2] != "Test Channel"  # the open basin is not the channel


def test_a_name_holding_too_few_points_is_not_a_region():
    """One sample in a marsh channel is not a place to land: it goes back to the descriptors."""
    lake = densify(
        shapely.union_all([box(-10 * KM, -10 * KM, 10 * KM, 0.0), box(-600.0, 0.0, 600.0, 1.2 * KM)])
    )
    names = [{"name": "Test Bay", "feature_class": "Bay", "x": 0.0, "y": 600.0}]
    xs, ys = [0.0, 0.0, 3 * KM], [600.0, 200.0, -3 * KM]
    widths = wf.run_by_bearing(lake, xs, ys, 0.0).min(axis=1)
    assert wf.assign_labels(xs, ys, lake, lake, names, widths, 0.0, min_named_points=2)[:2] == ["Test Bay"] * 2
    kept_one = wf.assign_labels([0.0, 3 * KM], [600.0, -3 * KM], lake, lake, names,
                                wf.run_by_bearing(lake, [0.0, 3 * KM], [600.0, -3 * KM], 0.0).min(axis=1),
                                0.0, min_named_points=2)
    assert "Test Bay" not in kept_one  # only one point fell in the bay
    assert all(lab in wf.DESCRIPTORS for lab in kept_one)


def test_every_point_gets_a_label():
    lake = densify(box(-3 * KM, -3 * KM, 3 * KM, 3 * KM))
    pts = wf.grid_points(lake, 800.0)
    widths = wf.run_by_bearing(lake, pts[:, 0], pts[:, 1], 0.0).min(axis=1)
    labels = wf.assign_labels(pts[:, 0], pts[:, 1], lake, lake, [], widths, 0.0)
    assert len(labels) == len(pts)
    assert all(lab in wf.DESCRIPTORS for lab in labels)


# --- GNIS ----------------------------------------------------------------------


GNIS_HEADER = (
    "feature_id|feature_name|feature_class|state_name|state_numeric|county_name|county_numeric|"
    "map_name|date_created|date_edited|bgn_type|bgn_authority|bgn_date|prim_lat_dms|prim_long_dms|"
    "prim_lat_dec|prim_long_dec|source_lat_dms|source_long_dms|source_lat_dec|source_long_dec"
)


def _gnis_row(name, klass, lat, lon):
    return f"1|{name}|{klass}|Michigan|26|St. Clair|147||01/01/1980|||||||{lat}|{lon}||||"


def _gnis_zip(path, rows):
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("Text/DomesticNames_XX.txt", "﻿" + "\n".join([GNIS_HEADER, *rows]) + "\n")
    return path


def test_gnis_reads_only_the_named_water_classes(tmp_path):
    path = _gnis_zip(
        tmp_path / "names.zip",
        [
            _gnis_row("Anchor Bay", "Bay", 42.65003, -82.71658),
            _gnis_row("South Channel", "Channel", 42.56115, -82.58241),
            _gnis_row("Harsens Island", "Island", 42.58, -82.60),
            _gnis_row("Nowhere Bay", "Bay", "", ""),
            _gnis_row("Null Island Bay", "Bay", 0.0, 0.0),
        ],
    )
    rows = gnis.read_names(path)
    assert [r["name"] for r in rows] == ["Anchor Bay", "South Channel"]
    assert rows[0]["lat"] == pytest.approx(42.65003)


def test_gnis_attaches_a_name_inside_or_within_200_m(tmp_path, monkeypatch):
    from seaplane_pipeline import gis
    from seaplane_pipeline.config import Config

    cfg = Config()
    monkeypatch.setattr(cfg, "gnis_states", ("MI",), raising=False)
    monkeypatch.setattr(Config, "data_dir", property(lambda self: tmp_path))
    (tmp_path / "cache").mkdir()
    _gnis_zip(
        tmp_path / "cache" / gis.DATASETS["gnis_mi"].filename,
        [
            _gnis_row("Inside Bay", "Bay", 42.6000, -83.0000),
            _gnis_row("Close Bay", "Bay", 42.6000, -82.99815),  # ~155 m east of the edge
            _gnis_row("Far Bay", "Bay", 42.6000, -82.9900),  # ~820 m east of the edge
        ],
    )
    import pandas as pd

    lake = gpd.GeoSeries([Point(-83.0, 42.6).buffer(0.001)], crs=wf.WGS84).to_crs(wf.MEASURE_CRS)
    lakes = pd.DataFrame({"id": [1234]})
    found = gnis.names_by_water_body(cfg, lakes, lake.to_numpy())
    assert sorted(r["name"] for r in found[1234]) == ["Close Bay", "Inside Bay"]


# --- depth ---------------------------------------------------------------------


def _tiny_grid(tmp_path, values, ulx=-83.0, uly=42.5, cell=0.001):
    rows, cols = values.shape
    (tmp_path / "t.flt").write_bytes(np.asarray(values, dtype="<f4").tobytes())
    (tmp_path / "t.hdr").write_text(
        f"NCOLS {cols}\nNROWS {rows}\nNBANDS 1\nULXMAP {ulx}\nULYMAP {uly}\n"
        f"XDIM {cell}\nYDIM {cell}\nNODATA -9999\nLAYOUT BIL\nBYTEORDER I\nNBITS 32\nPIXELTYPE FLOAT\n"
    )
    return bathymetry.read_flt_grid(tmp_path / "t.hdr")


def test_depth_sampling(tmp_path):
    values = np.array([[-1.0, -2.5, 12.0], [-0.02, -9999.0, 3.0], [0.0, -10.0, -63.2]])
    grid = _tiny_grid(tmp_path, values)
    # cell centres: lon -83.000, -82.999, -82.998; lat 42.500, 42.499, 42.498
    lon = [-83.0, -82.999, -82.998, -83.0, -82.999, -82.998, -83.0, -82.998, -70.0]
    lat = [42.5, 42.5, 42.5, 42.499, 42.499, 42.499, 42.498, 42.498, 42.0]
    got = grid.depth_dm(lon, lat)
    assert got.tolist() == [
        10, 25, wf.DEPTH_UNKNOWN,  # 1.0 m, 2.5 m, land
        0, wf.DEPTH_UNKNOWN, wf.DEPTH_UNKNOWN,  # 0.02 m rounds to 0 dm, nodata, land
        wf.DEPTH_UNKNOWN, 632,  # exactly at the datum is not water; 63.2 m
        wf.DEPTH_UNKNOWN,  # off the grid
    ]
    assert grid.bounds[0] == pytest.approx(-83.0005)
    assert grid.shape == (3, 3)


def test_depth_reader_handles_big_endian(tmp_path):
    (tmp_path / "t.flt").write_bytes(np.array([[-4.0]], dtype=">f4").tobytes())
    (tmp_path / "t.hdr").write_text(
        "NCOLS 1\nNROWS 1\nULXMAP -83.0\nULYMAP 42.5\nXDIM 0.001\nYDIM 0.001\n"
        "NODATA -9999\nBYTEORDER M\nNBITS 32\nPIXELTYPE FLOAT\n"
    )
    grid = bathymetry.read_flt_grid(tmp_path / "t.hdr")
    assert grid.depth_dm([-83.0], [42.5]).tolist() == [40]


def test_shore_snap_takes_nearest_wet_cell_within_two_cells(tmp_path):
    # Row 0 is lake, rows 1-4 are the DEM shore ramp (land above the datum), row 5 is nodata.
    values = np.array(
        [
            [-3.0, -3.0, -4.0, -3.0, -3.0, -3.0],
            [0.2, 0.2, 0.2, 0.2, 0.2, 0.2],
            [0.4, 0.4, 0.4, 0.4, 0.4, 0.4],
            [0.6, 0.6, 0.6, 0.6, 0.6, 0.6],
            [0.8, 0.8, 0.8, 0.8, 0.8, 0.8],
            [-9999.0] * 6,
        ]
    )
    grid = _tiny_grid(tmp_path, values, ulx=-83.0, uly=42.5, cell=0.001)
    lon = [-82.998, -82.998, -82.998, -82.998, -82.998]
    lat = [42.499, 42.498, 42.497, 42.496, 42.495]  # rows 1, 2, 3, 4, 5
    exact = grid.depth_dm(lon, lat)
    assert exact.tolist() == [wf.DEPTH_UNKNOWN] * 5
    snapped = grid.depth_dm(lon, lat, snap_cells=2)
    # One and two cells from the lake: straight up (4.0 m under the point's column). Three cells out
    # and beyond (and the nodata row) stay unknown: no depth is invented where the grid has none.
    assert snapped.tolist() == [40, 40, wf.DEPTH_UNKNOWN, wf.DEPTH_UNKNOWN, wf.DEPTH_UNKNOWN]


def test_shore_snap_prefers_nearest_then_deeper(tmp_path):
    values = np.array(
        [
            [-9.0, 0.5, 0.5, 0.5, -2.0],
            [0.5, 0.5, 0.5, 0.5, 0.5],
            [-1.0, 0.5, 0.5, 0.5, -5.0],
        ]
    )
    grid = _tiny_grid(tmp_path, values, ulx=-83.0, uly=42.5, cell=0.001)
    # From row 1, col 1 the nearest wet cells are (0,0) and (2,0), one diagonal step each (col 4 is
    # three cells away, outside the radius); of the tie the deeper one (9 m) wins.
    got = grid.depth_dm([-82.999], [42.499], snap_cells=2)
    assert got.tolist() == [90]
    # A wet cell in the point's own cell is never replaced by a deeper neighbour.
    assert grid.depth_dm([-83.0], [42.498], snap_cells=2).tolist() == [10]


def test_grids_exact_hit_in_any_grid_beats_a_snap(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    # Grid a: the point's cell is land with lake one cell west. Grid b: the same point is wet.
    a = _tiny_grid(tmp_path / "a", np.array([[-7.0, 0.3, 0.3]]), ulx=-83.0, uly=42.5)
    b = _tiny_grid(tmp_path / "b", np.array([[0.3, -1.5, 0.3]]), ulx=-83.0, uly=42.5)
    lon, lat = [-82.999, -82.998, -70.0], [42.5, 42.5, 42.5]
    assert bathymetry.DepthGrids([a, b]).depth_dm(lon, lat).tolist() == [15, 70, wf.DEPTH_UNKNOWN]
    # In list order the first grid with an exact wet cell wins.
    c = _tiny_grid(tmp_path, np.array([[-2.0, -2.0, -2.0]]), ulx=-83.0, uly=42.5)
    assert bathymetry.DepthGrids([c, b]).depth_dm([-82.999], [42.5]).tolist() == [20]
    assert bathymetry.DepthGrids([a, b], snap_cells=0).depth_dm(lon, lat).tolist() == [
        15, wf.DEPTH_UNKNOWN, wf.DEPTH_UNKNOWN
    ]


def test_grid_selection_by_bbox():
    keys = [g.key for g in bathymetry.grids_for_bbox((-83.2, 42.2, -82.4, 42.7))]  # Lake St. Clair
    assert keys == ["bathymetry_erie"]
    keys = [g.key for g in bathymetry.grids_for_bbox((-84.6, 45.7, -84.4, 45.9))]  # Straits of Mackinac
    assert keys == ["bathymetry_huron", "bathymetry_michigan"]
    assert bathymetry.grids_for_bbox((-100.0, 30.0, -99.0, 31.0)) == []
    whole = [g.key for g in bathymetry.grids_for_bbox((-90.5, 41.6, -82.3, 48.4))]
    assert whole == [g.key for g in bathymetry.GRID_SOURCES]
    for src in bathymetry.GRID_SOURCES:
        w, s, e, n = src.bbox
        assert w < e and s < n
        assert src.url.endswith(src.filename) and src.folder == src.filename.split(".")[0]


def test_grid_datasets_are_registered_from_the_source_list():
    from seaplane_pipeline import gis
    from seaplane_pipeline.config import MICHIGAN_BBOX

    for src in bathymetry.grids_for_bbox(MICHIGAN_BBOX):
        ds = gis.DATASETS[src.key]
        assert (ds.filename, ds.url, ds.store) == (src.filename, src.url, "cache")


def test_load_grid_reads_every_cached_grid(tmp_path, monkeypatch):
    import io
    import tarfile

    from seaplane_pipeline.config import Config

    monkeypatch.setattr(Config, "data_dir", property(lambda self: tmp_path))
    cache = tmp_path / "cache"
    cache.mkdir()
    src = bathymetry.GRID_SOURCES[1]
    with tarfile.open(cache / src.filename, "w:gz") as tf:
        for name, payload in (
            (f"{src.folder}/{src.folder}.flt", np.array([[-6.0]], dtype="<f4").tobytes()),
            (
                f"{src.folder}/{src.folder}.hdr",
                b"NCOLS 1\nNROWS 1\nULXMAP -83.0\nULYMAP 44.0\nXDIM 0.001\nYDIM 0.001\nNODATA -9999\n",
            ),
        ):
            info = tarfile.TarInfo(name)
            info.size = len(payload)
            tf.addfile(info, io.BytesIO(payload))
    grids = bathymetry.load_grid(Config())
    assert grids is not None and len(grids.grids) == 1
    assert grids.depth_dm([-83.0], [44.0]).tolist() == [60]


def test_no_bathymetry_means_unknown_everywhere(tmp_path, monkeypatch):
    from seaplane_pipeline.config import Config

    monkeypatch.setattr(Config, "data_dir", property(lambda self: tmp_path))
    (tmp_path / "cache").mkdir()
    assert bathymetry.load_grid(Config()) is None


# --- Lake St. Clair ------------------------------------------------------------


@pytest.fixture(scope="module")
def st_clair(request):
    path = request.path.parent / "fixtures" / "waves" / "lake_st_clair.geojson.gz"
    geom = shapely.from_geojson(gzip.decompress(path.read_bytes()).decode())
    return gpd.GeoSeries([geom], crs=wf.WGS84).to_crs(wf.MEASURE_CRS).iloc[0]


def _project(lat, lon):
    return gpd.GeoSeries([Point(lon, lat)], crs=wf.WGS84).to_crs(wf.MEASURE_CRS).iloc[0]


#: docs/gis-sources.md, "Trial fetch rays on layer 13" (km, 16 true bearings from N clockwise).
TRIAL_TABLE = {
    "Big Muscamoot Bay": ((42.5578, -82.6607),
                          [1.2, 1.1, 1.4, 3.8, 1.1, 1.0, 0.9, 0.9, 1.2, 2.4, 24.8, 19.5, 1.6, 1.3, 1.3, 1.4]),
    "Anchor Bay": ((42.650, -82.717),
                   [3.8, 4.9, 5.5, 7.5, 7.4, 4.8, 3.5, 5.6, 39.2, 38.3, 7.5, 9.0, 6.9, 3.8, 3.5, 3.5]),
    "mid-lake buoy 45147": ((42.430, -82.680),
                            [10.4, 12.1, 10.7, 15.0, 21.9, 22.8, 17.9, 15.1, 14.7, 15.9, 18.1, 21.5,
                             16.0, 18.4, 19.8, 26.7]),
}


def test_st_clair_reproduces_the_trial_fetch_table(st_clair):
    """Single rays, no arc mean and no convergence, exactly as docs/gis-sources.md measured them.

    The clip-line rule is off here for the same reason: that table was measured on this polygon on
    its own, and on its own the lake's closure across the Detroit River mouth is a clip line. In the
    real mask the Detroit River polygon is next to it and the ray carries on into the river.
    """
    mask = mask_of(*shapely.get_parts(shapely.normalize(st_clair)))
    for name, ((lat, lon), reference) in TRIAL_TABLE.items():
        p = _project(lat, lon)
        got = mask.fetch_rays([p.x], [p.y], grid_angles())[0] / KM
        for i, (a, b) in enumerate(zip(got, reference, strict=True)):
            assert a == pytest.approx(b, rel=0.06), f"{name} bin {i}: {a:.2f} km vs {b} km"


def test_st_clair_shelter_holds_with_the_arc_mean(st_clair):
    """Muscamoot is a kilometre of water in every direction but south-west; Anchor Bay only fears a
    south wind. That is what Bobby described, and it has to survive the five-ray arc."""
    mask = mask_of(*shapely.get_parts(shapely.normalize(st_clair)))
    p = _project(*TRIAL_TABLE["Big Muscamoot Bay"][0])
    conv = float(wf.geom_mod.meridian_convergence_deg(TRIAL_TABLE["Big Muscamoot Bay"][0][1],
                                                     TRIAL_TABLE["Big Muscamoot Bay"][0][0]))
    musc = wf.fetch_by_bearing(mask, [p.x], [p.y], conv)[0] / KM
    sheltered = [musc[i] for i in range(16) if i not in (10, 11, 12)]
    assert max(sheltered) < 3.0
    assert musc[10] > 15.0 and musc[11] > 10.0  # south-west and west-south-west

    p = _project(*TRIAL_TABLE["Anchor Bay"][0])
    anchor = wf.fetch_by_bearing(mask, [p.x], [p.y], conv)[0] / KM
    assert max(anchor[i] for i in range(16) if i not in (8, 9)) < 12.0
    assert max(anchor[8], anchor[9]) > 18.0


def test_st_clair_labels_name_the_bays_and_leave_the_open_lake_alone(st_clair, tmp_path):
    """The label rule on real water: bays own their own points, the middle of the lake does not."""
    core = shapely.make_valid(st_clair.buffer(-30.48))
    spacing = wf.point_spacing(st_clair.area, big_target=2000)
    pts = wf.grid_points(core, spacing)
    assert 1500 < len(pts) < 2500
    conv = float(wf.geom_mod.meridian_convergence_deg(-82.68, 42.55))
    widths = wf.run_by_bearing(core, pts[:, 0], pts[:, 1], conv).min(axis=1)
    names = []
    for name, klass, lat, lon in (
        ("Anchor Bay", "Bay", 42.65003, -82.71658),
        ("Big Muscamoot Bay", "Bay", 42.55781, -82.66074),
        ("Little Muscamoot Bay", "Bay", 42.57809, -82.62602),
        ("Goose Bay", "Bay", 42.58448, -82.67908),
        ("L'anse Creuse Bay", "Bay", 42.56198, -82.82214),
    ):
        q = _project(lat, lon)
        names.append({"name": name, "feature_class": klass, "x": q.x, "y": q.y})
    labels = wf.assign_labels(pts[:, 0], pts[:, 1], st_clair, core, names, widths, conv)
    counts = {n["name"]: labels.count(n["name"]) for n in names}
    assert counts["Anchor Bay"] > 30
    for name in ("Big Muscamoot Bay", "Little Muscamoot Bay", "Goose Bay", "L'anse Creuse Bay"):
        assert counts[name] >= 3, counts
    # The middle of the lake keeps a descriptor.
    mid = _project(42.430, -82.680)
    k = int(np.argmin(np.hypot(pts[:, 0] - mid.x, pts[:, 1] - mid.y)))
    assert labels[k] in wf.DESCRIPTORS


# --- the stage -----------------------------------------------------------------


def test_stage_writes_a_pack_for_one_lake(tmp_path, monkeypatch):
    from seaplane_pipeline.config import Config

    monkeypatch.setattr(Config, "data_dir", property(lambda self: tmp_path))
    cfg = Config()
    (tmp_path / "work").mkdir(parents=True)
    # Round in the measurement CRS, so the fetch from the centre really is the radius.
    centres = gpd.GeoSeries([Point(-83.35, 42.6), Point(-83.0, 42.9)], crs=wf.WGS84).to_crs(wf.MEASURE_CRS)
    proj = gpd.GeoSeries(
        [centres.iloc[0].buffer(1500.0, quad_segs=64), centres.iloc[1].buffer(100.0, quad_segs=16)],
        crs=wf.MEASURE_CRS,
    )
    lake, small = proj.to_crs(wf.WGS84)
    acres = proj.area / wf.M2_PER_ACRE
    lakes = gpd.GeoDataFrame(
        {
            "id": [11, 22],
            "name": ["Big Round Lake", "Pond"],
            "kind": ["lake", "lake"],
            "county": [None, "Oakland"],
            "lat": [42.6, 42.9],
            "lon": [-83.35, -83.0],
            "area_acres": acres.to_numpy(),
        },
        geometry=[lake, small],
        crs=wf.WGS84,
    )
    lakes.to_parquet(tmp_path / "work" / "lakes.parquet", index=False)
    usable = gpd.GeoDataFrame(
        {"id": [11, 22]},
        geometry=gpd.GeoSeries(proj.buffer(-30.48).to_numpy(), crs=wf.MEASURE_CRS).to_crs(wf.WGS84),
        crs=wf.WGS84,
    )
    usable.to_parquet(tmp_path / "work" / "usable_water.parquet", index=False)

    args = argparse.Namespace(skip_depth=True, skip_names=True, limit=None, lake_id=None,
                              min_acres=100.0, target=60, big_target=400)
    assert wf.run(cfg, args) == 0
    points, labels, meta = wf.read_wave_points(
        tmp_path / "work" / "wave_points.bin", tmp_path / "work" / "wave_points.json"
    )
    assert list(meta["lakes"]) == ["11"]  # the pond is under 100 acres
    assert len(points[11]) > 20
    assert all(lab in wf.DESCRIPTORS for lab in labels)
    assert all(p.depth_dm == wf.DEPTH_UNKNOWN for p in points[11])
    # A round lake: fetch from the centre is about the radius in every direction.
    centre = min(points[11], key=lambda p: (p.lon + 83.35) ** 2 + (p.lat - 42.6) ** 2)
    assert max(centre.fetch) - min(centre.fetch) < 0.15 * max(centre.fetch)
    # Deterministic: a second run writes the same bytes.
    first = (tmp_path / "work" / "wave_points.bin").read_bytes()
    assert wf.run(cfg, args) == 0
    assert (tmp_path / "work" / "wave_points.bin").read_bytes() == first


def test_stage_tolerates_an_unknown_kind_and_a_null_county(tmp_path, monkeypatch):
    from seaplane_pipeline.config import Config

    monkeypatch.setattr(Config, "data_dir", property(lambda self: tmp_path))
    cfg = Config()
    (tmp_path / "work").mkdir(parents=True)
    lake = Point(-82.9, 42.5).buffer(0.05, quad_segs=48)
    proj = gpd.GeoSeries([lake], crs=wf.WGS84).to_crs(wf.MEASURE_CRS)
    lakes = gpd.GeoDataFrame(
        {"id": [77], "name": ["Somewhere Water"], "kind": ["connecting_water"], "county": [None],
         "lat": [42.5], "lon": [-82.9], "area_acres": (proj.area / wf.M2_PER_ACRE).to_numpy()},
        geometry=[lake], crs=wf.WGS84,
    )
    lakes.to_parquet(tmp_path / "work" / "lakes.parquet", index=False)
    gpd.GeoDataFrame(
        {"id": [77]},
        geometry=gpd.GeoSeries(proj.buffer(-30.48).to_numpy(), crs=wf.MEASURE_CRS).to_crs(wf.WGS84),
        crs=wf.WGS84,
    ).to_parquet(tmp_path / "work" / "usable_water.parquet", index=False)
    args = argparse.Namespace(skip_depth=True, skip_names=True, limit=None, lake_id=None,
                              min_acres=100.0, target=60, big_target=400)
    assert wf.run(cfg, args) == 0
    points, _, meta = wf.read_wave_points(
        tmp_path / "work" / "wave_points.bin", tmp_path / "work" / "wave_points.json"
    )
    assert list(meta["lakes"]) == ["77"] and len(points[77]) > 10
