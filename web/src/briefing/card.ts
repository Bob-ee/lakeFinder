import { BRIEFING, BRIEFING_DISCLAIMER } from "../config";
import { lakeRow } from "../lists/row";
import { normalizeName } from "../search/normalize";
import type { Lake } from "../types";
import { ageMinutes, el, formatRelative, formatThousands } from "../ui/format";
import { icon } from "../ui/icons";
import {
  CONFIDENCE_WORD,
  SCORE_WORD,
  TREND_ARROW,
  TREND_WORD,
  anyCeilingUnknown,
  dayHeading,
  formatCeiling,
  formatWind,
  formatWindow,
  limitingLabel,
  morningHeading,
  outlookLeads,
  shortDate,
  shortWeekday,
  utcToZoneTime,
  zoneNow,
} from "./labels";
import type { BriefingState } from "./store";
import { renderTimeline } from "./timeline";
import type { WaveLimits } from "../waves/ramp";
import type {
  Briefing,
  BriefingBlock,
  BriefingDay,
  BriefingLake,
  BriefingObservation,
  BriefingRegion,
  Health,
  Outlook,
  OutlookHour,
  OutlookRun,
  Score,
  Timeline,
  Window,
} from "./types";

export interface BriefingCardDeps {
  /** index.json lookup, so a ranked lake carries the county and the real verdict. */
  lookupLake: (id: number) => Lake | null;
  /** The single selectLake(id) path. */
  onSelectLake: (id: number) => void;
  /** Select the water body and frame one of its regions. */
  onSelectRegion: (id: number, lat: number, lon: number) => void;
  onOpenSettings: () => void;
  onRefresh: () => void;
  /** Select the home water and lower the sheet so its waves show on the map. */
  onShowWaves: (id: number) => void;
}

export interface BriefingCardOptions extends BriefingCardDeps {
  state: BriefingState;
  /** null when `/api/health` failed; undefined while the probe is still running. */
  health: Health | null | undefined;
  refreshing: boolean;
  /** `limits.wave_ok_in` / `wave_max_in`, for the chart's guide lines. */
  limits: WaveLimits;
}

/** The whole card, rebuilt from scratch on every state change. It is a small tree. */
export function renderBriefingCard(opts: BriefingCardOptions): HTMLElement {
  const wrap = el("div", "brief");
  const { state } = opts;

  if (state.kind === "loading") {
    wrap.append(el("p", "muted", "Loading the briefing…"));
    return wrap;
  }
  if (state.kind === "empty" || state.kind === "unavailable") {
    wrap.append(emptyState(state.kind, opts));
    return wrap;
  }

  const b = state.briefing;
  const today = zoneNow(b.timezone).date;
  if (b.timeline && b.timeline.hours.length > 0) {
    return timelineCard(wrap, b, b.timeline, today, state.fromCache, opts);
  }
  const leadsWithOutlook = b.outlook != null && outlookLeads(b.outlook, b.timezone);

  wrap.append(header(b, leadsWithOutlook, state.fromCache, opts));

  // Home water leads the card: it is the one water body Bobby is on most days, and it is
  // in every briefing regardless of the search radius, so it is not part of the ranked list.
  if (b.home_water) wrap.append(homeWaterSection(b.home_water, b, opts, false));

  const sections = el("div", "brief-sections");
  const outlookNode = b.outlook ? outlookSection(b, b.outlook, today, opts) : outlookMissing();
  const daysNode = daysSection(b, today);
  if (leadsWithOutlook) sections.append(outlookNode, daysNode);
  else sections.append(daysNode, outlookNode);
  wrap.append(sections);

  if (b.alerts.length > 0) wrap.append(alertsSection(b));
  wrap.append(lakesSection("Calmest water nearby", b.lakes, b, opts, false, runHourLabel(b)));
  wrap.append(footer(b));
  return wrap;
}

// -- timeline layout -------------------------------------------------------

