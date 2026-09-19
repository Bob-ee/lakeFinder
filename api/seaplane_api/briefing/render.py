"""Template text for `summary` and `outlook.summary` (design 3.5). Strings only, no model.

House style, from CLAUDE.md and the design doc: the words are favorable / marginal / unfavorable
plus the limiting factor. Never "legal", never "safe", never "go". Numbers arrive already rounded
from the block and hour rows, so this module only formats and joins.
"""
from __future__ import annotations

from datetime import date, datetime

from .outlook import factor_phrase

_DAY_NAMES = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


def sentence_case(text: str) -> str:
    """Upper-case the first letter only. `str.capitalize()` would lower-case the G in `G14`."""
    return text[:1].upper() + text[1:]


def wind_phrase(wind: dict) -> str:
    """`250/9 G14`, or `calm` when there is nothing to report."""
    kt = wind.get("kt")
    if kt is None:
        return "wind unknown"
    if kt < 1:
        return "wind calm"
    gust = wind.get("gust")
    base = f"wind {int(wind.get('dir') or 0):03d}/{kt}"
    if gust and gust > kt:
        base += f" G{gust}"
    return base


def ceiling_phrase(ceiling_ft: int | None, known: bool) -> str:
    if not known:
        return "ceiling unknown"
    if ceiling_ft is None:
        return "no ceiling"
    return f"ceiling {ceiling_ft:,}"


def vis_phrase(vis_sm: int | None) -> str:
    return "vis unknown" if vis_sm is None else f"vis {vis_sm}"


def window_phrase(score: str, window: list[str] | None, when: str) -> str:
    if window:
        return f"{sentence_case(score)} {window[0]}–{window[1]} {when}."
    return f"{sentence_case(score)} {when}."


def lakes_phrase(rows: list[dict], limit: int = 4) -> str:
    """`Best water: <the calmest water on each, and where on it>.`

    Without a wave field a row reads as it always has -- `Cass Lake 6 in chop with 4,100 ft run into
    the wind; Orchard Lake 5 in`. With one it names the place, because that is the whole point:
    `Lake St. Clair, Big Muscamoot Bay 2 in (open lake 14 in); Cass Lake west end 3 in`.
    """
    if not rows:
        return "No candidate lakes."
    parts = []
    for i, row in enumerate(rows[:limit]):
        region = row.get("region")
        if row.get("frozen"):
            parts.append(f"{row['name']} likely frozen, verify")
        elif region and i == 0:
            parts.append(f"{row['name']}, {region} {row['hs_in']} in{_open_water_suffix(row)}")
        elif region:
            parts.append(f"{row['name']} {region} {row['hs_in']} in")
        elif i == 0:
            parts.append(f"{row['name']} {row['hs_in']} in chop with {row['run_ft']:,} ft run into the wind")
        else:
            parts.append(f"{row['name']} {row['hs_in']} in")
    return "Best water: " + "; ".join(parts) + "."


def home_water_phrase(home_water: dict | None, limit: int = 3) -> str | None:
    """`Lake St. Clair: Big Muscamoot Bay 2 in, Anchor Bay 5 in, open lake 14 in.`

    The calmest few regions with a usable run, then the open-water figure so the contrast between
    "where I would go" and "what the lake is doing" is in one sentence. `None` when there is no home
    water, or when it has no wave field and so has no regions to name.
    """
    if not home_water:
        return None
    usable = [r for r in home_water.get("regions") or [] if r.get("hs_in") is not None]
    bits = [f"{r['label']} {r['hs_in']} in" for r in usable[:limit]]
    open_in = home_water.get("hs_open_in")
    if open_in is not None and (not bits or open_in != usable[0].get("hs_in")):
        bits.append(f"open lake {open_in} in")
    if not bits:
        return None
    return f"{home_water['name']}: " + ", ".join(bits) + "."


def _open_water_suffix(row: dict) -> str:
    """` (open lake 14 in)` when the open water is rougher than the region being recommended."""
    open_in = row.get("hs_open_in")
    if open_in is None or open_in == row.get("hs_in"):
        return ""
    return f" (open lake {open_in} in)"


def summary(
    *,
    airport_id: str,
    today: dict | None,
    tomorrow: dict | None,
    first_block: dict | None,
    ceiling_known: bool,
    alerts: list[dict],
    lake_rows: list[dict],
) -> str:
    """The top-level `summary` line."""
    bits: list[str] = []
    if today is None:
        bits.append("No daylight blocks left today.")
    else:
        bits.append(window_phrase(today["score"], today.get("best_window"), "today"))
    if first_block:
        bits.append(
            f"{airport_id} {wind_phrase(first_block['wind'])}, "
            f"{ceiling_phrase(first_block.get('ceiling_ft'), ceiling_known)}, "
            f"{vis_phrase(first_block.get('vis_sm'))}."
        )
        if first_block.get("da_ft") is not None:
            bits.append(f"Density altitude {first_block['da_ft']:,} ft.")
    if alerts:
        bits.append("; ".join(f"{a['event']} in {a['area']}" for a in alerts[:2]) + ".")
    bits.append(lakes_phrase(lake_rows))
    if tomorrow is not None:
        tail = f"Tomorrow: {tomorrow['score']}"
        if tomorrow["score"] != "favorable":
            limiting = _worst_limiting(tomorrow)
            if limiting:
                tail += f", limited by {factor_phrase(limiting)}"
        elif tomorrow.get("best_window"):
            tail += f" {tomorrow['best_window'][0]}–{tomorrow['best_window'][1]}"
        bits.append(tail + ".")
    return " ".join(bits)


def outlook_summary(
    *,
    target: date,
    now_local: datetime,
    score: str,
    best_window: list[str] | None,
    limiting: str | None,
    watch: str | None,
    first_hour: dict | None,
    ceiling_known: bool,
    fog_until: str | None,
    lake_rows: list[dict],
    trend: str | None,
    previous_at: str | None,
    confidence: str,
    confidence_reasons: list[str],
    home_water: dict | None = None,
) -> str:
    """The `outlook.summary` line."""
    when = "Tomorrow morning" if target > now_local.date() else "This morning"
    head = f"{when} ({_DAY_NAMES[target.weekday()]}): {score}"
    if best_window:
        head += f" {best_window[0]}–{best_window[1]}"
    if score != "favorable" and limiting:
        head += f", limited by {factor_phrase(limiting)}"
    elif watch:
        head += f", watch {watch}"
    bits = [head + "."]
    if first_hour:
        bits.append(
            f"{sentence_case(wind_phrase(first_hour['wind']))}, "
            f"{ceiling_phrase(first_hour.get('ceiling_ft'), ceiling_known)}, "
            f"{vis_phrase(first_hour.get('vis_sm'))}."
        )
    if fog_until:
        bits.append(f"Fog risk until {fog_until}.")
    bits.append(lakes_phrase(lake_rows, limit=3))
    home = home_water_phrase(home_water)
    if home:
        bits.append(home)
    if trend:
        bits.append(f"{sentence_case(trend)}" + (f" since {previous_at}." if previous_at else "."))
    reason = f": {confidence_reasons[0]}" if confidence_reasons else ""
    bits.append(f"Confidence {confidence}{reason}.")
    return " ".join(bits)


def _worst_limiting(day: dict) -> str | None:
    for block in day.get("blocks", []):
        if block.get("score") == day.get("score") and block.get("limiting"):
            return block["limiting"]
    for block in day.get("blocks", []):
        if block.get("limiting"):
            return block["limiting"]
    return None
