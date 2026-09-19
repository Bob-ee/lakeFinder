"""A generated briefing carries exactly the keys `docs/data-contract.md` specifies, and no others.

The contract is the interface between three codebases, so this test spells the key sets out by hand
rather than deriving them from the code it is checking.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime

from seaplane_api.briefing import lakes as lakes_mod
from seaplane_api.briefing.generate import build_briefing
from seaplane_api.briefing.scoring import LEVELS
from seaplane_api.settings import HomeWater, Settings

from .conftest import DETROIT, load, make_feeds, make_wave_field, wave_point

TOP = {
    "schema", "generated_at", "run_kind", "timezone", "home_airport", "summary", "days",
    "outlook", "alerts", "lakes", "home_water", "sources", "links", "errors",
}
DAY = {"date", "score", "best_window", "blocks"}
BLOCK = {
    "start", "end", "score", "limiting", "wind", "xwind_kt", "runway",
    "ceiling_ft", "ceiling_known", "vis_sm", "da_ft", "temp_f", "precip_prob",
}
OUTLOOK = {
    "target_date", "window", "sun", "score", "limiting", "watch", "best_window", "hours",
    "lakes", "home_water", "confidence", "confidence_reasons", "trend", "runs", "summary",
}
HOUR = {
    "time", "score", "limiting", "wind", "xwind_kt", "runway", "ceiling_ft", "ceiling_known",
    "vis_sm", "temp_f", "dewpoint_f", "fog_risk", "precip_prob", "da_ft",
}
LAKE = {
    "id", "name", "kind", "score", "limiting", "hs_in", "run_ft", "region", "hs_open_in",
    "regions", "wind", "distance_nm", "bearing_deg", "verdict", "frozen",
}
REGION = {"label", "hs_in", "run_ft", "lat", "lon"}
HOME_WATER = {
    "id", "name", "kind", "score", "limiting", "wind", "regions", "hs_open_in", "observed",
    "marine_hs_in",
}
OBSERVED = {"station", "name", "kind", "at", "wind", "wave_ft", "distance_nm"}
RUN = {"at", "generated_at", "score", "best_window", "limiting", "max_gust_kt"}
LIMITING_IDS = {
    "wind", "gusts", "xwind_runway", "ceiling", "visibility", "fog", "precip", "convection",
    "density_altitude", "temperature", "alert", "daylight", "waves", "run", "xwind_water", "ice",
}

CASS_LAKE = 1900195525


def _candidates(settings: Settings, wave_field=None) -> list:
    return lakes_mod.select_candidates(
        load("index_sample.json"),
        home_lat=settings.home_airport.lat,
        home_lon=settings.home_airport.lon,
        radius_nm=settings.radius_nm,
        min_run_ft=settings.limits.min_run_ft,
        public_access_only=False,
        extents=load("lake_extents_sample.json"),
        wave_field=wave_field,
    )


def _feeds(candidates: list, **kw):
    cells = {}
    series = make_feeds("om_gusty.json").airport
    for c in candidates:
        cells[c.cell] = series
    return make_feeds(
        "om_gusty.json",
        taf_name="taf_vfr.json",
        metar=load("metar_kptk.json"),
        alerts=[{"event": "Lake Wind Advisory", "area": "Lake St. Clair", "ends": "2026-09-20T02:00:00Z"}],
        nws_hourly=load("nws_hourly.json")["properties"]["periods"],
        lake_cells=cells,
        **kw,
    )


def _briefing() -> dict:
    settings = Settings()
    candidates = _candidates(settings)
    return build_briefing(
        Settings(),
        _feeds(candidates),
        candidates,
        now_utc=datetime(2026, 9, 19, 18, 0, tzinfo=DETROIT).astimezone(UTC),
        run_kind="outlook",
        run_at="18:00",
    )


def _wave_field():
    """Cass Lake with three regions: a sheltered west end, a middle, and a bay with no run."""
    return make_wave_field(
        {
            CASS_LAKE: [
                wave_point(-83.39, 42.606, label=0, fetch_m=[600.0] * 16, run_ft=[4100.0] * 8, depth_m=3.0),
                wave_point(-83.38, 42.604, label=0, fetch_m=[700.0] * 16, run_ft=[4300.0] * 8, depth_m=3.0),
                wave_point(-83.36, 42.607, label=1, fetch_m=[3800.0] * 16, run_ft=[9000.0] * 8),
                wave_point(-83.35, 42.609, label=1, fetch_m=[4000.0] * 16, run_ft=[9500.0] * 8),
                wave_point(-83.34, 42.612, label=2, fetch_m=[500.0] * 16, run_ft=[900.0] * 8, depth_m=1.0),
            ]
        },
        ["west end", "middle", "Duck Bay"],
    )


def _briefing_with_wave_field() -> dict:
    """The same run with a wave field and Cass Lake as the home water."""
    settings = Settings(home_water=HomeWater(id=CASS_LAKE, name="Cass Lake"))
    field = _wave_field()
    candidates = _candidates(settings, field)
    home_water = lakes_mod.find_candidate(
        load("index_sample.json"),
        CASS_LAKE,
        home_lat=settings.home_airport.lat,
        home_lon=settings.home_airport.lon,
        extents=load("lake_extents_sample.json"),
        wave_field=field,
    )
    feeds = _feeds(
        candidates,
        buoys=[
            {
                "id": "45999", "source": "ndbc", "lat": 42.61, "lon": -83.37,
                "at": datetime(2026, 9, 19, 21, 50, tzinfo=UTC), "dir_deg": 250.0,
                "speed_kt": 11.0, "gust_kt": 15.0, "wave_height_m": 0.3,
            }
        ],
        home_water_metars=[
            {"icaoId": "KPTK", "name": "Oakland County Intl, MI, US", "lat": 42.6655, "lon": -83.4187,
             "obsTime": datetime(2026, 9, 19, 21, 53, tzinfo=UTC).timestamp(),
             "wdir": 250, "wspd": 9, "wgst": 14},
        ],
        marine={
            "latitude": 42.6, "longitude": -83.37,
            "hourly": {"time": [f"2026-09-{d:02d}T{h:02d}:00" for d in (19, 20) for h in range(24)],
                       "wave_height": [0.25] * 48},
        },
    )
    return build_briefing(
        settings,
        feeds,
        candidates,
        now_utc=datetime(2026, 9, 19, 18, 0, tzinfo=DETROIT).astimezone(UTC),
        run_kind="outlook",
        run_at="18:00",
        home_water=home_water,
    )


def test_top_level_keys():
    assert set(_briefing()) == TOP
    assert set(_briefing_with_wave_field()) == TOP


def test_home_water_is_null_when_no_home_water_is_set():
    b = _briefing()
    assert b["home_water"] is None and b["outlook"]["home_water"] is None


def test_the_home_water_block_and_its_regions_match_the_contract():
    b = _briefing_with_wave_field()
    hw = b["home_water"]
    assert set(hw) == HOME_WATER
    assert (hw["id"], hw["name"], hw["kind"]) == (CASS_LAKE, "Cass Lake", "lake")
    assert hw["score"] in LEVELS
    assert set(hw["wind"]) == {"dir", "kt", "gust"}
    # Every region, not the row's top four, and each with its own score.
    assert [r["label"] for r in hw["regions"]] == ["west end", "middle", "Duck Bay"]
    assert all(set(r) == REGION | {"score"} for r in hw["regions"])
    assert all(r["score"] in LEVELS for r in hw["regions"])
    duck = hw["regions"][-1]
    assert duck["hs_in"] is None and duck["run_ft"] is None and duck["score"] == "unfavorable"
    assert isinstance(hw["hs_open_in"], int)
    assert all(set(o) == OBSERVED for o in hw["observed"])
    assert all(o["kind"] in {"buoy", "metar"} for o in hw["observed"])
    assert hw["marine_hs_in"] == 10  # 0.25 m
    # The outlook copy is the same shape without `observed`.
    out = b["outlook"]["home_water"]
    assert set(out) == HOME_WATER - {"observed"}


def test_a_home_water_with_no_forecast_for_it_is_null_with_an_error():
    settings = Settings(home_water=HomeWater(id=CASS_LAKE, name="Cass Lake"))
    candidates = _candidates(settings, _wave_field())
    home_water = lakes_mod.find_candidate(
        load("index_sample.json"), CASS_LAKE, home_lat=settings.home_airport.lat,
        home_lon=settings.home_airport.lon, extents=None, wave_field=_wave_field(),
    )
    feeds = _feeds(candidates)
    feeds.lake_series = {}  # Open-Meteo gave nothing back for the lakes
    b = build_briefing(
        settings, feeds, candidates,
        now_utc=datetime(2026, 9, 19, 18, 0, tzinfo=DETROIT).astimezone(UTC),
        run_kind="manual", home_water=home_water,
    )
    assert b["home_water"] is None
    assert any("home_water" in e and "Cass Lake" in e for e in b["errors"])


def test_a_lake_row_with_a_wave_field_carries_its_regions():
    b = _briefing_with_wave_field()
    cass = next(row for row in b["lakes"] if row["id"] == CASS_LAKE)
    assert cass["region"] == "west end"
    assert cass["hs_in"] < cass["hs_open_in"]
    assert 1 <= len(cass["regions"]) <= 4
    assert all(set(r) == REGION for r in cass["regions"])
    assert [r["label"] for r in cass["regions"]] == ["west end", "middle", "Duck Bay"]
    other = next(row for row in b["lakes"] if row["id"] != CASS_LAKE)
    assert other["region"] is None and other["hs_open_in"] is None and other["regions"] == []


def test_nested_key_sets():
    b = _briefing()
    assert set(b["home_airport"]) == {"id", "name", "lat", "lon"}
    assert set(b["sources"]) == {"metar", "taf", "open_meteo", "nws"}
    assert set(b["links"]) == {"metar", "taf", "forecast"}
    assert b["days"] and all(set(d) == DAY for d in b["days"])
    for day in b["days"]:
        assert day["blocks"] and all(set(blk) == BLOCK for blk in day["blocks"])
        assert all(set(blk["wind"]) == {"dir", "kt", "gust"} for blk in day["blocks"])
    assert set(b["outlook"]) == OUTLOOK
    assert set(b["outlook"]["window"]) == {"start", "end"}
    assert set(b["outlook"]["sun"]) == {"civil_dawn", "sunrise", "sunset", "civil_dusk"}
    assert b["outlook"]["hours"] and all(set(h) == HOUR for h in b["outlook"]["hours"])
    assert b["outlook"]["runs"] and all(set(r) == RUN for r in b["outlook"]["runs"])
    assert b["lakes"] and all(set(row) == LAKE for row in b["lakes"])
    assert b["outlook"]["lakes"] and all(set(row) == LAKE for row in b["outlook"]["lakes"])
    assert all(set(a) == {"event", "area", "ends"} for a in b["alerts"])
    wave = _briefing_with_wave_field()
    assert all(set(row) == LAKE for row in wave["lakes"] + wave["outlook"]["lakes"])


def test_enumerated_values_are_from_the_contract():
    b = _briefing()
    assert b["schema"] == 1
    assert b["run_kind"] in {"scheduled", "outlook", "manual", "startup"}
    assert b["timezone"] == "America/Detroit"
    for day in b["days"]:
        assert day["score"] in LEVELS
        for blk in day["blocks"]:
            assert blk["score"] in LEVELS
            assert blk["limiting"] is None or blk["limiting"] in LIMITING_IDS
    o = b["outlook"]
    assert o["score"] in LEVELS
    assert o["confidence"] in {"high", "medium", "low"}
    assert o["trend"] in {"improving", "steady", "worsening", None}
    assert o["limiting"] is None or o["limiting"] in LIMITING_IDS
    for row in b["lakes"]:
        assert row["score"] in LEVELS
        assert row["verdict"] in {"clear", "conditional"}
        assert row["limiting"] is None or row["limiting"] in LIMITING_IDS
        assert row["kind"] in {"lake", "river", "great_lake", "connecting_water"}
    hw = _briefing_with_wave_field()["home_water"]
    assert hw["limiting"] is None or hw["limiting"] in LIMITING_IDS


def test_every_number_is_pre_rounded_for_display():
    b = _briefing()
    for day in b["days"]:
        for blk in day["blocks"]:
            for key in ("vis_sm", "temp_f", "precip_prob", "xwind_kt"):
                assert blk[key] is None or isinstance(blk[key], int), (key, blk[key])
            for key in ("ceiling_ft", "da_ft"):
                assert blk[key] is None or blk[key] % 100 == 0, (key, blk[key])
            assert all(v is None or isinstance(v, int) for v in blk["wind"].values())
    for row in b["lakes"]:
        assert isinstance(row["hs_in"], int) and isinstance(row["run_ft"], int)
        assert isinstance(row["bearing_deg"], int)
        assert round(row["distance_nm"], 1) == row["distance_nm"]
    wave = _briefing_with_wave_field()
    for row in wave["lakes"]:
        assert row["hs_open_in"] is None or isinstance(row["hs_open_in"], int)
        for region in row["regions"]:
            assert region["hs_in"] is None or isinstance(region["hs_in"], int)
            assert region["run_ft"] is None or isinstance(region["run_ft"], int)
    hw = wave["home_water"]
    assert hw["marine_hs_in"] is None or isinstance(hw["marine_hs_in"], int)
    for obs in hw["observed"]:
        assert obs["wave_ft"] is None or round(obs["wave_ft"], 1) == obs["wave_ft"]
        assert all(v is None or isinstance(v, int) for v in obs["wind"].values())
        assert round(obs["distance_nm"], 1) == obs["distance_nm"]


def test_times_are_utc_z_or_local_hhmm():
    b = _briefing()
    assert b["generated_at"].endswith("Z")
    datetime.fromisoformat(b["generated_at"])
    for day in b["days"]:
        for blk in day["blocks"]:
            assert len(blk["start"]) == 5 and blk["start"][2] == ":"
    o = b["outlook"]
    assert len(o["window"]["start"]) == 5
    assert all(len(h["time"]) == 5 for h in o["hours"])
    assert all(r["generated_at"].endswith("Z") for r in o["runs"])


def test_the_whole_thing_is_json_serialisable():
    for briefing in (_briefing(), _briefing_with_wave_field()):
        payload = json.dumps(briefing)
        assert "NaN" not in payload and "Infinity" not in payload


def test_the_product_never_says_legal_safe_or_go():
    for b in (_briefing(), _briefing_with_wave_field()):
        text = " ".join([b["summary"], b["outlook"]["summary"]]).lower()
        for word in ("legal", "safe", "go/no-go", "no-go"):
            assert word not in text
