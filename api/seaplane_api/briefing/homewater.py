"""The `home_water` block: one water body briefed on every run, whatever `radius_nm` says.

Shape and rules: `docs/data-contract.md`, "Briefing". `settings.home_water` names it; the block lists
**every** region rather than the top four, each with its own score, so the pilot can read the whole
water at a glance and pick an end. A region with no usable run into today's wind keeps its place with
`hs_in: null` and an `unfavorable` score -- dropping it would make the list change shape with the
wind, and "Anchor Bay is not landable in this" is the answer to the question as much as a number is.

Two second opinions ride along, and both are generic: nothing here knows it is usually Lake St.
Clair. `observed` is whatever real stations happen to be near this water -- NDBC buoys and shore
stations from the file already fetched, plus the nearest airport METARs -- and `marine_hs_in` is the
Open-Meteo marine model at the centroid. They sit beside the computed numbers, never instead of
them: the marine model has no idea where the delta islands are, and the buoy is one spot.
"""
from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta

from . import aero
from .lakes import Candidate, RankedLake, region_score
from .scoring import UNFAVORABLE
from .wave import M_PER_IN

OBS_RADIUS_NM = 15.0  # a buoy further off than this is reporting different water
OBS_MAX_AGE_MIN = 90  # the same staleness cut the METAR override uses (design 3.1)
METAR_CAP = 3
MARINE_MAX_SNAP_NM = 10.0  # see `marine_hs_in`
M_TO_FT = 3.280839895


def block(ranked: RankedLake, limits, *, observed: list[dict] | None = None, marine_hs_in: int | None = None) -> dict:
    """The contract's `home_water` object. `observed` is omitted (not null) for the outlook copy."""
    cand = ranked.cand
    hour = ranked.hour
    out: dict = {
        "id": cand.id,
        "name": cand.name,
        "kind": cand.kind,
        "score": ranked.level,
        "limiting": ranked.limiting,
        "wind": hour.wind.to_row(),
        "regions": [
            {**hour.region_row(region), "score": UNFAVORABLE if ranked.frozen else region_score(region, limits)}
            for region in hour.regions
        ],
        "hs_open_in": hour.hs_open_in,
    }
    if observed is not None:
        out["observed"] = observed
    out["marine_hs_in"] = marine_hs_in
    return out


def nearest_nm(cand: Candidate, lat: float, lon: float) -> float:
    """Distance from a station to the water: to the nearest sample point, else to the bbox.

    A station is being asked "are you reporting this water body", so the answer has to be measured
    against the water and not against a centroid that may be 20 nm from the end the pilot lands on.
    Without a wave field the bounding box is the best outline there is; without one of those either
    (an older pack), the centroid.
    """
    if cand.points:
        return min(aero.distance_nm(lat, lon, p.lat, p.lon) for p in cand.points)
    if cand.bbox:
        west, south, east, north = cand.bbox
        return aero.distance_nm(lat, lon, min(max(lat, south), north), min(max(lon, west), east))
    return aero.distance_nm(lat, lon, cand.lat, cand.lon)


def bbox_around(cand: Candidate, margin_nm: float = OBS_RADIUS_NM) -> tuple[float, float, float, float]:
    """`(min_lat, min_lon, max_lat, max_lon)` covering the water body plus a margin.

    Built from the sample points when there are any -- they *are* the water -- then from the water
    body's own bounding box, and from the centroid only when neither exists. The longitude margin is
    widened by latitude so the box is `margin_nm` wide on the ground rather than in degrees.
    """
    if cand.points:
        lats = [p.lat for p in cand.points]
        lons = [p.lon for p in cand.points]
    elif cand.bbox:
        lats = [cand.bbox[1], cand.bbox[3]]
        lons = [cand.bbox[0], cand.bbox[2]]
    else:
        lats, lons = [cand.lat], [cand.lon]
    d_lat = margin_nm / 60.0
    mid = (min(lats) + max(lats)) / 2.0
    d_lon = d_lat / max(0.2, abs(math.cos(math.radians(mid))))
    return (min(lats) - d_lat, min(lons) - d_lon, max(lats) + d_lat, max(lons) + d_lon)


