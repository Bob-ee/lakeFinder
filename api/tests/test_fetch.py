"""The fetch layer's parsing and mapping, on the recorded responses. No sockets.

Anything that talks to the network is exercised through a stubbed `httpx` transport, so these tests
check the shapes the live endpoints actually return rather than an invented schema.
"""
from __future__ import annotations

import httpx
import pytest

from seaplane_api.fetch import aviationweather, ndbc, nws, openmeteo
from seaplane_api.fetch.http import USER_AGENT, get_json

from .conftest import load


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        headers={"User-Agent": USER_AGENT},
        timeout=1.0,
    )


# --- aviationweather ----------------------------------------------------------------------

def test_airport_info_maps_onto_the_home_airport_shape():
    got = aviationweather.to_home_airport(load("airport_kptk.json"))
    assert got["id"] == "KPTK"
    assert got["elev_ft"] == 981  # `elev` is 299 metres on the wire, not feet
    assert got["runways"] == [
        {"id": "09L/27R", "heading": 88, "length_ft": 5676},
        {"id": "09R/27L", "heading": 88, "length_ft": 6521},
        {"id": "18/36", "heading": 172, "length_ft": 2582},
    ]


@pytest.mark.parametrize(
    "dimension, expected",
    [
        ("6521x150", 6521),
        ("2582x75", 2582),
        ("100x6521", 100),  # length is always the first number, whatever it means on odd data
        ("6521", None),  # no "x": can't tell length from width
        ("", None),
        (None, None),
        ("Nx150", None),
        (150, None),  # not a string at all
    ],
)
def test_dimension_parsing_is_tolerant_of_odd_or_missing_values(dimension, expected):
    assert aviationweather._parse_length_ft(dimension) == expected


def test_runway_alignment_is_true_not_magnetic():
    """KPTK's variation is 07W, so a magnetic 18/36 would read 179, not 172."""
    raw = load("airport_kptk.json")
    assert raw["magdec"] == "07W"
    rwy = next(r for r in raw["runways"] if r["id"] == "18/36")
    assert rwy["alignment"] == 172
    assert aviationweather.to_home_airport(raw)["runways"][-1]["heading"] == 172


def test_pick_station_takes_the_newest_report_for_the_requested_field():
    reports = [
        {"icaoId": "KDET", "obsTime": 100},
        {"icaoId": "KPTK", "obsTime": 100},
        {"icaoId": "KPTK", "obsTime": 200},
    ]
    assert aviationweather.pick_station(reports, "KPTK")["obsTime"] == 200
    assert aviationweather.pick_station([], "KPTK") is None
    assert aviationweather.pick_station(None, "KPTK") is None


def test_metar_fixture_has_the_fields_the_briefing_reads():
    m = load("metar_kptk.json")
    assert {"obsTime", "temp", "dewp", "wdir", "wspd", "visib", "altim", "clouds"} <= set(m)
    assert 900 < m["altim"] < 1100, "altim is hectopascals on this feed, not inHg"


@pytest.mark.anyio
async def test_fetch_metar_degrades_to_an_error_string():
    async def boom(_request):
        raise httpx.ConnectError("nope")

    async with _client(boom) as c:
        data, err = await aviationweather.fetch_metar(c, "KPTK")
    assert data is None and err == "metar: ConnectError"


@pytest.mark.anyio
async def test_fetch_airport_404s_into_an_error():
    async def missing(_request):
        return httpx.Response(200, json=[])

    async with _client(missing) as c:
        data, err = await aviationweather.fetch_airport(c, "ZZZZ")
    assert data is None and "ZZZZ" in err


@pytest.mark.anyio
async def test_the_bbox_metar_query_sends_min_lat_min_lon_max_lat_max_lon():
    """Verified live: `bbox=42.2,-83.2,42.8,-82.2` returns KMTC, KDET, KVLL and CYQG."""
    seen = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        seen["bbox"] = request.url.params["bbox"]
        return httpx.Response(200, json=[{"icaoId": "KMTC", "lat": 42.6045, "lon": -82.8353}])

    async with _client(handler) as c:
        rows, err = await aviationweather.fetch_metar_bbox(c, (42.2, -83.2, 42.8, -82.2))
    assert err is None and rows[0]["icaoId"] == "KMTC"
    assert seen["bbox"] == "42.200,-83.200,42.800,-82.200"


@pytest.mark.anyio
async def test_a_bbox_metar_failure_is_an_error_string_not_an_exception():
    async def boom(_request):
        raise httpx.ConnectError("nope")

    async with _client(boom) as c:
        rows, err = await aviationweather.fetch_metar_bbox(c, (42.0, -83.0, 43.0, -82.0))
    assert rows is None and err == "metar_bbox: ConnectError"


# --- Open-Meteo ----------------------------------------------------------------------------

