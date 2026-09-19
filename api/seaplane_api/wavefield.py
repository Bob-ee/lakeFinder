"""Reader for the pipeline's wave field pack (`wave_points.json` + `wave_points.bin`).

Format: `docs/data-contract.md`, "Wave field". The index names the labels and gives every water body
a `[first_record, count]` slice of a flat file of 60-byte little-endian records,
`struct "<ffHH16H8H"`: lon f32, lat f32, `depth_dm` u16 (`65535` = unknown), `label` u16 into
`labels`, `fetch[16]` u16 in units of 10 m, `run[8]` u16 in units of 10 ft.

Memory: the real file is a few MB for Michigan and will be much larger nationwide, so the bytes are
held once as an immutable blob and **records are only unpacked for the water bodies somebody asks
about** -- a briefing touches the candidates near home, not the country. `struct.iter_unpack` over a
`memoryview` slice does that without copying and without pulling numpy in for 30 integers a point.
Parsed points are cached per water body, since a run scores each one over several hours.

Nothing here raises: a missing, truncated, or unreadable pack means `load()` returns `None` and the
caller degrades to the lake-level wave numbers, which is exactly the behavior before the pack existed.
"""
from __future__ import annotations

import logging
import struct
from dataclasses import dataclass
from pathlib import Path

from .paths import read_json, wave_points_bin_path, wave_points_index_path

log = logging.getLogger(__name__)

RECORD = struct.Struct("<ffHH16H8H")
RECORD_BYTES = 60
DEPTH_UNKNOWN = 0xFFFF
VERSION = 1

assert RECORD.size == RECORD_BYTES, "the contract fixes the record at 60 bytes"


@dataclass(frozen=True)
class WavePoint:
    """One sample point, in the units the wave model wants rather than the pack's."""

    lon: float
    lat: float
    depth_m: float | None  # None when the pack has no bathymetry here
    label: int  # index into `WaveField.labels`
    fetch_m: tuple[float, ...]  # 16 bins
    run_ft: tuple[float, ...]  # 8 bins


class WaveField:
    """The pack's index plus lazy access to each water body's points."""

    def __init__(self, index: dict, blob: bytes) -> None:
        self.version: int = int(index.get("version") or VERSION)
        self.labels: tuple[str, ...] = tuple(str(s) for s in index.get("labels") or ())
        self.fetch_unit_m: float = float(index.get("fetch_unit_m") or 10)
        self.run_unit_ft: float = float(index.get("run_unit_ft") or 10)
        self._slices: dict[str, tuple[int, int]] = {}
        for key, value in (index.get("lakes") or {}).items():
            if isinstance(value, (list, tuple)) and len(value) == 2:
                self._slices[str(key)] = (int(value[0]), int(value[1]))
        self._blob = memoryview(blob)
        self._cache: dict[str, tuple[WavePoint, ...]] = {}

    # -- queries --------------------------------------------------------------------------

    def __contains__(self, lake_id: int | str) -> bool:
        return str(lake_id) in self._slices

    def __len__(self) -> int:
        return len(self._slices)

    def has_points(self, lake_id: int | str) -> bool:
        return self._slices.get(str(lake_id), (0, 0))[1] > 0

    def points(self, lake_id: int | str) -> tuple[WavePoint, ...]:
        """This water body's points, or `()` when the pack has none for it."""
        key = str(lake_id)
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        first, count = self._slices.get(key, (0, 0))
        points = self._unpack(first, count)
        self._cache[key] = points
        return points

    def label(self, index: int) -> str:
        return self.labels[index] if 0 <= index < len(self.labels) else str(index)

    # -- internals ------------------------------------------------------------------------

    def _unpack(self, first: int, count: int) -> tuple[WavePoint, ...]:
        start = first * RECORD_BYTES
        end = start + count * RECORD_BYTES
        if count <= 0 or start < 0 or end > len(self._blob):
            return ()
        fetch_unit, run_unit = self.fetch_unit_m, self.run_unit_ft
        out: list[WavePoint] = []
        for rec in RECORD.iter_unpack(self._blob[start:end]):
            lon, lat, depth_dm, label = rec[0], rec[1], rec[2], rec[3]
            out.append(
                WavePoint(
                    lon=lon,
                    lat=lat,
                    depth_m=None if depth_dm == DEPTH_UNKNOWN else depth_dm / 10.0,
                    label=label,
                    fetch_m=tuple(v * fetch_unit for v in rec[4:20]),
                    run_ft=tuple(v * run_unit for v in rec[20:28]),
                )
            )
        return tuple(out)


def load(index_path: Path | None = None, bin_path: Path | None = None) -> WaveField | None:
    """Read the pack from `data/out`, or `None` when it is missing or unusable."""
    index_file = index_path or wave_points_index_path()
    bin_file = bin_path or wave_points_bin_path()
    index = read_json(index_file)
    if not isinstance(index, dict) or not isinstance(index.get("lakes"), dict):
        return None
    record_bytes = int(index.get("record_bytes") or RECORD_BYTES)
    if record_bytes != RECORD_BYTES:
        log.warning("wave_points: record_bytes %d, expected %d", record_bytes, RECORD_BYTES)
        return None
    try:
        blob = bin_file.read_bytes()
    except OSError:
        return None
    if len(blob) % RECORD_BYTES:
        log.warning("wave_points: %s is not a whole number of records", bin_file)
        return None
    return WaveField(index, blob)
