"""Stage 2: turn cached DNR county pages into `restrictions.jsonl`.

Two passes, per docs/design.md section 5:

1. A deterministic pass (this module) that segments each page into entries,
   splits an entry into its `(a)`/`(b)`/`(c)` clauses, and classifies each
   clause against the template catalog in docs/dnr-pages.md section 4.
2. An optional LLM pass (`llm_extract`) over whatever pass 1 left as
   `restriction_type: other, needs_review: true`.

Structure notes that drive the segmentation (docs/dnr-pages.md section 3):

* Content lives in `div.field-content`; everything else on the page is chrome.
* A record starts at a block whose text is mostly inside `<strong>` **or**
  `<b>`; the tag varies by county, and the block is usually `<p>` but is a
  `<div>` at least once (Oakland, Orchard Lake).
* The 7 counties with no controls render a placeholder sentence and must yield
  zero records without raising.

Contract deviations, stated explicitly:

* `restriction_id` follows the contract formula for entries that produce a
  single record. When one entry decomposes into several records they would all
  hash to the same id (same county/lake/township/raw_text), so the clause label
  - or the restriction type when the clauses are unlabelled - is appended to
  the hashed key. See `restriction.compute_restriction_id`.
* `Restriction.lake_name_group` (the verbatim header a multi-lake record was
  split out of) exists on the model for the review queue but is not written to
  `restrictions.jsonl`.
* `lake_ids` is not written by this stage; the contract marks it build-stage.
"""

from __future__ import annotations

import json
import logging
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from bs4 import BeautifulSoup, Tag

from . import dnr_fetch
from .config import Config
from .names import descriptor_segments, normalize_name, split_multi_lake_names
from .plss import parse_plss
from .restriction import Hours, Plss, Restriction, Season

log = logging.getLogger(__name__)

BLOCK_TAGS = ["p", "div", "h1", "h2", "h3", "h4", "h5", "h6", "li", "td", "th", "blockquote"]
BOLD_TAGS = ["strong", "b"]

NO_CONTROLS_RE = re.compile(r"No Special Local Watercraft Controls", re.IGNORECASE)
PAGE_HEADING_RE = re.compile(r"SPECIAL LOCAL WATERCRAFT CONTROLS", re.IGNORECASE)

RULE_R_RE = re.compile(r"\bR\s?(\d{3})\.(\d+)\.(\d+)")
RULE_WC_RE = re.compile(r"\bWC\s*-?\s*(\d{1,3})\s*-\s*(\d{1,3})\s*-\s*(\d{1,4})")

HISTORY_RE = re.compile(r"^\s*History\b", re.IGNORECASE)
PUBLISHER_RE = re.compile(r"^\s*Publisher'?s?\s+Note", re.IGNORECASE)
RESCINDED_RE = re.compile(r"\bRescinded\b", re.IGNORECASE)
SIGNAGE_RE = re.compile(
    r"marked with signs|only enforceable when properly marked|placed as provided in a permit", re.IGNORECASE
)
DEFINITION_RE = re.compile(
    r"^\s*[\"“]?(?:slow-?-?\s?no wake speed|high[- ]speed boating)[\"”]?\s+"
    r"(?:means|is defined)|^\s*For the purpose of this ordinance",
    re.IGNORECASE,
)
DST_RE = re.compile(
    r"(?:The hours should be|The hours shall be|Hours are)[^\n]*?"
    r"Daylight\s+Saving[s]?\s+Time\s+is\s+in\s+effect\.",
    re.IGNORECASE,
)
SEE_RE = re.compile(r"\(([^)]*\bSee\b[^)]*)\)", re.IGNORECASE)

_CLAUSE_MARKER_RE = re.compile(r"(?m)(?:^|(?<=:)\s*)\(([a-z]{1,4}|\d{1,2})\)\s+")
_NUM_MARKER_RE = re.compile(r"(?m)^(\d{1,2})\.\s+(?=[A-Z\"“'])")
_RULE_NUMBER_PREFIX_RE = re.compile(r"^\s*(?:Rule\s+)?\d{1,3}\.\s*")

ALPHABET = "abcdefghijklmnopqrstuvwxyz"

COUNTY_SLUG_NAMES = {
    "grandtraverse": "Grand Traverse",
    "presqueisle": "Presque Isle",
    "stclair": "St. Clair",
    "stjoseph": "St. Joseph",
    "vanburen": "Van Buren",
}


# ---------------------------------------------------------------------------
# HTML -> blocks -> entries
# ---------------------------------------------------------------------------


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("\xa0", " ")).strip()


@dataclass
class Block:
    text: str
    is_header: bool


@dataclass
class Entry:
    header: str
    rule_id: str | None
    title: str
    lake_names: list[str]
    lake_name_group: str
    paragraphs: list[str] = field(default_factory=list)
    raw_text: str = ""


