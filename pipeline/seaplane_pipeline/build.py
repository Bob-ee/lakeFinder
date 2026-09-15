"""Stage 7 (`build`): emit the served data pack into `data/out/`.

Files (schemas in docs/data-contract.md): `lakes.pmtiles`, `usable_water.pmtiles`,
`overlays.pmtiles`, `basemap.pmtiles`, `index.json`, `restrictions.json`, `rules.json`, `pack.json`.

Tiles are cut with tippecanoe using the flags the contract pins for the `lakes` layer. Tippecanoe
2.17+ writes `.pmtiles` directly; older builds fall back to `.mbtiles` + `pmtiles convert`, which is
detected from `tippecanoe --version`.

The basemap is a Protomaps extract pulled over HTTP range requests from the latest daily planet
build (`https://build-metadata.protomaps.dev/builds.json` -> last entry). It is skipped when the
file already exists unless `--force`, and entirely with `--skip-basemap`.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import shutil
import subprocess
import time
from pathlib import Path

import geopandas as gpd
import httpx
import pandas as pd
import shapely

from . import gis, manual
from . import match as match_mod
from . import overlay as overlay_mod
from .config import MICHIGAN_BBOX, USER_AGENT, Config

log = logging.getLogger(__name__)

BUILDS_JSON = "https://build-metadata.protomaps.dev/builds.json"
BUILD_BASE = "https://build.protomaps.com/"
BASEMAP_WARN_BYTES = 150 * 1024 * 1024

LAKES_FLAGS = [
    "-z14", "-Z6",
    "--no-feature-limit", "--no-tile-size-limit",
    "--detect-shared-borders", "--coalesce-densest-as-needed",
    "--extend-zooms-if-still-dropping",
    "-l", "lakes",
]


def add_args(sp) -> None:
    sp.add_argument("--skip-basemap", action="store_true", help="Do not extract the Protomaps basemap")
    sp.add_argument("--skip-tiles", action="store_true", help="Only write the JSON files")


# --- helpers -----------------------------------------------------------------


def is_stable(path: Path, wait: float = 2.0, quiet_seconds: float = 10.0) -> bool:
    """True when `path` is not being written right now.

    Requires both an unchanged size across `wait` seconds and an mtime at least `quiet_seconds`
    old. The basemap extract is long-running and may be driven by a separate process; hashing a
    half-written file into pack.json would publish a manifest the client can never verify.
    """
    try:
        stat = path.stat()
    except FileNotFoundError:
        return False
    if time.time() - stat.st_mtime < quiet_seconds:
        return False
    time.sleep(wait)
    try:
        return path.stat().st_size == stat.st_size
    except FileNotFoundError:
        return False


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _clean(value):
    """NaN/NaT/pandas-NA -> None, numpy scalars -> plain Python."""
    if value is None:
        return None
    try:
        if bool(pd.isna(value)):
            return None
    except (TypeError, ValueError):  # arrays and other non-scalars
        pass
    return value.item() if hasattr(value, "item") else value


def write_geojson(path: Path, features) -> Path:
    """Write a FeatureCollection. `features` yields (geometry, properties, feature_id|None)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        fh.write('{"type":"FeatureCollection","features":[\n')
        first = True
        for geom, props, fid in features:
            if geom is None or geom.is_empty:
                continue
            if not first:
                fh.write(",\n")
            first = False
            head = f'{{"type":"Feature","id":{int(fid)},' if fid is not None else '{"type":"Feature",'
            fh.write(head + f'"properties":{json.dumps(props)},"geometry":{shapely.to_geojson(geom)}}}')
        fh.write("\n]}\n")
    return path


def tippecanoe_writes_pmtiles(tippecanoe: str = "tippecanoe") -> bool:
    try:
        out = subprocess.run([tippecanoe, "--version"], capture_output=True, text=True, check=False)
    except FileNotFoundError:
        raise RuntimeError("tippecanoe is not on PATH") from None
    text = (out.stdout + out.stderr).strip()
    m = re.search(r"v?(\d+)\.(\d+)", text)
    if not m:
        return False
    major, minor = int(m.group(1)), int(m.group(2))
    return (major, minor) >= (2, 17)


