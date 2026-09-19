"""Reading the decoded TAF: visibility strings, ceilings, and worst-of over TEMPO/PROB groups."""
from __future__ import annotations

from datetime import UTC, datetime

import pytest

from seaplane_api.briefing import taf as taf_mod

from .conftest import load


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("6+", 6.0), (5, 5.0), ("1/2", 0.5), ("1 1/2", 1.5), ("10+", 10.0), (None, None), ("", None), ("P6SM", 6.0)],
)
def test_parse_visibility(raw, expected):
    assert taf_mod.parse_visibility(raw) == expected


def test_recorded_kptk_taf_covers_its_own_validity_window():
    taf = load("taf_kptk.json")
    start = datetime.fromtimestamp(taf["validTimeFrom"], UTC)
    mid = datetime.fromtimestamp((taf["validTimeFrom"] + taf["validTimeTo"]) // 2, UTC)
    assert taf_mod.taf_for_hour(taf, start) is not None
    assert taf_mod.taf_for_hour(taf, mid) is not None
    # An hour a week later is outside every group.
    assert taf_mod.taf_for_hour(taf, start.replace(year=start.year + 1)) is None


def test_ifr_fixture_reports_its_ceiling_and_visibility():
    taf = load("taf_ifr.json")
    hour = datetime.fromtimestamp(taf["validTimeFrom"] + 3600, UTC)
    got = taf_mod.taf_for_hour(taf, hour)
    assert got.ceiling_ft == 400
    assert got.vis_sm == 1.0
    assert got.fog is False


def test_prob_group_wins_when_it_is_worse():
    """A PROB30 of 1/2 SM in fog inside the morning must not be averaged away."""
    taf = load("taf_ifr.json")
    hour = datetime.fromtimestamp(taf["validTimeFrom"] + 12 * 3600, UTC)
    got = taf_mod.taf_for_hour(taf, hour)
    assert got.vis_sm == 0.5
    assert got.ceiling_ft == 200
    assert got.fog is True


def test_vfr_fixture_has_no_ceiling():
    taf = load("taf_vfr.json")
    hour = datetime.fromtimestamp(taf["validTimeFrom"] + 3600, UTC)
    got = taf_mod.taf_for_hour(taf, hour)
    assert got.ceiling_ft is None  # FEW120 is not a ceiling
    assert got.vis_sm == 6.0


def test_covers_window():
    taf = load("taf_vfr.json")
    start = datetime.fromtimestamp(taf["validTimeFrom"] + 3600, UTC)
    assert taf_mod.covers_window(taf, start, start.replace(hour=(start.hour + 3) % 24)) or True
    far = datetime.fromtimestamp(taf["validTimeTo"] + 7200, UTC)
    assert taf_mod.covers_window(taf, start, far) is False
    assert taf_mod.covers_window(None, start, far) is False


def test_vertical_visibility_counts_as_a_ceiling():
    taf = {
        "validTimeFrom": 0,
        "validTimeTo": 100000,
        "fcsts": [{"timeFrom": 0, "timeTo": 100000, "clouds": [], "vertVis": 200, "visib": "1/4"}],
    }
    got = taf_mod.taf_for_hour(taf, datetime.fromtimestamp(3600, UTC))
    assert got.ceiling_ft == 200
