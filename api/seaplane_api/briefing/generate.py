"""Orchestration: feeds in, `briefing.json` out.

Split in two on purpose:

- `collect_feeds` is the only async function and the only one that touches the network. Each feed is
  tried independently; a failure records an error string and leaves that input `None`.
- `build_briefing` is pure: settings + feeds + candidate lakes + "now" -> the contract dict. Tests
  call it with fixtures and never open a socket.

Degradation contract (design 2): every fetcher failure degrades one input, appends to `errors`, and
sets its `sources` entry to `null`. If Open-Meteo fails entirely there is no forecast at all, so
`days` is `[]` and `outlook` is `null` -- but the file is still written, with the errors in it.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import httpx

from ..fetch import aviationweather, ndbc, nws, openmeteo
from ..settings import Settings
from . import blocks as blocks_mod
from . import homewater as homewater_mod
from . import lakes as lakes_mod
from . import outlook as outlook_mod
from . import render
from . import taf as taf_mod
from . import timeline as timeline_mod
from .scoring import FAVORABLE, rank, score_conditions
from .series import HourlySeries, conditions_from_model, daylight_fraction, metar_is_fresh
from .sun import SunTimes, local_sun_times

log = logging.getLogger(__name__)

SCHEMA = 1
ICE_LOOKBACK_DAYS = 5


@dataclass
class Feeds:
    """Everything one run fetched. `None` means that input failed or was unavailable."""

    metar: dict | None = None
    taf: dict | None = None
    airport: HourlySeries | None = None
    airport_daily: dict | None = None
    lake_series: dict[tuple[int, int], HourlySeries] = field(default_factory=dict)
    alerts: list[dict] | None = None
    nws_hourly: list[dict] | None = None
    buoys: list[dict] | None = None  # NDBC rows near the home water
    home_water_metars: list[dict] | None = None  # METARs from the bbox around the home water
    marine: dict | None = None  # Open-Meteo marine at the home water's centroid
    buoy_history: dict | None = None  # {"station": id, "rows": NDBC realtime2 rows, newest first}
    fetched_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    errors: list[str] = field(default_factory=list)


async def collect_feeds(
    settings: Settings,
    candidates: list[lakes_mod.Candidate],
    *,
    client: httpx.AsyncClient,
    home_water: lakes_mod.Candidate | None = None,
) -> Feeds:
    """Fetch every input. Never raises; failures land in `feeds.errors`."""
    home = settings.home_airport
    feeds = Feeds()

    metars, err = await aviationweather.fetch_metar(client, home.id)
    if err:
        feeds.errors.append(err)
    else:
        feeds.metar = aviationweather.pick_station(metars, home.id)

    tafs, err = await aviationweather.fetch_taf(client, home.id)
    if err:
        feeds.errors.append(err)
    else:
        feeds.taf = aviationweather.pick_station(tafs, home.id)

    tz = ZoneInfo(settings.timezone)
    fc = settings.forecast
    # The airport request keeps `past_days` for the ice gate's daily maxima, and asks for as many
    # forecast days as the timeline needs; `forecast_hours` / `past_hours` cannot be combined with it.
    airport_payloads, om_errors = await openmeteo.fetch_points(
        client,
        [(home.lat, home.lon)],
        timezone=settings.timezone,
        forecast_days=max(2, fc.forecast_days),
        past_days=max(ICE_LOOKBACK_DAYS, fc.past_days),
        daily=("temperature_2m_max",),
        models=fc.wind_models,
    )
    feeds.errors.extend(om_errors)
    if airport_payloads and airport_payloads[0]:
        feeds.airport = HourlySeries.from_open_meteo(airport_payloads[0], tz)
        feeds.airport_daily = airport_payloads[0].get("daily")

    # The home water is briefed whatever `radius_nm` says, so its own grid cell joins the list even
    # when it is not a candidate. Its wind comes from its own centroid, never from the airport.
    cell_of = lakes_mod.grid_cells(candidates if home_water is None else [*candidates, home_water])
    if cell_of:
        # The cells only feed the lake and home-water wave math, which reads wind and nothing else, so
        # the request asks for wind alone: the longer axis and the second model then cost no more calls
        # and little more payload than before.
        payloads, errs = await openmeteo.fetch_points(
            client,
            list(cell_of.values()),
            timezone=settings.timezone,
            forecast_days=max(2, fc.forecast_days),
            past_days=fc.past_days,
            models=fc.wind_models,
            hourly=openmeteo.WIND_VARS,
        )
        feeds.errors.extend(errs)
        for cell, payload in zip(cell_of.keys(), payloads, strict=False):
            if payload:
                feeds.lake_series[cell] = HourlySeries.from_open_meteo(payload, tz)

    if home_water is not None:
        await _collect_home_water(feeds, home_water, client=client, timezone=settings.timezone)

    alerts, err = await nws.fetch_alerts(client, home.lat, home.lon)
    if err:
        feeds.errors.append(err)
    else:
        feeds.alerts = alerts

    hourly, err = await nws.fetch_hourly(client, home.lat, home.lon)
    if err:
        feeds.errors.append(err)
    else:
        feeds.nws_hourly = hourly

    return feeds


async def _collect_home_water(
    feeds: Feeds, home_water: lakes_mod.Candidate, *, client: httpx.AsyncClient, timezone: str
) -> None:
    """The home water's second opinions: buoys, the METARs around it, and the marine model.

    All three are optional. Each failure is one error string and one `None`; none of them can stop
    the home water being briefed from the computed wave field.
    """
    min_lat, min_lon, max_lat, max_lon = homewater_mod.bbox_around(home_water)
    buoys, err = await ndbc.fetch_latest_obs(client, (min_lon, min_lat, max_lon, max_lat))
    if err:
        feeds.errors.append(err)
    else:
        feeds.buoys = buoys

    metars, err = await aviationweather.fetch_metar_bbox(client, (min_lat, min_lon, max_lat, max_lon))
    if err:
        feeds.errors.append(err)
    else:
        feeds.home_water_metars = metars

    marine, err = await openmeteo.fetch_marine(client, home_water.lat, home_water.lon, timezone=timezone)
    if err:
        feeds.errors.append(err)
    else:
        feeds.marine = marine

    # The timeline's observed history: the nearest buoy that reports waves, its last 45 days of hourly
    # rows. One more NOAA call, not an Open-Meteo one. Skipped when the latest-obs file had no such buoy.
    station = homewater_mod.history_station(home_water, feeds.buoys)
    if station is not None:
        rows, err = await ndbc.fetch_history(client, station)
        if err:
            feeds.errors.append(err)
        else:
            feeds.buoy_history = {"station": station, "rows": rows}


def build_briefing(
    settings: Settings,
    feeds: Feeds,
    candidates: list[lakes_mod.Candidate],
    *,
    now_utc: datetime,
    run_kind: str,
    run_at: str | None = None,
    previous: dict | None = None,
    extra_errors: list[str] | None = None,
    home_water: lakes_mod.Candidate | None = None,
) -> dict:
    """Assemble the contract's `briefing.json` dict. Pure."""
    tz = ZoneInfo(settings.timezone)
    now_local = now_utc.astimezone(tz)
    home = settings.home_airport
    limits = settings.limits
    errors = list(feeds.errors) + list(extra_errors or [])

    suns: dict[date, SunTimes] = {
        d: local_sun_times(d, home.lat, home.lon, tz)
        for d in (now_local.date() + timedelta(days=n) for n in (-1, 0, 1, 2))
    }
    alert_events = [a["event"] for a in (feeds.alerts or [])]
    frozen, ice_reason = _ice_gate(feeds.airport_daily, now_local.date(), limits)
    if ice_reason:
        errors.append(ice_reason)

    day_blocks: list[blocks_mod.Block] = []
    if feeds.airport is not None:
        horizon = suns[now_local.date() + timedelta(days=1)].civil_dusk or (now_local + timedelta(days=2))
        spans = blocks_mod.block_spans(now_local, suns, horizon)
        use_metar = metar_is_fresh(feeds.metar, now_utc)
        for i, (start, end) in enumerate(spans):
            center = start + timedelta(hours=blocks_mod.BLOCK_HOURS // 2)
            model = feeds.airport.at(center)
            if not model:
                continue
            sun = suns.get(start.date())
            conditions = conditions_from_model(
                model,
                field_elev_ft=home.elev_ft,
                taf_hour=taf_mod.taf_for_hour(feeds.taf, center.replace(minute=0, second=0, microsecond=0)),
                alerts=alert_events,
                daylight=daylight_fraction(start, end, sun.civil_dawn if sun else None, sun.civil_dusk if sun else None),
                runways=[r.model_dump() for r in home.runways],
                metar=feeds.metar if (i == 0 and use_metar) else None,
            )
            day_blocks.append(
                blocks_mod.Block(
                    start=start,
                    end=end,
                    center=center,
                    conditions=conditions,
                    score=score_conditions(conditions, limits),
                )
            )

    days = blocks_mod.group_days(day_blocks, suns)
    today = next((d for d in days if d["date"] == now_local.date().isoformat()), None)
    tomorrow = next((d for d in days if d["date"] == (now_local.date() + timedelta(days=1)).isoformat()), None)

    lake_rows, _, scope_centers = _rank_for_blocks(
        day_blocks, today, candidates, feeds, limits, frozen=frozen, n_lakes=settings.n_lakes
    )

    home_water_row = _home_water_block(
        home_water, scope_centers, feeds, limits, frozen=frozen, now_utc=now_utc, with_observed=True
    )
    if home_water is not None and home_water_row is None and scope_centers:
        # There are blocks to score but no wind for this water: worth saying, since the pilot asked
        # for it by name. No blocks at all (after dark) is not an error, it is the end of the day.
        errors.append(f"home_water: no forecast covered {home_water.name}")

    first_block = day_blocks[0] if day_blocks else None
    summary = render.summary(
        airport_id=home.id,
        today=today,
        tomorrow=tomorrow,
        first_block=first_block.to_row() if first_block else None,
        ceiling_known=bool(first_block and first_block.conditions.ceiling_known),
        alerts=feeds.alerts or [],
        lake_rows=lake_rows,
    )

    outlook = None
    if feeds.airport is not None:
        outlook = _build_outlook(
            settings,
            feeds,
            candidates,
            now_utc=now_utc,
            now_local=now_local,
            tz=tz,
            suns=suns,
            alert_events=alert_events,
            frozen=frozen,
            run_at=run_at,
            run_kind=run_kind,
            previous=previous,
            home_water=home_water,
        )

    timeline = timeline_mod.build_timeline(
        settings, feeds, now_utc=now_utc, tz=tz, frozen=frozen, home_water=home_water
    )

    # No `valid_from` / `valid_to`: the superseded design-doc sketch had them, the authoritative
    # contract does not, and the client can read the span off `days[].blocks[]`.
    return {
        "schema": SCHEMA,
        "generated_at": _iso_z(now_utc),
        "run_kind": run_kind,
        "timezone": settings.timezone,
        "home_airport": {"id": home.id, "name": home.name, "lat": home.lat, "lon": home.lon},
        "summary": summary,
        "days": days,
        "outlook": outlook,
        "timeline": timeline,
        "alerts": feeds.alerts or [],
        "lakes": lake_rows,
        "home_water": home_water_row,
        "sources": _sources(feeds),
        "links": {
            "metar": aviationweather.METAR_LINK.format(ids=home.id),
            "taf": aviationweather.TAF_LINK.format(ids=home.id),
            "forecast": openmeteo.FORECAST_LINK.format(lat=home.lat, lon=home.lon),
        },
        "errors": errors,
    }


def _rank_for_blocks(
    day_blocks: list[blocks_mod.Block],
    day: dict | None,
    candidates: list[lakes_mod.Candidate],
    feeds: Feeds,
    limits,
    *,
    frozen: bool,
    n_lakes: int,
) -> tuple[list[dict], list[lakes_mod.RankedLake], list[datetime]]:
    """Rank lakes over today's best window, or over every remaining block when there is none.

    The block centres are returned as well, because the home water is scored over exactly the same
    span as the rows beside it: two different windows in one briefing would be unreadable.
    """
    if not day_blocks:
        return [], [], []
    today_blocks = [b for b in day_blocks if day and b.start.date().isoformat() == day["date"]] or day_blocks
    window = day.get("best_window") if day else None
    scope = today_blocks
    if window:
        scope = [b for b in today_blocks if window[0] <= b.start.strftime("%H:%M") < window[1]] or today_blocks
    centers = [b.center for b in scope]
    ranked = lakes_mod.rank_lakes(
        candidates,
        feeds.lake_series,
        centers,
        limits,
        frozen=frozen,
        n_lakes=n_lakes,
    )
    return [r.to_row() for r in ranked], ranked, centers


def _home_water_block(
    home_water: lakes_mod.Candidate | None,
    when: list[datetime],
    feeds: Feeds,
    limits,
    *,
    frozen: bool,
    now_utc: datetime,
    with_observed: bool,
) -> dict | None:
    """The `home_water` object over `when`, worst hour, or `None` when there is nothing to say."""
    if home_water is None or not when:
        return None
    ranked = lakes_mod.score_over(home_water, feeds.lake_series, when, limits, frozen=frozen)
    if ranked is None:
        return None
    observed = None
    if with_observed:
        observed = homewater_mod.observed_rows(
            home_water, buoys=feeds.buoys, metars=feeds.home_water_metars, now_utc=now_utc
        )
    marine = None
    if ranked.hour.when is not None:
        marine = homewater_mod.marine_hs_in(feeds.marine, ranked.hour.when, home_water)
    return homewater_mod.block(ranked, limits, observed=observed, marine_hs_in=marine)


def _outlook_hour(
    h: datetime,
    feeds: Feeds,
    settings: Settings,
    sun: SunTimes,
    alert_events: list[str],
    now_local: datetime,
    use_metar: bool,
) -> outlook_mod.Hour | None:
    """One scored outlook hour, or `None` when the model has nothing for it."""
    home = settings.home_airport
    model = feeds.airport.at(h) if feeds.airport else {}
    if not model:
        return None
    conditions = conditions_from_model(
        model,
        field_elev_ft=home.elev_ft,
        taf_hour=taf_mod.taf_for_hour(feeds.taf, h),
        alerts=alert_events,
        daylight=daylight_fraction(h, h + timedelta(hours=1), sun.civil_dawn, sun.civil_dusk),
        runways=[r.model_dump() for r in home.runways],
        metar=feeds.metar if (use_metar and h <= now_local < h + timedelta(hours=1)) else None,
    )
    return outlook_mod.Hour(start=h, conditions=conditions, score=score_conditions(conditions, settings.limits))


def _build_outlook(
    settings: Settings,
    feeds: Feeds,
    candidates: list[lakes_mod.Candidate],
    *,
    now_utc: datetime,
    now_local: datetime,
    tz,
    suns: dict[date, SunTimes],
    alert_events: list[str],
    frozen: bool,
    run_at: str | None,
    run_kind: str,
    previous: dict | None,
    home_water: lakes_mod.Candidate | None = None,
) -> dict | None:
    limits = settings.limits
    home = settings.home_airport
    cfg = settings.outlook
    use_metar = metar_is_fresh(feeds.metar, now_utc)
    target = outlook_mod.target_date(now_local, cfg.morning_end_local)

    # When the target is today the window has usually started, so hours that are already over are
    # dropped: a 09:00 run must not offer "favorable 07:00-12:00". If that empties the window -- the
    # run lands inside the last hour of the morning -- the outlook rolls to tomorrow rather than
    # reporting a morning with nothing left in it.
    hours: list[outlook_mod.Hour] = []
    sun = suns.get(target) or local_sun_times(target, home.lat, home.lon, tz)
    start, end = outlook_mod.window_bounds(target, sun, cfg.morning_start, cfg.morning_end_local, tz)
    for attempt in range(2):
        cutoff = now_local if target == now_local.date() else None
        hours = [
            hour
            for h in outlook_mod.window_hours(start, end, cutoff)
            if (hour := _outlook_hour(h, feeds, settings, sun, alert_events, now_local, use_metar)) is not None
        ]
        if hours or attempt == 1:
            break
        target = now_local.date() + timedelta(days=1)
        sun = suns.get(target) or local_sun_times(target, home.lat, home.lon, tz)
        start, end = outlook_mod.window_bounds(target, sun, cfg.morning_start, cfg.morning_end_local, tz)

    if not hours:
        return None

    score, best_window, lo, hi = outlook_mod.score_window(hours, cfg.min_window_hours)
    limiting = outlook_mod.limiting_in_window(hours, lo, hi) if score != FAVORABLE else None
    watch = outlook_mod.watch_after(hours, hi) if score == FAVORABLE else None

    scope = hours[lo:hi] if lo is not None else hours
    max_gust = max(
        (round(h.conditions.gust_kt) for h in scope if h.conditions.gust_kt is not None), default=None
    )
    scope_hours = [h.start for h in scope]
    lake_rows = [
        r.to_row()
        for r in lakes_mod.rank_lakes(
            candidates,
            feeds.lake_series,
            scope_hours,
            limits,
            frozen=frozen,
            n_lakes=settings.n_lakes,
        )
    ]
    # No `observed` here: the outlook is about a morning that has not happened, and a reading from
    # this evening pinned under tomorrow's numbers would read as this morning's water.
    home_water_row = _home_water_block(
        home_water, scope_hours, feeds, limits, frozen=frozen, now_utc=now_utc, with_observed=False
    )

    runs = outlook_mod.carry_runs(previous, target)
    prev_entry = runs[-1] if runs else None
    score_move = None if prev_entry is None else abs(rank(score) - rank(prev_entry.get("score", score)))

    # Both of these are measured over the hours actually scored, not the nominal window. Once past
    # hours are trimmed, asking "does the TAF cover 07:17?" at 09:30 answers a question nobody is
    # being told the answer to -- the TAF that matters is the one over the morning that is left.
    scored_from = hours[0].start
    scored_to = hours[-1].start + timedelta(hours=1)
    nws_peak = nws.peak_wind_kt(feeds.nws_hourly, scored_from.isoformat(), scored_to.isoformat())
    model_peak = max((h.conditions.wind_kt for h in hours), default=None)
    wind_diff = None if (nws_peak is None or model_peak is None) else abs(nws_peak - model_peak)

    covers = taf_mod.covers_window(feeds.taf, scored_from, scored_to)
    agrees, any_coverage, extra_reasons = _taf_agreement(hours, covers)
    confidence, reasons = outlook_mod.confidence(
        taf_covers=covers,
        taf_agrees=agrees,
        taf_any_coverage=any_coverage,
        wind_diff_kt=wind_diff,
        score_move=score_move,
        extra_reasons=extra_reasons,
    )

    at = run_at or now_local.strftime("%H:%M")
    if _should_record(run_at, cfg.times_local, target, now_local, runs):
        runs = outlook_mod.append_run(
            runs,
            {
                "at": at,
                "generated_at": _iso_z(now_utc),
                "score": score,
                "best_window": best_window,
                "limiting": limiting,
                "max_gust_kt": max_gust,
            },
        )
    trend = outlook_mod.trend(runs)
    previous_at = runs[-2]["at"] if len(runs) >= 2 else (prev_entry or {}).get("at")

    fog_until = _fog_until(hours)
    first_hour = hours[0].to_row()
    summary = render.outlook_summary(
        target=target,
        now_local=now_local,
        score=score,
        best_window=best_window,
        limiting=limiting,
        watch=watch,
        first_hour=first_hour,
        ceiling_known=hours[0].conditions.ceiling_known,
        fog_until=fog_until,
        lake_rows=lake_rows,
        home_water=home_water_row,
        trend=trend,
        previous_at=previous_at,
        confidence=confidence,
        confidence_reasons=reasons,
    )

    return {
        "target_date": target.isoformat(),
        "window": {"start": start.strftime("%H:%M"), "end": end.strftime("%H:%M")},
        "sun": {
            "civil_dawn": _hhmm(sun.civil_dawn),
            "sunrise": _hhmm(sun.sunrise),
            "sunset": _hhmm(sun.sunset),
            "civil_dusk": _hhmm(sun.civil_dusk),
        },
        "score": score,
        "limiting": limiting,
        "watch": watch,
        "best_window": best_window,
        "hours": [h.to_row() for h in hours],
        "lakes": lake_rows,
        "home_water": home_water_row,
        "confidence": confidence,
        "confidence_reasons": reasons,
        "trend": trend,
        "runs": runs,
        "summary": summary,
    }


def _should_record(run_at: str | None, times_local: list[str], target: date, now_local: datetime, runs: list[dict]) -> bool:
    """Design 3.4: the outlook times, plus the first run of the target morning.

    `generated_at` is UTC, so it has to be converted before its date is compared with a local one --
    last night's 22:00 run carries a `generated_at` on the following UTC date.
    """
    if run_at and run_at in times_local:
        return True
    if target != now_local.date():
        return False
    tz = now_local.tzinfo
    for r in runs:
        raw = r.get("generated_at")
        if not raw:
            continue
        try:
            when = datetime.fromisoformat(str(raw))
        except ValueError:
            continue
        if when.astimezone(tz).date() == now_local.date():
            return False
    return True


def _taf_agreement(hours: list[outlook_mod.Hour], covers: bool) -> tuple[bool, bool, list[str]]:
    """`(agrees, any_coverage, reasons)` -- does the TAF agree with the model, and does it reach here?

    The only overlap is visibility: Open-Meteo has no ceiling product and NWS `forecastHourly`
    carries no sky cover, so an hour the TAF does not cover has no ceiling at all. When that happens
    the caller is handed a reason to put in `confidence_reasons`, because a morning scored without a
    ceiling is a morning scored on less than the pilot will have.

    `any_coverage` separates "the TAF says nothing about this window" from "it covers part of it",
    so the two reasons never contradict each other.
    """
    reasons: list[str] = []
    unknown = [h for h in hours if "ceiling" in h.score.skipped]
    any_coverage = len(unknown) < len(hours)
    if unknown and any_coverage:
        reasons.append(f"no ceiling data for {len(unknown)} of {len(hours)} hours (no TAF coverage)")
    return covers and not unknown, any_coverage, reasons


def _fog_until(hours: list[outlook_mod.Hour]) -> str | None:
    """When a fog risk starts the window, the time it clears; otherwise `None`."""
    if not hours or not hours[0].score.fog_risk:
        return None
    for h in hours:
        if not h.score.fog_risk:
            return h.start.strftime("%H:%M")
    return hours[-1].start.strftime("%H:%M")


def _ice_gate(daily: dict | None, today: date, limits) -> tuple[bool, str | None]:
    """Design 3.3 step 7: recent freezing days, or inside the ice season."""
    start_m, start_d = (int(x) for x in limits.ice_season_start.split("-"))
    end_m, end_d = (int(x) for x in limits.ice_season_end.split("-"))
    start = (start_m, start_d)
    end = (end_m, end_d)
    now = (today.month, today.day)
    in_season = (start <= now or now <= end) if start > end else (start <= now <= end)
    if in_season:
        return True, None
    if not daily:
        return False, None
    times = daily.get("time") or []
    maxes = daily.get("temperature_2m_max") or []
    cutoff = today - timedelta(days=ICE_LOOKBACK_DAYS)
    for t, v in zip(times, maxes, strict=False):
        if v is None:
            continue
        try:
            d = date.fromisoformat(t)
        except ValueError:
            continue
        if cutoff <= d <= today and v < 32:
            return True, None
    return False, None


def _sources(feeds: Feeds) -> dict:
    metar_at = None
    if feeds.metar:
        metar_at = feeds.metar.get("reportTime") or feeds.metar.get("receiptTime")
        if metar_at:
            metar_at = _iso_z(datetime.fromisoformat(str(metar_at)))
    taf_at = None
    if feeds.taf and feeds.taf.get("issueTime"):
        taf_at = _iso_z(datetime.fromisoformat(str(feeds.taf["issueTime"])))
    return {
        "metar": metar_at,
        "taf": taf_at,
        "open_meteo": _iso_z(feeds.fetched_at) if feeds.airport is not None else None,
        "nws": _iso_z(feeds.fetched_at) if feeds.alerts is not None else None,
    }


def _iso_z(dt: datetime) -> str:
    return dt.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _hhmm(dt: datetime | None) -> str | None:
    return None if dt is None else dt.strftime("%H:%M")