def run_tippecanoe(out_path: Path, args: list[str], tippecanoe: str = "tippecanoe") -> Path:
    """Run tippecanoe, converting from .mbtiles afterwards when it cannot write pmtiles itself."""
    direct = tippecanoe_writes_pmtiles(tippecanoe)
    target = out_path if direct else out_path.with_suffix(".mbtiles")
    cmd = [tippecanoe, "-o", str(target), "--force", *args]
    log.debug("running %s", " ".join(cmd))
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise RuntimeError(f"tippecanoe failed: {proc.stderr.strip()[-2000:]}")
    if not direct:
        conv = subprocess.run(
            ["pmtiles", "convert", str(target), str(out_path)],
            capture_output=True, text=True, check=False,
        )
        if conv.returncode != 0:
            raise RuntimeError(f"pmtiles convert failed: {conv.stderr.strip()[-2000:]}")
        target.unlink(missing_ok=True)
    return out_path


def latest_protomaps_build() -> str:
    with httpx.Client(headers={"User-Agent": USER_AGENT}, follow_redirects=True, timeout=60.0) as client:
        resp = client.get(BUILDS_JSON)
        resp.raise_for_status()
        builds = resp.json()
    if not builds:
        raise RuntimeError("builds.json was empty")
    return builds[-1]["key"]  # sorted oldest -> newest


def extract_basemap(dest: Path, force: bool = False) -> Path | None:
    if dest.exists() and not force:
        log.info("basemap.pmtiles already present (%.1f MB); skipping", dest.stat().st_size / 1e6)
        return dest
    key = latest_protomaps_build()
    bbox = ",".join(str(v) for v in MICHIGAN_BBOX)
    cmd = ["pmtiles", "extract", BUILD_BASE + key, str(dest), f"--bbox={bbox}", "--maxzoom=14"]
    log.info("extracting basemap from %s (this takes a while)", key)
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        log.error("pmtiles extract failed: %s", proc.stderr.strip()[-2000:])
        return None
    size = dest.stat().st_size
    log.info("basemap.pmtiles: %.1f MB from %s", size / 1e6, key)
    if size > BASEMAP_WARN_BYTES:
        log.warning("basemap.pmtiles is over the 150 MB target (%.1f MB); trim layers later", size / 1e6)
    return dest


# --- payloads ----------------------------------------------------------------


def build_index(lakes: gpd.GeoDataFrame, verdicts: dict, overlays: dict, by_lake: dict) -> list[dict]:
    entries = []
    for row in lakes.itertuples(index=False):
        lake_id = int(row.id)
        key = str(lake_id)
        verdict = verdicts.get(key) or {}
        ov = overlays.get(key) or {}
        name = _clean(getattr(row, "name", None))
        entries.append(
            {
                "id": lake_id,
                "name": str(name) if name else None,
                "name_norm": _clean(row.name_norm) or "",
                "county": _clean(row.county),
                "township": _clean(row.township),
                "lat": _clean(row.lat),
                "lon": _clean(row.lon),
                "bbox": [_clean(row.minx), _clean(row.miny), _clean(row.maxx), _clean(row.maxy)],
                "area_acres": _clean(row.area_acres),
                "chord_ft": _clean(row.chord_ft),
                "chord_bearing_deg": (
                    int(row.chord_bearing_deg) if _clean(row.chord_bearing_deg) is not None else None
                ),
                "verdict": verdict.get("verdict", "unknown"),
                "flags": verdict.get("flags", []),
                "restriction_ids": [r["restriction_id"] for r in by_lake.get(key, [])],
                "access": ov.get("access"),
            }
        )
    return entries


def build_restrictions(records: list[dict], matches: list[dict], synthetic: list[dict]) -> dict[str, dict]:
    """Active records only, keyed by restriction_id, with `lake_ids` filled in.

    A match below `match.ACCEPT` also sets `needs_review` on the published record, so the client,
    the engine's `needs_review` flag and `pack.json`'s `counts.needs_review` all agree about what a
    human still has to confirm.
    """
    lake_ids: dict[str, list[int]] = {}
    confidence: dict[str, float] = {}
    for m in matches:
        lake_ids.setdefault(m["restriction_id"], []).append(int(m["lake_id"]))
        confidence[m["restriction_id"]] = min(confidence.get(m["restriction_id"], 1.0), float(m["score"]))
    out: dict[str, dict] = {}
    for rec in records:
        if rec.get("status", "active") != "active":
            continue
        rid = rec["restriction_id"]
        published = dict(rec)
        published["lake_ids"] = sorted(set(lake_ids.get(rid, [])))
        if rid in confidence and confidence[rid] < 1.0:
            published["match_confidence"] = round(confidence[rid], 3)
            if confidence[rid] < match_mod.ACCEPT:
                published["needs_review"] = True
        out[rid] = published
    for rec in synthetic:
        published = dict(rec)
        published["lake_ids"] = sorted({int(i) for i in rec.get("lake_ids") or []})
        out[rec["restriction_id"]] = published
    return out


