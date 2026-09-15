"""Stable identifiers and name normalization.

`docs/data-contract.md` "Identifiers" is authoritative:

- lake `id` = `int(sha1(source_key).hexdigest()[:8], 16) & 0x7fffffff`, bumped by 1 on collision.
  The hydrography layer has no permanent id (see `docs/gis-sources.md` section 1: `OBJECTID` is
  sequential and not stable across rebuilds), so every lake uses the contract's documented fallback
  key `"{name_norm}|{lat:.4f}|{lon:.4f}"`.
- `restriction_id` = `sha1(f"{county}|{lake_name_raw}|{township}|{raw_text}")[:12]`.

Name normalization mirrors `rules/engine/index.js` `normalizeName` exactly (including the
parenthetical-segment decision documented in the contract). At runtime we prefer
`seaplane_pipeline.names.normalize_name` when that module exists (it is owned by another agent);
otherwise the local port below is used. `tests/test_ids.py` cross-checks the local port against the
JS engine through Node over `rules/fixtures/names.json`, so the two can never silently diverge.
"""
from __future__ import annotations

import hashlib
import re
import unicodedata
from collections.abc import Callable

ID_MASK = 0x7FFFFFFF

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
#: Qualifiers stay in `name_norm` (they disambiguate) but the matcher scores a
#: qualifier-stripped comparison as a weaker signal.
QUALIFIERS = frozenset({"big", "little", "north", "south", "east", "west", "upper", "lower", "middle"})

_PAREN_RE = re.compile(r"\(([^)]*)\)")
_PUNCT_RE = re.compile(r"[^a-z0-9\s]")


# --- ids ---------------------------------------------------------------------


def stable_lake_id(source_key: str) -> int:
    """Contract id: first 8 hex chars of sha1, masked to 31 bits."""
    return int(hashlib.sha1(source_key.encode("utf-8")).hexdigest()[:8], 16) & ID_MASK


def lake_source_key(name_norm: str | None, lat: float, lon: float) -> str:
    """Fallback source key for a hydrography polygon, which has no permanent id."""
    return f"{name_norm or ''}|{lat:.4f}|{lon:.4f}"


def assign_lake_id(source_key: str, used: set[int]) -> int:
    """`stable_lake_id` with the contract's collision resolver (bump by 1), recording the result."""
    lake_id = stable_lake_id(source_key)
    while lake_id in used:
        lake_id = (lake_id + 1) & ID_MASK
        if lake_id == 0:  # stay positive
            lake_id = 1
    used.add(lake_id)
    return lake_id


def restriction_id(county: str | None, lake_name_raw: str | None, township: str | None, raw_text: str) -> str:
    key = f"{county or ''}|{lake_name_raw or ''}|{township or ''}|{raw_text}"
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:12]


# --- name normalization ------------------------------------------------------


def _normalize_segment(text: str) -> str:
    s = str(text).lower()
    s = "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")
    s = _PUNCT_RE.sub(" ", s)
    tokens = [t for t in s.split() if t]
    if not tokens:
        return ""
    tokens = [ABBREVIATIONS.get(t, t) for t in tokens]
    while len(tokens) > 1 and tokens[0] in GENERIC_WORDS:
        tokens.pop(0)
    while len(tokens) > 1 and tokens[-1] in GENERIC_WORDS:
        tokens.pop()
    return " ".join(tokens)


def normalize_name_local(raw: str | None) -> str:
    """Local port of `rules/engine/index.js` `normalizeName`."""
    if raw is None:
        return ""
    paren_segments: list[str] = []

    def _capture(m: re.Match[str]) -> str:
        paren_segments.append(m.group(1))
        return " "

    main = _PAREN_RE.sub(_capture, str(raw))
    segments = [_normalize_segment(s) for s in [main, *paren_segments]]
    return " ".join(s for s in segments if s)


_normalizer: Callable[[str | None], str] | None = None


def _resolve_normalizer() -> Callable[[str | None], str]:
    """Prefer the shared `names` module when it exists; fall back to the local port."""
    global _normalizer
    if _normalizer is None:
        try:
            from . import names as _names  # type: ignore[attr-defined]

            fn = getattr(_names, "normalize_name", None)
            _normalizer = fn if callable(fn) else normalize_name_local
        except ImportError:
            _normalizer = normalize_name_local
    return _normalizer


def normalize_name(raw: str | None) -> str:
    return _resolve_normalizer()(raw)


def name_tokens(name_norm: str) -> set[str]:
    return {t for t in name_norm.split() if t}


def strip_qualifiers(name_norm: str) -> str:
    """Drop big/little/north/south/upper/lower/east/west/middle, keeping at least one token."""
    tokens = [t for t in name_norm.split() if t]
    kept = [t for t in tokens if t not in QUALIFIERS]
    return " ".join(kept) if kept else " ".join(tokens)
