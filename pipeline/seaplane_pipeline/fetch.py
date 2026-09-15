"""Stage 1: download every GIS dataset, then crawl the DNR county pages.

Large, date-independent downloads (hydrography ~124 MB, PLSS ~75 MB, county and civil-township
boundaries) land in `data/cache/`; the small per-run ones (BAS, NPS, FWS, FAA) land in
`data/raw/<date>/`. Existing files are skipped unless `--force`.

The DNR crawl lives in `dnr_fetch.crawl(cfg)` (another module); it is imported lazily so this stage
still runs if that module is not present yet.
"""
from __future__ import annotations

import logging
import time

from . import gis
from .config import Config

log = logging.getLogger(__name__)


def add_args(sp) -> None:
    sp.add_argument("--skip-gis", action="store_true", help="Do not download the GIS datasets")
    sp.add_argument("--skip-dnr", action="store_true", help="Do not crawl the DNR county pages")
    sp.add_argument(
        "--only",
        action="append",
        metavar="DATASET",
        help=f"Fetch only these datasets (repeatable): {', '.join(gis.DATASETS)}",
    )


def _human(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{n} B"
        n /= 1024.0
    return f"{n}"


def run(cfg: Config, args) -> int:
    only = getattr(args, "only", None)
    skip_gis = getattr(args, "skip_gis", False)
    skip_dnr = getattr(args, "skip_dnr", False)

    if only:
        unknown = [k for k in only if k not in gis.DATASETS]
        if unknown:
            log.error("unknown dataset(s): %s (known: %s)", ", ".join(unknown), ", ".join(gis.DATASETS))
            return 2

    if not skip_gis:
        keys = only or list(gis.DATASETS)
        for key in keys:
            ds = gis.DATASETS[key]
            started = time.monotonic()
            path, size, skipped = gis.download_dataset(cfg, ds, force=cfg.force)
            verb = "cached" if skipped else f"fetched in {time.monotonic() - started:.1f}s"
            log.info("%-15s %-34s %9s  (%s)", key, path.name, _human(size), verb)
    else:
        log.info("skipping GIS downloads (--skip-gis)")

    if skip_dnr or only:
        log.info("skipping DNR crawl (%s)", "--skip-dnr" if skip_dnr else "--only")
        return 0

    try:
        from . import dnr_fetch
    except ImportError as exc:
        log.warning("DNR crawl unavailable (%s); GIS downloads are done, skipping the county pages", exc)
        return 0
    started = time.monotonic()
    dnr_fetch.crawl(cfg)
    log.info("DNR crawl finished in %.1fs", time.monotonic() - started)
    return 0
