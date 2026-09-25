"""Candidate water bodies and the per-water-body score (design 3.3, plus the wave field).

Candidate filter: verdict `clear` or `conditional`, within `radius_nm` of the home airport,
`chord_ft >= min_run_ft`, and (optionally) a non-null `access`. Every `kind` is a candidate --
`lake`, `river`, `great_lake`, `connecting_water` -- with one exception: a `river` or
`connecting_water` **without** wave points is not, because a reach length says nothing about width,
and ranking one on its longest reach put Otter Creek and Halfway Creek in the briefing the morning
rivers landed. With points, its width is measured (`run[8]` is the straight line through the point
inside its own usable water), so it ranks like anything else. A missing `kind` means lake.

**Two ways to score a water body** (data contract, "Briefing"):

- *With* a wave field, by its **best region** for the wind at that water body: fetch is measured from
  each sample point upwind to the first land, so Muscamoot Bay behind the delta islands is a
  different number from the open lake in the same wind. The row carries the region's name and
  numbers, the open-water figure for contrast, and the calmest four regions.
- *Without* one, at lake level exactly as before: the extent along the wind from `lake_extents.json`
  for the run, the widest of the three bins around the wind for the fetch.

**Distance is to the water you would land on.** Lake St. Clair's centroid is 20+ nm from half its
bays, so a water body with a wave field reports its distance and bearing to the **best region's
point**, and ranks on that. The `radius_nm` gate uses the **nearest** point instead, so a water body
that straddles the edge of the circle is a candidate whenever any of its water is inside it, even if
today's calm end is beyond. Without a wave field both numbers are the centroid's, as before.

**Forecast points are snapped to a 0.1 deg grid.** A 40 nm radius around Pontiac leaves ~310
candidates, which would be seven Open-Meteo calls of 50 points; on a 0.1 deg grid (about 6 nm north-
south, 4.5 nm east-west at 42.7 deg) those collapse to ~78 cells, i.e. two calls. Lakes in the same
cell share a forecast. That is well inside the resolution of the underlying model -- Open-Meteo's
own response for Pontiac comes back snapped to its grid anyway (42.663, -83.402 for a request at
42.6655, -83.4187) -- and it keeps the run polite. The alternative considered, "fetch only the
nearest 150", was rejected because it silently drops the far half of the map.

**Wind per region.** A water body with a wave field takes a forecast per region, from the cell of
that region's centroid, so Anchor Bay and the south end of Lake St. Clair (25 miles apart) can be
scored in different winds. Around KONZ that is 63 cells instead of 45, still two calls. Only the
regions with water inside `radius_nm` are kept on a candidate: before, a region 50 nm up the St. Clair
River could be the "best water" of a candidate that only touched the circle. The home water keeps
every region.

Usable run without a wave field: the lake's extent along the wind bearing from `lake_extents.json`.
That file is written by the pipeline's `build` stage and may not exist yet; the fallback is
`chord_ft` in every direction, which is optimistic for run length and for fetch, so the caller
records it as an error. Wave fetch uses a three-bin arc rather than the single wind bin -- see
`Candidate.fetch_ft`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from ..wavefield import WaveField, WavePoint
from . import aero, wave
from .scoring import FAVORABLE, MARGINAL, UNFAVORABLE, band, rank, worse

GRID_DEG = 0.1
LIGHT_WIND_KT = 5.0  # below this the wind sets no usable direction (design 3.3 step 2)
MAX_ROW_REGIONS = 4  # the contract's cap on `lakes[].regions`
NO_WAVE_FIELD_KINDS = frozenset({"river", "connecting_water"})

Cell = tuple[int, int]


def cell_of(lat: float, lon: float) -> Cell:
    return (round(lat / GRID_DEG), round(lon / GRID_DEG))


def cell_point(cell: Cell) -> tuple[float, float]:
    return (round(cell[0] * GRID_DEG, 4), round(cell[1] * GRID_DEG, 4))


@dataclass(frozen=True)
class Wind:
    dir_deg: float
    kt: float
    gust_kt: float | None

    @property
    def wave_kt(self) -> float:
        """The speed the waves are computed at: the gust when there is one."""
        return self.gust_kt if self.gust_kt is not None else self.kt

    def to_row(self) -> dict:
        return {
            "dir": round(self.dir_deg),
            "kt": round(self.kt),
            "gust": None if self.gust_kt is None else round(self.gust_kt),
        }


@dataclass(frozen=True)
class Candidate:
    id: int
    name: str
    lat: float  # centroid: the forecast point and the marine query point
    lon: float
    verdict: str
    chord_ft: float
    chord_bearing_deg: float
    distance_nm: float  # home -> centroid
    bearing_deg: float
    extents_ft: tuple[int, ...] | None = None  # 16 bins, or None when lake_extents.json is missing
    kind: str = "lake"
    points: tuple[WavePoint, ...] = ()  # empty when this water body has no wave field
    labels: tuple[str, ...] = ()
    home: tuple[float, float] | None = None  # so a region's own distance can be measured
    nearest_nm: float | None = None  # home -> nearest sample point; the radius gate uses this
    bbox: tuple[float, float, float, float] | None = None  # west, south, east, north
    region_cells: dict[int, Cell] = field(default_factory=dict)  # label index -> its centroid's cell

    @property
    def has_wave_field(self) -> bool:
        return bool(self.points)

    @property
    def cell(self) -> Cell:
        return cell_of(self.lat, self.lon)

    @property
    def cell_point(self) -> tuple[float, float]:
        return cell_point(self.cell)

    @property
    def cells(self) -> list[Cell]:
        """Every forecast cell this water body is scored from: one per region, or the centroid's."""
        if self.has_wave_field and self.region_cells:
            return list(dict.fromkeys(self.region_cells.values()))
        return [self.cell]

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

    def at_region(self, region: wave.Region | None) -> tuple[float, float]:
        """`(distance_nm, bearing_deg)` to a region's point, or to the centroid without one."""
        if region is None or self.home is None:
            return self.distance_nm, self.bearing_deg
        return (
            aero.distance_nm(self.home[0], self.home[1], region.lat, region.lon),
            aero.bearing_deg(self.home[0], self.home[1], region.lat, region.lon),
        )


