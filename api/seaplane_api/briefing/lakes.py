"""Candidate lakes and the per-lake water score (design 3.3).

Candidate filter: verdict `clear` or `conditional`, within `radius_nm` of the home airport by
great-circle distance, `chord_ft >= min_run_ft`, and (optionally) a non-null `access`.

**Forecast points are snapped to a 0.1 deg grid.** A 40 nm radius around Pontiac leaves ~310
candidates, which would be seven Open-Meteo calls of 50 points; on a 0.1 deg grid (about 6 nm north-
south, 4.5 nm east-west at 42.7 deg) those collapse to ~78 cells, i.e. two calls. Lakes in the same
cell share a forecast. That is well inside the resolution of the underlying model -- Open-Meteo's
own response for Pontiac comes back snapped to its grid anyway (42.663, -83.402 for a request at
42.6655, -83.4187) -- and it keeps the run polite. The alternative considered, "fetch only the
nearest 150", was rejected because it silently drops the far half of the map.

Usable run: the lake's extent along the wind bearing from `lake_extents.json`. That file is written
by the pipeline's `build` stage and may not exist yet; the fallback is `chord_ft` in every direction,
which is optimistic for run length and for fetch, so the caller records it as an error. Wave fetch
uses a three-bin arc rather than the single wind bin -- see `Candidate.fetch_ft`.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from . import aero, wave
from .scoring import FAVORABLE, MARGINAL, UNFAVORABLE, band, rank, worse

GRID_DEG = 0.1
LIGHT_WIND_KT = 5.0  # below this the wind sets no usable direction (design 3.3 step 2)


@dataclass(frozen=True)
class Candidate:
    id: int
    name: str
    lat: float
    lon: float
    verdict: str
    chord_ft: float
    chord_bearing_deg: float
    distance_nm: float
    bearing_deg: float
    extents_ft: tuple[int, ...] | None = None  # 16 bins, or None when lake_extents.json is missing

    @property
    def cell(self) -> tuple[int, int]:
        return (round(self.lat / GRID_DEG), round(self.lon / GRID_DEG))

    @property
    def cell_point(self) -> tuple[float, float]:
        return (round(self.cell[0] * GRID_DEG, 4), round(self.cell[1] * GRID_DEG, 4))

    def run_ft(self, wind_dir_deg: float, wind_kt: float) -> float:
        """Water available along the wind; the longest chord when the wind is too light to matter."""
        if self.extents_ft is None or wind_kt < LIGHT_WIND_KT:
            return self.chord_ft
        return float(self.extents_ft[aero.extent_bin(wind_dir_deg)])

    def fetch_ft(self, wind_dir_deg: float, wind_kt: float) -> float:
        """Fetch for the wave model: the widest of the three bins around the wind, not just one.

        The pipeline samples 40 lines per bearing, so a single bin can undershoot the true extent on
        an irregular shoreline (Orchard Lake's bin 4 reads 6,616 ft against an 8,084 ft chord that
        threads a narrow neck). Undershooting is conservative for the usable RUN -- less water than
        you thought -- but anti-conservative for FETCH, because a short fetch predicts calmer water
        than the lake will actually give. Taking `max(bin-1, bin, bin+1)` also matches SPM practice
        of considering an arc either side of the wind rather than one radial.
        """
        if self.extents_ft is None or wind_kt < LIGHT_WIND_KT:
            return self.chord_ft
        b = aero.extent_bin(wind_dir_deg)
        return float(max(self.extents_ft[(b + d) % 16] for d in (-1, 0, 1)))


@dataclass
class LakeHour:
    """One lake at one block/hour."""

    hs_in: float
    tp_s: float
    run_ft: float
    wind_dir_deg: float
    wind_kt: float
    gust_kt: float | None
    xwind_kt: float | None
    level: str
    limiting: str | None


def select_candidates(
    index: list[dict],
    *,
    home_lat: float,
    home_lon: float,
    radius_nm: float,
    min_run_ft: float,
    public_access_only: bool,
    extents: dict[str, list[int]] | None,
) -> list[Candidate]:
    """Apply the design 3.3 candidate filter to `data/out/index.json`."""
    out: list[Candidate] = []
    for lake in index:
        if lake.get("verdict") not in ("clear", "conditional"):
            continue
        chord = lake.get("chord_ft") or 0.0
        if chord < min_run_ft:
            continue
        if public_access_only and not lake.get("access"):
            continue
        lat, lon = lake.get("lat"), lake.get("lon")
        if lat is None or lon is None:
            continue
        dist = aero.distance_nm(home_lat, home_lon, lat, lon)
        if dist > radius_nm:
            continue
        ext = None
        if extents is not None:
            raw = extents.get(str(lake["id"]))
            if raw and len(raw) == 16:
                ext = tuple(int(v) for v in raw)
        out.append(
            Candidate(
                id=int(lake["id"]),
                name=lake.get("name") or f"Unnamed lake {lake['id']}",
                lat=float(lat),
                lon=float(lon),
                verdict=lake["verdict"],
                chord_ft=float(chord),
                chord_bearing_deg=float(lake.get("chord_bearing_deg") or 0),
                distance_nm=dist,
                bearing_deg=aero.bearing_deg(home_lat, home_lon, lat, lon),
                extents_ft=ext,
            )
        )
    out.sort(key=lambda c: c.distance_nm)
    return out


def grid_points(candidates: list[Candidate]) -> list[tuple[float, float]]:
    """Distinct 0.1 deg forecast points, nearest-first so a truncated fetch keeps the close lakes."""
    seen: dict[tuple[int, int], tuple[float, float]] = {}
    for c in candidates:
        seen.setdefault(c.cell, c.cell_point)
    return list(seen.values())


def score_lake_hour(
    cand: Candidate,
    wind_dir_deg: float,
    wind_kt: float,
    gust_kt: float | None,
    limits,
) -> LakeHour:
    """Waves, usable run, and water crosswind for one lake at one hour (design 3.3 steps 2-6)."""
    run = cand.run_ft(wind_dir_deg, wind_kt)
    fetch_ft = cand.fetch_ft(wind_dir_deg, wind_kt)
    gust_or_wind = gust_kt if gust_kt is not None else wind_kt
    hs_in = wave.wave_height_in(gust_or_wind, fetch_ft)
    tp_s = wave.peak_period_s(gust_or_wind, fetch_ft * wave.FT_TO_M)

    factors: list[tuple[str, str]] = []
    factors.append(("waves", band(hs_in, limits.wave_ok_in, limits.wave_max_in)))
    factors.append(("run", FAVORABLE if run >= limits.min_run_ft else UNFAVORABLE))

    xw = None
    if wind_kt >= LIGHT_WIND_KT:
        # Landing is into the wind, so this is only the "I would rather use the long axis" number.
        xw = aero.crosswind_kt(wind_dir_deg, wind_kt, cand.chord_bearing_deg)
        factors.append(("xwind_water", FAVORABLE if xw <= limits.xwind_water_max else MARGINAL))

    level = FAVORABLE
    for _, lv in factors:
        level = worse(level, lv)
    limiting = None
    if level != FAVORABLE:
        limiting = next(fid for fid, lv in factors if lv == level)
    return LakeHour(
        hs_in=hs_in,
        tp_s=tp_s,
        run_ft=run,
        wind_dir_deg=wind_dir_deg,
        wind_kt=wind_kt,
        gust_kt=gust_kt,
        xwind_kt=xw,
        level=level,
        limiting=limiting,
    )


@dataclass
class RankedLake:
    cand: Candidate
    level: str  # WATER only: waves, run, xwind_water, ice. Never the airport score.
    limiting: str | None
    hour: LakeHour
    frozen: bool = False
    hours: list[LakeHour] = field(default_factory=list)

    def to_row(self) -> dict:
        """The `lakes[]` row shape from the data contract; every number already rounded."""
        return {
            "id": self.cand.id,
            "name": self.cand.name,
            "score": self.level,
            "limiting": self.limiting,
            "hs_in": round(self.hour.hs_in),
            "run_ft": round(self.hour.run_ft),
            "wind": {
                "dir": round(self.hour.wind_dir_deg),
                "kt": round(self.hour.wind_kt),
                "gust": None if self.hour.gust_kt is None else round(self.hour.gust_kt),
            },
            "distance_nm": round(self.cand.distance_nm, 1),
            "bearing_deg": round(self.cand.bearing_deg),
            "verdict": self.cand.verdict,
            "frozen": self.frozen,
        }


def rank_lakes(
    candidates: list[Candidate],
    forecasts: dict[tuple[int, int], object],
    windows: list,
    limits,
    *,
    frozen: bool,
    n_lakes: int,
) -> list[RankedLake]:
    """Score every candidate over `windows` (a list of datetimes) and return the best `n_lakes`.

    Each lake takes its **worst** hour: the largest `Hs` and the smallest usable run across the
    window, which is what design 3.4 asks for and is the conservative reading for the 3-hour blocks
    too.

    `score` and `limiting` are **water only** -- waves, run, water crosswind, ice. The airport
    weather is not folded in: it is already the header, the block strip and the hour strip, and
    repeating it here just made every row read "marginal / ceiling", which tells the pilot nothing
    about the water he is choosing between.

    Rank by score, then **distance ascending**, then `Hs` ascending. Ranking on `Hs` before distance
    sorted by "smallest puddle that still clears `min_run_ft`" and buried Cass and Orchard under
    2,000 ft ponds 36 nm away. Small lakes still win on a windy day, through the score.

    A frozen day does not drop the lakes: they are returned with `frozen: true`, `unfavorable`, and
    `limiting: "ice"` so the card can say why there is nothing to recommend. Dropping them would
    make the contract's `frozen` field unreachable.
    """
    ranked: list[RankedLake] = []
    for cand in candidates:
        series = forecasts.get(cand.cell)
        if series is None:
            continue
        hours: list[LakeHour] = []
        for when in windows:
            model = series.at(when)  # type: ignore[attr-defined]
            if not model or model.get("wind_speed_10m") is None:
                continue
            hours.append(
                score_lake_hour(
                    cand,
                    float(model.get("wind_direction_10m") or 0.0),
                    float(model["wind_speed_10m"]),
                    None if model.get("wind_gusts_10m") is None else float(model["wind_gusts_10m"]),
                    limits,
                )
            )
        if not hours:
            continue
        worst = max(hours, key=lambda h: (rank(h.level), h.hs_in, -h.run_ft))
        level, limiting = worst.level, worst.limiting
        if frozen:
            level, limiting = UNFAVORABLE, "ice"
        ranked.append(
            RankedLake(cand=cand, level=level, limiting=limiting, hour=worst, frozen=frozen, hours=hours)
        )
    ranked.sort(key=lambda r: (rank(r.level), r.cand.distance_nm, r.hour.hs_in))
    return ranked[:n_lakes]
