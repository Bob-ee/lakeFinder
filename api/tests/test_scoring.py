"""The design 3.2 factor table, and block scoring on the five day-type fixtures (design 7)."""
from __future__ import annotations

from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

import pytest

from seaplane_api.briefing import blocks as blocks_mod
from seaplane_api.briefing import taf as taf_mod
from seaplane_api.briefing.generate import build_briefing
from seaplane_api.briefing.scoring import Conditions, alert_level, score_conditions
from seaplane_api.briefing.series import conditions_from_model
from seaplane_api.settings import Limits, Settings

from .conftest import DETROIT, make_feeds

LIMITS = Limits()
RUNWAYS = [{"id": "09R/27L", "heading": 91}, {"id": "18/36", "heading": 179}]


def cond(**kw) -> Conditions:
    base = {
        "wind_dir_deg": 250, "wind_kt": 6, "gust_kt": 8, "ceiling_ft": None, "ceiling_known": True, "vis_sm": 10,
        "temp_f": 65, "dewpoint_f": 45, "precip_mm": 0.0, "precip_prob": 0, "weather_code": 0, "cape": 0.0,
        "da_ft": 1200, "alerts": [], "daylight_fraction": 1.0, "xwind_kt": 2, "xwind_gust_kt": 3,
    }
    base.update(kw)
    return Conditions(**base)


# --- individual factors -------------------------------------------------------------------

def test_calm_conditions_are_favorable_with_no_limiting_factor():
    s = score_conditions(cond(), LIMITS)
    assert s.level == "favorable"
    assert s.limiting is None


@pytest.mark.parametrize(
    ("kw", "level", "limiting"),
    [
        ({"wind_kt": 15}, "marginal", "wind"),
        ({"wind_kt": 25, "gust_kt": 25}, "unfavorable", "wind"),
        ({"wind_kt": 8, "gust_kt": 18}, "marginal", "gusts"),
        ({"wind_kt": 8, "gust_kt": 25}, "unfavorable", "gusts"),
        ({"xwind_kt": 10, "xwind_gust_kt": 11}, "marginal", "xwind_runway"),
        ({"xwind_kt": 6, "xwind_gust_kt": 15}, "unfavorable", "xwind_runway"),
        ({"ceiling_ft": 2000}, "marginal", "ceiling"),
        ({"ceiling_ft": 900}, "unfavorable", "ceiling"),
        ({"vis_sm": 4}, "marginal", "visibility"),
        ({"vis_sm": 2, "temp_f": 55, "dewpoint_f": 54}, "unfavorable", "visibility"),
        ({"precip_mm": 0.4, "precip_prob": 50}, "marginal", "precip"),
        ({"precip_mm": 4.0, "precip_prob": 80}, "unfavorable", "precip"),
        ({"cape": 700}, "marginal", "convection"),
        ({"cape": 2500}, "unfavorable", "convection"),
        ({"weather_code": 95}, "unfavorable", "convection"),
        ({"da_ft": 4200}, "marginal", "density_altitude"),
        ({"da_ft": 6000}, "unfavorable", "density_altitude"),
        ({"temp_f": 35, "dewpoint_f": 20}, "marginal", "temperature"),
        ({"temp_f": 25, "dewpoint_f": 10}, "unfavorable", "temperature"),
        ({"alerts": ["Wind Advisory"]}, "marginal", "alert"),
        ({"alerts": ["Lake Wind Advisory"]}, "unfavorable", "alert"),
        ({"daylight_fraction": 0.5}, "marginal", "daylight"),
        ({"daylight_fraction": 0.0}, "unfavorable", "daylight"),
    ],
)
def test_each_factor_bands_and_names_itself(kw, level, limiting):
    s = score_conditions(cond(**kw), LIMITS)
    assert (s.level, s.limiting) == (level, limiting)


