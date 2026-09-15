"""`seaplane` CLI. Each stage is a subcommand; each module exposes `run(cfg, args)`."""
from __future__ import annotations

import argparse
import importlib
import logging
import sys

from .config import Config

STAGES = [
    ("fetch", "Download hydrography, PLSS, BAS, federal, FAA, and crawl DNR county pages"),
    ("parse-dnr", "Extract structured restrictions from DNR county pages"),
    # geometry runs before match: match joins restrictions onto the polygons geometry writes.
    ("geometry", "Compute area, longest chord, shore buffer, centroid, bbox, id"),
    ("match", "Join restrictions to lake polygons"),
    ("overlay", "Public access, federal unit, airspace flags"),
    ("classify", "Run the shared rules engine"),
    ("build", "Emit tiles, index.json, restrictions.json, pack.json"),
    ("review", "Print the review queue"),
    ("all", "Run every stage in order (fetch through build)"),
]


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="seaplane", description="Michigan seaplane lake map pipeline")
    p.add_argument("--run-date", help="YYYY-MM-DD; defaults to today (UTC)")
    p.add_argument("--county", action="append", help="Limit to a county (repeatable, e.g. --county oakland)")
    p.add_argument("--force", action="store_true", help="Ignore cached inputs")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="stage", required=True)
    for name, help_text in STAGES:
        sp = sub.add_parser(name, help=help_text)
        mod = _module_for(name, quiet=True)
        add_args = getattr(mod, "add_args", None) if mod else None
        if add_args:
            add_args(sp)
    return p


def _module_for(stage: str, quiet: bool = False):
    """Import a stage module. `quiet` tolerates a sibling stage that is broken or half-written,
    so building the parser (which imports every stage for its `add_args`) never takes the CLI down;
    the stage actually being run still raises."""
    if stage == "all":
        return None
    try:
        return importlib.import_module(f".{stage.replace('-', '_')}", __package__)
    except ModuleNotFoundError as e:  # stage not built yet
        if e.name and e.name.endswith(stage.replace("-", "_")):
            return None
        if quiet:
            logging.getLogger(__name__).warning("stage %r is not importable: %s", stage, e)
            return None
        raise
    except ImportError as e:
        if quiet:
            logging.getLogger(__name__).warning("stage %r is not importable: %s", stage, e)
            return None
        raise


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    cfg = Config(
        counties=[c.lower() for c in args.county] if args.county else None,
        force=args.force,
        verbose=args.verbose,
    )
    if args.run_date:
        cfg.run_date = args.run_date
    cfg.ensure_dirs()

    stages = [s for s, _ in STAGES if s not in ("all", "review")] if args.stage == "all" else [args.stage]
    for stage in stages:
        mod = _module_for(stage)
        if mod is None:
            print(f"stage '{stage}' is not implemented yet", file=sys.stderr)
            return 2
        rc = mod.run(cfg, args)
        if rc:
            return rc
    return 0


if __name__ == "__main__":
    sys.exit(main())