/**
 * The card when the briefing carries a timeline: the one-line answer, the chart, then only
 * what the chart does not already show. The day blocks, the hour strip, the summary and
 * the home water block are all in the chart (the home water per hour in its detail).
 */
function timelineCard(
  wrap: HTMLElement,
  b: Briefing,
  tl: Timeline,
  today: string,
  fromCache: boolean,
  opts: BriefingCardOptions,
): HTMLElement {
  wrap.append(compactHeader(b, fromCache, opts));
  wrap.append(
    renderTimeline(tl, {
      airportId: b.home_airport.id,
      homeRegions: b.home_water?.regions ?? [],
      limits: opts.limits,
      onSelectRegion: opts.onSelectRegion,
      onShowWaves: opts.onShowWaves,
    }),
  );
  // A home water the timeline has no rows for still gets its block, labelled with its hour.
  if (b.home_water && !tl.home_water) wrap.append(homeWaterSection(b.home_water, b, opts, false));
  if (b.alerts.length > 0) wrap.append(alertsSection(b));
  if (b.outlook) wrap.append(outlookCompact(b, b.outlook, today, opts));
  wrap.append(lakesSection("Calmest water nearby", b.lakes, b, opts, false, runHourLabel(b)));
  wrap.append(footer(b));
  return wrap;
}

/**
 * With a timeline the sheet's own header (peek row) already carries the one-line answer, so
 * the card keeps a single compact row: age · airport on the left, refresh and settings on
 * the right.
 */
function compactHeader(b: Briefing, fromCache: boolean, opts: BriefingCardOptions): HTMLElement {
  const head = header(b, false, fromCache, opts);
  head.classList.add("brief-head--compact");
  const top = head.querySelector<HTMLElement>(".brief-head-top");
  const meta = head.querySelector<HTMLElement>(".brief-meta");
  head.querySelector(".brief-title")?.remove();
  if (top && meta) top.prepend(meta);
  return head;
}

/** What is left of the outlook once the chart shows its hours: runs, trend, confidence. */
function outlookCompact(b: Briefing, o: Outlook, today: string, opts: BriefingCardOptions): HTMLElement {
  const sec = el("section", "brief-section brief-section--outlook");
  sec.append(
    sectionHead(
      morningHeading(o.target_date, today),
      `${shortWeekday(o.target_date)} ${shortDate(o.target_date)} · ${o.window.start}–${o.window.end}`,
    ),
  );
  const line = el("div", "brief-lead");
  line.append(scorePill(o.score, o.limiting ? `${SCORE_WORD[o.score]} · ${limitingLabel(o.limiting)}` : undefined));
  line.append(el("span", "brief-lead-window", `Best window ${formatWindow(o.best_window)}`));
  sec.append(line);
  if (o.watch) sec.append(el("p", "brief-watch", `Watch: ${o.watch}`));
  if (o.runs.length > 0) sec.append(runHistory(o));
  const conf = el("p", "brief-confidence");
  conf.append(el("span", "brief-conf-word", `${CONFIDENCE_WORD[o.confidence]} confidence`));
  if (o.confidence_reasons.length > 0) {
    conf.append(el("span", "muted", ` — ${o.confidence_reasons.join("; ")}`));
  }
  sec.append(conf);
  sec.append(
    lakesSection("Calmest water in the window", o.lakes, b, opts, true, `${o.window.start}–${o.window.end}`),
  );
  return sec;
}

/** "wind at 09:00": the hour the ranked rows' numbers were computed for. */
function runHourLabel(b: Briefing): string {
  return `wind at ${utcToZoneTime(b.generated_at, b.timezone).slice(0, 2)}:00`;
}

// -- header ----------------------------------------------------------------