def test_fog_factor():
    # Tight spread plus light wind is a fog risk: marginal, and flagged.
    s = score_conditions(cond(temp_f=52, dewpoint_f=50, wind_kt=3), LIMITS)
    assert s.level == "marginal" and s.limiting == "fog" and s.fog_risk is True
    # The same spread with wind is not.
    s = score_conditions(cond(temp_f=52, dewpoint_f=50, wind_kt=10), LIMITS)
    assert s.fog_risk is False and s.level == "favorable"
    # A TAF carrying FG is unfavorable outright.
    s = score_conditions(cond(taf_fog=True, wind_kt=10), LIMITS)
    assert s.level == "unfavorable" and s.limiting == "fog"
    # Model visibility below the minimum also trips fog, but visibility names itself first.
    s = score_conditions(cond(vis_sm=1), LIMITS)
    assert s.level == "unfavorable" and s.limiting == "visibility"
    assert s.factors["fog"] == "unfavorable"


def test_unknown_inputs_are_skipped_not_assumed():
    s = score_conditions(
        Conditions(wind_kt=5, gust_kt=6, ceiling_known=False, vis_sm=None, temp_f=None, dewpoint_f=None),
        LIMITS,
    )
    assert s.level == "favorable"
    assert {"ceiling", "visibility", "temperature", "fog"} <= set(s.skipped)
    assert "ceiling" not in s.factors


def test_reported_ceiling_of_none_means_no_ceiling():
    s = score_conditions(cond(ceiling_ft=None, ceiling_known=True), LIMITS)
    assert s.factors["ceiling"] == "favorable"


def test_worst_factor_wins_and_the_first_one_at_that_level_is_named():
    s = score_conditions(cond(wind_kt=15, vis_sm=4), LIMITS)
    assert s.level == "marginal"
    assert s.limiting == "wind"  # wind comes before visibility in the design's table order


def test_alert_level_classification():
    assert alert_level([]) == "favorable"
    assert alert_level(["Wind Advisory"]) == "marginal"
    assert alert_level(["Special Weather Statement"]) == "marginal"
    assert alert_level(["Small Craft Advisory"]) == "unfavorable"
    assert alert_level(["Severe Thunderstorm Warning"]) == "unfavorable"
    assert alert_level(["Winter Weather Advisory"]) == "unfavorable"
    assert alert_level(["Wind Advisory", "Lake Wind Advisory"]) == "unfavorable"


# --- day types, end to end on the recorded fixtures ---------------------------------------

def _blocks_for(om: str, taf: str | None, now_local: datetime, settings: Settings):
    feeds = make_feeds(om, taf_name=taf)
    briefing = build_briefing(settings, feeds, [], now_utc=now_local.astimezone(UTC), run_kind="manual")
    return briefing


def test_calm_day_is_favorable_all_day():
    now = datetime(2026, 9, 19, 9, 0, tzinfo=DETROIT)
    b = _blocks_for("om_calm.json", "taf_vfr.json", now, Settings())
    today = b["days"][0]
    assert today["score"] == "favorable"
    assert today["best_window"] == ["09:00", "18:00"]
    # Only the block that runs past civil dusk is anything other than favorable.
    assert [blk["limiting"] for blk in today["blocks"]] == [None, None, None, "daylight"]
    assert b["summary"].startswith("Favorable 09:00–18:00 today.")


def test_gusty_day_is_limited_by_gusts():
    now = datetime(2026, 9, 19, 9, 0, tzinfo=DETROIT)
    b = _blocks_for("om_gusty.json", "taf_vfr.json", now, Settings())
    blk = b["days"][0]["blocks"][0]
    assert blk["wind"] == {"dir": 180, "kt": 11, "gust": 26}
    # 11 kt sustained is inside wind_ok; the 15 kt spread is what fails.
    assert blk["score"] == "unfavorable"
    assert blk["limiting"] == "gusts"


