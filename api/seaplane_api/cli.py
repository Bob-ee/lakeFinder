"""`seaplane-api` CLI.

    seaplane-api                      uvicorn on 127.0.0.1:8000, scheduler in-process
    seaplane-api --host 0.0.0.0 --port 8080
    seaplane-api briefing --once      one run, no server (for launchd or cron)

Mirrors the pipeline's `seaplane` CLI: a bare invocation is the normal thing, subcommands are the
exceptions.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="seaplane-api", description="Michigan seaplane briefing service")
    p.add_argument("--host", default="127.0.0.1", help="bind address (default 127.0.0.1)")
    p.add_argument("--port", type=int, default=8000, help="bind port (default 8000)")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="command")
    b = sub.add_parser("briefing", help="Generate the briefing")
    b.add_argument("--once", action="store_true", help="Run once and exit (no server)")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    if args.command == "briefing":
        if not args.once:
            print("seaplane-api briefing needs --once (the server runs the schedule)", file=sys.stderr)
            return 2
        return _run_once()

    import uvicorn

    uvicorn.run("seaplane_api.app:app", host=args.host, port=args.port, log_level="info")
    return 0


def _run_once() -> int:
    from .paths import briefing_path
    from .service import run_briefing
    from .settings import load_settings

    settings = load_settings()
    briefing = asyncio.run(run_briefing(settings, run_kind="manual"))
    print(f"wrote {briefing_path()}")
    print(briefing["summary"])
    if briefing.get("outlook"):
        print(briefing["outlook"]["summary"])
    for err in briefing.get("errors", []):
        print(f"  error: {err}", file=sys.stderr)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
