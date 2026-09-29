"""`settings.forecast` (contract, "Forecast timeline"): wind models, horizon, past hours."""
from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from seaplane_api import paths
from seaplane_api.settings import Settings, load_settings, settings_from_dict


def test_forecast_defaults_and_a_missing_object_gets_them(tmp_path, monkeypatch):
    s = Settings()
    assert s.forecast.wind_models == ["ncep_hrrr_conus", "best_match"]
    assert (s.forecast.horizon_h, s.forecast.past_h) == (72, 6)
    monkeypatch.setenv("SEAPLANE_DATA_MANUAL", str(tmp_path))
    paths.settings_path().write_text(json.dumps({"radius_nm": 30}))  # a file from before `forecast` existed
    assert load_settings().forecast.horizon_h == 72


def test_forecast_day_counts_cover_the_axis_from_any_hour_of_the_day():
    f = Settings().forecast
    assert (f.forecast_days, f.past_days) == (4, 1)  # 23:00 + 72 h still lands inside day 4
    g = Settings.model_validate({"forecast": {"horizon_h": 24, "past_h": 0}}).forecast
    assert (g.forecast_days, g.past_days) == (2, 0)


def test_forecast_accepts_a_custom_model_order_ending_in_best_match():
    order = ["ncep_nbm_conus", "ncep_hrrr_conus", "best_match"]
    assert settings_from_dict({"forecast": {"wind_models": order}}).forecast.wind_models == order
    assert settings_from_dict({"forecast": {"wind_models": ["best_match"]}}).forecast.wind_models == ["best_match"]


@pytest.mark.parametrize(
    "forecast",
    [
        {"wind_models": []},
        {"wind_models": ["ncep_nbm_conus"]},  # best_match must be there, and last
        {"wind_models": ["best_match", "ncep_nbm_conus"]},
        {"wind_models": ["ncep_nbm_conus", "ncep_nbm_conus", "best_match"]},
        {"wind_models": ["NCEP NBM", "best_match"]},  # not a model id
        {"wind_models": ["a;b=c", "best_match"]},
        {"horizon_h": 0},
        {"horizon_h": 241},
        {"past_h": -1},
        {"past_h": 49},
        {"horizon_h": "soon"},
        {"mystery": 1},
    ],
)
def test_bad_forecast_settings_are_rejected(forecast):
    with pytest.raises(ValidationError):
        settings_from_dict({"forecast": forecast})


def test_the_put_endpoint_rejects_a_bad_forecast_with_422(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from seaplane_api import app as app_mod

    monkeypatch.setenv("SEAPLANE_DATA_MANUAL", str(tmp_path))
    body = Settings().model_dump(mode="json")
    body["forecast"]["wind_models"] = ["ncep_nbm_conus"]

    class Sched:
        settings = Settings()

    with TestClient(app_mod.create_app(scheduler=Sched())) as c:
        assert c.put("/api/settings", json=body).status_code == 422
    assert not paths.settings_path().exists()  # nothing written
