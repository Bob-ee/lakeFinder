"""Reading a decoded aviationweather.gov TAF (`/api/data/taf?format=json`) for one clock hour.

The JSON is already decoded, so no raw-TAF parsing is needed. `fcsts` is a flat list of periods:

    {"timeFrom": epoch, "timeTo": epoch, "fcstChange": null|"FM"|"BECMG"|"TEMPO"|"PROB",
     "probability": null|30|40, "wdir": 60, "wspd": 5, "wgst": null, "visib": "6+"|5|"1/2",
     "clouds": [{"cover": "OVC", "base": 6000}], "wxString": "-SHRA BR", "vertVis": null}

Prevailing groups (`null`, `FM`, `BECMG`) replace each other in time order; `TEMPO` and `PROB`
groups overlay them. The briefing takes **the worst of the prevailing group and any TEMPO/PROB group
covering the hour** -- a PROB30 of 1/2 SM in fog is exactly the thing that ruins a morning, so it is
not averaged away. That is the conservative direction and matches design 3.4.

A ceiling is the lowest BKN/OVC/OVX base, or the vertical visibility when the sky is obscured. No
BKN/OVC layer means there is genuinely no ceiling, which is different from "nobody told us".
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

_CEILING_COVERS = frozenset({"BKN", "OVC", "OVX"})
_FOG_TOKENS = ("FG", "FZFG", "MIFG", "BCFG", "PRFG")
_OVERLAY_CHANGES = frozenset({"TEMPO", "PROB"})


@dataclass(frozen=True)
class TafHour:
    """What the TAF says for one hour. `ceiling_ft is None` means the TAF reports no ceiling."""

    ceiling_ft: float | None
    vis_sm: float | None
    fog: bool
    wind_dir_deg: float | None
    wind_kt: float | None
    gust_kt: float | None


def parse_visibility(value) -> float | None:
    """`"6+"` -> 6.0, `"1 1/2"` -> 1.5, `"1/2"` -> 0.5, numbers pass through."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip().upper().replace("+", "").replace("SM", "").strip()
    if s.startswith(("P", "M")):  # P6SM / M1/4SM: "more than" / "less than"
        s = s[1:].strip()
    if not s:
        return None
    total = 0.0
    for part in s.split():
        if "/" in part:
            num, _, den = part.partition("/")
            try:
                total += float(num) / float(den)
            except (ValueError, ZeroDivisionError):
                return None
        else:
            try:
                total += float(part)
            except ValueError:
                return None
    return total


def _ceiling(period: dict) -> float | None:
    bases = [
        c["base"]
        for c in (period.get("clouds") or [])
        if c.get("cover") in _CEILING_COVERS and c.get("base") is not None
    ]
    vert = period.get("vertVis")
    if vert is not None:
        bases.append(float(vert))
    return float(min(bases)) if bases else None


def _has_fog(period: dict) -> bool:
    wx = (period.get("wxString") or "").upper()
    return any(tok in wx.split() or tok in wx for tok in _FOG_TOKENS)


def _covers(period: dict, epoch: float) -> bool:
    start, end = period.get("timeFrom"), period.get("timeTo")
    if start is None or end is None:
        return False
    return start <= epoch < end


def taf_for_hour(taf: dict | None, hour_start: datetime) -> TafHour | None:
    """The TAF's picture of the hour beginning at `hour_start` (aware datetime), or `None`.

    `None` means the TAF does not cover the hour at all -- the caller then falls back to the model
    and records that the ceiling is unknown.
    """
    if not taf:
        return None
    periods = taf.get("fcsts") or []
    # Score the middle of the hour: a FM group at :00 should own the whole hour.
    epoch = hour_start.timestamp() + 1800

    prevailing = None
    overlays: list[dict] = []
    for p in periods:
        if not _covers(p, epoch):
            continue
        if (p.get("fcstChange") or "") in _OVERLAY_CHANGES or p.get("probability") is not None:
            overlays.append(p)
        else:
            prevailing = p  # later groups win; `fcsts` is in time order
    if prevailing is None and not overlays:
        return None

    base = prevailing or overlays[0]
    ceiling = _ceiling(base)
    vis = parse_visibility(base.get("visib"))
    fog = _has_fog(base)
    wdir, wspd, wgst = base.get("wdir"), base.get("wspd"), base.get("wgst")

    for p in overlays:
        c = _ceiling(p)
        if c is not None and (ceiling is None or c < ceiling):
            ceiling = c
        v = parse_visibility(p.get("visib"))
        if v is not None and (vis is None or v < vis):
            vis = v
        fog = fog or _has_fog(p)
        if p.get("wspd") is not None and (wspd is None or p["wspd"] > wspd):
            wdir, wspd = p.get("wdir", wdir), p["wspd"]
        if p.get("wgst") is not None and (wgst is None or p["wgst"] > wgst):
            wgst = p["wgst"]

    return TafHour(
        ceiling_ft=ceiling,
        vis_sm=vis,
        fog=fog,
        wind_dir_deg=None if wdir is None else float(wdir),
        wind_kt=None if wspd is None else float(wspd),
        gust_kt=None if wgst is None else float(wgst),
    )


def covers_window(taf: dict | None, start: datetime, end: datetime) -> bool:
    """True when the TAF's valid period spans the whole window."""
    if not taf:
        return False
    vf, vt = taf.get("validTimeFrom"), taf.get("validTimeTo")
    if vf is None or vt is None:
        return False
    return vf <= start.timestamp() and vt >= end.timestamp()
