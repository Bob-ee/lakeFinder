"""Public Land Survey System (PLSS) references inside DNR rule text.

The DNR writes the locus of a rule in one of two spellings, sometimes both in
one sentence:

    section 33, T3N, R8E, White Lake township, Oakland county
    section 16, town 2 north, range 8 east, Commerce township, Oakland county

`parse_plss` returns one entry per township/range pair, with the sections that
belong to it:

    [{"township": "4N", "range": "7E", "sections": [16]}]

Quarter-section fragments ("the north 1/2 of the northwest 1/4 of the southwest
1/4 of section 35") are ignored; only the section number is kept. Entries with
a township/range but no cited section come back with `sections: []`.
"""

from __future__ import annotations

import re
from typing import TypedDict


class PlssEntry(TypedDict):
    township: str
    range: str
    sections: list[int]


_SECTION_RE = re.compile(
    r"\bsections?\s+(\d{1,3}(?:\s*(?:,|,?\s*and|&)\s*\d{1,3})*)",
    re.IGNORECASE,
)
_TOWN_ABBR_RE = re.compile(r"\bT\s?(\d{1,3})\s?([NS])\b")
_RANGE_ABBR_RE = re.compile(r"\bR\s?(\d{1,3})\s?([EW])\b")
_TOWN_LONG_RE = re.compile(r"\btown(?:ship)?\s+(\d{1,3})\s+(north|south)\b", re.IGNORECASE)
_RANGE_LONG_RE = re.compile(r"\brange\s+(\d{1,3})\s+(east|west)\b", re.IGNORECASE)
_NUM_RE = re.compile(r"\d{1,3}")


def _tokens(text: str) -> list[tuple[int, str, object]]:
    out: list[tuple[int, str, object]] = []
    for m in _SECTION_RE.finditer(text):
        nums = [int(n) for n in _NUM_RE.findall(m.group(1))]
        nums = [n for n in nums if 1 <= n <= 36]
        if nums:
            out.append((m.start(), "sections", nums))
    for m in _TOWN_ABBR_RE.finditer(text):
        out.append((m.start(), "town", f"{int(m.group(1))}{m.group(2).upper()}"))
    for m in _TOWN_LONG_RE.finditer(text):
        out.append((m.start(), "town", f"{int(m.group(1))}{m.group(2)[0].upper()}"))
    for m in _RANGE_ABBR_RE.finditer(text):
        out.append((m.start(), "range", f"{int(m.group(1))}{m.group(2).upper()}"))
    for m in _RANGE_LONG_RE.finditer(text):
        out.append((m.start(), "range", f"{int(m.group(1))}{m.group(2)[0].upper()}"))
    out.sort(key=lambda t: t[0])
    # A long-form match ("town 4 north") and an abbreviated match cannot overlap,
    # but "T4N" inside "town 4 north" cannot happen either; positions are unique.
    return out


def parse_plss(text: str) -> list[PlssEntry]:
    """Extract every township/range group and its sections from `text`."""
    if not text:
        return []
    entries: list[PlssEntry] = []
    pending: list[int] = []
    town: str | None = None
    rng: str | None = None

    def flush() -> None:
        nonlocal town, rng, pending
        if town and rng:
            entries.append({"township": town, "range": rng, "sections": sorted(set(pending))})
            pending = []
        town = rng = None

    for _pos, kind, value in _tokens(text):
        if kind == "sections":
            assert isinstance(value, list)
            pending.extend(value)
        elif kind == "town":
            if town is not None:
                flush()
            town = str(value)
            if rng is not None:
                flush()
        else:
            if rng is not None:
                flush()
            rng = str(value)
            if town is not None:
                flush()
    flush()
    if pending and entries:
        entries[-1]["sections"] = sorted(set(entries[-1]["sections"]) | set(pending))
    return entries


def format_plss(entry: PlssEntry) -> str:
    """"T4N R7E sections 16, 21" - for review output."""
    sections = ", ".join(str(s) for s in entry["sections"])
    base = f"T{entry['township']} R{entry['range']}"
    return f"{base} sections {sections}" if sections else base