function header(
  b: Briefing,
  leadsWithOutlook: boolean,
  fromCache: boolean,
  opts: BriefingCardOptions,
): HTMLElement {
  const head = el("header", "brief-head");

  const lead = leadsWithOutlook && b.outlook ? b.outlook : b.days[0];
  const score: Score = lead?.score ?? "unfavorable";
  const window: Window | null = lead?.best_window ?? null;

  const top = el("div", "brief-head-top");
  const title = el("div", "brief-title");
  title.dataset["score"] = score;
  title.append(el("span", "brief-score", SCORE_WORD[score]));
  title.append(el("span", "brief-window", formatWindow(window)));
  top.append(title);

  const actions = el("div", "brief-actions");
  if (opts.health !== null) {
    const refresh = el("button", "icon-btn brief-icon-btn");
    refresh.type = "button";
    refresh.title = "Refresh now";
    refresh.setAttribute("aria-label", "Refresh the briefing now");
    refresh.innerHTML = icon("refresh");
    refresh.disabled = opts.refreshing;
    refresh.addEventListener("click", () => opts.onRefresh());

    const gear = el("button", "icon-btn brief-icon-btn");
    gear.type = "button";
    gear.title = "Briefing settings";
    gear.setAttribute("aria-label", "Briefing settings");
    gear.innerHTML = icon("sliders");
    gear.addEventListener("click", () => opts.onOpenSettings());
    actions.append(refresh, gear);
  }
  top.append(actions);
  head.append(top);

  const meta = el("div", "brief-meta");
  const mins = ageMinutes(b.generated_at);
  const age = el("span", "brief-age", formatRelative(b.generated_at));
  if (mins != null && mins >= BRIEFING.staleMinutes) {
    age.classList.add("is-stale");
    age.title = "Older than six hours";
    meta.append(age, el("span", "brief-dot", "·"), el("span", "tag brief-stale-tag", "stale"));
  } else {
    meta.append(age);
  }
  meta.append(el("span", "brief-dot", "·"));
  const airport = el("span", "brief-airport", b.home_airport.id);
  airport.title = b.home_airport.name;
  meta.append(airport);
  if (fromCache) {
    meta.append(el("span", "brief-dot", "·"), el("span", "brief-offline", "last stored copy"));
  }
  head.append(meta);

  if (opts.health === null) {
    head.append(el("p", "notice brief-notice", "Briefing service not reachable"));
  }
  return head;
}

// -- outlook ---------------------------------------------------------------

function outlookSection(
  b: Briefing,
  o: Outlook,
  today: string,
  opts: BriefingCardOptions,
): HTMLElement {
  const sec = el("section", "brief-section brief-section--outlook");
  sec.append(
    sectionHead(
      morningHeading(o.target_date, today),
      `${shortWeekday(o.target_date)} ${shortDate(o.target_date)} · ${o.window.start}–${o.window.end}`,
    ),
  );

  const line = el("div", "brief-lead");
  line.append(scorePill(o.score, o.limiting ? `${SCORE_WORD[o.score]} · ${limitingLabel(o.limiting)}` : undefined));
  line.append(el("span", "brief-lead-window", `Best window ${formatWindow(o.best_window)}`));
  sec.append(line);

  if (o.summary) sec.append(el("p", "brief-summary", o.summary));

  if (o.hours.length > 0) {
    const strip = el("div", "strip strip--hours");
    strip.setAttribute("role", "list");
    strip.setAttribute("aria-label", "Hour by hour");
    for (const hour of o.hours) strip.append(hourCell(hour));
    sec.append(strip);
    if (anyCeilingUnknown(o.hours)) sec.append(ceilingUnknownNote());
  }

  if (o.watch) {
    sec.append(el("p", "brief-watch", `Watch: ${o.watch}`));
  }

  if (o.runs.length > 0) sec.append(runHistory(o));

  const conf = el("p", "brief-confidence");
  conf.append(el("span", "brief-conf-word", `${CONFIDENCE_WORD[o.confidence]} confidence`));
  if (o.confidence_reasons.length > 0) {
    conf.append(el("span", "muted", ` — ${o.confidence_reasons.join("; ")}`));
  }
  sec.append(conf);

  sec.append(
    el(
      "p",
      "brief-sun",
      `Civil dawn ${o.sun.civil_dawn} · Sunrise ${o.sun.sunrise} · Sunset ${o.sun.sunset} · Civil dusk ${o.sun.civil_dusk}`,
    ),
  );

  if (o.home_water) sec.append(homeWaterSection(o.home_water, b, opts, true));
  sec.append(
    lakesSection("Calmest water in the window", o.lakes, b, opts, true, `${o.window.start}–${o.window.end}`),
  );
  return sec;
}