@dataclass
class LakeHour:
    """One water body at one block/hour."""

    hs_in: float
    tp_s: float
    run_ft: float
    wind_dir_deg: float
    wind_kt: float
    gust_kt: float | None
    xwind_kt: float | None
    level: str
    limiting: str | None
    regions: tuple[wave.Region, ...] = ()  # calm to rough, unusable last; empty without a field
    chosen: wave.Region | None = None  # the region `hs_in` / `run_ft` / the distance describe
    hs_open_in: int | None = None  # roughest region's `hs_all_in`
    when: datetime | None = None  # the block centre or hour this was scored for
    region_winds: dict[int, Wind] = field(default_factory=dict)  # label index -> that region's wind

    @property
    def region_label(self) -> str | None:
        return None if self.chosen is None else self.chosen.label

    @property
    def wind(self) -> Wind:
        return Wind(self.wind_dir_deg, self.wind_kt, self.gust_kt)

    def region_row(self, region: wave.Region) -> dict:
        """The contract's `regions[]` entry, with the wind that region was scored in."""
        return {**region.to_row(), "wind": self.region_winds.get(region.label_index, self.wind).to_row()}


def select_candidates(
    index: list[dict],
    *,
    home_lat: float,
    home_lon: float,
    radius_nm: float,
    min_run_ft: float,
    public_access_only: bool,
    extents: dict[str, list[int]] | None,
    wave_field: WaveField | None = None,
) -> list[Candidate]:
    """Apply the design 3.3 candidate filter to `data/out/index.json`.

    The cheap tests come first and the `bbox` pre-filter before them all, so a nationwide pack is
    never unpacked for the water bodies in the next state: only a water body whose bounding box
    reaches inside `radius_nm` has its sample points read at all.
    """
    out: list[Candidate] = []
    for lake in index:
        if lake.get("verdict") not in ("clear", "conditional"):
            continue
        has_points = wave_field is not None and wave_field.has_points(lake.get("id"))
        if (lake.get("kind") or "lake") in NO_WAVE_FIELD_KINDS and not has_points:
            continue
        if (lake.get("chord_ft") or 0.0) < min_run_ft:
            continue
        if public_access_only and not lake.get("access"):
            continue
        if _bbox_distance_nm(lake, home_lat, home_lon) > radius_nm:
            continue
        cand = _candidate(
            lake,
            home_lat=home_lat,
            home_lon=home_lon,
            extents=extents,
            wave_field=wave_field,
            radius_nm=radius_nm,
        )
        if cand is None:
            continue
        if (cand.nearest_nm if cand.nearest_nm is not None else cand.distance_nm) > radius_nm:
            continue
        out.append(cand)
    out.sort(key=lambda c: c.distance_nm)
    return out