def field_content(html: str) -> Tag | None:
    """The `div.field-content` node holding the county's rule text."""
    soup = BeautifulSoup(html, "lxml")
    nodes = soup.select("div.field-content")
    if not nodes:
        return None
    return max(nodes, key=lambda n: len(n.get_text(" ", strip=True)))


def iter_blocks(node: Tag) -> list[Block]:
    """Flatten the WYSIWYG dump into leaf block elements, flagging bold headers."""
    blocks: list[Block] = []
    for el in node.find_all(BLOCK_TAGS):
        if el.find(BLOCK_TAGS):
            continue  # container, not a leaf
        text = _norm(el.get_text(" ", strip=False))
        if not text:
            continue
        bold = _norm(" ".join(b.get_text(" ", strip=False) for b in el.find_all(BOLD_TAGS)))
        blocks.append(Block(text=text, is_header=bool(bold) and len(bold) >= 0.5 * len(text)))
    return blocks


def parse_rule_id(text: str) -> tuple[str | None, int, int]:
    """First rule number in `text`, normalized, with its span."""
    m = RULE_R_RE.search(text)
    if m:
        return f"R {m.group(1)}.{m.group(2)}.{m.group(3)}", m.start(), m.end()
    m = RULE_WC_RE.search(text)
    if m:
        return f"WC-{m.group(1)}-{m.group(2)}-{m.group(3)}", m.start(), m.end()
    return None, -1, -1


def find_rule_ids(text: str) -> list[str]:
    out: list[str] = []
    for m in RULE_R_RE.finditer(text):
        out.append(f"R {m.group(1)}.{m.group(2)}.{m.group(3)}")
    for m in RULE_WC_RE.finditer(text):
        out.append(f"WC-{m.group(1)}-{m.group(2)}-{m.group(3)}")
    return out


def _lake_from_publisher_note(text: str) -> str | None:
    """Rescinded entries hide the lake name in a "Publisher's Note" sentence."""
    m = re.search(r"\b((?:[A-Z][\w'.-]+\s+)+?[Ll]ake)\b", text)
    if m:
        return _norm(m.group(1))
    m = re.search(r"\b[Ll]ake\s+([A-Z][\w'.-]+)", text)
    if m:
        return f"Lake {m.group(1)}"
    return None


def segment_entries(blocks: list[Block]) -> list[Entry]:
    entries: list[Entry] = []
    current: Entry | None = None
    for block in blocks:
        if block.is_header:
            if PAGE_HEADING_RE.search(block.text) and parse_rule_id(block.text)[0] is None:
                current = None
                continue
            rule_id, start, end = parse_rule_id(block.text)
            if rule_id:
                lake_part = block.text[:start].strip(" -–—.\t")
                title = block.text[end:].strip(" -–—.\t")
            else:
                lake_part, title = block.text.strip(" -–—.\t"), ""
            if not lake_part and not RESCINDED_RE.search(block.text):
                # header is only a rule number; fall back to the whole header
                lake_part = block.text
            current = Entry(
                header=block.text,
                rule_id=rule_id,
                title=title,
                lake_names=split_multi_lake_names(lake_part) if lake_part else [],
                lake_name_group=lake_part or block.text,
            )
            entries.append(current)
        elif current is not None:
            current.paragraphs.append(block.text)
    for entry in entries:
        entry.raw_text = "\n".join([entry.header, *entry.paragraphs])
    return entries


# ---------------------------------------------------------------------------
# Clause splitting
# ---------------------------------------------------------------------------


@dataclass
class Clause:
    label: str | None
    text: str


def _is_next_label(label: str, index: int, kind: str) -> bool:
    if kind == "num":
        return label == str(index + 1)
    return index < len(ALPHABET) and label == ALPHABET[index]


def split_clauses(paragraphs: list[str]) -> tuple[str, list[Clause]]:
    """Split an entry body into a shared preamble and its top-level clauses.

    Sub-clauses nested one level down ("(a) ... shall not: (i) ... (ii) ...")
    stay inside their parent clause's text, because the pair they form is one
    legal restriction, not two.
    """
    if not paragraphs:
        return "", []
    head = _RULE_NUMBER_PREFIX_RE.sub("", paragraphs[0], count=1)
    body = "\n".join([head, *paragraphs[1:]])

    markers: list[tuple[int, int, str]] = [
        (m.start(1) - 1, m.end(), m.group(1)) for m in _CLAUSE_MARKER_RE.finditer(body)
    ]
    markers += [(m.start(), m.end(), m.group(1)) for m in _NUM_MARKER_RE.finditer(body)]
    markers.sort()
    if not markers:
        return "", [Clause(label=None, text=body.strip())]

    preamble = body[: markers[0][0]].strip()
    kind = "num" if markers[0][2].isdigit() else "alpha"
    clauses: list[Clause] = []
    index = 0
    for i, (start, end, label) in enumerate(markers):
        stop = markers[i + 1][0] if i + 1 < len(markers) else len(body)
        text = body[end:stop].strip()
        top = not clauses or _is_next_label(label, index, kind)
        if top:
            clauses.append(Clause(label=f"({label})", text=text))
            if label.isdigit():
                index = int(label)
            elif label in ALPHABET:
                index = ALPHABET.index(label) + 1
            else:
                index += 1
        else:
            clauses[-1].text = f"{clauses[-1].text} ({label}) {text}".strip()
    return preamble, clauses


