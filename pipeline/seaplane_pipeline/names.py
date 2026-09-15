"""Lake-name normalization and multi-lake header splitting.

`normalize_name` is the Python half of the shared normalization contract
(docs/data-contract.md, "Name normalization"). The JS half lives in
`rules/engine/index.js` and the shared cases in `rules/fixtures/names.json`;
`tests/test_names.py` asserts every shared fixture case passes here too.
"""

from __future__ import annotations

import re
import unicodedata

GENERIC_WORDS = frozenset({"lake", "pond", "reservoir", "impoundment", "flowage", "basin"})

ABBREVIATIONS = {
    "lk": "lake",
    "mt": "mount",
    "st": "saint",
    "n": "north",
    "s": "south",
    "e": "east",
    "w": "west",
    "upr": "upper",
    "lwr": "lower",
    "twp": "township",
}

#: Qualifiers that stay in `name_norm` because they disambiguate, and that are
#: also the words a multi-lake header shares a base name across
#: ("Big and Little School Lot Lakes").
QUALIFIERS = frozenset(
    {
        "big",
        "little",
        "north",
        "south",
        "east",
        "west",
        "upper",
        "lower",
        "middle",
        "first",
        "second",
        "third",
        "old",
        "new",
    }
)

_PAREN_RE = re.compile(r"\(([^)]*)\)")
_COMBINING_RE = re.compile(r"[̀-ͯ]")
_NON_ALNUM_RE = re.compile(r"[^a-z0-9\s]")


def _normalize_segment(text: str) -> str:
    s = str(text).lower()
    s = _COMBINING_RE.sub("", unicodedata.normalize("NFD", s))
    s = _NON_ALNUM_RE.sub(" ", s)
    tokens = [t for t in s.split() if t]
    if not tokens:
        return ""
    tokens = [ABBREVIATIONS.get(t, t) for t in tokens]
    while len(tokens) > 1 and tokens[0] in GENERIC_WORDS:
        tokens.pop(0)
    while len(tokens) > 1 and tokens[-1] in GENERIC_WORDS:
        tokens.pop()
    return " ".join(tokens)


def normalize_name(raw: str | None) -> str:
    """Normalize a raw lake name to `name_norm` per docs/data-contract.md.

    A parenthetical qualifier is treated as a separate segment: the main name
    and each parenthetical are normalized independently and joined with a
    space, so `"Crooked Lake (Big)"` -> `"crooked big"`.
    """
    if raw is None:
        return ""
    parens: list[str] = []

    def _grab(match: re.Match[str]) -> str:
        parens.append(match.group(1))
        return " "

    main = _PAREN_RE.sub(_grab, str(raw))
    segments = [_normalize_segment(s) for s in [main, *parens]]
    return " ".join(s for s in segments if s)


# ---------------------------------------------------------------------------
# Multi-lake header splitting
# ---------------------------------------------------------------------------

#: Words that make a header segment a waterbody rather than a structural
#: descriptor ("CASS LAKE, GERUNDEGUT BAY AND CANALS AND CHANNELS").
WATERBODY_WORDS = frozenset(
    {
        "lake",
        "lakes",
        "pond",
        "ponds",
        "reservoir",
        "flowage",
        "river",
        "creek",
        "bayou",
        "millpond",
    }
)

#: A header segment carrying one of these words and no waterbody word describes
#: a part of a lake or a connecting structure, not a lake of its own.
DESCRIPTOR_WORDS = frozenset(
    {
        "township",
        "townhip",
        "twsp",
        "twp",
        "city",
        "village",
        "bay",
        "bays",
        "basin",
        "part",
        "parts",
        "portion",
        "portions",
        "section",
        "sections",
        "canal",
        "canals",
        "channel",
        "channels",
        "connecting",
        "connected",
        "lagoon",
        "lagoons",
        "waters",
        "tip",
        "adjoining",
        "access",
        "site",
        "arm",
        "inlet",
        "outlet",
        "dam",
        "impoundment",
    }
)

_TRAILING_STRUCTURE_RE = re.compile(
    r"[\s,]+(?:and\s+)?(?:all\s+)?(?:the\s+)?(?:artificial\s+|natural\s+)?"
    r"(?:connecting|connected(?:\s+to)?|channels?|canals?|lagoons?|waters|thereto)\s*$",
    re.IGNORECASE,
)

_TRAILING_PLACE_RE = re.compile(
    r"\s*,?\s+in\s+[\w'.-]+(?:\s+[\w'.-]+)*?\s+(?:township|city|village)\s*$",
    re.IGNORECASE,
)

