"""Wind components, runway selection, density altitude, and great-circle geometry.

All bearings are degrees TRUE. METAR wind direction is already true, Open-Meteo's is true, and
aviationweather.gov's runway `alignment` is true as well (verified against KSEA 16L/34R = 180 with
16E variation and KBOS 04L/22R = 20 with 15W), so nothing here converts magnetic to true.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

EARTH_R_NM = 3440.065
HPA_TO_INHG = 1.0 / 33.8638866667


def wind_components(wind_dir_deg: float, wind_kt: float, runway_heading_deg: float) -> tuple[float, float]:
    """`(crosswind, headwind)` in knots. Crosswind is signed; positive means from the right."""
    angle = math.radians(wind_dir_deg - runway_heading_deg)
    return wind_kt * math.sin(angle), wind_kt * math.cos(angle)


def crosswind_kt(wind_dir_deg: float, wind_kt: float, heading_deg: float) -> float:
    """Absolute crosswind component in knots."""
    return abs(wind_components(wind_dir_deg, wind_kt, heading_deg)[0])


@dataclass(frozen=True)
class RunwayEnd:
    id: str  # "27L"
    heading: int  # degrees true


def runway_ends(runways: list[dict]) -> list[RunwayEnd]:
    """Expand `[{"id": "09R/27L", "heading": 91}]` into both landable ends.

    `heading` is the true heading of the *first-named* end; the reciprocal gets the other id.
    A single-ended id ("18") still yields both directions, the second one named "<id> rev" so the
    template never prints a bare heading.
    """
    ends: list[RunwayEnd] = []
    for rwy in runways:
        ident = str(rwy.get("id", "")).strip()
        heading = round(float(rwy.get("heading", 0)))
        parts = [p.strip() for p in ident.split("/") if p.strip()]
        first = parts[0] if parts else f"{heading:03d}"
        second = parts[1] if len(parts) > 1 else f"{first} rev"
        ends.append(RunwayEnd(first, heading % 360))
        ends.append(RunwayEnd(second, (heading + 180) % 360))
    return ends


def best_runway(runways: list[dict], wind_dir_deg: float, wind_kt: float) -> RunwayEnd | None:
    """The end with the least crosswind, breaking ties toward the one with a headwind.

    With no wind information every end is equal, so the first one is returned rather than `None`.
    """
    ends = runway_ends(runways)
    if not ends:
        return None
    if wind_kt <= 0:
        return ends[0]

    def key(end: RunwayEnd) -> tuple[float, float]:
        xw, hw = wind_components(wind_dir_deg, wind_kt, end.heading)
        return (round(abs(xw), 3), -hw)

    return min(ends, key=key)


def pressure_altitude_ft(field_elev_ft: float, altimeter_inhg: float) -> float:
    """`PA = field_elev + (29.92 - altimeter) * 1000` (design 3.2)."""
    return field_elev_ft + (29.92 - altimeter_inhg) * 1000.0


def density_altitude_ft(field_elev_ft: float, altimeter_inhg: float, oat_c: float) -> float:
    """`DA = PA + 120 * (OAT_C - ISA_C)` with `ISA_C = 15 - 2 * PA / 1000` (design 3.2)."""
    pa = pressure_altitude_ft(field_elev_ft, altimeter_inhg)
    isa_c = 15.0 - 2.0 * pa / 1000.0
    return pa + 120.0 * (oat_c - isa_c)


def f_to_c(f: float) -> float:
    return (f - 32.0) * 5.0 / 9.0


def c_to_f(c: float) -> float:
    return c * 9.0 / 5.0 + 32.0


def hpa_to_inhg(hpa: float) -> float:
    return hpa * HPA_TO_INHG


def distance_nm(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in nautical miles (haversine)."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_R_NM * math.asin(min(1.0, math.sqrt(a)))


def bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Initial great-circle bearing from point 1 to point 2, degrees true, 0-359."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    y = math.sin(dl) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return (math.degrees(math.atan2(y, x)) + 360.0) % 360.0


def extent_bin(bearing: float) -> int:
    """`lake_extents.json` bin for a bearing: `round(bearing / 22.5) % 16` (data contract)."""
    return round((bearing % 360.0) / 22.5) % 16


def compass_point(bearing: float) -> str:
    names = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE", "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"]
    return names[extent_bin(bearing)]