# ---------------------------------------------------------------------------
# Field extraction
# ---------------------------------------------------------------------------

_TIME = r"\d{1,2}:\d{2}\s*[ap]\.?\s?m\.?"
HOURS_RE = re.compile(
    r"(?P<except>except\s+)?"
    r"(?:between\s+the\s+hours\s+of|during\s+the\s+period\s+(?:from|of)|between\s+the\s+hours|between|from)\s+"
    rf"(?P<start>{_TIME})\s*(?:and|to|until)\s*(?P<end>{_TIME})"
    r"(?P<following>\s*,?\s*of\s+the\s+following\s+day)?",
    re.IGNORECASE,
)
SUNSET_RE = re.compile(
    r"(?:during\s+the\s+period\s+of\s+)?(\d+\s+hours?\s+after\s+sunset\s+to\s+\d+\s+hours?\s+before\s+sunrise)",
    re.IGNORECASE,
)
DAYS_RE = re.compile(
    r"(?:(?:Sun|Mon|Tues?|Wednes|Thurs?|Fri|Satur)days?|[Hh]olidays?|Memorial\s+Day|"
    r"Independence\s+Day|Labor\s+Day|New\s+Year'?s?\s+Day)"
    r"(?:\s*,\s*and\s+|\s*,\s*|\s+and\s+)?",
    re.IGNORECASE,
)
DAYS_ANCHOR_RE = re.compile(r"(?:sun|mon|tues|wednes|thurs|fri|satur)days|holidays?", re.IGNORECASE)

