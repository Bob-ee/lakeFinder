"""Fetch-limited wave height (SPM 1984) and the per-point wave field's region aggregation.

USACE Shore Protection Manual 1984, eqs. 3-33/3-34 (deep water) and 3-39/3-40 (shallow water), as
restated in `docs/data-contract.md`, "Wave field":

    UA = 0.71 * U10**1.23                          # wind stress factor, U10 in m/s
    f  = g * F / UA**2                             # dimensionless fetch
    d  = g * max(depth, 0.1 m) / UA**2             # dimensionless depth, when depth is known
    depth unknown:  h = 1.6e-3 * sqrt(f)                          t = 0.2857 * f**(1/3)
    depth known:    a = tanh(0.530 * d**0.75)   h = 0.283 * a * tanh(0.00565 * sqrt(f) / a)
                    b = tanh(0.833 * d**0.375)  t = 7.54  * b * tanh(0.0379 * f**(1/3) / b)
    Hs = min(h, 0.2433) * UA**2 / g                Tp = min(t, 8.134) * UA / g

capped at the fully developed sea. **Deviation from the design doc:** it quotes the fully developed
cap as `2.482e-2 * UA**2 / g`, which is an order of magnitude below SPM's own value and would cap the
doc's *own* worked example (20 kt over 5 km -> 0.45 m) at 0.39 m. SPM 1984 gives the fully developed
limits as `g*Hmo/UA**2 = 2.433e-1` and `g*Tm/UA = 8.134`, which is what is used here (and what the
data contract carries); it reproduces both worked values and only binds past roughly 370 km of fetch.

Depth is optional because bathymetry exists for almost no lake in the country. Without it the model
is the deep-water form, which overstates shallow water: the conservative direction.

`KT_TO_MS` stays at the package's 0.5144444 rather than the contract's printed 0.514444. The two
agree to 1.2e-7 relative, which is four orders below the precision the shared fixtures are written to
(`rules/fixtures/waves.json` rounds Hs to 4 decimal places), and both reproduce every fixture case.

The "steep chop" rule in design 3.3 step 5 (unfavorable when Tp < 2 s and Hs > 6 in) is deliberately
NOT applied: the doc conditions it on Bobby confirming it matters for the SeaRey hull, and there is
no settings knob for it. `steep_chop()` is here so turning it on is one call.
"""
from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

G = 9.80665
KT_TO_MS = 0.5144444
M_TO_IN = 39.3700787
FT_TO_M = 0.3048
M_PER_IN = 0.0254

_FULLY_DEVELOPED_H = 2.433e-1  # g*Hmo/UA^2 at full development (SPM 1984)
_FULLY_DEVELOPED_T = 8.134  # g*Tm/UA at full development (SPM 1984)


def wind_stress_factor(wind_kt: float) -> float:
    """`UA` from a 10 m wind in knots."""
    u10 = max(0.0, wind_kt) * KT_TO_MS
    if u10 <= 0:
        return 0.0
    return 0.71 * u10**1.23


def spm_wave(wind_kt: float, fetch_m: float, depth_m: float | None = None) -> tuple[float, float]:
    """`(Hs metres, Tp seconds)` for a wind over a fetch, shallow-water form when depth is known.

    The contract formula, shared with `rules/waves/` in JS and checked against
    `rules/fixtures/waves.json`. `depth_m is None` is the deep-water form and is numerically the same
    calculation as `significant_wave_height_m` / `peak_period_s`, which predate it and stay.
    """
    if wind_kt <= 0 or fetch_m <= 0:
        return 0.0, 0.0
    ua = wind_stress_factor(wind_kt)
    if ua <= 0:
        return 0.0, 0.0
    f = G * fetch_m / ua**2
    if depth_m is None:
        h = 1.6e-3 * math.sqrt(f)
        t = 0.2857 * f ** (1.0 / 3.0)
    else:
        d = G * max(depth_m, 0.1) / ua**2
        a = math.tanh(0.530 * d**0.75)
        h = 0.283 * a * math.tanh(0.00565 * math.sqrt(f) / a)
        b = math.tanh(0.833 * d**0.375)
        t = 7.54 * b * math.tanh(0.0379 * f ** (1.0 / 3.0) / b)
    h = min(h, _FULLY_DEVELOPED_H)
    t = min(t, _FULLY_DEVELOPED_T)
    return h * ua**2 / G, t * ua / G


def significant_wave_height_m(wind_kt: float, fetch_m: float) -> float:
    """Significant wave height in metres for `wind_kt` blowing over `fetch_m` of open water."""
    ua = wind_stress_factor(wind_kt)
    if ua <= 0 or fetch_m <= 0:
        return 0.0
    hs = 1.6e-3 * ua**2 / G * math.sqrt(G * fetch_m / ua**2)
    return min(hs, _FULLY_DEVELOPED_H * ua**2 / G)


def peak_period_s(wind_kt: float, fetch_m: float) -> float:
    """Peak period in seconds, capped at the fully developed value."""
    ua = wind_stress_factor(wind_kt)
    if ua <= 0 or fetch_m <= 0:
        return 0.0
    tp = 0.2857 * ua / G * (G * fetch_m / ua**2) ** (1.0 / 3.0)
    return min(tp, _FULLY_DEVELOPED_T * ua / G)


