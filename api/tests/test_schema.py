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
from seaplane_api.settings import Settings

from .conftest import DETROIT, load, make_feeds

TOP = {
    "schema", "generated_at", "run_kind", "timezone", "home_airport", "summary", "days",
    "outlook", "alerts", "lakes", "sources", "links", "errors",
}
DAY = {"date", "score", "best_window", "blocks"}
BLOCK = {
    "start", "end", "score", "limiting", "wind", "xwind_kt", "runway",
    "ceiling_ft", "ceiling_known", "vis_sm", "da_ft", "temp_f", "precip_prob",
}
OUTLOOK = {
    "target_date", "window", "sun", "score", "limiting", "watch", "best_window", "hours",
    "lakes", "confidence", "confidence_reasons", "trend", "runs", "summary",
}
HOUR = {
    "time", "score", "limiting", "wind", "xwind_kt", "runway", "ceiling_ft", "ceiling_known",
    "vis_sm", "temp_f", "dewpoint_f", "fog_risk", "precip_prob", "da_ft",
}
LAKE = {
    "id", "name", "score", "limiting", "hs_in", "run_ft", "wind",
    "distance_nm", "bearing_deg", "verdict", "frozen",
}
RUN = {"at", "generated_at", "score", "best_window", "limiting", "max_gust_kt"}
LIMITING_IDS = {
    "wind", "gusts", "xwind_runway", "ceiling", "visibility", "fog", "precip", "convection",
    "density_altitude", "temperature", "alert", "daylight", "waves", "run", "xwind_water", "ice",
}


def _briefing() -> dict:
    settings = Settings()
    candidates = lakes_mod.select_candidates(
        load("index_sample.json"),
        home_lat=settings.home_airport.lat,
        home_lon=settings.home_airport.lon,
        radius_nm=settings.radius_nm,
        min_run_ft=settings.limits.min_run_ft,
        public_access_only=False,
        extents=load("lake_extents_sample.json"),
    )
    cells = {}
    series = make_feeds("om_gusty.json").airport
    for c in candidates:
        cells[c.cell] = series
    feeds = make_feeds(
        "om_gusty.json",
        taf_name="taf_vfr.json",
        metar=load("metar_kptk.json"),
        alerts=[{"event": "Lake Wind Advisory", "area": "Lake St. Clair", "ends": "2026-09-20T02:00:00Z"}],
        nws_hourly=load("nws_hourly.json")["properties"]["periods"],
        lake_cells=cells,
    )
    return build_briefing(
        Settings(),
        feeds,
        candidates,
        now_utc=datetime(2026, 9, 19, 18, 0, tzinfo=DETROIT).astimezone(UTC),
        run_kind="outlook",
        run_at="18:00",
    )


def test_top_level_keys():
    assert set(_briefing()) == TOP


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
    payload = json.dumps(_briefing())
    assert "NaN" not in payload and "Infinity" not in payload


def test_the_product_never_says_legal_safe_or_go():
    b = _briefing()
    text = " ".join([b["summary"], b["outlook"]["summary"]]).lower()
    for word in ("legal", "safe", "go/no-go", "no-go"):
        assert word not in text