MONTHS = [
    "january",
    "february",
    "march",
    "april",
    "may",
    "june",
    "july",
    "august",
    "september",
    "october",
    "november",
    "december",
]
MONTH_DAYS = [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
SEASON_RE = re.compile(
    r"during\s+(?:the\s+months?\s+of\s+)?"
    r"((?:" + "|".join(MONTHS) + r")(?:\s*,\s*and\s+|\s*,\s*|\s+and\s+|\s*,?\s*)?)+",
    re.IGNORECASE,
)
HOLIDAY_SEASON_RE = re.compile(
    r"(from\s+)?Memorial\s+Day\s+(?:through|to|until)\s+Labor\s+Day", re.IGNORECASE
)
SPEED_RE = re.compile(r"(\d{1,3}(?:\.\d+)?)\s*(?:statute\s+)?miles?\s+per\s+hour", re.IGNORECASE)

TOWNSHIP_OF_RE = re.compile(r"(?:charter\s+)?[Tt]ownship of ([A-Z][\w'.-]*(?:\s+[A-Z][\w'.-]*)*)")
TOWNSHIP_NAME_RE = re.compile(r"\b([A-Z][\w'.-]*(?:\s+[A-Z][\w'.-]*)*)\s+(?:charter\s+)?township\b")
CITY_RE = re.compile(r"\b([Cc]ity|[Vv]illage) of ([A-Z][\w'.-]*(?:\s+[A-Z][\w'.-]*)*)")

ZONE_TRIGGERS = [
    r"north(?:erly|east|west)?(?:ly)?\s+(?:of|from)\b",
    r"south(?:erly|east|west)?(?:ly)?\s+(?:of|from)\b",
    r"east(?:erly)?\s+(?:of|from)\b",
    r"west(?:erly)?\s+(?:of|from)\b",
    r"\bbeginning\s+(?:at|where)\b",
    r"\benclosed by a line\b",
    r"\bbetween a line\b",
    r"\bbetween day beacons\b",
    r"\bfor a distance of\s+\d+\s+feet\b",
    r"\bthat\s+(?:part|portion)\s+of\b",
    r"\bthose\s+portions\s+of\b",
    r"\bon\s+that\s+part\b",
    r"\bin\s+the\s+(?:northerly|southerly|easterly|westerly|north|south|east|west)\b[^,]{0,40}\btip\b",
    r"\b\d\s*/\s*\d\s+of\s+the\b",
    r"\bknown\s+(?:locally\s+)?as\b",
    r"\bbays?\b",
    r"\blying\s+(?:west|east|north|south)\b",
    r"\bupstream\b",
    r"\bdownstream\b",
    r"\badjacent\s+to\b",
    r"\bfrom\s+the\s+mouth\b",
    r"\bbasin\b",
]
ZONE_RE = re.compile("|".join(ZONE_TRIGGERS), re.IGNORECASE)
ZONE_STOP_RE = re.compile(
    r",\s*(?:it\s+is\s+unlawful|it\s+shall\s+be\s+unlawful|an?\s+operator|no\s+operator|"
    r"the\s+following\s+controls|persons\s+operating)",
    re.IGNORECASE,
)
#: Header descriptor words that name a *part* of the lake (so: a zone) rather
#: than a connected structure such as a canal (so: its own little waterbody).
PART_DESCRIPTOR_WORDS = frozenset(
    {
        "bay",
        "bays",
        "basin",
        "part",
        "parts",
        "portion",
        "portions",
        "tip",
        "section",
        "sections",
        "township",
        "townhip",
        "twsp",
        "city",
        "village",
        "adjoining",
        "access",
        "site",
        "arm",
        "north",
        "south",
        "east",
        "west",
        "northeast",
        "northwest",
        "southeast",
        "southwest",
        "northerly",
        "southerly",
        "easterly",
        "westerly",
        "upper",
        "lower",
        "middle",
    }
)


PLSS_WORDS_RE = re.compile(
    r"\b(?:town|range)\s+\d{1,3}\s+(?:north|south|east|west)\b|\bT\s?\d{1,3}\s?[NS]\b|\bR\s?\d{1,3}\s?[EW]\b",
    re.IGNORECASE,
)


def strip_cross_references(text: str) -> str:
    """Drop "(See R281.747.1 for ...)" notes; they describe the *other* county."""
    return SEE_RE.sub(" ", text)


def _to_24h(raw: str) -> str | None:
    m = re.match(r"(\d{1,2}):(\d{2})\s*([ap])", raw.strip(), re.IGNORECASE)
    if not m:
        return None
    hour, minute, ampm = int(m.group(1)), int(m.group(2)), m.group(3).lower()
    if hour == 12:
        hour = 0
    if ampm == "p":
        hour += 12
    return f"{hour:02d}:{minute:02d}"


def extract_hours(text: str, dst_sentence: str | None = None) -> Hours | None:
    m = HOURS_RE.search(text)
    if m:
        start_raw = _norm(m.group("start"))
        end_raw = _norm(m.group("end"))
        label = f"{start_raw} to {end_raw}"
        if m.group("following"):
            label += " of the following day"
        if m.group("except"):
            label = f"except {label}"
        if dst_sentence:
            label = f"{label} ({_norm(dst_sentence)})"
        return Hours(text=label, start=_to_24h(start_raw), end=_to_24h(end_raw), days=extract_days(text))
    m = SUNSET_RE.search(text)
    if m:
        return Hours(text=_norm(m.group(1)), start=None, end=None, days=extract_days(text))
    return None


def extract_days(text: str) -> str | None:
    best: str | None = None
    for m in re.finditer(r"(?:" + DAYS_RE.pattern + r")+", text, re.IGNORECASE):
        candidate = _norm(m.group(0)).strip(" ,")
        candidate = re.sub(r"\s*,?\s*and\s*$", "", candidate)
        if not DAYS_ANCHOR_RE.search(candidate):
            continue
        if best is None or len(candidate) > len(best):
            best = candidate
    return best


def extract_season(text: str) -> Season | None:
    m = HOLIDAY_SEASON_RE.search(text)
    if m:
        return Season(text=_norm(m.group(0)), start="05-25", end="09-07")
    m = SEASON_RE.search(text)
    if m:
        names = [w.lower() for w in re.findall("|".join(MONTHS), m.group(0), re.IGNORECASE)]
        if not names:
            return None
        first, last = MONTHS.index(names[0]), MONTHS.index(names[-1])
        return Season(
            text=_norm(m.group(0)).strip(" ,"),
            start=f"{first + 1:02d}-01",
            end=f"{last + 1:02d}-{MONTH_DAYS[last]:02d}",
        )
    return None


def extract_speed(text: str) -> float | None:
    m = SPEED_RE.search(text)
    if not m:
        return None
    value = float(m.group(1))
    return int(value) if value.is_integer() else value


def extract_townships(text: str) -> str | None:
    """Every township / city / village named in the locus, in document order."""
    found: list[tuple[int, str]] = []
    for m in TOWNSHIP_OF_RE.finditer(text):
        found.append((m.start(), f"{_norm(m.group(1))} Township"))
    for m in TOWNSHIP_NAME_RE.finditer(text):
        found.append((m.start(), f"{_norm(m.group(1))} Township"))
    for m in CITY_RE.finditer(text):
        found.append((m.start(), f"{m.group(1).title()} of {_norm(m.group(2))}"))
    out: list[str] = []
    for _pos, name in sorted(found):
        if name not in out:
            out.append(name)
    return ", ".join(out) if out else None


def _part_descriptors(header: str) -> list[str]:
    out = []
    for seg in descriptor_segments(header):
        words = {w.lower() for w in re.findall(r"[A-Za-z]+", seg)}
        if words & PART_DESCRIPTOR_WORDS:
            out.append(seg)
    return out


def _paren_depth(text: str, index: int) -> int:
    return text.count("(", 0, index) - text.count(")", 0, index)


def _zone_description(text: str) -> str | None:
    """The sub-area phrase in `text`, or None when it describes the whole lake."""
    clean = PLSS_WORDS_RE.sub(" ", strip_cross_references(text))
    for m in ZONE_RE.finditer(clean):
        if _paren_depth(clean, m.start()) > 0:
            continue  # inside an aside, not the operative description
        start = m.start()
        back = clean.rfind(",", 0, start)
        back = 0 if back < 0 else back + 1
        if start - back <= 30:
            start = back
        tail = clean[start:]
        stop = ZONE_STOP_RE.search(tail)
        description = _norm(tail[: stop.start()] if stop else tail)
        description = re.sub(r"(?:,\s*){2,}", ", ", description).strip(" .;,")
        if len(description) > 400:
            description = description[:397].rstrip() + "..."
        if description:
            return description
    return None


def detect_zone(*texts: str, header: str = "") -> tuple[str, str | None]:
    """`("zone", description)` when a text describes a sub-area, else lakewide.

    `texts` are tried in priority order, so a clause's own boundary description
    wins over the entry preamble's.
    """
    for text in texts:
        if not text:
            continue
        description = _zone_description(text)
        if description:
            return "zone", description
    parts = _part_descriptors(header)
    if parts:
        return "zone", ", ".join(parts)
    return "lakewide", None


# ---------------------------------------------------------------------------
# Clause classification
# ---------------------------------------------------------------------------

ELECTRIC_ONLY_RE = re.compile(
    r"except\s+an\s+electric\s+motor|other\s+tha[nt]\s+an\s+electric|electric\s+motors?\s+only|"
    r"except\s+electric\s+motors?",
    re.IGNORECASE,
)
MOTORBOAT_BAN_RE = re.compile(
    r"unlawful\s+to\s+operate\s+a\s+motorboat|motorboats?\s+(?:are|is)\s+prohibited|"
    r"no\s+motorboats?\s+(?:shall|may)\s+be\s+operated|prohibition\s+of\s+motorboats",
    re.IGNORECASE,
)
NO_VESSELS_RE = re.compile(
    r"boating\s+is\s+prohibited|no\s+vessel\s+shall\s+operate|vessels?\s+(?:are|is)\s+prohibited|"
    r"prohibition\s+of\s+vessels",
    re.IGNORECASE,
)
HIGH_SPEED_RE = re.compile(r"high[\s-]?speed|plan[in]{1,3}g\s+condition", re.IGNORECASE)
SLOW_NO_WAKE_RE = re.compile(r"slow-?-?\s?no[\s-]?wake", re.IGNORECASE)
TOWING_RE = re.compile(
    r"have\s+in\s+tow|assist(?:ing)?\s+in\s+the\s+propulsion|water\s*ski|water\s*sled|to\s+tow\b", re.IGNORECASE
)
SHORE_BUFFER_RE = re.compile(
    r"within\s+100\s+feet\s+of\s+any\s+shore|"
    r"maintain\s+a\s+distance\s+of\s+100\s+feet\s+from\s+the\s+shoreline",
    re.IGNORECASE,
)
AIRBOAT_RE = re.compile(r"\bairboat", re.IGNORECASE)
RAFT_RE = re.compile(r"rubber\s+rafts?|inflatable\s+raft|flotation|floating\s+device", re.IGNORECASE)
MOORING_RE = re.compile(
    r"moored,\s+docked,?\s+or\s+anchored|unlawful\s+to\s+anchor\s+or\s+moor|obstruct\s+or\s+restrict\s+the\s+passage",
    re.IGNORECASE,
)
HEADCOUNT_RE = re.compile(r"more\s+than\s+\d+\s+persons?\s+at\s+\d+\s+time", re.IGNORECASE)
SWIM_AREA_RE = re.compile(
    r"areas?\s+prohibited\s+to\s+boating|swimming,?\s+bathing\s+or\s+wading|"
    r"swim\s+areas?|swimming\s+areas?",
    re.IGNORECASE,
)
PWC_RE = re.compile(r"personal\s+watercraft|\bPWC\b|jet\s?ski", re.IGNORECASE)


@dataclass
class ClauseResult:
    restriction_type: str
    speed_mph: float | None = None
    needs_review: bool = False


def classify_clause(text: str) -> list[ClauseResult]:
    """Classify one clause (preamble + clause text) into restriction records.

    Follows "Parsing rules learned from the corpus" in docs/data-contract.md:
    electric-motor-only is `no_motorboats` (plus a `speed_limit` record when the
    same clause caps speed); a high-speed clause that also bans towing is one
    record; a towing-only clause is `no_towing`; the statewide 100-ft echo is
    `shore_buffer`; airboat / mooring / raft / headcount clauses are
    `not_applicable`.
    """
    has_hours = HOURS_RE.search(text) is not None or SUNSET_RE.search(text) is not None

    if SHORE_BUFFER_RE.search(text):
        return [ClauseResult("shore_buffer")]
    if AIRBOAT_RE.search(text) or RAFT_RE.search(text) or MOORING_RE.search(text):
        return [ClauseResult("not_applicable")]
    if HEADCOUNT_RE.search(text):
        return [ClauseResult("not_applicable")]
    if SWIM_AREA_RE.search(text) and not SLOW_NO_WAKE_RE.search(text) and not HIGH_SPEED_RE.search(text):
        return [ClauseResult("not_applicable")]
    if PWC_RE.search(text):
        return [ClauseResult("no_pwc", needs_review=True)]

    speed = extract_speed(text)

    if ELECTRIC_ONLY_RE.search(text):
        out = [ClauseResult("no_motorboats")]
        if speed is not None:
            out.append(ClauseResult("speed_limit", speed_mph=speed))
        return out
    if speed is not None:
        out = [ClauseResult("speed_limit", speed_mph=speed)]
        if MOTORBOAT_BAN_RE.search(text):
            out.insert(0, ClauseResult("no_motorboats"))
        return out
    if MOTORBOAT_BAN_RE.search(text):
        return [ClauseResult("no_motorboats")]
    if NO_VESSELS_RE.search(text):
        return [ClauseResult("no_vessels")]
    if HIGH_SPEED_RE.search(text):
        return [ClauseResult("high_speed_hours" if has_hours else "no_high_speed")]
    if SLOW_NO_WAKE_RE.search(text):
        return [ClauseResult("slow_no_wake")]
    if TOWING_RE.search(text):
        return [ClauseResult("no_towing")]
    return [ClauseResult("other", needs_review=True)]


# ---------------------------------------------------------------------------
# Entry -> records
# ---------------------------------------------------------------------------


def _paragraph_kind(text: str) -> str:
    if HISTORY_RE.match(text):
        return "history"
    if PUBLISHER_RE.match(text):
        return "publisher"
    if DEFINITION_RE.search(text):
        return "definition"
    if DST_RE.search(text) and not SLOW_NO_WAKE_RE.search(text):
        return "dst"
    if SIGNAGE_RE.search(text):
        return "signage"
    return "clause"


def records_for_entry(
    entry: Entry, county: str, source_url: str, fetched_at: str
) -> list[Restriction]:
    kinds = [(_paragraph_kind(p), p) for p in entry.paragraphs]
    clause_paragraphs = [p for kind, p in kinds if kind == "clause"]
    signage = any(kind == "signage" for kind, _ in kinds) or bool(
        SIGNAGE_RE.search(entry.raw_text)
    )
    dst_match = DST_RE.search(entry.raw_text)
    dst_sentence = _norm(dst_match.group(0)) if dst_match else None
    publisher = " ".join(p for kind, p in kinds if kind == "publisher")

    rescinded = bool(RESCINDED_RE.search(entry.header)) or any(
        RESCINDED_RE.match(p) for p in entry.paragraphs
    )

    lake_names = list(entry.lake_names)
    if rescinded and (not lake_names or not any(re.search(r"[A-Za-z]", n) for n in lake_names)):
        guess = _lake_from_publisher_note(publisher or entry.raw_text)
        lake_names = split_multi_lake_names(guess) if guess else []
    if not lake_names:
        lake_names = [entry.lake_name_group or "(unnamed)"]

    body = strip_cross_references(" ".join(clause_paragraphs) or entry.raw_text)
    townships = extract_townships(body) or extract_townships(strip_cross_references(entry.raw_text))
    seen_plss: list[dict[str, Any]] = []
    for item in parse_plss(body):
        if item not in seen_plss:
            seen_plss.append(dict(item))
    plss = [Plss(**p) for p in seen_plss]
    related = []
    for m in SEE_RE.finditer(entry.raw_text):
        for rid in find_rule_ids(m.group(1)):
            if rid != entry.rule_id and rid not in related:
                related.append(rid)

    records: list[Restriction] = []

    if rescinded:
        for name in lake_names:
            records.append(
                Restriction(
                    rule_id=entry.rule_id,
                    county=county,
                    township=townships,
                    lake_name_raw=name,
                    lake_name_norm=normalize_name(name),
                    plss=plss,
                    restriction_type="other",
                    status="rescinded",
                    signage_required=signage,
                    related_rule_ids=related,
                    raw_text=entry.raw_text,
                    source_url=source_url,
                    fetched_at=fetched_at,
                    parser="regex",
                    needs_review=False,
                    lake_name_group=entry.lake_name_group,
                )
            )
        return _assign_ids(records)

    preamble, clauses = split_clauses(clause_paragraphs)

    per_clause: list[tuple[Clause, list[ClauseResult]]] = []
    for clause in clauses:
        text = f"{preamble} {clause.text}".strip()
        per_clause.append((clause, classify_clause(text)))

    per_clause = _merge_high_speed_towing(per_clause)

    multi = sum(len(results) for _, results in per_clause) > 1
    for clause, results in per_clause:
        text = f"{preamble} {clause.text}".strip()
        hours = extract_hours(text, dst_sentence)
        season = extract_season(text)
        scope, description = detect_zone(clause.text, preamble, header=entry.lake_name_group)
        for result in results:
            for name in lake_names:
                records.append(
                    Restriction(
                        rule_id=entry.rule_id,
                        county=county,
                        township=townships,
                        lake_name_raw=name,
                        lake_name_norm=normalize_name(name),
                        plss=plss,
                        restriction_type=result.restriction_type,  # type: ignore[arg-type]
                        scope=scope,  # type: ignore[arg-type]
                        scope_description=description,
                        hours=hours,
                        season=season,
                        speed_mph=result.speed_mph,
                        clause=clause.label if multi else None,
                        signage_required=signage,
                        related_rule_ids=related,
                        raw_text=entry.raw_text,
                        source_url=source_url,
                        fetched_at=fetched_at,
                        parser="regex",
                        needs_review=result.needs_review,
                        lake_name_group=entry.lake_name_group,
                    )
                )
    return _assign_ids(records)


def _merge_high_speed_towing(
    per_clause: list[tuple[Clause, list[ClauseResult]]],
) -> list[tuple[Clause, list[ClauseResult]]]:
    """A high-speed clause and its sibling towing clause are one restriction."""
    high = [
        i
        for i, (_, results) in enumerate(per_clause)
        if len(results) == 1 and results[0].restriction_type in ("no_high_speed", "high_speed_hours")
    ]
    towing = [
        i
        for i, (_, results) in enumerate(per_clause)
        if len(results) == 1 and results[0].restriction_type == "no_towing"
    ]
    if len(high) != 1 or not towing:
        return per_clause
    keep = high[0]
    merged: list[tuple[Clause, list[ClauseResult]]] = []
    for i, (clause, results) in enumerate(per_clause):
        if i in towing:
            per_clause[keep][0].text = f"{per_clause[keep][0].text} {clause.text}".strip()
            continue
        merged.append((clause, results))
    if len(merged) == 1:
        merged[0][0].label = None
    return merged


def _assign_ids(records: list[Restriction]) -> list[Restriction]:
    single = len(records) == 1
    seen: dict[str, int] = {}
    for record in records:
        discriminator = None
        if not single:
            discriminator = record.clause or record.restriction_type
        rid = record.assign_id(discriminator)
        if rid in seen:
            seen[rid] += 1
            record.assign_id(f"{discriminator or ''}#{seen[rid]}")
        else:
            seen[rid] = 1
    return records


def parse_county_page(
    html: str, county_name: str, source_url: str, fetched_at: str
) -> list[Restriction]:
    """Parse one cached DNR county page into restriction records.

    A page with no controls (7 of 83 counties) yields `[]` without raising.
    """
    node = field_content(html)
    if node is None:
        log.warning("%s: no div.field-content", county_name)
        return []
    text = node.get_text(" ", strip=True)
    if NO_CONTROLS_RE.search(text):
        log.info("%s: no special local watercraft controls", county_name)
        return []
    entries = segment_entries(iter_blocks(node))
    records: list[Restriction] = []
    for entry in entries:
        try:
            records.extend(records_for_entry(entry, county_name, source_url, fetched_at))
        except Exception:  # one bad entry must not lose the page
            log.exception("%s: failed to parse entry %r", county_name, entry.header[:80])
    return records


# ---------------------------------------------------------------------------
# Stage entry point
# ---------------------------------------------------------------------------


def add_args(sp: Any) -> None:
    sp.add_argument(
        "--llm",
        action="store_true",
        help="Second pass: send unclassified entries to Claude (needs ANTHROPIC_API_KEY)",
    )


def _county_name(slug: str, manifest: dict[str, dict[str, Any]]) -> str:
    entry = manifest.get(slug)
    if entry and entry.get("name"):
        return str(entry["name"])
    return COUNTY_SLUG_NAMES.get(slug, slug.title())


def _diff(previous: Path, records: list[Restriction]) -> dict[str, Any]:
    old: dict[str, dict[str, Any]] = {}
    for line in previous.read_text(encoding="utf-8").splitlines():
        if line.strip():
            row = json.loads(line)
            old[row["restriction_id"]] = row
    new = {r.restriction_id: r.to_jsonl_dict() for r in records}
    added = sorted(set(new) - set(old))
    removed = sorted(set(old) - set(new))
    changed = sorted(rid for rid in set(new) & set(old) if new[rid] != old[rid])
    return {"new": added, "changed": changed, "removed": removed}


def run(cfg: Config, args: Any) -> int:
    manifest = dnr_fetch.load_manifest(cfg)
    raw_dir = dnr_fetch.dnr_dir(cfg)
    if not raw_dir.exists():
        log.error("no cached DNR pages in %s; run the fetch stage first", raw_dir)
        return 1

    wanted = set(cfg.counties) if cfg.counties else None
    pages = sorted(p for p in raw_dir.glob("*.html") if not p.name.startswith("_"))
    if wanted:
        pages = [p for p in pages if p.stem in wanted]
    if not pages:
        log.error("no cached county pages matched %s", sorted(wanted) if wanted else "all")
        return 1

    records: list[Restriction] = []
    for page in pages:
        slug = page.stem
        info = manifest.get(slug, {})
        county = _county_name(slug, manifest)
        found = parse_county_page(
            page.read_text(encoding="utf-8"),
            county,
            str(info.get("url", "")),
            str(info.get("fetched_at", "")),
        )
        log.info("%s: %d records", slug, len(found))
        records.extend(found)

    if getattr(args, "llm", False):
        from . import llm_extract

        records = llm_extract.upgrade_records(cfg, records)

    records.sort(key=lambda r: r.sort_key())

    cfg.work_dir.mkdir(parents=True, exist_ok=True)
    out = cfg.work_dir / "restrictions.jsonl"
    with out.open("w", encoding="utf-8") as fh:
        for record in records:
            fh.write(json.dumps(record.to_jsonl_dict(), ensure_ascii=False) + "\n")

    review = cfg.work_dir / "restrictions_review.jsonl"
    flagged = [r for r in records if r.needs_review]
    with review.open("w", encoding="utf-8") as fh:
        for record in flagged:
            row = record.to_jsonl_dict()
            row["lake_name_group"] = record.lake_name_group
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    previous = cfg.work_dir / "restrictions.prev.jsonl"
    diff: dict[str, Any] | None = None
    if previous.exists():
        diff = _diff(previous, records)
        (cfg.work_dir / "restrictions_diff.json").write_text(
            json.dumps(diff, indent=2), encoding="utf-8"
        )
    shutil.copyfile(out, previous)

    _print_summary(records, flagged, diff, out, review)
    return 0


def _print_summary(
    records: list[Restriction],
    flagged: list[Restriction],
    diff: dict[str, Any] | None,
    out: Path,
    review: Path,
) -> None:
    from rich.console import Console
    from rich.table import Table

    console = Console()

    by_type: dict[str, list[Restriction]] = {}
    for record in records:
        by_type.setdefault(record.restriction_type, []).append(record)
    table = Table(title="restrictions by type")
    table.add_column("restriction_type")
    table.add_column("records", justify="right")
    table.add_column("needs_review", justify="right")
    for name in sorted(by_type, key=lambda k: (-len(by_type[k]), k)):
        rows = by_type[name]
        table.add_row(name, str(len(rows)), str(sum(1 for r in rows if r.needs_review)))
    table.add_row("TOTAL", str(len(records)), str(len(flagged)), style="bold")
    console.print(table)

    by_county: dict[str, list[Restriction]] = {}
    for record in records:
        by_county.setdefault(record.county, []).append(record)
    table = Table(title="restrictions by county")
    table.add_column("county")
    table.add_column("records", justify="right")
    table.add_column("lakes", justify="right")
    table.add_column("rescinded", justify="right")
    table.add_column("needs_review", justify="right")
    for name in sorted(by_county):
        rows = by_county[name]
        table.add_row(
            name,
            str(len(rows)),
            str(len({r.lake_name_norm for r in rows})),
            str(sum(1 for r in rows if r.status == "rescinded")),
            str(sum(1 for r in rows if r.needs_review)),
        )
    console.print(table)

    if diff is not None:
        console.print(
            f"diff vs previous run: [green]{len(diff['new'])} new[/], "
            f"[yellow]{len(diff['changed'])} changed[/], [red]{len(diff['removed'])} removed[/]"
        )
    console.print(f"wrote {out} ({len(records)} records), {review} ({len(flagged)} for review)")