def _bbox_distance_nm(lake: dict, home_lat: float, home_lon: float) -> float:
    """Distance from home to the nearest point of the water body's bounding box; 0 inside it.

    A lower bound on the distance to any of its water, so anything it rules out is genuinely out of
    range. An entry with no `bbox` (an older pack) is not pre-filtered.
    """
    bbox = lake.get("bbox")
    if not bbox or len(bbox) != 4:
        return 0.0
    west, south, east, north = (float(v) for v in bbox)
    lat = min(max(home_lat, south), north)
    lon = min(max(home_lon, west), east)
    return aero.distance_nm(home_lat, home_lon, lat, lon)


def find_candidate(
    index: list[dict],
    lake_id: int,
    *,
    home_lat: float,
    home_lon: float,
    extents: dict[str, list[int]] | None,
    wave_field: WaveField | None = None,
) -> Candidate | None:
    """One water body by id, with no radius, verdict, or kind filter: the home water (contract).

    No radius filter on its regions either: the home water is briefed whole.
    """
    for lake in index:
        if str(lake.get("id")) == str(lake_id):
            return _candidate(lake, home_lat=home_lat, home_lon=home_lon, extents=extents, wave_field=wave_field)
    return None


def _candidate(
    lake: dict,
    *,
    home_lat: float,
    home_lon: float,
    extents: dict[str, list[int]] | None,
    wave_field: WaveField | None,
    radius_nm: float | None = None,
) -> Candidate | None:
    lat, lon = lake.get("lat"), lake.get("lon")
    if lat is None or lon is None or lake.get("id") is None:
        return None
    lat, lon = float(lat), float(lon)
    ext = None
    if extents is not None:
        raw = extents.get(str(lake["id"]))
        if raw and len(raw) == 16:
            ext = tuple(int(v) for v in raw)
    points = wave_field.points(lake["id"]) if wave_field is not None else ()
    labels = wave_field.labels if wave_field is not None else ()
    nearest = None
    region_cells: dict[int, Cell] = {}
    if points:
        by_label: dict[int, list[WavePoint]] = {}
        for p in points:
            by_label.setdefault(p.label, []).append(p)
        near = {
            label: min(aero.distance_nm(home_lat, home_lon, p.lat, p.lon) for p in group)
            for label, group in by_label.items()
        }
        nearest = min(near.values())
        if radius_nm is not None:
            # Whole regions, not points: a bay straddling the circle is kept entire.
            keep = {label for label, nm in near.items() if nm <= radius_nm}
            if len(keep) < len(by_label):
                points = tuple(p for p in points if p.label in keep)
                by_label = {label: g for label, g in by_label.items() if label in keep}
        region_cells = {
            label: cell_of(sum(p.lat for p in g) / len(g), sum(p.lon for p in g) / len(g))
            for label, g in by_label.items()
        }
    return Candidate(
        id=int(lake["id"]),
        name=lake.get("name") or f"Unnamed water {lake['id']}",
        lat=lat,
        lon=lon,
        verdict=lake.get("verdict") or "unknown",
        chord_ft=float(lake.get("chord_ft") or 0.0),
        chord_bearing_deg=float(lake.get("chord_bearing_deg") or 0),
        distance_nm=aero.distance_nm(home_lat, home_lon, lat, lon),
        bearing_deg=aero.bearing_deg(home_lat, home_lon, lat, lon),
        extents_ft=ext,
        kind=lake.get("kind") or "lake",
        points=points,
        labels=labels,
        home=(home_lat, home_lon),
        nearest_nm=nearest,
        bbox=_bbox(lake),
        region_cells=region_cells,
    )


def _bbox(lake: dict) -> tuple[float, float, float, float] | None:
    raw = lake.get("bbox")
    if not raw or len(raw) != 4:
        return None
    west, south, east, north = (float(v) for v in raw)
    return (west, south, east, north)


def grid_cells(candidates: list[Candidate]) -> dict[Cell, tuple[float, float]]:
    """`{cell: forecast point}`, nearest-first so a truncated fetch keeps the close water."""
    seen: dict[Cell, tuple[float, float]] = {}
    for c in candidates:
        for cell in c.cells:
            seen.setdefault(cell, cell_point(cell))
    return seen


