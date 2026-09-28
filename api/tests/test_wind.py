"""The wind proxy (`docs/data-contract.md`, "Wind proxy"): tiling, caching, merge, and the two
routes. Every upstream fetcher is monkeypatched -- nothing here opens a socket.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from seaplane_api import app as app_mod
from seaplane_api import wind
from seaplane_api.fetch import aviationweather, ndbc, openmeteo, synoptic

ST_CLAIR_BBOX = "-83.2,42.2,-82.4,42.8"


@pytest.fixture
def client():
    with TestClient(app_mod.create_app(scheduler=object())) as c:
        yield c


def _freeze(monkeypatch, at: datetime):
    monkeypatch.setattr(wind, "_now", lambda: at)


# --- tiling --------------------------------------------------------------------------------


def test_tiles_cover_the_lake_st_clair_bbox_with_two_tiles():
    tiles = wind.tiles_for_bbox((-83.2, 42.2, -82.4, 42.8))
    assert sorted(tiles) == [(-84, 42), (-83, 42)]


def test_an_exact_sixteen_tile_bbox_is_not_rejected():
    # Edges on whole degrees must not pull in an extra row/column of empty tiles.
    tiles = wind.tiles_for_bbox((-88.0, 40.0, -84.0, 44.0))
    assert len(tiles) == 16


def test_a_degenerate_bbox_has_no_tiles():
    assert wind.tiles_for_bbox((-83.0, 42.0, -83.0, 42.0)) == []
    assert wind.tiles_for_bbox((-82.0, 42.0, -83.0, 42.0)) == []  # east < west


def test_more_than_sixteen_tiles_is_rejected(client):
    r = client.get("/api/wind/stations?bbox=-90,40,-80,45")  # well over 16 tiles
    assert r.status_code == 400
    assert r.json()["detail"] == "bbox too large"


def test_a_malformed_bbox_is_rejected(client):
    assert client.get("/api/wind/stations?bbox=1,2,3").status_code == 400
    assert client.get("/api/wind/stations?bbox=a,b,c,d").status_code == 400


# --- row mapping: calm / variable ------------------------------------------------------------


def test_metar_variable_wind_has_no_direction():
    rows = wind._metar_rows(
        [{"icaoId": "KDET", "lat": 42.4, "lon": -83.0, "obsTime": 1_800_000_000, "wdir": "VRB", "wspd": 3}]
    )
    assert rows[0]["dir_deg"] is None and rows[0]["speed_kt"] == 3


def test_metar_calm_forces_direction_null_even_if_one_was_reported():
    rows = wind._metar_rows(
        [{"icaoId": "KDET", "lat": 42.4, "lon": -83.0, "obsTime": 1_800_000_000, "wdir": 270, "wspd": 0}]
    )
    assert rows[0]["speed_kt"] == 0 and rows[0]["dir_deg"] is None


def test_ndbc_calm_forces_direction_null():
    rows = wind._ndbc_rows(
        [{"id": "45147", "lat": 42.46, "lon": -82.71, "at": datetime(2026, 9, 28, 15, 0, tzinfo=UTC),
          "dir_deg": 10.0, "speed_kt": 0.0, "gust_kt": None}]
    )
    assert rows[0]["speed_kt"] == 0 and rows[0]["dir_deg"] is None


def test_a_station_with_no_wind_data_at_all_is_dropped():
    rows = wind._metar_rows(
        [{"icaoId": "KARB", "lat": 42.2, "lon": -83.7, "obsTime": 1_800_000_000, "wdir": None, "wspd": None}]
    )
    assert rows == []


# --- merge: dedupe, age cut, clip -------------------------------------------------------------


def _station(source, id_, lat, lon, obs_time, speed=10):
    return {"id": id_, "source": source, "name": None, "lat": lat, "lon": lon, "dir_deg": 240,
            "speed_kt": speed, "gust_kt": None, "obs_time": obs_time}


def test_merge_dedupes_by_source_and_id_keeping_the_newest():
    now = datetime(2026, 9, 28, 16, 0, tzinfo=UTC)
    old = _station("metar", "KDET", 42.4, -83.0, now - timedelta(minutes=20), speed=8)
    new = _station("metar", "KDET", 42.4, -83.0, now - timedelta(minutes=5), speed=12)
    a = wind._TileEntry(stations=[old], errors=[], fetched_at=now)
    b = wind._TileEntry(stations=[new], errors=[], fetched_at=now)
    out, errors = wind.merge_stations([a, b], (-84.0, 42.0, -82.0, 43.0), now)
    assert len(out) == 1 and out[0]["speed_kt"] == 12 and errors == []


def test_merge_drops_observations_older_than_three_hours():
    now = datetime(2026, 9, 28, 16, 0, tzinfo=UTC)
    fresh = _station("metar", "KDET", 42.4, -83.0, now - timedelta(hours=1))
    stale = _station("ndbc", "45147", 42.46, -82.71, now - timedelta(hours=3, minutes=1))
    entry = wind._TileEntry(stations=[fresh, stale], errors=[], fetched_at=now)
    out, _ = wind.merge_stations([entry], (-84.0, 42.0, -82.0, 43.0), now)
    assert [s["id"] for s in out] == ["KDET"]


def test_merge_clips_to_the_requested_bbox_not_the_tile():
    now = datetime(2026, 9, 28, 16, 0, tzinfo=UTC)
    inside = _station("metar", "IN", 42.5, -83.1, now)
    outside = _station("metar", "OUT", 43.9, -83.9, now)  # in the tile, not the requested bbox
    entry = wind._TileEntry(stations=[inside, outside], errors=[], fetched_at=now)
    out, _ = wind.merge_stations([entry], (-83.2, 42.2, -82.4, 42.8), now)
    assert [s["id"] for s in out] == ["IN"]


def test_merge_collects_errors_without_duplicates():
    now = datetime(2026, 9, 28, 16, 0, tzinfo=UTC)
    a = wind._TileEntry(stations=[], errors=["ndbc: timeout"], fetched_at=now)
    b = wind._TileEntry(stations=[], errors=["ndbc: timeout", "metar_bbox: HTTP 500"], fetched_at=now)
    _, errors = wind.merge_stations([a, b], (-84.0, 42.0, -82.0, 43.0), now)
    assert errors == ["ndbc: timeout", "metar_bbox: HTTP 500"]


# --- one source failing, the other still answers ----------------------------------------------


@pytest.mark.anyio
async def test_one_source_failing_still_lets_the_other_answer(monkeypatch):
    monkeypatch.delenv("SYNOPTIC_TOKEN", raising=False)

    async def boom(_c, _bbox):
        return None, "metar_bbox: HTTP 500"

    async def ok(_c, _bbox):
        return [{"id": "45147", "lat": 42.46, "lon": -82.71, "at": datetime(2026, 9, 28, 15, 55, tzinfo=UTC),
                  "dir_deg": 90.0, "speed_kt": 12.0, "gust_kt": 18.0, "wave_height_m": None}], None

    monkeypatch.setattr(aviationweather, "fetch_metar_bbox", boom)
    monkeypatch.setattr(ndbc, "fetch_latest_obs", ok)
    entry = await wind._fetch_tile((-83, 42))
    assert entry.errors == ["metar_bbox: HTTP 500"]
    assert [s["id"] for s in entry.stations] == ["45147"]


@pytest.mark.anyio
async def test_synoptic_is_skipped_silently_when_unset(monkeypatch):
    monkeypatch.delenv("SYNOPTIC_TOKEN", raising=False)

    async def no_metars(_c, _bbox):
        return [], None

    async def no_buoys(_c, _bbox):
        return [], None

    called = {"synoptic": False}

    async def would_fail(_c, _bbox):
        called["synoptic"] = True
        return [], "synoptic: should not be called"

    monkeypatch.setattr(aviationweather, "fetch_metar_bbox", no_metars)
    monkeypatch.setattr(ndbc, "fetch_latest_obs", no_buoys)
    monkeypatch.setattr(synoptic, "fetch_stations", would_fail)
    entry = await wind._fetch_tile((-83, 42))
    assert entry.errors == [] and called["synoptic"] is False


# --- cache hit within TTL, and the lock dedupes concurrent fetches ----------------------------


@pytest.mark.anyio
async def test_a_second_request_within_the_ttl_does_not_refetch(monkeypatch):
    monkeypatch.delenv("SYNOPTIC_TOKEN", raising=False)
    calls = {"n": 0}

    async def counted(_c, _bbox):
        calls["n"] += 1
        return [], None

    monkeypatch.setattr(aviationweather, "fetch_metar_bbox", counted)
    monkeypatch.setattr(ndbc, "fetch_latest_obs", counted)
    t0 = datetime(2026, 9, 28, 16, 0, tzinfo=UTC)
    _freeze(monkeypatch, t0)
    await wind._get_tile((-83, 42))
    assert calls["n"] == 2  # metar + ndbc, once each

    _freeze(monkeypatch, t0 + timedelta(minutes=4))  # still inside the 5 minute TTL
    await wind._get_tile((-83, 42))
    assert calls["n"] == 2  # cache hit: no new upstream calls

    _freeze(monkeypatch, t0 + timedelta(minutes=6))  # past the TTL
    await wind._get_tile((-83, 42))
    assert calls["n"] == 4


@pytest.mark.anyio
async def test_concurrent_requests_for_the_same_tile_fetch_once(monkeypatch):
    import asyncio

    monkeypatch.delenv("SYNOPTIC_TOKEN", raising=False)
    calls = {"n": 0}

    async def slow(_c, _bbox):
        calls["n"] += 1
        await asyncio.sleep(0.05)
        return [], None

    monkeypatch.setattr(aviationweather, "fetch_metar_bbox", slow)
    monkeypatch.setattr(ndbc, "fetch_latest_obs", slow)
    await asyncio.gather(*(wind._get_tile((-83, 42)) for _ in range(5)))
    assert calls["n"] == 2  # one metar call and one ndbc call, not five of each


# --- point: snapping, caching, 502 --------------------------------------------------------------


@pytest.mark.anyio
async def test_point_is_snapped_to_the_zero_point_one_degree_cell(monkeypatch):
    seen = []

    async def fake(_c, lat, lon):
        seen.append((lat, lon))
        return {"current": {"time": "2026-09-28T16:00", "wind_speed_10m": 10, "wind_direction_10m": 250,
                             "wind_gusts_10m": 15}}, None

    monkeypatch.setattr(openmeteo, "fetch_current", fake)
    cell = wind.cell_of(42.4, -82.7)
    entry = await wind._get_point(cell, 42.4, -82.7)
    assert entry.payload is not None
    row = wind._point_row(entry.payload, 42.4, -82.7)
    assert row == {"lat": 42.4, "lon": -82.7, "dir_deg": 250, "speed_kt": 10, "gust_kt": 15,
                    "time": "2026-09-28T16:00:00Z", "source": "model"}
    assert seen == [(42.4, -82.7)]


def test_get_point_endpoint_snaps_before_calling_upstream(client, monkeypatch):
    seen = []

    async def fake(_c, lat, lon):
        seen.append((lat, lon))
        return {"current": {"time": "2026-09-28T16:00", "wind_speed_10m": 10, "wind_direction_10m": 250,
                             "wind_gusts_10m": None}}, None

    monkeypatch.setattr(openmeteo, "fetch_current", fake)
    r = client.get("/api/wind/point?lat=42.42&lon=-82.73")
    assert r.status_code == 200
    body = r.json()
    assert (body["lat"], body["lon"]) == (42.4, -82.7)
    assert body["gust_kt"] is None
    assert seen == [(42.4, -82.7)]

    # A second point in the same 0.1 degree cell reuses the cached lookup.
    r2 = client.get("/api/wind/point?lat=42.44&lon=-82.66")
    assert r2.status_code == 200
    assert seen == [(42.4, -82.7)]  # no second upstream call


def test_get_point_calm_has_no_direction(client, monkeypatch):
    async def fake(_c, lat, lon):
        return {"current": {"time": "2026-09-28T16:00", "wind_speed_10m": 0, "wind_direction_10m": 0,
                             "wind_gusts_10m": None}}, None

    monkeypatch.setattr(openmeteo, "fetch_current", fake)
    body = client.get("/api/wind/point?lat=42.4&lon=-82.7").json()
    assert body["speed_kt"] == 0 and body["dir_deg"] is None


def test_get_point_502s_when_open_meteo_fails(client, monkeypatch):
    async def fake(_c, lat, lon):
        return None, "open_meteo_current: HTTP 503"

    monkeypatch.setattr(openmeteo, "fetch_current", fake)
    r = client.get("/api/wind/point?lat=42.4&lon=-82.7")
    assert r.status_code == 502
    assert r.json()["detail"] == "open_meteo_current: HTTP 503"


def test_get_point_failure_is_not_cached_and_a_retry_can_succeed(client, monkeypatch):
    calls = {"n": 0}

    async def fake(_c, lat, lon):
        calls["n"] += 1
        if calls["n"] == 1:
            return None, "open_meteo_current: HTTP 503"
        return {"current": {"time": "2026-09-28T16:00", "wind_speed_10m": 5, "wind_direction_10m": 200,
                             "wind_gusts_10m": None}}, None

    monkeypatch.setattr(openmeteo, "fetch_current", fake)
    assert client.get("/api/wind/point?lat=42.4&lon=-82.7").status_code == 502
    assert client.get("/api/wind/point?lat=42.4&lon=-82.7").status_code == 200
    assert calls["n"] == 2


# --- the full stations endpoint end to end, fixtures stubbed ----------------------------------


def test_stations_endpoint_merges_and_reports_fetched_at(client, monkeypatch):
    async def metars(_c, bbox):
        return [{"icaoId": "KDET", "lat": 42.409, "lon": -83.01, "name": "Detroit/C Young Arpt, MI, US",
                  "obsTime": 1_800_000_000, "wdir": 240, "wspd": 12, "wgst": 18}], None

    async def buoys(_c, bbox):
        return [], None

    monkeypatch.setattr(aviationweather, "fetch_metar_bbox", metars)
    monkeypatch.setattr(ndbc, "fetch_latest_obs", buoys)
    r = client.get(f"/api/wind/stations?bbox={ST_CLAIR_BBOX}")
    assert r.status_code == 200
    body = r.json()
    assert body["errors"] == []
    assert body["stations"] == [
        {"id": "KDET", "source": "metar", "name": "Detroit/C Young Arpt, MI, US", "lat": 42.409,
         "lon": -83.01, "dir_deg": 240, "speed_kt": 12, "gust_kt": 18,
         "obs_time": datetime.fromtimestamp(1_800_000_000, tz=UTC).isoformat().replace("+00:00", "Z")}
    ]
    assert body["fetched_at"]


@pytest.fixture
def anyio_backend():
    return "asyncio"