def test_ifr_day_is_limited_by_the_ceiling():
    now = datetime(2026, 9, 19, 9, 0, tzinfo=DETROIT)
    b = _blocks_for("om_ifr.json", "taf_ifr.json", now, Settings())
    rows = b["days"][0]["blocks"]
    # The 09:00 block centres on 10:30 EDT, inside the PROB30 group's OVC002 / 1/2 SM.
    assert rows[0]["ceiling_ft"] == 200
    # Later blocks fall back to the prevailing OVC004 / 1 SM.
    assert rows[1]["ceiling_ft"] == 400 and rows[1]["vis_sm"] == 1
    assert all(r["score"] == "unfavorable" for r in rows)
    assert rows[0]["limiting"] == "ceiling"
    assert b["days"][0]["best_window"] is None or b["days"][0]["score"] == "unfavorable"


def test_convective_afternoon_is_favorable_in_the_morning_only():
    now = datetime(2026, 7, 15, 7, 0, tzinfo=DETROIT)
    b = _blocks_for("om_convective.json", None, now, Settings())
    rows = b["days"][0]["blocks"]
    morning = [r for r in rows if r["start"] < "13:00"]
    afternoon = [r for r in rows if r["start"] >= "13:00"]
    assert morning and all(r["score"] == "favorable" for r in morning)
    assert afternoon and all(r["score"] == "unfavorable" for r in afternoon)
    assert afternoon[0]["limiting"] == "convection"
    assert b["days"][0]["best_window"] is not None


def test_january_day_trips_the_ice_gate_and_the_temperature_factor():
    now = datetime(2026, 1, 15, 10, 0, tzinfo=DETROIT)
    settings = Settings()
    feeds = make_feeds("om_january.json")
    b = build_briefing(settings, feeds, [], now_utc=now.astimezone(UTC), run_kind="manual")
    blk = b["days"][0]["blocks"][0]
    assert blk["temp_f"] == 24
    assert blk["score"] == "unfavorable"
    assert blk["limiting"] == "temperature"
    # 15 January is inside the default 12-01 to 04-01 ice season.
    from seaplane_api.briefing.generate import _ice_gate

    assert _ice_gate(None, now.date(), settings.limits)[0] is True
    assert _ice_gate(None, date(2026, 7, 4), settings.limits)[0] is False


def test_metar_overrides_the_first_block_when_it_is_fresh():
    tz = ZoneInfo("America/Detroit")
    now = datetime(2026, 9, 19, 9, 30, tzinfo=tz)
    metar = {
        "obsTime": int(now.timestamp()) - 600,
        "temp": 10.0, "dewp": 9.5, "wdir": 90, "wspd": 12, "wgst": 22,
        "visib": 2, "altim": 1005.0, "clouds": [{"cover": "OVC", "base": 700}], "wxString": "BR",
    }
    feeds = make_feeds("om_calm.json", taf_name="taf_vfr.json", metar=metar)
    b = build_briefing(Settings(), feeds, [], now_utc=now.astimezone(UTC), run_kind="manual")
    first, second = b["days"][0]["blocks"][0], b["days"][0]["blocks"][1]
    assert first["ceiling_ft"] == 700 and first["vis_sm"] == 2
    assert first["wind"]["kt"] == 12
    assert second["wind"]["kt"] == 4  # the model again, untouched


def test_block_spans_only_cover_daylight():
    from seaplane_api.briefing.sun import local_sun_times

    tz = DETROIT
    day = datetime(2026, 9, 19, 4, 0, tzinfo=tz)
    suns = {day.date(): local_sun_times(day.date(), 42.6655, -83.4187, tz)}
    spans = blocks_mod.block_spans(day, suns, day.replace(hour=23))
    assert spans[0][0].hour >= 4
    assert all(s[0] < suns[day.date()].civil_dusk for s in spans)


def test_conditions_from_model_prefers_the_taf_for_ceiling():
    from .conftest import load

    taf = load("taf_ifr.json")
    hour = datetime.fromtimestamp(taf["validTimeFrom"] + 3600, UTC)
    c = conditions_from_model(
        {"wind_speed_10m": 8, "wind_direction_10m": 90, "temperature_2m": 55, "dew_point_2m": 54,
         "visibility": 24000.0, "pressure_msl": 1013.0},
        field_elev_ft=981,
        taf_hour=taf_mod.taf_for_hour(taf, hour),
        alerts=[],
        daylight=1.0,
        runways=RUNWAYS,
    )
    assert c.ceiling_known is True and c.ceiling_ft == 400
    assert c.vis_sm == 1.0  # the TAF's 1 SM beats the model's 15 SM