@pytest.mark.anyio
async def test_points_are_batched_fifty_at_a_time():
    calls: list[int] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        n = len(request.url.params["latitude"].split(","))
        calls.append(n)
        return httpx.Response(200, json=[{"hourly": {"time": []}} for _ in range(n)])

    points = [(42.0 + i * 0.1, -83.0) for i in range(120)]
    async with _client(handler) as c:
        out, errors = await openmeteo.fetch_points(c, points, timezone="America/Detroit")
    assert calls == [50, 50, 20]
    assert len(out) == 120 and errors == []


@pytest.mark.anyio
async def test_a_failed_batch_does_not_lose_the_others():
    seen = {"n": 0}

    async def handler(request: httpx.Request) -> httpx.Response:
        seen["n"] += 1
        if seen["n"] <= 2:  # the first batch fails its attempt and its one retry
            return httpx.Response(500)
        n = len(request.url.params["latitude"].split(","))
        return httpx.Response(200, json=[{"hourly": {"time": []}} for _ in range(n)])

    points = [(42.0 + i * 0.1, -83.0) for i in range(60)]
    async with _client(handler) as c:
        out, errors = await openmeteo.fetch_points(c, points, timezone="America/Detroit")
    assert len(out) == 60
    assert out[0] == {} and out[50] != {}
    assert errors == ["open_meteo[0]: HTTP 500"]


@pytest.mark.anyio
async def test_a_single_point_response_is_normalised_to_a_list():
    async def handler(_request):
        return httpx.Response(200, json={"hourly": {"time": ["2026-09-19T00:00"]}})

    async with _client(handler) as c:
        out, errors = await openmeteo.fetch_points(c, [(42.0, -83.0)], timezone="America/Detroit")
    assert len(out) == 1 and errors == []


@pytest.mark.anyio
async def test_the_marine_request_asks_one_point_for_wave_height():
    seen = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.update(dict(request.url.params))
        assert "marine-api" in str(request.url)
        return httpx.Response(200, json={"latitude": 42.46, "longitude": -82.71,
                                         "hourly": {"time": ["2026-09-19T12:00"], "wave_height": [0.3]}})

    async with _client(handler) as c:
        payload, err = await openmeteo.fetch_marine(c, 42.45, -82.70, timezone="America/Detroit")
    assert err is None and payload["hourly"]["wave_height"] == [0.3]
    assert seen["hourly"] == "wave_height" and seen["timezone"] == "America/Detroit"
    assert (seen["latitude"], seen["longitude"]) == ("42.4500", "-82.7000")


@pytest.mark.anyio
async def test_a_marine_failure_degrades_to_an_error_string():
    async def down(_request):
        return httpx.Response(503)

    async with _client(down) as c:
        payload, err = await openmeteo.fetch_marine(c, 42.45, -82.70, timezone="America/Detroit")
    assert payload is None and err == "open_meteo_marine: HTTP 503"


def test_the_request_asks_for_pressure_msl():
    """Density altitude needs an altimeter setting; `surface_pressure` is station pressure."""
    assert "pressure_msl" in openmeteo.HOURLY_VARS


def test_the_recorded_open_meteo_response_has_the_units_the_code_assumes():
    """Guards the unit assumptions in `briefing/series.py` against a quiet upstream change."""
    payload = load("open_meteo_kptk.json")
    units = payload["hourly_units"]
    assert units["wind_speed_10m"] == "kn" and units["wind_gusts_10m"] == "kn"
    assert units["temperature_2m"] == "\u00b0F" and units["dew_point_2m"] == "\u00b0F"
    assert units["visibility"] == "m"           # metres, not statute miles
    assert units["pressure_msl"] == "hPa"
    assert units["precipitation"] == "mm" and units["cape"] == "J/kg"
    assert payload["timezone"] == "America/Detroit"
    assert payload["hourly"]["time"][0].count(":") == 1  # naive local ISO, no offset
    assert "temperature_2m_max" in payload["daily"]      # the ice gate's lookback


def test_the_recorded_points_response_carries_the_hourly_link():
    props = load("nws_points.json")["properties"]
    assert props["forecastHourly"].startswith("https://api.weather.gov/gridpoints/")
    assert props["timeZone"] == "America/Detroit"


def test_a_quiet_day_has_no_alerts():
    payload = load("nws_alerts.json")
    assert payload["features"] == []


# --- NWS -----------------------------------------------------------------------------------

def test_nws_wind_strings_are_parsed_to_knots():
    assert nws.wind_kt({"windSpeed": "7 mph"}) == pytest.approx(7 * 0.868976)
    assert nws.wind_kt({"windSpeed": "5 to 10 mph"}) == pytest.approx(10 * 0.868976)
    assert nws.wind_kt({"windSpeed": None}) is None
    assert nws.wind_dir_deg({"windDirection": "E"}) == 90
    assert nws.wind_dir_deg({"windDirection": "WNW"}) == 292.5
    assert nws.wind_dir_deg({"windDirection": ""}) is None


def test_peak_wind_over_a_window_uses_the_recorded_periods():
    periods = load("nws_hourly.json")["properties"]["periods"]
    start = periods[0]["startTime"]
    end = periods[5]["startTime"]
    peak = nws.peak_wind_kt(periods, start, end)
    expected = max(nws.wind_kt(p) for p in periods[:5])
    assert peak == pytest.approx(expected)
    assert nws.peak_wind_kt(None, start, end) is None


