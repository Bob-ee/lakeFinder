"""Paths, dates, and constants shared by every stage."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)
MICHIGAN_BBOX = (-90.5, 41.6, -82.3, 48.4)  # west, south, east, north (WGS84)


@dataclass
class Config:
    repo_root: Path = REPO_ROOT
    run_date: str = field(default_factory=lambda: datetime.now(UTC).strftime("%Y-%m-%d"))
    counties: list[str] | None = None  # None = all
    force: bool = False  # ignore caches
    verbose: bool = False

    @property
    def data_dir(self) -> Path:
        return Path(os.environ.get("SEAPLANE_DATA_DIR", self.repo_root / "data"))

    @property
    def raw_dir(self) -> Path:
        return self.data_dir / "raw" / self.run_date

    @property
    def cache_dir(self) -> Path:
        """Date-independent cache for large, rarely changing downloads (hydrography, PLSS)."""
        return self.data_dir / "cache"

    @property
    def work_dir(self) -> Path:
        """Intermediate stage outputs (restrictions.jsonl, matches, geometry parquet)."""
        return self.data_dir / "work"

    @property
    def out_dir(self) -> Path:
        return self.data_dir / "out"

    @property
    def manual_dir(self) -> Path:
        return self.data_dir / "manual"

    @property
    def rules_dir(self) -> Path:
        return self.repo_root / "rules"

    def ensure_dirs(self) -> None:
        for d in (self.raw_dir, self.cache_dir, self.work_dir, self.out_dir):
            d.mkdir(parents=True, exist_ok=True)
