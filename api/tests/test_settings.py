"""Settings: contract defaults, missing keys filled, unknown keys rejected, atomic write."""
from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from seaplane_api import paths
from seaplane_api.settings import Settings, load_settings, save_settings, settings_from_dict


def test_defaults_match_the_data_contract():
    s = Settings()
    assert s.timezone == "America/Detroit"
    assert (s.radius_nm, s.n_lakes, s.public_access_only) == (40, 8, False)
    assert s.home_airport.id == "KPTK" and s.home_airport.elev_ft == 981
    # True headings from aviationweather.gov, not the magnetic ones the old defaults carried.
    assert [(r.id, r.heading) for r in s.home_airport.runways] == [
        ("09L/27R", 88), ("09R/27L", 88), ("18/36", 172),
    ]
    assert [r.length_ft for r in s.home_airport.runways] == [5676, 6521, 2582]
    assert s.schedule.run_times_local == ["06:00", "09:00", "12:00", "15:00", "18:00", "20:00", "22:00"]
    assert s.outlook.times_local == ["18:00", "20:00", "22:00"]
    assert (s.outlook.morning_start, s.outlook.morning_end_local, s.outlook.min_window_hours) == (
        "sunrise", "12:00", 2,
    )
    assert s.notify.ntfy_url is None
    limits = s.limits.model_dump()
    assert limits["wind_ok"] == 12 and limits["wind_max"] == 18
    assert limits["fog_spread_f"] == 3 and limits["min_run_ft"] == 2000
    assert limits["ice_season_start"] == "12-01" and limits["ice_season_end"] == "04-01"


def test_missing_keys_are_filled_so_an_older_file_keeps_working():
    s = settings_from_dict({"radius_nm": 25})
    assert s.radius_nm == 25
    assert s.limits.wind_ok == 12  # whole `limits` object absent
    assert s.outlook.times_local == ["18:00", "20:00", "22:00"]
    s = settings_from_dict({"limits": {"wind_ok": 10}})
    assert s.limits.wind_ok == 10 and s.limits.wind_max == 18


def test_unknown_keys_are_rejected_at_every_level():
    for payload in (
        {"radius_nm": 40, "mystery": 1},
        {"limits": {"wind_ok": 10, "mystery": 1}},
        {"outlook": {"mystery": 1}},
        {"home_airport": {"mystery": 1}},
        {"notify": {"webhook": "x"}},
    ):
        with pytest.raises(ValidationError):
            settings_from_dict(payload)


@pytest.mark.parametrize(
    "payload",
    [
        {"timezone": "Mars/Olympus"},
        {"schedule": {"run_times_local": ["6:00"]}},
        {"schedule": {"run_times_local": ["25:00"]}},
        {"outlook": {"morning_end_local": "noon"}},
        {"outlook": {"morning_start": "dawn"}},
        {"limits": {"ice_season_start": "Dec 1"}},
        {"limits": {"ice_season_end": "13-01"}},
    ],
)
def test_bad_values_are_rejected(payload):
    with pytest.raises(ValidationError):
        settings_from_dict(payload)


def test_a_runway_without_length_ft_still_loads():
    """Files written before 2026-09-20 have no `length_ft` key on a runway at all."""
    s = settings_from_dict({"home_airport": {"runways": [{"id": "09R/27L", "heading": 88}]}})
    assert s.home_airport.runways[0].length_ft is None


def test_a_runway_with_length_ft_null_or_set_loads():
    s = settings_from_dict(
        {"home_airport": {"runways": [
            {"id": "09L/27R", "heading": 88, "length_ft": None},
            {"id": "09R/27L", "heading": 88, "length_ft": 6521},
        ]}}
    )
    assert [r.length_ft for r in s.home_airport.runways] == [None, 6521]


def test_home_water_is_optional_and_an_older_file_without_it_still_loads():
    """`home_water` landed after Bobby's settings file was written; its absence means none set."""
    assert Settings().home_water is None
    old = settings_from_dict({"radius_nm": 30, "limits": {"wind_ok": 11}})
    assert old.home_water is None and old.radius_nm == 30


def test_home_water_takes_an_id_and_a_name():
    s = settings_from_dict({"home_water": {"id": 7654321, "name": "Lake St. Clair"}})
    assert s.home_water.id == 7654321 and s.home_water.name == "Lake St. Clair"
    assert settings_from_dict({"home_water": None}).home_water is None
    # The name is only a label for the client; the id is what the briefing resolves.
    assert settings_from_dict({"home_water": {"id": 42}}).home_water.name == ""


@pytest.mark.parametrize(
    "payload",
    [
        {"home_water": {"id": 42, "mystery": 1}},
        {"home_water": {"name": "No id"}},
        {"home_water": {"id": "not a number"}},
        {"home_water": 42},
    ],
)
def test_a_malformed_home_water_is_rejected(payload):
    with pytest.raises(ValidationError):
        settings_from_dict(payload)


def test_morning_start_accepts_an_explicit_time():
    assert settings_from_dict({"outlook": {"morning_start": "06:30"}}).outlook.morning_start == "06:30"


def test_all_run_times_is_the_union_of_the_schedule_and_the_outlook():
    s = settings_from_dict(
        {"schedule": {"run_times_local": ["06:00", "18:00"]}, "outlook": {"times_local": ["18:00", "21:30"]}}
    )
    assert s.all_run_times() == ["06:00", "18:00", "21:30"]


def test_load_creates_the_file_with_defaults_on_first_start(tmp_path, monkeypatch):
    monkeypatch.setenv("SEAPLANE_DATA_MANUAL", str(tmp_path))
    assert not paths.settings_path().exists()
    s = load_settings()
    assert s.home_airport.id == "KPTK"
    assert paths.settings_path().exists()
    on_disk = json.loads(paths.settings_path().read_text())
    assert on_disk["timezone"] == "America/Detroit"
    assert set(on_disk) == {
        "home_airport", "timezone", "radius_nm", "n_lakes", "public_access_only", "home_water",
        "schedule", "outlook", "notify", "limits", "forecast",
    }
    assert on_disk["home_water"] is None


def test_save_is_atomic_and_leaves_no_temp_files(tmp_path, monkeypatch):
    monkeypatch.setenv("SEAPLANE_DATA_MANUAL", str(tmp_path))
    save_settings(settings_from_dict({"radius_nm": 30}))
    save_settings(settings_from_dict({"radius_nm": 35}))
    assert load_settings().radius_nm == 35
    assert [p.name for p in tmp_path.iterdir()] == ["settings.json"]


def test_a_hand_broken_file_raises_rather_than_being_silently_replaced(tmp_path, monkeypatch):
    monkeypatch.setenv("SEAPLANE_DATA_MANUAL", str(tmp_path))
    paths.settings_path().write_text(json.dumps({"radius_nm": 40, "mystery": True}))
    with pytest.raises(ValidationError):
        load_settings()


def test_corrupt_json_falls_back_to_defaults(tmp_path, monkeypatch):
    monkeypatch.setenv("SEAPLANE_DATA_MANUAL", str(tmp_path))
    paths.settings_path().write_text("{not json")
    assert load_settings().radius_nm == 40


def test_write_json_atomic_replaces_in_place(tmp_path):
    target = tmp_path / "out" / "briefing.json"
    paths.write_json_atomic(target, {"a": 1})
    paths.write_json_atomic(target, {"a": 2})
    assert json.loads(target.read_text()) == {"a": 2}
    assert [p.name for p in target.parent.iterdir()] == ["briefing.json"]
    assert paths.read_json(tmp_path / "nope.json") is None