def grid_points(candidates: list[Candidate]) -> list[tuple[float, float]]:
    """Distinct 0.1 deg forecast points, in the order the cells were first seen."""
    return list(grid_cells(candidates).values())


def score_lake_hour(
    cand: Candidate,
    wind_dir_deg: float,
    wind_kt: float,
    gust_kt: float | None,
    limits,
    region_winds: dict[int, Wind] | None = None,
) -> LakeHour:
    """Waves, usable run, and water crosswind for one water body at one hour (design 3.3 steps 2-6).

    The wave numbers are computed at the **gust** when there is one, as they have been since the
    first build: the gust is what picks the water up, and the marginal band exists to catch exactly
    that. The region aggregation is given the same wind, so a region row and a lake-level row mean
    the same thing.

    `region_winds` is the per-region wind (label index -> `Wind`); without it every region gets the
    one wind passed in. With it, the returned hour's wind is the chosen region's.
    """
    gust_or_wind = gust_kt if gust_kt is not None else wind_kt
    regions: tuple[wave.Region, ...] = ()
    chosen: wave.Region | None = None
    hs_open_in: int | None = None
    winds = dict(region_winds or {})

    if cand.has_wave_field:
        regions = tuple(
            wave.regions(
                cand.points,
                cand.labels,
                wind_dir_deg=wind_dir_deg,
                wind_kt=gust_or_wind,
                min_run_ft=limits.min_run_ft,
                winds=None if region_winds is None else {k: (w.dir_deg, w.wave_kt) for k, w in winds.items()},
            )
        )
        hs_open_in = wave.open_water_in(regions)
        best = wave.best_region(regions)
        if best is not None:
            chosen = best
            hs_in: float = float(best.hs_in or 0)
            run = float(best.run_ft or 0)
        else:
            # No region has enough run into this wind. The row still names the calmest water so the
            # pilot knows what he is being turned away from; `run` decides the score below.
            chosen = min(regions, key=lambda r: (r.hs_all_in, r.label))
            hs_in = float(chosen.hs_all_in)
            run = float(chosen.run_all_ft)
        if chosen.label_index in winds:
            w = winds[chosen.label_index]
            wind_dir_deg, wind_kt, gust_kt, gust_or_wind = w.dir_deg, w.kt, w.gust_kt, w.wave_kt
        tp_s = _region_tp_s(cand, chosen, wind_dir_deg, gust_or_wind)
    else:
        run = cand.run_ft(wind_dir_deg, wind_kt)
        fetch_ft = cand.fetch_ft(wind_dir_deg, wind_kt)
        hs_in = wave.wave_height_in(gust_or_wind, fetch_ft)
        tp_s = wave.peak_period_s(gust_or_wind, fetch_ft * wave.FT_TO_M)

    factors: list[tuple[str, str]] = []
    factors.append(("waves", band(hs_in, limits.wave_ok_in, limits.wave_max_in)))
    # Landing is into the wind, so the crosswind on the longest chord is only the "I would rather use
    # the long axis" number: reported always, scored only when the run into the wind is too short.
    # Then a lake-level water body whose long axis is long enough and within the crosswind limit is
    # marginal on `xwind_water` rather than unfavorable on `run`. (Scoring it unconditionally made
    # Lake St. Clair and the Detroit River "marginal, crosswind" against a 28 mile chord.) With a wave
    # field the whole-lake chord says nothing about a region, so only `run` applies.
    xw = None
    if wind_kt >= LIGHT_WIND_KT:
        xw = aero.crosswind_kt(wind_dir_deg, wind_kt, cand.chord_bearing_deg)
    if run >= limits.min_run_ft:
        factors.append(("run", FAVORABLE))
    elif (
        not cand.has_wave_field
        and cand.chord_ft >= limits.min_run_ft
        and (xw is None or xw <= limits.xwind_water_max)
    ):
        factors.append(("xwind_water", MARGINAL))
    else:
        factors.append(("run", UNFAVORABLE))

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
        regions=regions,
        chosen=chosen,
        hs_open_in=hs_open_in,
        region_winds=winds,
    )


def region_score(region: wave.Region, limits) -> str:
    """One region's own level: the wave band, or unfavorable with no usable run into the wind."""
    if region.hs_in is None:
        return UNFAVORABLE
    return band(region.hs_in, limits.wave_ok_in, limits.wave_max_in)


