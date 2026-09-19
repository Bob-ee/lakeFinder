"""Where the service reads and writes.

`SEAPLANE_DATA_OUT` / `SEAPLANE_DATA_MANUAL` (data-contract "api service"); the defaults are the
repo-relative `../data/out` and `../data/manual` so `cd api && uv run seaplane-api` works in place.
Resolved lazily on every call rather than at import so tests can monkeypatch the environment.
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]


def data_out() -> Path:
    return Path(os.environ.get("SEAPLANE_DATA_OUT", REPO_ROOT / "data" / "out")).resolve()


def data_manual() -> Path:
    return Path(os.environ.get("SEAPLANE_DATA_MANUAL", REPO_ROOT / "data" / "manual")).resolve()


def settings_path() -> Path:
    return data_manual() / "settings.json"


def briefing_path() -> Path:
    return data_out() / "briefing.json"


def index_path() -> Path:
    return data_out() / "index.json"


def lake_extents_path() -> Path:
    return data_out() / "lake_extents.json"


def write_json_atomic(path: Path, payload: Any) -> None:
    """Write via a sibling temp file + `os.replace`, so a reader never sees a half-written file.

    The temp file goes in the target directory because `os.replace` is only atomic within a
    filesystem, and `/tmp` is a different one on the deploy target.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, separators=(",", ":"))
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def read_json(path: Path) -> Any | None:
    """Read a JSON file, returning `None` when it is missing or corrupt."""
    try:
        with path.open(encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return None
