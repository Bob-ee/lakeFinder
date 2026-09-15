"""Second pass of `parse-dnr`: ask Claude about the entries regex could not classify.

Only entries the deterministic pass left as `restriction_type: other` with
`needs_review: true` are sent (rescinded entries are skipped). Every record this
module produces is `parser: "llm"` and keeps `needs_review: true` until a human
clears it, per docs/design.md section 5.

The pass is gated twice: the `--llm` flag on `seaplane parse-dnr`, and
credentials in the environment (`ANTHROPIC_API_KEY` or `ANTHROPIC_AUTH_TOKEN`).
Without both it logs one line and leaves the records untouched. Responses are
cached under `work/llm_cache/<restriction_id>.json` so reruns cost nothing.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Literal, Protocol

from pydantic import BaseModel, Field

from .config import Config
from .restriction import Restriction, RestrictionType

log = logging.getLogger(__name__)

MODEL = "claude-opus-5"
MAX_TOKENS = 16000

SYSTEM_PROMPT = """You extract structured boating restrictions from Michigan DNR \
"special local watercraft control" orders. You are given the verbatim text of one \
order (one rule number, one or more lakes) and must return one entry per legally \
distinct restriction it imposes.

Assign each clause exactly one `restriction_type` from this list:

- no_vessels: all vessels / boating prohibited
- no_motorboats: motorboats prohibited (electric-motor-only counts)
- slow_no_wake: slow-no-wake / no-wake speed; may carry hours or a season
- no_high_speed: high-speed boating / planing prohibited; may carry hours/season
- high_speed_hours: high speed permitted only during the stated hours (or season)
- speed_limit: numeric limit; set speed_mph
- no_towing: water skiing / towing prohibited or hour-limited
- no_pwc: personal watercraft prohibited or hour-limited
- no_wake_zone_marked: buoyed no-wake zone (statewide-type marker rule)
- shore_buffer: local echo of the statewide slow-no-wake within 100 feet of \
shore/docks/swimmers rule
- not_applicable: confidently does not touch landing or takeoff - airboat bans, \
mooring/anchoring, rafts and flotation devices, towed-person headcount limits, \
swimming areas
- other: cannot be classified

Parsing rules, from the project's data contract:

- One order that bundles several clauses (a), (b), (c) becomes several entries, \
each with its own restriction_type, scope, hours and clause label.
- "Motorboats prohibited except electric motors" is no_motorboats. If the same \
clause also caps speed, emit a second speed_limit entry.
- "High speed" clauses that also ban towing are ONE no_high_speed (or \
high_speed_hours) entry, not two.
- A towing/skiing-only clause with no speed clause is no_towing.
- The "within 100 feet of any shore, dock, raft, buoyed or occupied bathing area, \
or vessel moored or at anchor" echo is shore_buffer.
- Hours like "6:30 p.m. to 10:00 a.m. of the following day" keep the verbatim \
phrase in `hours`. A Daylight Saving Time variant sentence belongs in `hours` too. \
Day qualifiers ("Sundays, Memorial Day, Independence Day, and Labor Day", \
"Saturdays and holidays") belong in `hours` as written.
- Month-name seasons ("during September, October and November") go in `season` \
verbatim.
- scope is "zone" when the clause describes a sub-area of the waterbody (a named \
bay, a lot-line or quarter-section boundary, "that portion of", "north of X \
road"); then scope_description is that descriptive phrase, verbatim. Otherwise \
scope is "lakewide" and scope_description is null.
- Report the signage/buoy boilerplate and the "History: Eff." line as no entry at \
all; they are not restrictions.