# --- stage -------------------------------------------------------------------


def _overlay_layers(cfg: Config, tmp: Path) -> list[str]:
    """Write one GeoJSON per overlay layer and return the tippecanoe -L arguments."""
    args: list[str] = []
    federal = overlay_mod.load_federal_units(cfg)
    if len(federal):
        path = write_geojson(
            tmp / "federal.geojson",
            ((g, {"name": str(n), "kind": str(k)}, None) for g, n, k in
             zip(federal.geometry, federal["name"], federal["kind"], strict=True)),
        )
        args += ["-L", f"federal:{path}"]

    air_path = gis.find_dataset(cfg, "faa_airspace")
    if air_path:
        air = gpd.read_file(air_path)
        path = write_geojson(
            tmp / "airspace.geojson",
            (
                (
                    row.geometry,
                    {
                        "name": str(getattr(row, "NAME", "") or ""),
                        "kind": "airspace",
                        "class": _clean(getattr(row, "CLASS", None)),
                        "lower_val": _clean(getattr(row, "LOWER_VAL", None)),
                        "upper_val": _clean(getattr(row, "UPPER_VAL", None)),
                    },
                    None,
                )
                for row in air.itertuples(index=False)
            ),
        )
        args += ["-L", f"airspace:{path}"]

    bas_path = gis.find_dataset(cfg, "bas")
    if bas_path:
        bas = gpd.read_file(bas_path)
        path = write_geojson(
            tmp / "bas.geojson",
            (
                (
                    row.geometry,
                    {
                        "name": str(getattr(row, "name", "") or ""),
                        "kind": "bas",
                        "waterbody": _clean(getattr(row, "waterbody", None)),
                        "waterbodytype": _clean(getattr(row, "waterbodytype", None)),
                        "ownedby": _clean(getattr(row, "ownedby", None)),
                    },
                    None,
                )
                for row in bas.itertuples(index=False)
            ),
        )
        args += ["-L", f"bas:{path}"]

    apt_path = gis.find_dataset(cfg, "faa_airports")
    if apt_path:
        apt = gpd.read_file(apt_path)
        path = write_geojson(
            tmp / "airports.geojson",
            (
                (
                    row.geometry,
                    {
                        "name": str(getattr(row, "NAME", "") or ""),
                        "kind": "airport",
                        "ident": _clean(getattr(row, "IDENT", None)),
                        "icao_id": _clean(getattr(row, "ICAO_ID", None)),
                        "type_code": _clean(getattr(row, "TYPE_CODE", None)),
                    },
                    None,
                )
                for row in apt.itertuples(index=False)
            ),
        )
        args += ["-L", f"airports:{path}"]
    return args