function outlookMissing(): HTMLElement {
  const sec = el("section", "brief-section");
  sec.append(sectionHead("Morning outlook", null));
  sec.append(el("p", "muted", "No morning outlook in this run: the forecast input failed."));
  return sec;
}

function hourCell(hour: OutlookHour): HTMLElement {
  const cell = el("div", "cell cell--hour");
  cell.dataset["score"] = hour.score;
  cell.setAttribute("role", "listitem");
  const limit = limitingLabel(hour.limiting);
  cell.title =
    `${hour.time} ${SCORE_WORD[hour.score].toLowerCase()}${limit ? ` (${limit})` : ""} · ` +
    `${hour.wind.dir}/${hour.wind.kt}${hour.wind.gust == null ? "" : ` G${hour.wind.gust}`} · ` +
    `${formatCeiling(hour.ceiling_ft, hour.ceiling_known)} · vis ${hour.vis_sm} · ${hour.temp_f}°F`;

  cell.append(el("span", "cell-time", hour.time));
  cell.append(el("span", "cell-wind", `${pad3(hour.wind.dir)}/${hour.wind.kt}`));
  cell.append(el("span", "cell-gust", hour.wind.gust == null ? "—" : `G${hour.wind.gust}`));
  const fog = el("span", "cell-fog", hour.fog_risk ? "fog" : "");
  fog.classList.toggle("is-on", hour.fog_risk);
  cell.append(fog);
  return cell;
}

function runHistory(o: Outlook): HTMLElement {
  const wrap = el("div", "brief-runs");
  wrap.append(el("span", "brief-runs-label", "Runs"));
  const chips = el("div", "chips brief-run-chips");
  for (const run of o.runs) chips.append(runChip(run));
  if (o.trend) {
    const trend = el("span", "chip brief-trend", `${TREND_WORD[o.trend]} ${TREND_ARROW[o.trend]}`);
    trend.dataset["trend"] = o.trend;
    trend.title = "Compared with the previous run for this morning";
    chips.append(trend);
  }
  wrap.append(chips);
  return wrap;
}

function runChip(run: OutlookRun): HTMLElement {
  const limit = limitingLabel(run.limiting);
  const chip = el(
    "span",
    "chip brief-run",
    `${run.at} ${SCORE_WORD[run.score].toLowerCase()}${limit ? ` (${limit})` : ""}`,
  );
  chip.dataset["score"] = run.score;
  chip.title = `${formatWindow(run.best_window)} · peak gust ${run.max_gust_kt} kt`;
  return chip;
}

// -- days ------------------------------------------------------------------

function daysSection(b: Briefing, today: string): HTMLElement {
  const sec = el("section", "brief-section");
  sec.append(sectionHead("Today and tomorrow", null));
  if (b.summary) sec.append(el("p", "brief-summary", b.summary));
  if (b.days.length === 0) {
    sec.append(el("p", "muted", "No day blocks in this run."));
    return sec;
  }
  for (const day of b.days) sec.append(dayBlock(day, today));
  return sec;
}