def _region_tp_s(cand: Candidate, region: wave.Region | None, wind_dir_deg: float, wind_kt: float) -> float:
    """Peak period at the region's own point. Not in the output; kept for the steep-chop rule."""
    if region is None or region.point is None or not (0 <= region.point < len(cand.points)):
        return 0.0
    p = cand.points[region.point]
    return wave.spm_wave(wind_kt, p.fetch_m[wave.wind_bin(wind_dir_deg)], p.depth_m)[1]


@dataclass
class RankedLake:
    cand: Candidate
    level: str  # WATER only: waves, run, xwind_water, ice. Never the airport score.
    limiting: str | None
    hour: LakeHour
    frozen: bool = False
    hours: list[LakeHour] = field(default_factory=list)

    @property
    def distance_nm(self) -> float:
        """To the best region's point on a water body with a field; to the centroid without one."""
        return self.cand.at_region(self.hour.chosen)[0]

    def to_row(self) -> dict:
        """The `lakes[]` row shape from the data contract; every number already rounded."""
        distance, bearing = self.cand.at_region(self.hour.chosen)
        return {
            "id": self.cand.id,
            "name": self.cand.name,
            "kind": self.cand.kind,
            "score": self.level,
            "limiting": self.limiting,
            "hs_in": round(self.hour.hs_in),
            "run_ft": round(self.hour.run_ft),
            "region": self.hour.region_label,
            "hs_open_in": self.hour.hs_open_in,
            "regions": [self.hour.region_row(r) for r in self.hour.regions[:MAX_ROW_REGIONS]],
            "wind": self.hour.wind.to_row(),
            "distance_nm": round(distance, 1),
            "bearing_deg": round(bearing),
            "verdict": self.cand.verdict,
            "frozen": self.frozen,
        }


def score_over(
    cand: Candidate,
    forecasts: dict[tuple[int, int], object],
    windows: list,
    limits,
    *,
    frozen: bool,
) -> RankedLake | None:
    """One water body over `windows`, taking its worst hour. `None` when no forecast covers it.

    With a wave field each region takes the wind of its own cell; a region whose cell has nothing
    for the hour sits that hour out, and an hour with no region left is skipped.
    """
    per_region = cand.has_wave_field and bool(cand.region_cells)
    if not per_region and forecasts.get(cand.cell) is None:
        return None
    hours: list[LakeHour] = []
    for when in windows:
        if per_region:
            by_cell = {cell: _wind_at(forecasts.get(cell), when) for cell in cand.cells}
            winds = {
                label: w for label, cell in cand.region_cells.items() if (w := by_cell.get(cell)) is not None
            }
            if not winds:
                continue
            first = next(iter(winds.values()))
            hour = score_lake_hour(cand, first.dir_deg, first.kt, first.gust_kt, limits, region_winds=winds)
        else:
            w = _wind_at(forecasts.get(cand.cell), when)
            if w is None:
                continue
            hour = score_lake_hour(cand, w.dir_deg, w.kt, w.gust_kt, limits)
        hour.when = when
        hours.append(hour)
    if not hours:
        return None
    worst = max(hours, key=lambda h: (rank(h.level), h.hs_in, -h.run_ft))
    level, limiting = worst.level, worst.limiting
    if frozen:
        level, limiting = UNFAVORABLE, "ice"
    return RankedLake(cand=cand, level=level, limiting=limiting, hour=worst, frozen=frozen, hours=hours)


def _wind_at(series: object | None, when: datetime) -> Wind | None:
    """The model wind at `when` from one cell's series, or `None` when it has none."""
    if series is None:
        return None
    model = series.at(when)  # type: ignore[attr-defined]
    if not model or model.get("wind_speed_10m") is None:
        return None
    gust = model.get("wind_gusts_10m")
    return Wind(
        float(model.get("wind_direction_10m") or 0.0),
        float(model["wind_speed_10m"]),
        None if gust is None else float(gust),
    )


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

    Each water body takes its **worst** hour: the largest `Hs` and the smallest usable run across the
    window, which is what design 3.4 asks for and is the conservative reading for the 3-hour blocks
    too. With a wave field, "largest Hs" is the largest *best-region* Hs -- the calmest water it
    offers at its worst hour.

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
        scored = score_over(cand, forecasts, windows, limits, frozen=frozen)
        if scored is not None:
            ranked.append(scored)
    ranked.sort(key=lambda r: (rank(r.level), r.distance_nm, r.hour.hs_in))
    return ranked[:n_lakes]
