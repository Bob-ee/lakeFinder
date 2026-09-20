"""`data/manual/settings.json` -- the model, the defaults, and atomic load/save.

The contract (`docs/data-contract.md`, "Briefing") says: unknown keys are rejected, missing keys are
filled from the defaults, so an older file keeps working. Both fall out of pydantic: every model is
`extra="forbid"` (rejects unknown keys -> 422 from the PUT endpoint) and every field has a default
(fills missing keys, including whole missing sub-objects).

The file is gitignored and is created with these defaults the first time the service starts.
"""
from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .paths import read_json, settings_path, write_json_atomic

_HHMM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
_MMDD = re.compile(r"^(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])$")


class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Runway(_Base):
    id: str = "09R/27L"
    heading: int = 88  # degrees TRUE of the first-named end (see fetch/aviationweather.py)
    # Optional: null/absent in files written before 2026-09-20, which must keep loading. Breaks a
    # crosswind/headwind tie between parallel runways toward the longer one (briefing/aero.py).
    length_ft: int | None = None


class HomeAirport(_Base):
    id: str = "KPTK"
    name: str = "Oakland County Intl"
    lat: float = 42.6655
    lon: float = -83.4187
    elev_ft: int = 981
    # True headings, exactly as aviationweather.gov's `runways[].alignment` gives them for KPTK.
    # The older defaults (91 / 179) were magnetic; KPTK's variation is 07W.
    runways: list[Runway] = Field(
        default_factory=lambda: [
            Runway(id="09L/27R", heading=88, length_ft=5676),
            Runway(id="09R/27L", heading=88, length_ft=6521),
            Runway(id="18/36", heading=172, length_ft=2582),
        ]
    )


class Schedule(_Base):
    run_times_local: list[str] = Field(
        default_factory=lambda: ["06:00", "09:00", "12:00", "15:00", "18:00", "20:00", "22:00"]
    )

    @field_validator("run_times_local")
    @classmethod
    def _check(cls, v: list[str]) -> list[str]:
        return _check_times(v)


class Outlook(_Base):
    times_local: list[str] = Field(default_factory=lambda: ["18:00", "20:00", "22:00"])
    morning_start: str = "sunrise"  # "sunrise" | "civil_twilight" | "HH:MM"
    morning_end_local: str = "12:00"
    min_window_hours: int = 2

    @field_validator("times_local")
    @classmethod
    def _check_times_local(cls, v: list[str]) -> list[str]:
        return _check_times(v)

    @field_validator("morning_start")
    @classmethod
    def _check_start(cls, v: str) -> str:
        if v not in ("sunrise", "civil_twilight") and not _HHMM.match(v):
            raise ValueError("morning_start must be 'sunrise', 'civil_twilight', or HH:MM")
        return v

    @field_validator("morning_end_local")
    @classmethod
    def _check_end(cls, v: str) -> str:
        if not _HHMM.match(v):
            raise ValueError("morning_end_local must be HH:MM")
        return v


class HomeWater(_Base):
    """The one water body briefed on every run whatever `radius_nm` says (contract, "Briefing").

    `id` is an `index.json` water-body id; the PUT endpoint checks it resolves there (422 when it
    does not), which is a filesystem question and so cannot live in this model.
    """

    id: int
    name: str = ""


class Notify(_Base):
    ntfy_url: str | None = None


class Limits(_Base):
    wind_ok: float = 12
    wind_max: float = 18
    gust_spread_ok: float = 8
    gust_spread_max: float = 12
    xwind_runway_ok: float = 8
    xwind_runway_max: float = 12
    xwind_water_max: float = 10
    ceiling_ok: float = 3000
    ceiling_min: float = 1500
    vis_ok: float = 6
    vis_min: float = 3
    da_ok: float = 3500
    da_max: float = 5000
    temp_water_min_f: float = 40
    fog_spread_f: float = 3
    wave_ok_in: float = 8
    wave_max_in: float = 12
    min_run_ft: float = 2000
    ice_season_start: str = "12-01"
    ice_season_end: str = "04-01"

    @field_validator("ice_season_start", "ice_season_end")
    @classmethod
    def _check_mmdd(cls, v: str) -> str:
        if not _MMDD.match(v):
            raise ValueError("ice season dates must be MM-DD")
        return v


class Settings(_Base):
    home_airport: HomeAirport = Field(default_factory=HomeAirport)
    timezone: str = "America/Detroit"
    radius_nm: float = 40
    n_lakes: int = 8
    public_access_only: bool = False
    home_water: HomeWater | None = None  # absent in older files, which is the same as "none set"
    schedule: Schedule = Field(default_factory=Schedule)
    outlook: Outlook = Field(default_factory=Outlook)
    notify: Notify = Field(default_factory=Notify)
    limits: Limits = Field(default_factory=Limits)

    @field_validator("timezone")
    @classmethod
    def _check_tz(cls, v: str) -> str:
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

        try:
            ZoneInfo(v)
        except (ZoneInfoNotFoundError, ValueError) as e:
            raise ValueError(f"unknown timezone {v!r}") from e
        return v

    def all_run_times(self) -> list[str]:
        """`schedule.run_times_local` union `outlook.times_local` -- the outlook times always run."""
        return sorted(set(self.schedule.run_times_local) | set(self.outlook.times_local))


def _check_times(v: list[str]) -> list[str]:
    for t in v:
        if not _HHMM.match(t):
            raise ValueError(f"{t!r} is not an HH:MM local time")
    return v


def load_settings() -> Settings:
    """Read `settings.json`, creating it with the contract defaults when it is missing.

    A file that no longer validates (hand-edited to something impossible) is *not* silently
    replaced: the error propagates so the operator sees it rather than losing their limits.
    """
    raw = read_json(settings_path())
    if raw is None:
        s = Settings()
        save_settings(s)
        return s
    return Settings.model_validate(raw)


def save_settings(settings: Settings) -> None:
    write_json_atomic(settings_path(), settings.model_dump(mode="json"))


def settings_from_dict(payload: dict[str, Any]) -> Settings:
    return Settings.model_validate(payload)
