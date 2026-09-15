"""Stage 8 (`review`): print the review queue.

Five sections, from whatever intermediate files exist:

1. unmatched restrictions (`unmatched.json`)
2. low-confidence matches, 0.5-0.8 (`matches.json`)
3. records the parser flagged (`restrictions_review.jsonl`, else `needs_review` rows of
   `restrictions.jsonl`)
4. lakes whose verdict is `unknown` but that do have restrictions attached
5. the run-over-run diff summary (`restrictions_diff.json`)

`--json` dumps exactly the same data as one JSON object instead of the tables.
"""
from __future__ import annotations

import json
import logging

from rich.console import Console
from rich.table import Table

from . import match
from .config import Config

log = logging.getLogger(__name__)

LOW_CONFIDENCE = 0.8


def add_args(sp) -> None:
    sp.add_argument("--json", action="store_true", dest="as_json", help="Emit JSON instead of tables")
    sp.add_argument("--limit", type=int, default=50, help="Rows per section (0 = all)")


def _read_json(path, default):
    return json.loads(path.read_text()) if path.exists() else default


def collect(cfg: Config) -> dict:
    work = cfg.work_dir
    unmatched = _read_json(work / "unmatched.json", [])
    # Lake-named rows are the real worklist; rivers, channels, and bays have no polygon and sort last.
    unmatched = sorted(unmatched, key=lambda u: (u.get("kind") == "waterway", u.get("county") or "", u.get("lake_name_raw") or ""))
    matches = _read_json(work / "matches.json", [])
    verdicts = _read_json(work / "verdicts.json", {})
    diff = _read_json(work / "restrictions_diff.json", None)

    review_path = work / "restrictions_review.jsonl"
    records = match.read_restrictions(work / "restrictions.jsonl") if (work / "restrictions.jsonl").exists() else []
    if review_path.exists():
        flagged = match.read_restrictions(review_path)
    else:
        flagged = [r for r in records if r.get("needs_review")]

    by_rid = {r.get("restriction_id"): r for r in records}
    low = []
    for m in matches:
        if float(m.get("score", 1.0)) < LOW_CONFIDENCE:
            rec = by_rid.get(m["restriction_id"], {})
            low.append({**m, "lake_name_raw": rec.get("lake_name_raw"), "county": rec.get("county")})

    lakes_with_restrictions: dict[str, int] = {}
    for m in matches:
        key = str(int(m["lake_id"]))
        lakes_with_restrictions[key] = lakes_with_restrictions.get(key, 0) + 1
    unknown_with_restrictions = [
        {"lake_id": int(k), "restrictions": n, "flags": (verdicts.get(k) or {}).get("flags", [])}
        for k, n in lakes_with_restrictions.items()
        if (verdicts.get(k) or {}).get("verdict") == "unknown"
    ]

    return {
        "unmatched": unmatched,
        "low_confidence": low,
        "needs_review": flagged,
        "unknown_with_restrictions": unknown_with_restrictions,
        "diff": diff,
    }


def _table(console: Console, title: str, columns: list[str], rows: list[list], limit: int) -> None:
    table = Table(title=f"{title} ({len(rows)})", title_justify="left", header_style="bold")
    for col in columns:
        table.add_column(col, overflow="fold")
    shown = rows if not limit else rows[:limit]
    for row in shown:
        table.add_row(*["" if v is None else str(v) for v in row])
    if not rows:
        table.add_row(*["-"] * len(columns))
    console.print(table)
    if limit and len(rows) > limit:
        console.print(f"  ... {len(rows) - limit} more (use --limit 0)", style="dim")


def run(cfg: Config, args) -> int:
    data = collect(cfg)
    if getattr(args, "as_json", False):
        print(json.dumps(data, indent=1))
        return 0

    limit = getattr(args, "limit", 50)
    console = Console()
    _table(
        console, "Unmatched restrictions",
        ["restriction_id", "lake", "county", "township", "kind", "cands", "best", "reason"],
        [
            [u.get("restriction_id"), u.get("lake_name_raw"), u.get("county"), u.get("township"), u.get("kind"),
             u.get("candidates"), u.get("best_score"), u.get("reason")]
            for u in data["unmatched"]
        ],
        limit,
    )
    _table(
        console, "Low-confidence matches (0.5-0.8, applied)",
        ["restriction_id", "lake", "county", "lake_id", "score", "method"],
        [
            [m.get("restriction_id"), m.get("lake_name_raw"), m.get("county"), m.get("lake_id"),
             m.get("score"), m.get("method")]
            for m in data["low_confidence"]
        ],
        limit,
    )
    _table(
        console, "Records flagged for review",
        ["restriction_id", "lake", "county", "type", "parser", "rule_id"],
        [
            [r.get("restriction_id"), r.get("lake_name_raw"), r.get("county"),
             r.get("restriction_type"), r.get("parser"), r.get("rule_id")]
            for r in data["needs_review"]
        ],
        limit,
    )
    _table(
        console, "Lakes with restrictions but an unknown verdict",
        ["lake_id", "restrictions", "flags"],
        [[u["lake_id"], u["restrictions"], ",".join(u["flags"])] for u in data["unknown_with_restrictions"]],
        limit,
    )
    diff = data["diff"]
    if diff is None:
        console.print("No restrictions_diff.json from this run.", style="dim")
    else:
        summary = diff.get("summary") if isinstance(diff, dict) else None
        rows = (
            [[k, v] for k, v in summary.items()]
            if isinstance(summary, dict)
            else [[k, len(v) if isinstance(v, list | dict) else v] for k, v in diff.items()]
            if isinstance(diff, dict)
            else [["entries", len(diff)]]
        )
        _table(console, "Diff against the previous run", ["key", "value"], rows, 0)
    return 0
