"""The five endpoints, with the fetch layer stubbed so nothing touches the network."""
from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from seaplane_api import app as app_mod
from seaplane_api import service
from seaplane_api.briefing.generate import build_briefing
from seaplane_api.fetch import aviationweather
from seaplane_api.scheduler import Scheduler
from seaplane_api.settings import Settings

from .conftest import DETROIT, load, make_feeds

NOW = datetime(2026, 9, 19, 18, 0, tzinfo=DETROIT)


class StubScheduler(Scheduler):
    """A scheduler that builds the briefing from fixtures instead of fetching."""

    def __init__(self, tmp_path):
        self.settings = Settings()
        self.last_run = None
        self.runs: list[str] = []
        self._tmp = tmp_path

    async def run(self, *, run_kind: str, run_at: str | None = None) -> dict:
        self.runs.append(run_kind)
        briefing = build_briefing(
            self.settings,
            make_feeds("om_calm.json", taf_name="taf_vfr.json"),
            [],
            now_utc=NOW.astimezone(UTC),
            run_kind=run_kind,
            run_at=run_at,
        )
        from seaplane_api.paths import briefing_path, write_json_atomic

        write_json_atomic(briefing_path(), briefing)
        self.last_run = NOW
        return briefing


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("SEAPLANE_DATA_OUT", str(tmp_path / "out"))
    monkeypatch.setenv("SEAPLANE_DATA_MANUAL", str(tmp_path / "manual"))
    (tmp_path / "out").mkdir()
    (tmp_path / "manual").mkdir()
    sched = StubScheduler(tmp_path)
    with TestClient(app_mod.create_app(scheduler=sched)) as c:
        c.scheduler = sched  # type: ignore[attr-defined]
        yield c


def test_health_before_any_briefing_exists(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["briefing_generated_at"] is None
    assert body["next_run_local"] in {"06:00", "09:00", "12:00", "15:00", "18:00", "20:00", "22:00"}


def test_refresh_writes_a_briefing_and_health_then_reports_it(client):
    r = client.post("/api/briefing/refresh")
    assert r.status_code == 200
    briefing = r.json()
    assert briefing["run_kind"] == "manual"
    assert briefing["schema"] == 1
    assert client.scheduler.runs == ["manual"]
    assert client.get("/api/health").json()["briefing_generated_at"] == briefing["generated_at"]


def test_get_settings_returns_the_contract_object(client):
    body = client.get("/api/settings").json()
    assert set(body) == {
        "home_airport", "timezone", "radius_nm", "n_lakes", "public_access_only",
        "schedule", "outlook", "notify", "limits",
    }
    assert body["home_airport"]["id"] == "KPTK"


def test_put_settings_validates_writes_and_triggers_a_run(client, tmp_path):
    payload = client.get("/api/settings").json()
    payload["radius_nm"] = 25
    payload["limits"]["wind_max"] = 20
    r = client.put("/api/settings", json=payload)
    assert r.status_code == 200
    assert r.json()["radius_nm"] == 25
    assert client.get("/api/settings").json()["limits"]["wind_max"] == 20
    on_disk = json.loads((tmp_path / "manual" / "settings.json").read_text())
    assert on_disk["radius_nm"] == 25
    assert "manual" in client.scheduler.runs  # the background run fired


def test_put_settings_rejects_unknown_keys_with_422(client):
    payload = client.get("/api/settings").json()
    payload["mystery"] = True
    assert client.put("/api/settings", json=payload).status_code == 422


def test_put_settings_rejects_a_bad_timezone_with_422(client):
    payload = client.get("/api/settings").json()
    payload["timezone"] = "Mars/Olympus"
    assert client.put("/api/settings", json=payload).status_code == 422


def test_get_airport_maps_the_live_shape(client, monkeypatch):
    async def fake(_c, ident):
        assert ident == "KPTK"
        return load("airport_kptk.json"), None

    monkeypatch.setattr(aviationweather, "fetch_airport", fake)
    body = client.get("/api/airports/kptk").json()
    assert body["id"] == "KPTK"
    assert body["name"] == "Pontiac/Oakland County Intl"
    assert body["lat"] == pytest.approx(42.6656) and body["lon"] == pytest.approx(-83.4205)
    assert body["elev_ft"] == 981  # `elev` is 299 metres on the wire
    assert {"id": "18/36", "heading": 172} in body["runways"]
    assert {"id": "09R/27L", "heading": 88} in body["runways"]


def test_get_airport_404s_on_an_unknown_identifier(client, monkeypatch):
    async def fake(_c, ident):
        return None, f"airport: {ident} not found"

    monkeypatch.setattr(aviationweather, "fetch_airport", fake)
    r = client.get("/api/airports/ZZZZ")
    assert r.status_code == 404
    assert "ZZZZ" in r.json()["detail"]


def test_the_wind_proxy_slot_is_not_mounted_yet(client):
    assert client.get("/api/wind/stations?bbox=1,2,3,4").status_code == 404


def test_load_candidates_flags_a_missing_extents_file(tmp_path, monkeypatch):
    monkeypatch.setenv("SEAPLANE_DATA_OUT", str(tmp_path))
    (tmp_path / "index.json").write_text(json.dumps(load("index_sample.json")))
    candidates, errors = service.load_candidates(Settings())
    assert errors == [service.MISSING_EXTENTS_ERROR]
    assert candidates and all(c.extents_ft is None for c in candidates)


def test_load_candidates_uses_the_extents_file_when_it_is_there(tmp_path, monkeypatch):
    monkeypatch.setenv("SEAPLANE_DATA_OUT", str(tmp_path))
    (tmp_path / "index.json").write_text(json.dumps(load("index_sample.json")))
    (tmp_path / "lake_extents.json").write_text(json.dumps(load("lake_extents_sample.json")))
    candidates, errors = service.load_candidates(Settings())
    assert errors == []
    assert all(c.extents_ft is not None and len(c.extents_ft) == 16 for c in candidates)


def test_load_candidates_reports_a_missing_index(tmp_path, monkeypatch):
    monkeypatch.setenv("SEAPLANE_DATA_OUT", str(tmp_path))
    candidates, errors = service.load_candidates(Settings())
    assert candidates == [] and errors and "index.json" in errors[0]