function dayBlock(day: BriefingDay, today: string): HTMLElement {
  const wrap = el("div", "brief-day");
  const head = el("div", "brief-day-head");
  head.append(el("span", "brief-day-name", dayHeading(day.date, today)));
  head.append(scorePill(day.score));
  head.append(el("span", "brief-day-window", formatWindow(day.best_window)));
  wrap.append(head);

  const strip = el("div", "strip strip--blocks");
  strip.setAttribute("role", "list");
  strip.setAttribute("aria-label", `${dayHeading(day.date, today)} three-hour blocks`);
  for (const block of day.blocks) strip.append(blockCell(block));
  wrap.append(strip);
  if (anyCeilingUnknown(day.blocks)) wrap.append(ceilingUnknownNote());
  return wrap;
}

/** Said once per strip rather than per cell: a missing ceiling is not a clear sky. */
function ceilingUnknownNote(): HTMLElement {
  return el("p", "muted small brief-note", "Ceiling not reported for part of this period.");
}

function blockCell(block: BriefingBlock): HTMLElement {
  const cell = el("div", "cell cell--block");
  cell.dataset["score"] = block.score;
  cell.setAttribute("role", "listitem");
  cell.title =
    `${block.start}–${block.end} · ${pad3(block.wind.dir)}/${block.wind.kt}` +
    `${block.wind.gust == null ? "" : ` G${block.wind.gust}`} · xwind ${block.xwind_kt} kt on ${block.runway} · ` +
    `${formatCeiling(block.ceiling_ft, block.ceiling_known)} · vis ${block.vis_sm} · DA ${formatThousands(block.da_ft)} ft · ` +
    `${block.temp_f}°F · precip ${block.precip_prob}%`;

  cell.append(el("span", "cell-time", `${block.start}–${block.end}`));
  cell.append(el("span", "cell-score", SCORE_WORD[block.score]));
  // Only a non-favorable block needs to say what is holding it back.
  const limit = block.score === "favorable" ? null : limitingLabel(block.limiting);
  cell.append(el("span", "cell-limit", limit ?? `${pad3(block.wind.dir)}/${block.wind.kt}`));
  return cell;
}

// -- alerts ----------------------------------------------------------------

function alertsSection(b: Briefing): HTMLElement {
  const sec = el("section", "brief-section");
  sec.append(sectionHead("Alerts", null));
  const list = el("ul", "brief-alerts");
  for (const alert of b.alerts) {
    const li = el("li", "brief-alert");
    li.append(el("span", "brief-alert-event", alert.event));
    li.append(el("span", "brief-alert-area", alert.area));
    li.append(
      el("span", "brief-alert-ends", `until ${utcToZoneTime(alert.ends, b.timezone)}`),
    );
    list.append(li);
  }
  sec.append(list);
  return sec;
}

// -- ranked lakes ----------------------------------------------------------

function lakesSection(
  title: string,
  rows: BriefingLake[],
  b: Briefing,
  opts: BriefingCardDeps,
  nested = false,
  sub: string | null = null,
): HTMLElement {
  const sec = el("section", nested ? "brief-sub" : "brief-section");
  sec.append(sectionHead(title, sub));
  if (rows.length === 0) {
    sec.append(el("p", "muted", "No lake in range had usable water."));
    return sec;
  }
  const list = el("div", "brief-lakes");
  list.setAttribute("role", "listbox");
  for (const row of rows) list.append(rankedLakeRow(row, b, opts));
  sec.append(list);
  return sec;
}

/**
 * "Notably higher" for the open-water contrast line. Two inches is the smallest step that
 * is not rounding noise on a number the server already rounded to whole inches, and it is
 * the difference between a bay you would land in and one you would not.
 */
const OPEN_WATER_GAP_IN = 2;

/**
 * Regions of the home water shown before the list folds; the rest are one tap away.
 * `.brief-regions.is-clipped` in styles/waves.css hides the rows past this one, so the two
 * numbers have to agree.
 */
const HOME_REGIONS_SHOWN = 6;