def test_the_recorded_hourly_forecast_has_no_sky_cover():
    """Why the briefing has no model-derived ceiling: `forecastHourly` simply does not carry one."""
    periods = load("nws_hourly.json")["properties"]["periods"]
    assert "skyCover" not in periods[0]


@pytest.mark.anyio
async def test_alerts_are_flattened_to_the_contract_shape():
    payload = {
        "features": [
            {"properties": {"event": "Lake Wind Advisory", "areaDesc": "Lake St. Clair", "ends": "2026-09-20T02:00:00Z"}}
        ]
    }

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["User-Agent"] == "lakeFinder (https://github.com/Bob-ee/lakeFinder)"
        return httpx.Response(200, json=payload)

    async with _client(handler) as c:
        alerts, err = await nws.fetch_alerts(c, 42.6655, -83.4187)
    assert err is None
    assert alerts == [{"event": "Lake Wind Advisory", "area": "Lake St. Clair", "ends": "2026-09-20T02:00:00Z"}]


@pytest.mark.anyio
async def test_hourly_follows_the_points_link():
    async def handler(request: httpx.Request) -> httpx.Response:
        if "/points/" in str(request.url):
            return httpx.Response(200, json={"properties": {"forecastHourly": "https://api.weather.gov/h"}})
        return httpx.Response(200, json={"properties": {"periods": [{"number": 1}]}})

    async with _client(handler) as c:
        periods, err = await nws.fetch_hourly(c, 42.6655, -83.4187)
    assert err is None and periods == [{"number": 1}]


# --- NDBC ----------------------------------------------------------------------------------

def test_ndbc_rows_are_filtered_by_bbox_and_converted_to_knots():
    rows = ndbc.parse_latest_obs(load("ndbc_latest_obs.txt"), (-88.0, 41.0, -82.0, 46.5))
    assert rows
    assert all(-88.0 <= r["lon"] <= -82.0 and 41.0 <= r["lat"] <= 46.5 for r in rows)
    with_wind = [r for r in rows if r["speed_kt"] is not None]
    assert with_wind and all(0 <= r["speed_kt"] < 100 for r in with_wind)
    assert all(
        set(r) == {"id", "source", "lat", "lon", "at", "dir_deg", "speed_kt", "gust_kt", "wave_height_m"}
        for r in rows
    )
    # `at` is built from the file's own UTC date columns: the home water's `observed` list drops
    # anything older than 90 minutes, and it has no other timestamp to do that with.
    assert all(r["at"] is None or r["at"].tzinfo is not None for r in rows)
    buoy = next(r for r in rows if r["id"] == "45147")
    assert buoy["at"].isoformat() == "2026-09-19T13:00:00+00:00"


def test_ndbc_missing_values_become_none():
    text = (
        "#STN LAT LON YYYY MM DD hh mm WDIR WSPD GST WVHT DPD APD MWD PRES PTDY ATMP WTMP DEWP VIS TIDE\n"
        "#text deg deg yr mo day hr mn degT m/s m/s m sec sec degT hPa hPa degC degC degC nmi ft\n"
        "45007 42.674 -87.026 2026 09 19 12 00 MM MM MM MM MM MM MM 1015.0 MM 18.0 19.0 MM MM MM\n"
    )
    rows = ndbc.parse_latest_obs(text, (-90.0, 40.0, -80.0, 48.0))
    assert rows[0]["speed_kt"] is None and rows[0]["dir_deg"] is None


# --- http wrapper ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_a_five_hundred_is_retried_once_and_a_404_is_not():
    attempts = {"n": 0}

    async def flaky(_request):
        attempts["n"] += 1
        return httpx.Response(500)

    async with _client(flaky) as c:
        data, err = await get_json(c, "https://example.test/x", label="x")
    assert attempts["n"] == 2 and data is None and err == "x: HTTP 500"

    attempts["n"] = 0

    async def missing(_request):
        attempts["n"] += 1
        return httpx.Response(404)

    async with _client(missing) as c:
        _, err = await get_json(c, "https://example.test/x", label="x")
    assert attempts["n"] == 1 and err == "x: HTTP 404"


@pytest.mark.anyio
async def test_bad_json_is_an_error_not_an_exception():
    async def garbage(_request):
        return httpx.Response(200, content=b"<html>")

    async with _client(garbage) as c:
        data, err = await get_json(c, "https://example.test/x", label="x")
    assert data is None and err == "x: bad JSON"


def test_no_email_address_appears_anywhere_in_the_package():
    """The brief is explicit: nothing identifying beyond the project URL leaves this service."""
    import pathlib
    import re

    pkg = pathlib.Path(__file__).resolve().parents[1] / "seaplane_api"
    pattern = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
    for path in pkg.rglob("*.py"):
        assert not pattern.search(path.read_text()), path


@pytest.fixture
def anyio_backend():
    return "asyncio"