_LEADING_STRUCTURE_RE = re.compile(
    r"^(?:the\s+)?(?:all\s+)?(?:artificial\s+)?(?:channels?|canals?)\s+"
    r"(?:connecting|connected\s+to|connected|to)\s+",
    re.IGNORECASE,
)

_SMALL_WORDS = frozenset({"and", "of", "the", "to", "in", "at", "on", "for", "a", "an", "or"})

_SPLIT_RE = re.compile(r"\s*(?:,\s*and\s+|,\s*|\s+and\s+|\s*&\s*)\s*", re.IGNORECASE)

_WORD_RE = re.compile(r"[A-Za-z][A-Za-z']*")


def title_case(raw: str) -> str:
    """Title-case the shouty parts of a DNR header, leaving mixed case alone.

    DNR headers are ALL CAPS but occasionally carry a lowercase parenthetical
    ("SQUAW LAKE (lagoon)"); only the shouting words are touched.
    """
    text = re.sub(r"\s+", " ", raw).strip()
    if not text:
        return ""
    state = {"first": True}

    def fix(m: re.Match[str]) -> str:
        word = m.group(0)
        first = state["first"]
        state["first"] = False
        if len(word) > 1 and not word.isupper():
            return word  # already mixed/lower case: trust the source
        low = word.lower()
        if not first and low in _SMALL_WORDS:
            return low
        return low[:1].upper() + low[1:]

    return _WORD_RE.sub(fix, text)


def _words(segment: str) -> list[str]:
    return [w.lower() for w in re.findall(r"[A-Za-z]+", segment)]


def _is_waterbody(segment: str) -> bool:
    return any(w in WATERBODY_WORDS for w in _words(segment))


#: A header chunk opening with one of these positions describes where on a
#: waterbody the rule applies ("HURON RIVER, ADJACENT TO CEDAR ISLAND LAKE").
_POSITION_PREFIX_RE = re.compile(
    r"^(?:adjacent|near|opposite|upstream|downstream|lying|part\s+of|portion\s+of|that\s+part|"
    r"that\s+portion|from|between|within|along|above|below)\b",
    re.IGNORECASE,
)


def _is_descriptor(segment: str) -> bool:
    ws = _words(segment)
    if not ws:
        return True
    if _POSITION_PREFIX_RE.match(segment.strip()):
        return True
    if any(w in WATERBODY_WORDS for w in ws):
        return False
    return any(w in DESCRIPTOR_WORDS for w in ws)


def _split_outside_parens(text: str) -> list[str]:
    out, buf, depth = [], [], 0
    for ch in text:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth = max(0, depth - 1)
        if ch in ",;" and depth == 0:
            out.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
    out.append("".join(buf))
    return [c.strip() for c in out if c.strip()]


def _singular_generic(name: str) -> str:
    """"Big School Lot Lakes" -> "Big School Lot Lake" (only the trailing generic)."""
    return re.sub(r"\b(Lakes|Ponds)\b\s*$", lambda m: m.group(1)[:-1], name, flags=re.IGNORECASE)


def _strip_structure(text: str) -> str:
    prev = None
    while prev != text:
        prev = text
        text = _TRAILING_PLACE_RE.sub("", text)
        text = _TRAILING_STRUCTURE_RE.sub("", text)
        text = text.strip(" ,;")
    return text


def _expand_shared_base(parts: list[str], generic: str) -> list[str] | None:
    """["Big", "Little School Lot"] + " Lake" -> Big/Little School Lot Lake."""
    if len(parts) < 2:
        return None
    base = parts[-1]
    leading = parts[:-1]
    if not all(len(p.split()) == 1 and p.lower() in QUALIFIERS for p in leading):
        return None
    base_words = base.split()
    if not base_words:
        return None
    if len(base_words) == 1 and base_words[0].lower() in QUALIFIERS:
        return None
    out = []
    if base_words[0].lower() in QUALIFIERS and len(base_words) > 1:
        shared = " ".join(base_words[1:])
        for q in [*leading, base_words[0]]:
            out.append(f"{q} {shared}{generic}")
    else:
        for q in leading:
            out.append(f"{q} {base}{generic}")
        out.append(f"{base}{generic}")
    return out