function rankedLakeRow(row: BriefingLake, b: Briefing, opts: BriefingCardDeps): HTMLElement {
  const known = opts.lookupLake(row.id);
  const lake = known ?? standInLake(row);

  // No region of this water body has a long enough run into the wind. The api still names
  // the calmest one and its ungated numbers, which must not be printed as if they were an
  // answer, so the run figure is replaced by what is actually wrong with it.
  const noUsableRun =
    (row.regions?.length ?? 0) > 0 && row.regions!.every((r) => r.hs_in == null);

  // Three stacked lines in the trailing slot: the region's water, the open water it is
  // sheltered from when that is a real difference, then where the water body is.
  const trailing = el("span", "brief-trail");
  trailing.append(
    el(
      "span",
      "brief-trail-main",
      noUsableRun ? `${row.hs_in} in` : `${row.hs_in} in · ${formatThousands(row.run_ft)} ft`,
    ),
  );
  if (noUsableRun) {
    trailing.append(el("span", "brief-trail-sub", "run too short into this wind"));
  }
  if (row.hs_open_in != null && row.hs_open_in - row.hs_in >= OPEN_WATER_GAP_IN) {
    trailing.append(el("span", "brief-trail-sub", `open water ${row.hs_open_in} in`));
  }
  // The row's wind is its best region's own forecast, which on a big lake can differ from
  // the airport's; it is what the wave figure above was computed from.
  trailing.append(el("span", "brief-trail-sub", `wind ${formatWind(row.wind)}`));
  trailing.append(
    el("span", "brief-trail-sub", `${row.distance_nm} nm · ${pad3(row.bearing_deg)}°`),
  );

  // The row score covers the water only (waves, usable run, crosswind on the water, ice);
  // the weather for getting there is the header, the day blocks and the outlook hours.
  const parts = [`Water ${SCORE_WORD[row.score].toLowerCase()}`];
  const limit = limitingLabel(row.limiting);
  if (limit) parts.push(limit);
  if (row.frozen) parts.push("likely frozen, verify");
  const note = el("span", undefined, parts.join(" · "));
  note.dataset["score"] = row.score;
  if (row.frozen) note.classList.add("is-frozen");

  const element = lakeRow(lake, {
    onSelect: (id) => opts.onSelectLake(id),
    // "Cass Lake · west end": the calmest region is what the row is actually about.
    ...(row.region ? { nameSuffix: row.region } : {}),
    trailing,
    note,
  });
  element.classList.add("brief-lake-row");
  element.dataset["score"] = row.score;
  const wind = `${pad3(row.wind.dir)}/${row.wind.kt}${row.wind.gust == null ? "" : ` G${row.wind.gust}`}`;
  element.title = `${row.name} · wind ${wind} · ${b.home_airport.id} ${row.distance_nm} nm`;
  if (!known) element.classList.add("is-unknown-lake");
  return element;
}

/**
 * The briefing and index.json are built from the same pipeline output, so a miss here
 * means the two are out of step. Render the row anyway rather than dropping a lake; the
 * tap then reports "not in this data pack" through the usual path.
 */
function standInLake(row: BriefingLake): Lake {
  return {
    id: row.id,
    name: row.name,
    name_norm: normalizeName(row.name),
    kind: row.kind ?? "lake",
    county: "not in this data pack",
    township: null,
    lat: 0,
    lon: 0,
    bbox: [0, 0, 0, 0],
    area_acres: 0,
    chord_ft: 0,
    chord_bearing_deg: 0,
    verdict: row.verdict,
    flags: [],
    restriction_ids: [],
    access: null,
  };
}

// -- home water ------------------------------------------------------------

/**
 * The home water block: one water body in full, every region ranked, with the second
 * opinions beside the computed numbers rather than instead of them.
 *
 * It is not a lake row because the question is different. A ranked row answers "which lake
 * today"; this answers "where on the water I am going anyway", which is a list of regions,
 * a contrast with the open water, and whatever a real buoy is measuring right now.
 */
