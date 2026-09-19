"""The wave pack reader, against the shared sample pack and against a pack that is not there."""
from __future__ import annotations

import json
import struct

from seaplane_api import wavefield

from .conftest import make_wave_field, pack_wave_points, sample_wave_field, shared, wave_point


def test_the_shared_sample_pack_reads_back_exactly_what_it_holds():
    field = sample_wave_field()
    index = shared("wave_points.sample.json")
    assert field.labels == tuple(index["labels"])
    assert len(field) == len(index["lakes"]) == 2
    assert [len(field.points(k)) for k in ("111", "222")] == [v[1] for v in index["lakes"].values()]

    first = field.points("111")[0]
    assert round(first.lon, 4) == -82.7166 and round(first.lat, 4) == 42.65
    assert first.depth_m == 3.0  # 30 decimetres
    assert field.label(first.label) == "Anchor Bay"
    assert len(first.fetch_m) == 16 and len(first.run_ft) == 8
    assert first.fetch_m[0] == 3800.0  # 3.8 km, in units of 10 m on the wire
    assert first.run_ft[0] == 9000.0


def test_an_unknown_depth_comes_back_as_none_rather_than_six_thousand_metres():
    field = sample_wave_field()
    assert all(p.depth_m is None for p in field.points("222"))
    assert all(p.depth_m is not None for p in field.points("111"))


def test_a_water_body_that_is_not_in_the_pack_has_no_points():
    field = sample_wave_field()
    assert field.points(999) == ()
    assert not field.has_points(999)
    assert 999 not in field
    assert "111" in field and field.has_points(111)  # ints and strings both resolve


def test_ids_are_looked_up_as_strings_whichever_type_the_caller_has():
    field = sample_wave_field()
    assert field.points(111) == field.points("111")


def test_points_are_only_unpacked_once_per_water_body():
    field = sample_wave_field()
    assert field.points("111") is field.points("111")


def test_units_come_from_the_index_not_from_a_constant():
    """A pack written in metres and feet rather than tens of them still reads correctly."""
    index, blob = pack_wave_points(
        {7: [wave_point(-83.0, 42.0, label=0, fetch_m=[5000.0] * 16, run_ft=[4000.0] * 8)]}, ["middle"]
    )
    index["fetch_unit_m"], index["run_unit_ft"] = 1, 1
    field = wavefield.WaveField(index, blob)
    assert field.points(7)[0].fetch_m[0] == 500.0  # 500 records of 1 m, not 5,000
    assert field.points(7)[0].run_ft[0] == 400.0


def test_a_missing_pack_is_none_rather_than_an_exception(tmp_path):
    assert wavefield.load(tmp_path / "nope.json", tmp_path / "nope.bin") is None


def test_a_pack_whose_index_is_there_but_whose_blob_is_not_is_none(tmp_path):
    (tmp_path / "wave_points.json").write_text('{"version": 1, "labels": [], "lakes": {}}')
    assert wavefield.load(tmp_path / "wave_points.json", tmp_path / "wave_points.bin") is None


def test_a_pack_with_a_different_record_size_is_refused(tmp_path):
    index, blob = pack_wave_points(
        {1: [wave_point(-83.0, 42.0, label=0, fetch_m=[100.0] * 16, run_ft=[100.0] * 8)]}, ["middle"]
    )
    index["record_bytes"] = 64
    (tmp_path / "wave_points.json").write_text(json.dumps(index))
    (tmp_path / "wave_points.bin").write_bytes(blob)
    assert wavefield.load(tmp_path / "wave_points.json", tmp_path / "wave_points.bin") is None


def test_a_truncated_blob_is_refused_rather_than_read_half_way(tmp_path):
    index, blob = pack_wave_points(
        {1: [wave_point(-83.0, 42.0, label=0, fetch_m=[100.0] * 16, run_ft=[100.0] * 8)]}, ["middle"]
    )
    (tmp_path / "wave_points.json").write_text(json.dumps(index))
    (tmp_path / "wave_points.bin").write_bytes(blob[:-3])
    assert wavefield.load(tmp_path / "wave_points.json", tmp_path / "wave_points.bin") is None


def test_a_slice_that_runs_past_the_end_of_the_blob_yields_nothing(tmp_path):
    """A stale index against a newer blob must not raise `struct.error` mid-briefing."""
    index, blob = pack_wave_points(
        {1: [wave_point(-83.0, 42.0, label=0, fetch_m=[100.0] * 16, run_ft=[100.0] * 8)]}, ["middle"]
    )
    index["lakes"]["1"] = [0, 40]
    assert wavefield.WaveField(index, blob).points(1) == ()


def test_the_record_layout_is_the_contract_layout():
    assert wavefield.RECORD.format == "<ffHH16H8H"
    assert wavefield.RECORD.size == 60 == struct.calcsize("<ffHH16H8H")
    assert len(shared("wave_points.sample.bin")) % 60 == 0


def test_a_pack_built_in_a_test_round_trips_through_the_same_reader():
    field = make_wave_field(
        {
            42: [
                wave_point(-83.3, 42.5, label=0, fetch_m=[800.0] * 16, run_ft=[2500.0] * 8, depth_m=1.5),
                wave_point(-83.2, 42.5, label=1, fetch_m=[9000.0] * 16, run_ft=[9000.0] * 8),
            ]
        },
        ["west end", "middle"],
    )
    points = field.points(42)
    assert [field.label(p.label) for p in points] == ["west end", "middle"]
    assert points[0].depth_m == 1.5 and points[1].depth_m is None