def observed_rows(
    cand: Candidate,
    *,
    buoys: list[dict] | None,
    metars: list[dict] | None,
    now_utc: datetime,
    radius_nm: float = OBS_RADIUS_NM,
    max_age_min: int = OBS_MAX_AGE_MIN,
    metar_cap: int = METAR_CAP,
) -> list[dict]:
    """Real observations near this water, nearest first: buoys inside `radius_nm`, then METARs.

    Buoys have to be on (or beside) the water to mean anything, so they are cut at `radius_nm`;
    airport METARs are the nearest few whatever the distance, because on most of the country that is
    the only wind anybody is measuring. Every row is the newest observation for that station and is
    dropped once it is older than `max_age_min`, which is the same cut the METAR override uses: an
    hours-old wind next to a forecast reads as agreement when it is nothing of the kind.

    A station with nothing to say is dropped rather than listed empty: a buoy reporting neither wind
    nor wave height, or a field whose wind sensor is out.
    """
    rows: list[dict] = []
    for b in buoys or []:
        at = b.get("at")
        if not _fresh(at, now_utc, max_age_min):
            continue
        if b.get("speed_kt") is None and b.get("wave_height_m") is None:
            continue
        distance = nearest_nm(cand, float(b["lat"]), float(b["lon"]))
        if distance > radius_nm:
            continue
        wave_m = b.get("wave_height_m")
        rows.append(
            {
                "station": str(b.get("id") or ""),
                "name": None,  # latest_obs.txt carries no station names
                "kind": "buoy",
                "at": _iso_z(at),
                "wind": {
                    "dir": None if b.get("dir_deg") is None else round(float(b["dir_deg"])),
                    "kt": None if b.get("speed_kt") is None else round(float(b["speed_kt"])),
                    "gust": None if b.get("gust_kt") is None else round(float(b["gust_kt"])),
                },
                "wave_ft": None if wave_m is None else round(float(wave_m) * M_TO_FT, 1),
                "distance_nm": round(distance, 1),
            }
        )

    metar_rows: list[dict] = []
    for m in metars or []:
        at = _metar_time(m)
        if not _fresh(at, now_utc, max_age_min):
            continue
        lat, lon = m.get("lat"), m.get("lon")
        if lat is None or lon is None:
            continue
        wind_dir, wind_kt = _int_or_none(m.get("wdir")), _int_or_none(m.get("wspd"))
        if wind_dir is None and wind_kt is None:
            # A field whose wind sensor is out (KARB, `PWINO`, on the live check) reports a METAR
            # with no wind in it at all. Listing it would spend one of three slots on nothing.
            continue
        metar_rows.append(
            {
                "station": str(m.get("icaoId") or m.get("stationId") or ""),
                "name": _clean_name(m.get("name")),
                "kind": "metar",
                "at": _iso_z(at),
                "wind": {"dir": wind_dir, "kt": wind_kt, "gust": _int_or_none(m.get("wgst"))},
                "wave_ft": None,
                "distance_nm": round(nearest_nm(cand, float(lat), float(lon)), 1),
            }
        )
    metar_rows.sort(key=lambda r: r["distance_nm"])
    rows.extend(_newest_per_station(metar_rows)[:metar_cap])
    rows.sort(key=lambda r: r["distance_nm"])
    return rows


def marine_hs_in(payload: dict | None, when_local: datetime, cand: Candidate) -> int | None:
    """Open-Meteo marine wave height at the water body's centroid for `when_local`, in inches.

    `None` when the feed failed, when the model has no value for that hour (it returns a list of
    nulls for narrow bays and anywhere inland), or when it answered about water more than
    `MARINE_MAX_SNAP_NM` away: the API snaps a request to its nearest wet cell without saying so, and
    an inland home water would otherwise be handed the nearest Great Lake's sea state as if it were
    its own.
    """
    if not payload:
        return None
    lat, lon = payload.get("latitude"), payload.get("longitude")
    if lat is not None and lon is not None and (
        aero.distance_nm(float(lat), float(lon), cand.lat, cand.lon) > MARINE_MAX_SNAP_NM
    ):
        return None
    hourly = payload.get("hourly") or {}
    times = hourly.get("time") or []
    values = hourly.get("wave_height") or []
    key = when_local.strftime("%Y-%m-%dT%H:00")
    for t, v in zip(times, values, strict=False):
        if str(t)[:16] == key:
            return None if v is None else round(float(v) / M_PER_IN)
    return None


def _newest_per_station(rows: list[dict]) -> list[dict]:
    """One row per station, the newest. The bbox feed can return several for one field."""
    best: dict[str, dict] = {}
    for r in rows:
        prev = best.get(r["station"])
        if prev is None or (r["at"] or "") > (prev["at"] or ""):
            best[r["station"]] = r
    return sorted(best.values(), key=lambda r: r["distance_nm"])


def _metar_time(m: dict) -> datetime | None:
    obs = m.get("obsTime")
    if obs is None:
        return None
    try:
        return datetime.fromtimestamp(float(obs), tz=UTC)
    except (TypeError, ValueError, OSError):
        return None


def _fresh(at: datetime | None, now_utc: datetime, max_age_min: int) -> bool:
    if at is None:
        return False
    age = now_utc - at
    return timedelta(minutes=-max_age_min) <= age <= timedelta(minutes=max_age_min)


def _iso_z(at: datetime | None) -> str | None:
    if at is None:
        return None
    return at.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _clean_name(raw) -> str | None:
    if not raw:
        return None
    return " ".join(str(raw).split()) or None


def _int_or_none(value) -> int | None:
    if value is None or isinstance(value, str):
        return None
    return round(float(value))