def split_multi_lake_names(raw: str) -> list[str]:
    """Split a DNR entry header into one name per physical waterbody.

    The DNR writes one `<strong>` header per rule, and that header often names
    several lakes ("BIG AND LITTLE SCHOOL LOT LAKES AND CONNECTING CHANNEL",
    "STRINGY LAKES (TAN, CLEAR, SQUAW, SECOND, SPRING, CEDAR, AND LONG)").
    The data contract has one record per *lake mention*, so `parse_dnr` emits
    one record per returned name and puts that name in `lake_name_raw`. The
    untouched header is kept on `Restriction.lake_name_group` for review; it is
    not part of the JSONL contract.

    Rules, in order:

    1. Structural descriptor segments ("CERTAIN BAYS", "WEST BLOOMFIELD
       TOWNSHIP", "CHANNEL CONNECTING", "ALL ARTIFICIAL CHANNELS AND CANALS
       CONNECTED TO") are dropped when a real waterbody segment survives.
       `parse_dnr` reuses the dropped descriptor as a zone `scope_description`.
    2. A plural base with a parenthetical list ("Stringy Lakes (Tan, Clear,
       ... and Long)") expands to one `"<X> Lake"` per listed name.
    3. A conjunction list expands to one name per element. When every element
       but the last is a bare qualifier (Big/Little/Upper/Lower/...), the last
       element's words are the shared base: "Big and Little School Lot Lakes"
       -> ["Big School Lot Lake", "Little School Lot Lake"]; "Upper and Lower
       Pettibone Lakes" -> ["Upper Pettibone Lake", "Lower Pettibone Lake"].
       Otherwise each element stands alone: "Indian, Patterson and Bain Lakes"
       -> ["Indian Lake", "Patterson Lake", "Bain Lake"].
    4. Anything else stays a single name, title-cased, with a trailing plural
       generic made singular ("Twin Lakes" -> "Twin Lake").
    """
    header = title_case(raw)
    if not header:
        return []

    # (1) drop structural descriptor segments
    chunks = _split_outside_parens(header)
    kept = [c for c in chunks if not _is_descriptor(c)]
    text = ", ".join(kept) if kept else header

    text = _LEADING_STRUCTURE_RE.sub("", text)
    if re.match(r"^(?:the\s+)?(?:channel|canal)", raw.strip(), re.IGNORECASE):
        text = re.sub(r"\s+to\s+.*$", "", text, flags=re.IGNORECASE)
    # river reach descriptions: "Black River From Meyers Creek To Black Lake"
    text = re.sub(r"\s+from\s+.+?\s+to\s+.+$", "", text, flags=re.IGNORECASE)
    text = _strip_structure(text) or header

    # (2) plural base with a parenthetical list of member lakes
    m = re.match(r"^(.*?)\s*\(([^)]*)\)\s*$", text)
    if m and re.search(r"\b(Lakes|Ponds)\b", m.group(1), re.IGNORECASE):
        members = [p.strip() for p in _SPLIT_RE.split(m.group(2)) if p.strip()]
        if len(members) > 1:
            generic = " Pond" if re.search(r"\bPonds\b", m.group(1), re.IGNORECASE) else " Lake"
            return [
                _singular_generic(title_case(p)) + ("" if _is_waterbody(p) else generic) for p in members
            ]

    # (3) conjunction list
    if re.search(r"(?:,|\s+and\s+|\s*&\s*)", text, re.IGNORECASE):
        parts = [p.strip() for p in _SPLIT_RE.split(text) if p.strip()]
        parts = [p for p in parts if not _is_descriptor(p)] or parts
        if len(parts) > 1:
            if all(_is_waterbody(p) for p in parts):
                return [_singular_generic(p) for p in parts]
            generic_m = re.search(r"\b(Lakes|Lake|Ponds|Pond)\b\s*$", parts[-1], re.IGNORECASE)
            if generic_m:
                generic = " " + _singular_generic(generic_m.group(1)).title()
                stripped = [
                    re.sub(r"\s*\b(Lakes|Lake|Ponds|Pond)\b\s*$", "", p, flags=re.IGNORECASE).strip() for p in parts
                ]
                stripped = [p for p in stripped if p]
                if len(stripped) > 1:
                    expanded = _expand_shared_base(stripped, generic)
                    if expanded:
                        return expanded
                    return [p + generic for p in stripped]

    return [_singular_generic(text)]


def descriptor_segments(raw: str) -> list[str]:
    """The structural descriptor chunks `split_multi_lake_names` drops.

    `parse_dnr` reuses them as a zone `scope_description` ("CEDAR ISLAND LAKE,
    CERTAIN BAYS" -> "Certain Bays").
    """
    header = title_case(raw)
    if not header:
        return []
    chunks = _split_outside_parens(header)
    if len(chunks) < 2:
        return []
    kept = [c for c in chunks if not _is_descriptor(c)]
    if not kept:
        return []
    return [c for c in chunks if _is_descriptor(c)]