Set `confidence` between 0 and 1. Use `other` with low confidence rather than \
guessing: a wrong confident answer is worse than an unclassified one. Return no \
entries only if the text imposes no restriction at all."""


class ExtractedClause(BaseModel):
    """One legally distinct restriction the model found in an entry."""

    clause: str | None = Field(default=None, description='Clause label such as "(a)", or null')
    restriction_type: RestrictionType = "other"
    scope: Literal["lakewide", "zone"] = "lakewide"
    scope_description: str | None = None
    hours: str | None = Field(default=None, description="Verbatim hours phrase, or null")
    season: str | None = Field(default=None, description="Verbatim season phrase, or null")
    speed_mph: float | None = None
    confidence: float = 0.0


class ExtractedEntry(BaseModel):
    clauses: list[ExtractedClause] = Field(default_factory=list)


class _MessagesClient(Protocol):  # pragma: no cover - typing only
    def parse(self, **kwargs: Any) -> Any: ...


class _Client(Protocol):  # pragma: no cover - typing only
    @property
    def messages(self) -> _MessagesClient: ...


def credentials_available() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))


def build_user_content(record: Restriction) -> str:
    header = f"County: {record.county}\nLake: {record.lake_name_group or record.lake_name_raw}"
    if record.rule_id:
        header += f"\nRule: {record.rule_id}"
    if record.township:
        header += f"\nTownship: {record.township}"
    return f"{header}\n\nOrder text:\n{record.raw_text}"


def cache_path(cfg: Config, record: Restriction) -> Path:
    return cfg.work_dir / "llm_cache" / f"{record.restriction_id}.json"


def extract_entry(
    client: _Client, record: Restriction, cfg: Config, use_cache: bool = True
) -> ExtractedEntry | None:
    """One `messages.parse` call (or a cache hit) for one unclassified entry."""
    path = cache_path(cfg, record)
    if use_cache and path.exists() and not cfg.force:
        try:
            return ExtractedEntry.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            log.warning("llm cache for %s is unreadable; refetching", record.restriction_id)

    response = client.messages.parse(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": build_user_content(record)}],
        output_format=ExtractedEntry,
    )
    parsed = response.parsed_output
    if parsed is None:
        return None
    entry = parsed if isinstance(parsed, ExtractedEntry) else ExtractedEntry.model_validate(parsed)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(entry.model_dump_json(indent=2), encoding="utf-8")
    return entry


def _apply(record: Restriction, entry: ExtractedEntry) -> list[Restriction]:
    from .parse_dnr import extract_hours, extract_season

    out: list[Restriction] = []
    for i, clause in enumerate(entry.clauses):
        new = record.model_copy(deep=True)
        new.restriction_type = clause.restriction_type
        new.scope = clause.scope
        new.scope_description = clause.scope_description
        new.speed_mph = clause.speed_mph
        new.clause = clause.clause
        new.parser = "llm"
        new.needs_review = True
        if clause.hours:
            new.hours = extract_hours(clause.hours) or new.hours
            if new.hours is None:
                from .restriction import Hours

                new.hours = Hours(text=clause.hours)
        if clause.season:
            from .restriction import Season

            new.season = extract_season(clause.season) or Season(text=clause.season)
        discriminator = clause.clause or f"{clause.restriction_type}#{i}"
        new.assign_id(f"llm|{discriminator}")
        out.append(new)
    return out


def upgrade_records(
    cfg: Config, records: list[Restriction], client: _Client | None = None
) -> list[Restriction]:
    """Replace unclassified records with LLM-extracted ones. Never raises."""
    targets = [
        r
        for r in records
        if r.restriction_type == "other" and r.needs_review and r.status != "rescinded"
    ]
    if not targets:
        log.info("llm pass: nothing to do (0 unclassified entries)")
        return records

    if client is None:
        if not credentials_available():
            log.info(
                "llm pass: skipped, no ANTHROPIC_API_KEY/ANTHROPIC_AUTH_TOKEN "
                "(%d entries stay needs_review)",
                len(targets),
            )
            return records
        import anthropic

        client = anthropic.Anthropic()

    replacements: dict[str, list[Restriction]] = {}
    for record in targets:
        try:
            entry = extract_entry(client, record, cfg)
        except Exception:
            log.exception("llm pass: %s (%s) failed", record.lake_name_raw, record.rule_id)
            continue
        if entry and entry.clauses:
            replacements[record.restriction_id] = _apply(record, entry)

    if not replacements:
        return records

    out: list[Restriction] = []
    for record in records:
        out.extend(replacements.get(record.restriction_id, [record]))
    log.info(
        "llm pass: %d of %d unclassified entries upgraded into %d records",
        len(replacements),
        len(targets),
        sum(len(v) for v in replacements.values()),
    )
    return out
