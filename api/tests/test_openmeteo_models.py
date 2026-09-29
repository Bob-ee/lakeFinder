"""Several wind models in one Open-Meteo request: the per-hour fallback and the request shape.

The suffixed response shape below is the real one, checked live on 2026-09-28
(`models=ncep_nbm_conus,best_match` renames every variable `<var>_<model>`).
"""
from __future__ import annotations

import httpx
import pytest

from seaplane_api.fetch import openmeteo
from seaplane_api.fetch.http import USER_AGENT

TIMES = ["2026-09-29T06:00", "2026-09-29T07:00", "2026-09-29T08:00", "2026-09-29T09:00"]


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), headers={"User-Agent": USER_AGENT}, timeout=1.0)


def _multi() -> dict:
    """HRRR: all three at hour 0, no gust at hour 1, nothing at hours 2-3. best_match: everything."""
    return {
        "utc_offset_seconds": -14400,
        "hourly": {
            "time": TIMES,
            "wind_speed_10m_ncep_hrrr_conus": [5.0, 6.0, None, None],
            "wind_gusts_10m_ncep_hrrr_conus": [8.0, None, None, None],
            "wind_direction_10m_ncep_hrrr_conus": [90, 100, None, None],
            "temperature_2m_ncep_hrrr_conus": [50.0, 51.0, None, None],
            "wind_speed_10m_best_match": [2.0, 3.0, 4.0, 5.0],
            "wind_gusts_10m_best_match": [4.0, 5.0, 6.0, 7.0],
            "wind_direction_10m_best_match": [270, 280, 290, 300],
            "temperature_2m_best_match": [60.0, 61.0, 62.0, 63.0],
        },
    }


def test_wind_comes_from_the_first_model_with_all_three_values_hour_by_hour():
    out = openmeteo.merge_models(_multi(), ["ncep_hrrr_conus", "best_match"])
    h = out["hourly"]
    assert h["wind_model"] == ["ncep_hrrr_conus", "best_match", "best_match", "best_match"]
    assert h["wind_speed_10m"] == [5.0, 3.0, 4.0, 5.0]
    assert h["wind_gusts_10m"] == [8.0, 5.0, 6.0, 7.0]
    # Hour 1: HRRR has speed and direction but no gust, so none of its three is used (no mixing).
    assert h["wind_direction_10m"] == [90, 280, 290, 300]


def test_every_other_variable_comes_from_best_match_never_the_first_model():
    h = openmeteo.merge_models(_multi(), ["ncep_hrrr_conus", "best_match"])["hourly"]
    assert h["temperature_2m"] == [60.0, 61.0, 62.0, 63.0]
    assert not any(k.endswith(("_ncep_hrrr_conus", "_best_match")) for k in h)
    assert h["time"] == TIMES


def test_an_hour_no_model_covers_is_null_in_all_three_with_no_model():
    pay = _multi()
    for k in ("wind_speed_10m_best_match", "wind_gusts_10m_best_match", "wind_direction_10m_best_match"):
        pay["hourly"][k][3] = None
    h = openmeteo.merge_models(pay, ["ncep_hrrr_conus", "best_match"])["hourly"]
    assert h["wind_model"][3] is None
    assert (h["wind_speed_10m"][3], h["wind_gusts_10m"][3], h["wind_direction_10m"][3]) == (None, None, None)


def test_a_single_model_response_keeps_its_bare_names_and_records_the_model():
    pay = {"hourly": {"time": TIMES[:2], "wind_speed_10m": [1, 2], "wind_gusts_10m": [3, 4], "wind_direction_10m": [5, 6],
                      "temperature_2m": [70, 71]}}
    h = openmeteo.merge_models(pay, ["best_match"])["hourly"]
    assert h["wind_model"] == ["best_match", "best_match"]
    assert h["wind_speed_10m"] == [1, 2] and h["temperature_2m"] == [70, 71]


def test_a_model_id_that_ends_another_is_not_confused_with_it():
    pay = {"hourly": {"time": TIMES[:1], "wind_speed_10m_gfs_seamless": [9], "wind_gusts_10m_gfs_seamless": [9],
                      "wind_direction_10m_gfs_seamless": [9], "temperature_2m_seamless": [1],
                      "temperature_2m_best_match": [2], "wind_speed_10m_best_match": [1],
                      "wind_gusts_10m_best_match": [1], "wind_direction_10m_best_match": [1]}}
    h = openmeteo.merge_models(pay, ["gfs_seamless", "best_match"])["hourly"]
    assert h["wind_speed_10m"] == [9] and h["temperature_2m"] == [2]


@pytest.mark.anyio
async def test_the_request_lists_the_models_and_the_payloads_come_back_merged():
    seen = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.update(dict(request.url.params))
        return httpx.Response(200, json=_multi())

    async with _client(handler) as c:
        out, errors = await openmeteo.fetch_points(
            c, [(42.0, -83.0)], timezone="America/Detroit", models=["ncep_hrrr_conus", "best_match"],
            hourly=openmeteo.WIND_VARS, forecast_days=4, past_days=1,
        )
    assert errors == []
    assert seen["models"] == "ncep_hrrr_conus,best_match"
    assert seen["hourly"] == "wind_speed_10m,wind_gusts_10m,wind_direction_10m"
    assert (seen["forecast_days"], seen["past_days"]) == ("4", "1")
    assert "forecast_hours" not in seen and "past_hours" not in seen  # they do not combine with the day counts
    assert out[0]["hourly"]["wind_model"][0] == "ncep_hrrr_conus"


@pytest.mark.anyio
async def test_no_models_means_the_request_and_payloads_are_unchanged():
    seen = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.update(dict(request.url.params))
        return httpx.Response(200, json={"hourly": {"time": TIMES[:1], "wind_speed_10m": [3]}})

    async with _client(handler) as c:
        out, _ = await openmeteo.fetch_points(c, [(42.0, -83.0)], timezone="America/Detroit")
    assert "models" not in seen and "wind_model" not in out[0]["hourly"]


@pytest.mark.anyio
async def test_a_rejected_model_list_retries_with_best_match_and_still_reports_it():
    calls = []

    async def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.params["models"])
        if "bogus" in request.url.params["models"]:
            return httpx.Response(400, json={"error": True, "reason": "Cannot initialize"})
        return httpx.Response(
            200,
            json={"hourly": {"time": TIMES[:1], "wind_speed_10m": [3], "wind_gusts_10m": [4], "wind_direction_10m": [5]}},
        )

    async with _client(handler) as c:
        out, errors = await openmeteo.fetch_points(
            c, [(42.0, -83.0)], timezone="America/Detroit", models=["bogus_model", "best_match"]
        )
    assert calls == ["bogus_model,best_match", "best_match"]
    assert out[0]["hourly"]["wind_speed_10m"] == [3] and out[0]["hourly"]["wind_model"] == ["best_match"]
    assert len(errors) == 1 and "bogus_model,best_match" in errors[0]


@pytest.mark.anyio
async def test_the_marine_request_asks_for_a_past_day_so_past_hours_have_a_value():
    seen = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.update(dict(request.url.params))
        return httpx.Response(200, json={"hourly": {"time": [], "wave_height": []}})

    async with _client(handler) as c:
        await openmeteo.fetch_marine(c, 42.45, -82.70, timezone="America/Detroit")
    assert seen["past_days"] == "1"