def run(cfg: Config, args) -> int:
    started = time.monotonic()
    work, out = cfg.work_dir, cfg.out_dir
    out.mkdir(parents=True, exist_ok=True)
    tmp = work / "tiles-src"
    tmp.mkdir(parents=True, exist_ok=True)

    lakes_path = work / "lakes.parquet"
    if not lakes_path.exists():
        log.error("%s not found; run `seaplane geometry` first", lakes_path)
        return 2
    lakes = gpd.read_parquet(lakes_path)

    def _load(name, default):
        p = work / name
        return json.loads(p.read_text()) if p.exists() else default

    verdicts = _load("verdicts.json", {})
    overlays = _load("overlays.json", {})
    matches = _load("matches.json", [])
    synthetic = _load("synthetic_restrictions.json", [])
    rpath = work / "restrictions.jsonl"
    records = match_mod.read_restrictions(rpath) if rpath.exists() else []

    restrictions = build_restrictions(records, matches, synthetic)
    by_lake: dict[str, list[dict]] = {}
    for rec in restrictions.values():
        for lake_id in rec.get("lake_ids") or []:
            by_lake.setdefault(str(int(lake_id)), []).append(rec)

    index = build_index(lakes, verdicts, overlays, by_lake)
    (out / "index.json").write_text(json.dumps(index, separators=(",", ":")), encoding="utf-8")
    (out / "restrictions.json").write_text(json.dumps(restrictions, separators=(",", ":")), encoding="utf-8")
    rules_json = json.loads((cfg.rules_dir / "rules.json").read_text())
    shutil.copyfile(cfg.rules_dir / "rules.json", out / "rules.json")

    emitted = ["index.json", "restrictions.json", "rules.json"]

    if not getattr(args, "skip_tiles", False):
        by_id = {e["id"]: e for e in index}
        lakes_geojson = write_geojson(
            tmp / "lakes.geojson",
            (
                (
                    row.geometry,
                    {
                        # `lake_id` is consumed by --use-attribute-for-id (tippecanoe drops the
                        # attribute it promotes), so `id` is carried separately to keep the
                        # contract's `id` property for MapLibre's promoteId: "id".
                        "id": int(row.id),
                        "lake_id": int(row.id),
                        "name": by_id[int(row.id)]["name"],
                        "verdict": by_id[int(row.id)]["verdict"],
                        "flags": ",".join(by_id[int(row.id)]["flags"]),
                        "county": by_id[int(row.id)]["county"],
                    },
                    int(row.id),
                )
                for row in lakes.itertuples(index=False)
            ),
        )
        t0 = time.monotonic()
        run_tippecanoe(
            out / "lakes.pmtiles", [*LAKES_FLAGS, "--use-attribute-for-id=lake_id", str(lakes_geojson)]
        )
        log.info("lakes.pmtiles in %.1fs", time.monotonic() - t0)
        emitted.append("lakes.pmtiles")

        usable_path = work / "usable_water.parquet"
        if usable_path.exists():
            usable = gpd.read_parquet(usable_path)
            usable_geojson = write_geojson(
                tmp / "usable_water.geojson",
                (
                    (row.geometry, {"id": int(row.id), "lake_id": int(row.id)}, int(row.id))
                    for row in usable.itertuples(index=False)
                ),
            )
            run_tippecanoe(
                out / "usable_water.pmtiles",
                [
                    "-z14", "-Z12", "--no-feature-limit", "--no-tile-size-limit",
                    "--use-attribute-for-id=lake_id", "-l", "usable_water", str(usable_geojson),
                ],
            )
            emitted.append("usable_water.pmtiles")

        layer_args = _overlay_layers(cfg, tmp)
        if layer_args:
            run_tippecanoe(
                out / "overlays.pmtiles",
                ["-z14", "-Z6", "--no-feature-limit", "--no-tile-size-limit", "-r1", *layer_args],
            )
            emitted.append("overlays.pmtiles")

    basemap = out / "basemap.pmtiles"
    if not getattr(args, "skip_basemap", False):
        if extract_basemap(basemap, force=cfg.force):
            emitted.append("basemap.pmtiles")
    else:
        log.info("skipping basemap extract (--skip-basemap)")
        if basemap.exists():
            if is_stable(basemap):
                emitted.append("basemap.pmtiles")
            else:
                log.warning(
                    "basemap.pmtiles is still growing (another process is extracting it); "
                    "leaving it out of pack.json -- rerun `build` once it settles"
                )

    mac = manual.load_mac_record(cfg)
    needs_review = sum(1 for r in restrictions.values() if r.get("needs_review"))
    pack = {
        "version": cfg.run_date,
        "built_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "rules_version": rules_json.get("version"),
        "files": [
            {"name": name, "bytes": (out / name).stat().st_size, "sha256": sha256_file(out / name)}
            for name in emitted
            if (out / name).exists()
        ],
        "counts": {"lakes": len(index), "restrictions": len(restrictions), "needs_review": needs_review},
        "mac_record_loaded": bool(mac["loaded"]),
    }
    (out / "pack.json").write_text(json.dumps(pack, indent=1), encoding="utf-8")
    log.info(
        "built %d files into %s in %.1fs: %s",
        len(pack["files"]) + 1, out, time.monotonic() - started,
        ", ".join(f"{f['name']} {f['bytes'] / 1e6:.2f} MB" for f in pack["files"]),
    )
    return 0
