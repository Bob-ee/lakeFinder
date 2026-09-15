"""The `restrictions.jsonl` record, as pydantic models.

Authoritative schema: docs/data-contract.md, section "restrictions.jsonl". The
field order in `to_jsonl_dict` matches the contract's example record.
"""

from __future__ import annotations

import hashlib
from typing import Any, Literal

from pydantic import BaseModel, Field

RestrictionType = Literal[
    "no_vessels",
    "no_motorboats",
    "slow_no_wake",
    "no_high_speed",
    "high_speed_hours",
    "speed_limit",
    "no_towing",
    "no_pwc",
    "no_wake_zone_marked",
    "shore_buffer",
    "not_applicable",
    "mac_ordinance",
    "mac_conditional",
    "federal_no_landing",
    "other",
]

RESTRICTION_TYPES: tuple[str, ...] = (
    "no_vessels",
    "no_motorboats",
    "slow_no_wake",
    "no_high_speed",
    "high_speed_hours",
    "speed_limit",
    "no_towing",
    "no_pwc",
    "no_wake_zone_marked",
    "shore_buffer",
    "not_applicable",
    "mac_ordinance",
    "mac_conditional",
    "federal_no_landing",
    "other",
)

Scope = Literal["lakewide", "zone"]
Status = Literal["active", "rescinded"]
ParserKind = Literal["regex", "llm", "manual"]


class Hours(BaseModel):
    """`{"text": "6:30 p.m. to 10:00 a.m.", "start": "18:30", "end": "10:00", "days": null}`."""

    text: str
    start: str | None = None
    end: str | None = None
    days: str | None = None


class Season(BaseModel):
    """`{"text": "during September, October and November", "start": "09-01", "end": "11-30"}`."""

    text: str
    start: str | None = None
    end: str | None = None


class Plss(BaseModel):
    township: str
    range: str
    sections: list[int] = Field(default_factory=list)


def compute_restriction_id(
    county: str,
    lake_name_raw: str,
    township: str | None,
    raw_text: str,
    discriminator: str | None = None,
) -> str:
    """`sha1(f"{county}|{lake_name_raw}|{township}|{raw_text}")[:12]` per the contract.

    Deviation, documented in `parse_dnr`: when one DNR entry decomposes into
    several records they share county/lake/township/raw_text and would collide,
    so a `discriminator` (the clause label, else the restriction type) is
    appended to the hashed key. Single-record entries pass `None` and keep the
    contract's exact formula.
    """
    key = f"{county}|{lake_name_raw}|{township}|{raw_text}"
    if discriminator:
        key = f"{key}|{discriminator}"
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:12]


class Restriction(BaseModel):
    """One (lake mention, rule clause) row of `restrictions.jsonl`."""

    restriction_id: str = ""
    rule_id: str | None = None
    county: str
    township: str | None = None
    lake_name_raw: str
    lake_name_norm: str = ""
    plss: list[Plss] = Field(default_factory=list)
    restriction_type: RestrictionType = "other"
    scope: Scope = "lakewide"
    scope_description: str | None = None
    hours: Hours | None = None
    season: Season | None = None
    speed_mph: float | None = None
    status: Status = "active"
    clause: str | None = None
    signage_required: bool = False
    related_rule_ids: list[str] = Field(default_factory=list)
    raw_text: str = ""
    source_url: str = ""
    fetched_at: str = ""
    parser: ParserKind = "regex"
    needs_review: bool = False

    #: Not part of the JSONL contract: the verbatim DNR header this record was
    #: split out of, kept for the review queue when one header names several
    #: lakes ("BIG AND LITTLE SCHOOL LOT LAKES AND CONNECTING CHANNEL").
    lake_name_group: str | None = None

    def assign_id(self, discriminator: str | None = None) -> str:
        self.restriction_id = compute_restriction_id(
            self.county, self.lake_name_raw, self.township, self.raw_text, discriminator
        )
        return self.restriction_id

    def to_jsonl_dict(self) -> dict[str, Any]:
        """The contract record, in contract key order. `lake_ids` is added by `build`."""
        return {
            "restriction_id": self.restriction_id,
            "rule_id": self.rule_id,
            "county": self.county,
            "township": self.township,
            "lake_name_raw": self.lake_name_raw,
            "lake_name_norm": self.lake_name_norm,
            "plss": [p.model_dump() for p in self.plss],
            "restriction_type": self.restriction_type,
            "scope": self.scope,
            "scope_description": self.scope_description,
            "hours": self.hours.model_dump() if self.hours else None,
            "season": self.season.model_dump() if self.season else None,
            "speed_mph": self.speed_mph,
            "status": self.status,
            "clause": self.clause,
            "signage_required": self.signage_required,
            "related_rule_ids": list(self.related_rule_ids),
            "raw_text": self.raw_text,
            "source_url": self.source_url,
            "fetched_at": self.fetched_at,
            "parser": self.parser,
            "needs_review": self.needs_review,
        }

    def sort_key(self) -> tuple[str, str, str, str]:
        return (self.county, self.lake_name_norm, self.rule_id or "", self.clause or "")