function homeWaterSection(
  row: BriefingLake,
  b: Briefing,
  opts: BriefingCardDeps,
  nested: boolean,
): HTMLElement {
  const sec = el("section", nested ? "brief-sub brief-home" : "brief-section brief-home");
  sec.dataset["score"] = row.score;

  const head = el("div", "brief-section-head");
  const title = el("h3", "detail-h brief-home-name", row.name);
  head.append(title);
  // Two home-water blocks can show two different winds; each says which hour it is for.
  const when =
    nested && b.outlook ? `${b.outlook.window.start}–${b.outlook.window.end}` : runHourLabel(b).slice(8);
  head.append(el("span", "brief-section-sub", `home water · wind ${when} ${formatWind(row.wind)}`));
  sec.append(head);

  const lead = el("div", "brief-lead");
  lead.append(
    scorePill(
      row.score,
      row.limiting ? `${SCORE_WORD[row.score]} · ${limitingLabel(row.limiting)}` : undefined,
    ),
  );
  if (row.hs_open_in != null) {
    lead.append(el("span", "brief-lead-window", `open water ${row.hs_open_in} in`));
  }
  if (row.frozen) lead.append(el("span", "brief-home-frozen", "likely frozen, verify"));
  sec.append(lead);

  const regions = row.regions ?? [];
  if (regions.length === 0) {
    sec.append(el("p", "muted small", "No wave field for this water body yet."));
  } else {
    const list = el("div", "brief-regions");
    list.setAttribute("role", "list");
    for (const region of regions) list.append(homeRegionRow(row, region, opts));
    sec.append(list);
    // A big lake can have a dozen regions and the card has a briefing under it. The calm
    // end is what gets read, so the rest folds away until asked for.
    if (regions.length > HOME_REGIONS_SHOWN) {
      const hidden = regions.length - HOME_REGIONS_SHOWN;
      list.classList.add("is-clipped");
      const more = el("button", "btn btn-quiet brief-regions-more", `Show ${hidden} rougher`);
      more.type = "button";
      more.addEventListener("click", () => {
        const open = list.classList.toggle("is-clipped");
        more.textContent = open ? `Show ${hidden} rougher` : "Show the calm end only";
      });
      sec.append(more);
    }
  }

  const observed = row.observed ?? [];
  if (observed.length > 0) {
    sec.append(el("div", "brief-obs-head", "Observed"));
    const list = el("ul", "brief-obs");
    for (const station of observed) list.append(observationRow(station, b));
    sec.append(list);
  }
  if (row.marine_hs_in != null) {
    sec.append(
      el(
        "p",
        "muted small brief-marine",
        `Model forecast, open lake: ${row.marine_hs_in} in.`,
      ),
    );
  }
  return sec;
}

/**
 * One region of the home water: score bar, label, wave height, then that region's own wind
 * over its run. Tapping frames it.
 */
function homeRegionRow(
  row: BriefingLake,
  region: BriefingRegion,
  opts: BriefingCardDeps,
): HTMLElement {
  const usable = region.hs_in != null;
  const item = el("button", "brief-region");
  item.type = "button";
  item.setAttribute("role", "listitem");
  if (region.score) item.dataset["score"] = region.score;
  item.classList.toggle("is-unusable", !usable);

  item.append(el("span", "brief-region-bar"));
  item.append(el("span", "brief-region-label", region.label));

  const trail = el("span", "brief-region-trail");
  if (usable) trail.append(el("span", "brief-region-in", `${region.hs_in} in`));
  const detail = el("span", "brief-region-detail");
  if (region.wind) detail.append(el("span", "brief-region-wind", formatWind(region.wind)));
  if (!usable) {
    detail.append(el("span", "brief-region-run", "run too short into this wind"));
  } else if (region.run_ft != null) {
    detail.append(el("span", "brief-region-run", `${formatThousands(region.run_ft)} ft`));
  }
  trail.append(detail);
  item.append(trail);
  if (region.wind) {
    item.title = `${region.label}: wind ${formatWind(region.wind)}`;
  }

  item.addEventListener("click", () => opts.onSelectRegion(row.id, region.lat, region.lon));
  return item;
}