def wind_bin(wind_dir_deg: float) -> int:
    """The `fetch[16]` bin for a wind **from** `wind_dir_deg`: `floor(d / 22.5 + 0.5) % 16`.

    Not `aero.extent_bin`, which rounds with Python's banker's rounding and so sends an exact 11.25
    to bin 0 where the contract sends it to bin 1. The two agree everywhere except on the half-bin
    boundaries, and the fixtures pin those.
    """
    return math.floor((wind_dir_deg % 360.0) / 22.5 + 0.5) % 16


def hs_inches(hs_m: float) -> int:
    """Whole inches, `floor(x / 0.0254 + 0.5)` (the contract's round-half-up, not banker's)."""
    return math.floor(hs_m / M_PER_IN + 0.5)


def wave_height_in(wind_kt: float, fetch_ft: float) -> float:
    """Significant wave height in inches for a fetch given in feet (what `lake_extents.json` holds)."""
    return significant_wave_height_m(wind_kt, fetch_ft * FT_TO_M) * M_TO_IN


def steep_chop(hs_in: float, tp_s: float) -> bool:
    """Design 3.3 step 5's optional steep-chop flag. Not wired into scoring; see the module docstring."""
    return tp_s < 2.0 and hs_in > 6.0


# --- the wave field: points to regions ---------------------------------------------------------


class WavePointLike(Protocol):
    """What the aggregation needs from one sample point; `wavefield.WavePoint` is the real one."""

    lat: float
    lon: float
    depth_m: float | None
    label: int
    fetch_m: Sequence[float]  # 16 bins, metres
    run_ft: Sequence[float]  # 8 bins, feet


@dataclass(frozen=True)
class Region:
    """Every point of one water body sharing a label, scored for one wind.

    `hs_in` / `run_ft` / `point` are `None` when no point of the region has enough run into the wind
    to land on. `lat` / `lon` still locate the region in that case (its calmest point), because the
    client draws it on the map whether or not it is usable today.
    """

    label: str
    hs_in: int | None  # 75th percentile Hs over the usable points, whole inches
    run_ft: int | None  # lower median of the usable runs, feet
    lat: float
    lon: float
    hs_all_in: int  # the same percentile over every point: the region's own open-water figure
    point: int | None  # index of the calmest usable point within the water body's points
    n_points: int
    n_usable: int
    run_all_ft: int  # lower median run over every point, gate ignored (display only)

    def to_row(self) -> dict:
        """The contract's `regions[]` entry: label, the two numbers, and where it is."""
        return {
            "label": self.label,
            "hs_in": self.hs_in,
            "run_ft": self.run_ft,
            "lat": round(self.lat, 5),
            "lon": round(self.lon, 5),
        }


def regions(
    points: Sequence[WavePointLike],
    labels: Sequence[str],
    *,
    wind_dir_deg: float,
    wind_kt: float,
    min_run_ft: float,
) -> list[Region]:
    """The contract's region aggregation for one wind, calm to rough with the unusable last.

    Points sharing a label are one region. Within a region, `usable` is the points whose run along
    the wind reaches `min_run_ft`; `hs_in` is the 75th percentile (nearest rank) of their wave
    heights, `run_ft` the lower median of their runs, and `point` the calmest of them.
    """
    b = wind_bin(wind_dir_deg)
    grouped: dict[int, list[tuple[float, float, int]]] = {}
    for i, p in enumerate(points):
        hs_m, _ = spm_wave(wind_kt, p.fetch_m[b], p.depth_m)
        grouped.setdefault(p.label, []).append((hs_m, p.run_ft[b % 8], i))

    out: list[Region] = []
    for label, group in grouped.items():
        usable = [x for x in group if x[1] >= min_run_ft]
        calmest = min(group, key=lambda x: (x[0], x[2]))
        anchor = points[calmest[2]]
        best = min(usable, key=lambda x: (x[0], x[2])) if usable else None
        out.append(
            Region(
                label=labels[label] if 0 <= label < len(labels) else str(label),
                hs_in=hs_inches(_percentile75([x[0] for x in usable])) if usable else None,
                run_ft=round(_median_low([x[1] for x in usable])) if usable else None,
                lat=anchor.lat,
                lon=anchor.lon,
                hs_all_in=hs_inches(_percentile75([x[0] for x in group])),
                point=None if best is None else best[2],
                n_points=len(group),
                n_usable=len(usable),
                run_all_ft=round(_median_low([x[1] for x in group])),
            )
        )
    out.sort(key=lambda r: (r.hs_in is None, r.hs_in or 0, r.label))
    return out


def best_region(rows: Sequence[Region]) -> Region | None:
    """The calmest region with a usable run, or `None` when the wind leaves none."""
    return next((r for r in rows if r.hs_in is not None), None)


def open_water_in(rows: Sequence[Region]) -> int | None:
    """The water body's open-water figure: the roughest region's `hs_all_in`."""
    return max((r.hs_all_in for r in rows), default=None)


def _percentile75(values: Sequence[float]) -> float:
    """75th percentile, nearest rank: sorted ascending, index `ceil(0.75 n) - 1`."""
    s = sorted(values)
    return s[max(0, math.ceil(0.75 * len(s)) - 1)]


def _median_low(values: Sequence[float]) -> float:
    s = sorted(values)
    return s[(len(s) - 1) // 2]
