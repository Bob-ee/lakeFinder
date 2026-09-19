"""Fetch-limited wave height on an inland lake (SPM 1984, deep water).

USACE Shore Protection Manual 1984, eqs. 3-33/3-34, as restated in `docs/briefing-design.md` 3.3:

    UA = 0.71 * U10**1.23                          # wind stress factor, U10 in m/s
    Hs = 1.6e-3 * UA**2 / g * sqrt(g * F / UA**2)   # == 5.108e-4 * UA * sqrt(F), metres
    Tp = 0.2857 * UA / g * (g * F / UA**2)**(1/3)   # seconds

capped at the fully developed sea. **Deviation from the design doc:** it quotes the fully developed
cap as `2.482e-2 * UA**2 / g`, which is an order of magnitude below SPM's own value and would cap the
doc's *own* worked example (20 kt over 5 km -> 0.45 m) at 0.39 m. SPM 1984 gives the fully developed
limits as `g*Hmo/UA**2 = 2.433e-1` and `g*Tm/UA = 8.134`, which is what is used here; it reproduces
both worked values and only binds past roughly 370 km of fetch, i.e. never on a Michigan inland lake.

Deep water overestimates on shallow lakes. That is the conservative direction, so it stands until a
depth source appears (design 5).

The "steep chop" rule in design 3.3 step 5 (unfavorable when Tp < 2 s and Hs > 6 in) is deliberately
NOT applied: the doc conditions it on Bobby confirming it matters for the SeaRey hull, and there is
no settings knob for it. `steep_chop()` is here so turning it on is one call.
"""
from __future__ import annotations

import math

G = 9.80665
KT_TO_MS = 0.5144444
M_TO_IN = 39.3700787
FT_TO_M = 0.3048

_FULLY_DEVELOPED_H = 2.433e-1  # g*Hmo/UA^2 at full development (SPM 1984)
_FULLY_DEVELOPED_T = 8.134  # g*Tm/UA at full development (SPM 1984)


def wind_stress_factor(wind_kt: float) -> float:
    """`UA` from a 10 m wind in knots."""
    u10 = max(0.0, wind_kt) * KT_TO_MS
    if u10 <= 0:
        return 0.0
    return 0.71 * u10**1.23


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


def wave_height_in(wind_kt: float, fetch_ft: float) -> float:
    """Significant wave height in inches for a fetch given in feet (what `lake_extents.json` holds)."""
    return significant_wave_height_m(wind_kt, fetch_ft * FT_TO_M) * M_TO_IN


def steep_chop(hs_in: float, tp_s: float) -> bool:
    """Design 3.3 step 5's optional steep-chop flag. Not wired into scoring; see the module docstring."""
    return tp_s < 2.0 and hs_in > 6.0