def test_the_best_window_never_promises_flying_time_after_civil_dusk():
    """The last block of a September day runs to 21:00; civil dusk is 20:04, so the window stops."""
    now = datetime(2026, 9, 19, 12, 0, tzinfo=DETROIT)
    b = _blocks_for("om_ifr.json", "taf_ifr.json", now, Settings())
    today = b["days"][0]
    assert today["blocks"][-1]["end"] == "21:00"
    assert today["best_window"] == ["12:00", "20:04"]
    assert b["summary"].startswith("Unfavorable 12:00–20:04 today.")


def test_sentence_case_does_not_lower_the_gust_letter():
    from seaplane_api.briefing.render import sentence_case, wind_phrase

    phrase = wind_phrase({"dir": 250, "kt": 9, "gust": 14})
    assert sentence_case(phrase) == "Wind 250/9 G14"
    assert phrase.capitalize() == "Wind 250/9 g14"  # which is why capitalize() is not used


def test_blocks_carry_ceiling_known():
    """`ceiling_ft: null` + `ceiling_known: true` is "no ceiling"; false is "nobody told us"."""
    now = datetime(2026, 9, 19, 9, 0, tzinfo=DETROIT)
    covered = _blocks_for("om_calm.json", "taf_vfr.json", now, Settings())
    first = covered["days"][0]["blocks"][0]
    assert first["ceiling_known"] is True and first["ceiling_ft"] is None
    assert "no ceiling" in covered["summary"]

    uncovered = _blocks_for("om_calm.json", None, now, Settings())
    blk = uncovered["days"][0]["blocks"][0]
    assert blk["ceiling_known"] is False and blk["ceiling_ft"] is None
    assert "ceiling unknown" in uncovered["summary"]
    # ...and the factor is not evaluated either way round.
    assert blk["limiting"] != "ceiling"


def test_block_visibility_is_capped_at_ten_for_display():
    now = datetime(2026, 9, 19, 9, 0, tzinfo=DETROIT)
    b = _blocks_for("om_calm.json", None, now, Settings())
    # The fixture's 24,000 m is about 15 SM.
    assert all(blk["vis_sm"] == 10 for blk in b["days"][0]["blocks"])


def test_display_vis_helper():
    from seaplane_api.briefing.series import display_vis

    assert display_vis(None) is None
    assert display_vis(14.9) == 10
    assert display_vis(10.0) == 10
    assert display_vis(2.4) == 2
    assert display_vis(0.4) == 0


def test_lake_rows_no_longer_repeat_the_airport_limiting_factor():
    """The real 09:38 run had eight rows all reading "marginal / ceiling"."""
    from seaplane_api.briefing import lakes as lakes_mod

    cand = lakes_mod.Candidate(
        id=1, name="Somewhere", lat=42.66, lon=-83.42, verdict="clear", chord_ft=4000,
        chord_bearing_deg=0, distance_nm=3.0, bearing_deg=90, extents_ft=tuple([4000] * 16),
    )
    feeds = make_feeds("om_ifr.json", taf_name="taf_ifr.json", lake_cells={cand.cell: None})
    feeds.lake_series[cand.cell] = feeds.airport
    now = datetime(2026, 9, 19, 9, 0, tzinfo=DETROIT)
    b = build_briefing(Settings(), feeds, [cand], now_utc=now.astimezone(UTC), run_kind="manual")
    assert b["days"][0]["blocks"][0]["limiting"] == "ceiling"   # the header still says so
    assert b["lakes"][0]["score"] == "favorable"                 # the water is fine
    assert b["lakes"][0]["limiting"] is None
