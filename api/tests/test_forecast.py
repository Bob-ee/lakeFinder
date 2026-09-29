"""`GET /api/forecast/wind?lake=`: cell doubling, regions, the 30 minute cache, error behaviour and the
fixture mode. Open-Meteo is replaced by a function that answers with hand-made payloads; no sockets.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from seaplane_api import app as app_mod
from seaplane_api import forecast
from seaplane_api.briefing import lakes as lakes_mod
from seaplane_api.fetch import openmeteo
from seaplane_api.settings import Settings

from .conftest import DETROIT, make_wave_field, pack_wave_points, wave_point
from .test_timeline import naive, payload

NOW = datetime(2026, 9, 29, 10, 30, tzinfo=DETROIT).astimezone(UTC)
SMALL = 111  # two regions in two cells
BIG = 222  # 300 points: too many cells at 0.1 and at 0.2
POND = 333  # no wave points: forecast at its centroid


def _pt(lat: float, lon: float, label: int) -> dict:
    return wave_point(lon, lat, label=label, fetch_m=[1500.0] * 16, run_ft=[9000.0] * 8, depth_m=4.0)


def _lakes() -> dict[int, list[dict]]:
    grid = [(round(42.0 + 0.1 * i, 4), round(-83.0 + 0.1 * j, 4)) for i in range(30) for j in range(10)]
    return {
        SMALL: [_pt(42.40, -82.90, 0), _pt(42.41, -82.91, 0), _pt(42.60, -83.30, 1)],
        BIG: [_pt(lat, lon, 0 if k < 150 else 1) for k, (lat, lon) in enumerate(grid)],
    }


@pytest.fixture(autouse=True)
def _reset(monkeypatch, tmp_path):
    forecast._cache.clear()
    forecast._index_cache = None
    forecast._wave_cache = None
    index = [
        {"id": SMALL, "name": "Small Water", "lat": 42.45, "lon": -83.0},
        {"id": BIG, "name": "Big Water", "lat": 44.0, "lon": -83.0},
        {"id": POND, "name": "Pond", "lat": 42.7, "lon": -83.4},
    ]
    wp_index, blob = pack_wave_points(_lakes(), ["west end", "east end"])
    (tmp_path / "index.json").write_text(json.dumps(index))
    (tmp_path / "wave_points.json").write_text(json.dumps(wp_index))
    (tmp_path / "wave_points.bin").write_bytes(blob)
    monkeypatch.setenv("SEAPLANE_DATA_OUT", str(tmp_path))
    monkeypatch.delenv("SEAPLANE_WIND_FIXTURES", raising=False)
    monkeypatch.setattr(forecast, "_now", lambda: NOW)
    yield
    forecast._cache.clear()
    forecast._index_cache = None
    forecast._wave_cache = None


class _Sched:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings


@pytest.fixture
def client():
    with TestClient(app_mod.create_app(scheduler=_Sched(Settings()))) as c:
        yield c


class FakeOpenMeteo:
    """Stands in for `openmeteo.fetch_points`: wind direction encodes the point's latitude."""

    def __init__(self, monkeypatch, *, fail_points=(), fail_all=False) -> None:
        self.calls: list[dict] = []
        self.fail_points = set(fail_points)
        self.fail_all = fail_all
        monkeypatch.setattr(forecast.openmeteo, "fetch_points", self)

    async def __call__(self, c, points, **kw):
        self.calls.append({"points": list(points), **kw})
        if self.fail_all:
            return [{} for _ in points], ["open_meteo[0]: HTTP 500"]
        out, errs = [], []
        for lat, lon in points:
            if (lat, lon) in self.fail_points:
                out.append({})
                errs.append("open_meteo[0]: HTTP 500")
                continue
            n = 24 * 6
            pay = payload(naive(2026, 9, 27), n, lambda i, lat=lat: (round(lat * 10) % 360, 10 + (i % 24) // 6, 14), models=["ncep_hrrr_conus"] * n)
            out.append(pay)
        return out, errs


def _get(client, lake) -> dict:
    r = client.get(f"/api/forecast/wind?lake={lake}")
    assert r.status_code == 200, r.text
    return r.json()


# --- cells ------------------------------------------------------------------------------------------


def test_one_point_is_one_cell_at_a_tenth_of_a_degree():
    assert forecast.cells_for([(42.43, -82.71)]) == (0.1, [(424, -827)])


def test_a_point_belongs_to_the_cell_of_its_rounded_coordinates():
    deg, cells = forecast.cells_for([(42.44, -82.76), (42.46, -82.74)])
    assert deg == 0.1 and cells == [(424, -828), (425, -827)]
    assert forecast.cell_center((424, -828), deg) == (42.4, -82.8)


def test_exactly_120_cells_keep_the_tenth_degree_and_121_double_it():
    row = lambda n: [(round(40.0 + 0.1 * i, 4), -83.0) for i in range(n)]
    deg, cells = forecast.cells_for(row(120))
    assert deg == 0.1 and len(cells) == 120
    deg, cells = forecast.cells_for(row(121))
    assert deg == 0.2 and 0 < len(cells) <= 120


def test_the_cell_size_doubles_until_it_fits():
    grid = [(round(40.0 + 0.1 * i, 4), round(-88.0 + 0.1 * j, 4)) for i in range(50) for j in range(50)]  # 2500 at 0.1
    deg, cells = forecast.cells_for(grid)
    assert deg in (0.4, 0.8) and len(cells) <= 120
    assert forecast.cells_for(grid, max_cells=4)[0] > deg  # a tighter limit doubles further


def test_cell_order_does_not_depend_on_point_order():
    pts = [(42.6, -83.3), (42.4, -82.9), (42.5, -83.1)]
    assert forecast.cells_for(pts) == forecast.cells_for(list(reversed(pts)))


# --- the answer ---------------------------------------------------------------------------------------


def test_the_answer_has_the_contract_shape_and_the_time_axis(client, monkeypatch):
    fake = FakeOpenMeteo(monkeypatch)
    d = _get(client, SMALL)
    assert set(d) == {"lake_id", "cell_deg", "times", "past", "models", "cells", "regions", "fetched_at", "errors"}
    assert d["lake_id"] == SMALL and d["cell_deg"] == 0.1 and d["past"] == 6 and d["errors"] == []
    assert len(d["times"]) == 79 and d["times"][6] == "2026-09-29T10:00:00-04:00"
    assert len(d["models"]) == 79 and set(d["models"]) == {"ncep_hrrr_conus"}
    assert d["fetched_at"] == "2026-09-29T14:30:00Z"
    # The two regions sit in two cells, and every array is on the axis.
    assert [(c["lat"], c["lon"]) for c in d["cells"]] == [(42.4, -82.9), (42.6, -83.3)]
    assert all(len(c[k]) == 79 for c in d["cells"] for k in ("dir", "kt", "gust"))
    assert d["cells"][0]["dir"][0] == 64  # 42.4 * 10 % 360
    # One batched call, wind only, the settings' models, and day counts that cover the axis.
    assert len(fake.calls) == 1
    call = fake.calls[0]
    assert call["models"] == ["ncep_hrrr_conus", "best_match"]
    assert tuple(call["hourly"]) == openmeteo.WIND_VARS
    assert (call["forecast_days"], call["past_days"]) == (4, 1)


def test_a_lake_without_wave_points_is_forecast_at_its_centroid(client, monkeypatch):
    FakeOpenMeteo(monkeypatch)
    d = _get(client, POND)
    assert [(c["lat"], c["lon"]) for c in d["cells"]] == [(42.7, -83.4)]
    assert d["regions"] == []


def test_regions_are_one_per_label_at_the_centroid_cell_and_match_the_briefings_cells(client, monkeypatch):
    FakeOpenMeteo(monkeypatch)
    d = _get(client, SMALL)
    assert [r["label"] for r in d["regions"]] == ["west end", "east end"]
    field = make_wave_field({SMALL: _lakes()[SMALL]}, ["west end", "east end"])
    cand = lakes_mod.find_candidate(
        [{"id": SMALL, "name": "x", "lat": 42.45, "lon": -83.0}], SMALL, home_lat=42.0, home_lon=-83.0, extents=None, wave_field=field
    )
    for r, (_label, cell) in zip(d["regions"], sorted(cand.region_cells.items()), strict=True):
        assert (r["lat"], r["lon"]) == lakes_mod.cell_point(cell)
    assert all(len(r[k]) == 79 for r in d["regions"] for k in ("dir", "kt", "gust"))
    # West end's centroid (42.405, -82.905) rounds to the 42.4 / -82.9 cell.
    assert (d["regions"][0]["lat"], d["regions"][0]["lon"]) == (42.4, -82.9)


def test_a_big_lake_doubles_its_cells_but_regions_stay_at_a_tenth_and_do_not_count(client, monkeypatch):
    fake = FakeOpenMeteo(monkeypatch)
    d = _get(client, BIG)
    assert d["cell_deg"] > 0.1
    assert 0 < len(d["cells"]) <= 120
    assert len(d["regions"]) == 2
    # Region cells are 0.1 degrees whatever cell_deg is, and their wind is that exact cell's.
    for r in d["regions"]:
        assert round(r["lat"], 4) == round(round(r["lat"] / 0.1) * 0.1, 4)
        assert r["dir"][0] == round(r["lat"] * 10) % 360
    fetched = {p for call in fake.calls for p in call["points"]}
    assert {(r["lat"], r["lon"]) for r in d["regions"]} <= fetched
    assert sum(len(c["points"]) for c in fake.calls) == len(d["cells"]) + 2  # the two region cells ride along


def test_cells_and_regions_share_cache_entries_at_a_tenth_of_a_degree(client, monkeypatch):
    fake = FakeOpenMeteo(monkeypatch)
    _get(client, SMALL)
    assert sum(len(c["points"]) for c in fake.calls) == 2  # not 4: the region cells are the cells


# --- cache and errors ---------------------------------------------------------------------------------------


def test_cells_are_cached_for_thirty_minutes(client, monkeypatch):
    fake = FakeOpenMeteo(monkeypatch)
    first = _get(client, SMALL)
    _get(client, SMALL)
    assert len(fake.calls) == 1  # the second answer came from the cache
    monkeypatch.setattr(forecast, "_now", lambda: NOW + timedelta(minutes=29))
    _get(client, SMALL)
    assert len(fake.calls) == 1
    monkeypatch.setattr(forecast, "_now", lambda: NOW + timedelta(minutes=31))
    later = _get(client, SMALL)
    assert len(fake.calls) == 2
    assert later["fetched_at"] > first["fetched_at"]


def test_a_second_lake_only_fetches_the_cells_it_is_missing(client, monkeypatch):
    fake = FakeOpenMeteo(monkeypatch)
    _get(client, SMALL)
    _get(client, POND)  # 42.7 / -83.4 is not one of SMALL's cells
    assert len(fake.calls) == 2 and len(fake.calls[1]["points"]) == 1


def test_a_failed_cell_comes_back_null_with_an_error_and_is_not_cached(client, monkeypatch):
    fake = FakeOpenMeteo(monkeypatch, fail_points={(42.6, -83.3)})
    d = _get(client, SMALL)
    assert d["errors"] == ["open_meteo[0]: HTTP 500"]
    good, bad = d["cells"]
    assert good["kt"][0] is not None
    assert bad["kt"] == [None] * 79 and bad["dir"] == [None] * 79 and bad["gust"] == [None] * 79
    fake.fail_points.clear()
    assert _get(client, SMALL)["errors"] == [] and len(fake.calls) == 2  # only the failed cell was refetched
    assert fake.calls[1]["points"] == [(42.6, -83.3)]


def test_nothing_fetched_and_nothing_cached_is_a_502(client, monkeypatch):
    FakeOpenMeteo(monkeypatch, fail_all=True)
    r = client.get(f"/api/forecast/wind?lake={SMALL}")
    assert r.status_code == 502 and r.json()["detail"] == "open_meteo[0]: HTTP 500"


def test_a_stale_cache_still_answers_when_open_meteo_is_down(client, monkeypatch):
    FakeOpenMeteo(monkeypatch)
    _get(client, SMALL)
    monkeypatch.setattr(forecast, "_now", lambda: NOW + timedelta(hours=2))
    FakeOpenMeteo(monkeypatch, fail_all=True)
    d = _get(client, SMALL)
    assert d["errors"] == ["open_meteo[0]: HTTP 500"] and d["cells"][0]["kt"][0] is not None


def test_an_unknown_lake_is_a_404(client, monkeypatch):
    FakeOpenMeteo(monkeypatch)
    r = client.get("/api/forecast/wind?lake=999")
    assert r.status_code == 404
    assert client.get("/api/forecast/wind").status_code == 422  # lake is required


def test_hours_a_model_does_not_cover_are_null_in_all_three_arrays(client, monkeypatch):
    async def short(c, points, **kw):
        pay = payload(naive(2026, 9, 29), 12, lambda i: (90, 5, 8), models=["best_match"] * 12)
        return [pay for _ in points], []

    monkeypatch.setattr(forecast.openmeteo, "fetch_points", short)
    d = _get(client, POND)
    cell = d["cells"][0]
    assert cell["kt"][6] == 5 and cell["kt"][-1] is None and cell["dir"][-1] is None and cell["gust"][-1] is None
    assert d["models"][6] == "best_match" and d["models"][-1] is None


# --- fixture mode ---------------------------------------------------------------------------------------------


def test_fixture_mode_is_deterministic_and_never_touches_the_network(client, monkeypatch):
    monkeypatch.setenv("SEAPLANE_WIND_FIXTURES", "1")

    async def boom(*a, **kw):
        raise AssertionError("fixture mode fetched")

    monkeypatch.setattr(forecast.openmeteo, "fetch_points", boom)
    a = _get(client, SMALL)
    b = _get(client, SMALL)
    assert a == b
    assert len(a["times"]) == 79 and a["past"] == 6 and a["errors"] == []
    assert set(a["models"]) == {"fixture"}
    cell = a["cells"][0]
    assert cell["dir"][0] == 0 and cell["kt"][0] == 4 and cell["kt"][-1] == 4
    assert max(cell["kt"]) == 22 and cell["kt"][39] == 22  # the peak is mid-axis
    assert cell["dir"][39] == 180  # veered half way round at mid-axis
    assert all(g > k for k, g in zip(cell["kt"], cell["gust"], strict=True))
    assert len({tuple(c["dir"]) for c in a["cells"]}) == 1  # the same on every cell
    # Regions are filled too, at their own 0.1 degree cells.
    assert [r["label"] for r in a["regions"]] == ["west end", "east end"]
    assert (a["regions"][0]["lat"], a["regions"][0]["lon"]) == (42.4, -82.9)
    assert a["regions"][0]["kt"] == cell["kt"]


def test_fixture_mode_still_404s_an_unknown_lake(client, monkeypatch):
    monkeypatch.setenv("SEAPLANE_WIND_FIXTURES", "1")
    assert client.get("/api/forecast/wind?lake=999").status_code == 404


def test_fixture_mode_covers_a_lake_with_no_wave_field(client, monkeypatch):
    monkeypatch.setenv("SEAPLANE_WIND_FIXTURES", "1")
    d = _get(client, POND)
    assert len(d["cells"]) == 1 and d["regions"] == []


def test_the_axis_follows_the_settings(monkeypatch):
    monkeypatch.setenv("SEAPLANE_WIND_FIXTURES", "1")
    s = Settings.model_validate({"forecast": {"horizon_h": 12, "past_h": 3}})
    with TestClient(app_mod.create_app(scheduler=_Sched(s))) as c:
        d = _get(c, SMALL)
    assert len(d["times"]) == 16 and d["past"] == 3


def test_fixture_wind_shape():
    d, k, _g = forecast.fixture_wind(79)
    assert (d[0], d[-1]) == (0, 0) and k[0] == k[-1] == 4 and max(k) == 22
    assert forecast.fixture_wind(1) == ([0], [4], [7])
