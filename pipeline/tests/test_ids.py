"""ids.py: stable lake ids, collision bumps, and name normalization."""
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess

import pytest

from seaplane_pipeline import ids


def test_stable_lake_id_matches_the_contract_formula():
    key = "big school lot|42.7712|-83.5901"
    expected = int(hashlib.sha1(key.encode()).hexdigest()[:8], 16) & 0x7FFFFFFF
    assert ids.stable_lake_id(key) == expected
    assert 0 < ids.stable_lake_id(key) <= 0x7FFFFFFF


def test_stable_lake_id_is_deterministic_and_distinct():
    a = ids.stable_lake_id("mud|42.1234|-83.5000")
    assert a == ids.stable_lake_id("mud|42.1234|-83.5000")
    assert a != ids.stable_lake_id("mud|42.1235|-83.5000")


def test_lake_source_key_uses_four_decimals():
    assert ids.lake_source_key("angelus", 42.77119999, -83.59014) == "angelus|42.7712|-83.5901"
    assert ids.lake_source_key(None, 42.0, -83.0) == "|42.0000|-83.0000"


def test_assign_lake_id_bumps_on_collision():
    key = "duplicate|42.0000|-83.0000"
    base = ids.stable_lake_id(key)
    used: set[int] = set()
    first = ids.assign_lake_id(key, used)
    second = ids.assign_lake_id(key, used)
    third = ids.assign_lake_id(key, used)
    assert first == base
    assert second == base + 1
    assert third == base + 2
    assert used == {base, base + 1, base + 2}


def test_assign_lake_id_bump_stays_in_range():
    used = {0x7FFFFFFF}
    assert ids.assign_lake_id("x", {0x7FFFFFFF}) != 0
    bumped = ids.assign_lake_id("y", used)
    assert 0 < bumped <= 0x7FFFFFFF


def test_restriction_id_is_twelve_hex_and_stable():
    rid = ids.restriction_id("Oakland", "Big School Lot Lake", "Rose Township", "…text…")
    assert len(rid) == 12 and all(c in "0123456789abcdef" for c in rid)
    assert rid == ids.restriction_id("Oakland", "Big School Lot Lake", "Rose Township", "…text…")
    assert rid != ids.restriction_id("Oakland", "Big School Lot Lake", "Rose Township", "other")


def test_normalize_name_matches_shared_fixtures(repo_root):
    cases = json.loads((repo_root / "rules" / "fixtures" / "names.json").read_text())
    for case in cases:
        assert ids.normalize_name_local(case["raw"]) == case["norm"], case["raw"]


@pytest.mark.skipif(shutil.which("node") is None, reason="node not on PATH")
def test_normalize_name_matches_the_js_engine(repo_root):
    """Cross-check the Python port against rules/engine/index.js itself, in one Node call."""
    cases = json.loads((repo_root / "rules" / "fixtures" / "names.json").read_text())
    raws = [c["raw"] for c in cases] + [
        "Crooked Lake (Big)", "N. Lk. Leelanau", "  Mud   Lake  ", "Île aux Galets", "Pond", "",
    ]
    script = (
        "import {normalizeName} from './rules/engine/index.js';"
        "const raws = JSON.parse(process.argv[1]);"
        "process.stdout.write(JSON.stringify(raws.map(normalizeName)));"
    )
    out = subprocess.run(
        ["node", "--input-type=module", "-e", script, "--", json.dumps(raws)],
        cwd=repo_root, capture_output=True, text=True, check=True,
    )
    assert json.loads(out.stdout) == [ids.normalize_name_local(r) for r in raws]


def test_normalize_name_uses_the_shared_names_module_when_present(monkeypatch):
    """The `names` module (owned by the parse-dnr side) wins; the local port is the fallback."""
    import sys
    import types

    import seaplane_pipeline

    fake = types.ModuleType("seaplane_pipeline.names")
    fake.normalize_name = lambda raw: "from-names-module"
    monkeypatch.setitem(sys.modules, "seaplane_pipeline.names", fake)
    monkeypatch.setattr(seaplane_pipeline, "names", fake, raising=False)
    monkeypatch.setattr(ids, "_normalizer", None)
    try:
        assert ids.normalize_name("Anything Lake") == "from-names-module"
    finally:
        ids._normalizer = None


def test_resolved_normalizer_agrees_with_the_local_port(repo_root):
    """Whichever implementation is live must still satisfy the shared name fixtures."""
    cases = json.loads((repo_root / "rules" / "fixtures" / "names.json").read_text())
    mismatches = [c["raw"] for c in cases if ids.normalize_name(c["raw"]) != c["norm"]]
    assert not mismatches, f"live normalize_name disagrees with rules/fixtures/names.json: {mismatches}"


def test_strip_qualifiers():
    assert ids.strip_qualifiers("big school lot") == "school lot"
    assert ids.strip_qualifiers("north long") == "long"
    assert ids.strip_qualifiers("big") == "big"  # never strips the last token
    assert ids.name_tokens("big school lot") == {"big", "school", "lot"}