function observationRow(station: BriefingObservation, b: Briefing): HTMLElement {
  const li = el("li", "brief-obs-row");
  // Buoys and shore stations arrive without a name; the station id is what a pilot looks up.
  li.append(el("span", "brief-obs-name", station.name ?? station.station));
  const parts: string[] = [];
  if (station.wind) parts.push(formatWind(station.wind));
  if (station.wave_ft != null) parts.push(`${station.wave_ft} ft`);
  parts.push(utcToZoneTime(station.at, b.timezone));
  parts.push(`${station.distance_nm} nm`);
  li.append(el("span", "brief-obs-detail", parts.join(" · ")));
  li.title = `${station.kind} ${station.station} · reported ${formatRelative(station.at)}`;
  return li;
}

// -- footer ----------------------------------------------------------------

const LINK_LABELS: Array<[keyof Briefing["links"], string]> = [
  ["metar", "METAR"],
  ["taf", "TAF"],
  ["forecast", "Forecast"],
];

function footer(b: Briefing): HTMLElement {
  const foot = el("footer", "brief-foot");

  const links = el("div", "brief-links");
  let any = false;
  for (const [key, label] of LINK_LABELS) {
    const href = b.links?.[key];
    if (!href) continue;
    any = true;
    const a = el("a", "source-link");
    a.href = href;
    a.target = "_blank";
    a.rel = "noopener noreferrer";
    a.innerHTML = `<span>${label}</span>${icon("external")}`;
    links.append(a);
  }
  if (any) foot.append(links);

  if (b.errors.length > 0) {
    foot.append(el("p", "muted small brief-errors", `Inputs that failed: ${b.errors.join("; ")}`));
  }

  foot.append(el("p", "muted small brief-disclaimer", BRIEFING_DISCLAIMER));
  return foot;
}

// -- empty states ----------------------------------------------------------

function emptyState(kind: "empty" | "unavailable", opts: BriefingCardOptions): HTMLElement {
  const wrap = el("div", "brief-empty");
  if (kind === "empty") {
    wrap.append(el("h3", "detail-h", "No briefing yet"));
    wrap.append(
      el(
        "p",
        "muted",
        "The briefing service writes /data/briefing.json on its next run. It will appear here on its own.",
      ),
    );
  } else {
    wrap.append(el("h3", "detail-h", "Briefing unavailable"));
    wrap.append(
      el("p", "muted", "The briefing could not be read and nothing is stored on this device yet."),
    );
  }
  if (opts.health === null) {
    wrap.append(el("p", "notice brief-notice", "Briefing service not reachable"));
  } else {
    const btn = el("button", "btn", "Refresh now");
    btn.type = "button";
    btn.disabled = opts.refreshing;
    btn.addEventListener("click", () => opts.onRefresh());
    wrap.append(btn);
  }
  wrap.append(el("p", "muted small brief-disclaimer", BRIEFING_DISCLAIMER));
  return wrap;
}

// -- small pieces ----------------------------------------------------------

function sectionHead(title: string, sub: string | null): HTMLElement {
  const head = el("div", "brief-section-head");
  head.append(el("h3", "detail-h", title));
  if (sub) head.append(el("span", "brief-section-sub", sub));
  return head;
}

function scorePill(score: Score, text?: string): HTMLElement {
  const pill = el("span", "score-pill", text ?? SCORE_WORD[score]);
  pill.dataset["score"] = score;
  return pill;
}

function pad3(n: number): string {
  return String(Math.round(n)).padStart(3, "0");
}

