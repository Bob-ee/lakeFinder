import { openDialog } from "../ui/dialog";
import { el } from "../ui/format";
import { toast } from "../ui/toast";
import {
  ApiError,
  ValidationError,
  getHealth,
  getSettings,
  lookupAirport,
  putSettings,
  refreshBriefing,
} from "./api";
import { homeWater } from "./home-water";
import { isHhmm } from "./labels";
import type { Briefing, HomeAirport, LimitKey, Settings } from "./types";

export interface SettingsScreenOptions {
  /** Called with the body of POST /api/briefing/refresh so the card updates at once. */
  onBriefing: (briefing: Briefing) => void;
}

const SERVICE_DOWN = "Briefing service not reachable";

/**
 * Settings and "Refresh now", both of which need the api service. When `/api/health`
 * fails, everything here is replaced by one notice and the card keeps showing the last
 * briefing (data-contract, "Client").
 */
export function openBriefingSettings(opts: SettingsScreenOptions): void {
  const body = el("div", "prose brief-settings");
  body.append(el("p", "muted", "Checking the briefing service…"));
  const close = openDialog({ title: "Briefing settings", body });

  void (async () => {
    const health = await getHealth();
    if (!health) {
      body.replaceChildren(serviceDownNotice());
      return;
    }
    let settings: Settings;
    try {
      settings = await getSettings();
    } catch (err) {
      body.replaceChildren(
        el("p", "notice notice--warn", err instanceof ApiError ? err.message : SERVICE_DOWN),
      );
      return;
    }
    // Whoever opened this dialog now has the freshest settings; share them with the sheet's
    // "Make this my home water" button rather than making it fetch again.
    homeWater.adopt(settings);
    body.replaceChildren(form(settings, health.next_run_local, opts, close));
  })();
}

function serviceDownNotice(): HTMLElement {
  const wrap = el("div");
  wrap.append(el("p", "notice notice--warn", SERVICE_DOWN));
  wrap.append(
    el(
      "p",
      "muted small",
      "Settings and manual refresh need the briefing service on the tailnet. The card still " +
        "shows the last briefing it managed to read.",
    ),
  );
  return wrap;
}

// -- the form --------------------------------------------------------------

function form(
  loaded: Settings,
  nextRun: string | null,
  opts: SettingsScreenOptions,
  close: () => void,
): HTMLElement {
  // A deep copy, so cancelling leaves nothing half-applied.
  const draft: Settings = JSON.parse(JSON.stringify(loaded)) as Settings;
  const wrap = el("div");
  const problems = el("div", "brief-problems");
  problems.hidden = true;
  wrap.append(problems);

  // --- home airport -------------------------------------------------------
  const airportSection = section("Home airport");
  const resolved = el("div", "brief-airport-card");
  renderAirport(resolved, draft.home_airport, false);

  const identRow = el("div", "field-row");
  const ident = textInput(draft.home_airport.id, "KPTK");
  ident.setAttribute("aria-label", "Airport identifier");
  ident.autocapitalize = "characters";
  ident.spellcheck = false;
  const lookupBtn = el("button", "btn", "Look up");
  lookupBtn.type = "button";
  lookupBtn.addEventListener("click", () => {
    void (async () => {
      const value = ident.value.trim();
      if (!value) return;
      lookupBtn.disabled = true;
      lookupBtn.textContent = "Looking up…";
      try {
        const found = await lookupAirport(value);
        if (!found) {
          renderAirport(resolved, draft.home_airport, false);
          showProblems(problems, [`No airport with identifier ${value.toUpperCase()}.`]);
        } else {
          draft.home_airport = found;
          renderAirport(resolved, found, true);
          hideProblems(problems);
        }
      } catch (err) {
        showProblems(problems, [err instanceof Error ? err.message : "Lookup failed."]);
      } finally {
        lookupBtn.disabled = false;
        lookupBtn.textContent = "Look up";
      }
    })();
  });
  identRow.append(labelled("Identifier", ident), lookupBtn);
  airportSection.append(identRow, resolved);
  airportSection.append(
    el("p", "muted small", "Runways come from the service; their headings drive the crosswind check."),
  );
  wrap.append(airportSection);

  // --- home water ---------------------------------------------------------
  // Set from a water body's sheet ("Make this my home water"), cleared here: choosing one
  // needs the map and the search, clearing one only needs a button.
  const home = section("Home water");
  const homeCard = el("div", "brief-airport-card brief-home-card");
  const drawHome = (): void => {
    homeCard.replaceChildren();
    if (draft.home_water) {
      homeCard.append(el("div", "brief-airport-id", draft.home_water.name));
      homeCard.append(el("div", "muted small", `id ${draft.home_water.id}`));
      const clear = el("button", "btn", "Clear home water");
      clear.type = "button";
      clear.addEventListener("click", () => {
        draft.home_water = null;
        drawHome();
      });
      homeCard.append(clear);
    } else {
      homeCard.append(el("div", "muted", "No home water set."));
    }
  };
  drawHome();
  home.append(homeCard);
  home.append(
    el(
      "p",
      "muted small",
      "Your home water is briefed on every run whatever the search radius says. Set it from " +
        'a water body\'s sheet with "Make this my home water".',
    ),
  );
  wrap.append(home);

  // --- candidate lakes ----------------------------------------------------
  const lakes = section("Candidate lakes");
  lakes.append(
    numberField("Search radius (nm)", draft.radius_nm, (v) => (draft.radius_nm = v), { min: 1, step: 1 }),
  );
  lakes.append(
    numberField("Lakes to rank", draft.n_lakes, (v) => (draft.n_lakes = v), { min: 1, step: 1 }),
  );
  lakes.append(
    checkboxField("Public access only", draft.public_access_only, (v) => (draft.public_access_only = v)),
  );
  wrap.append(lakes);

  // --- morning outlook ----------------------------------------------------
  const outlook = section("Morning outlook");
  outlook.append(
    el("p", "muted small", "Evening runs that get recorded in the run history, default 18:00, 20:00 and 22:00."),
  );
  outlook.append(timeListField(draft.outlook.times_local));
  outlook.append(morningStartField(draft));
  outlook.append(
    timeField("Window ends", draft.outlook.morning_end_local, (v) => (draft.outlook.morning_end_local = v)),
  );
  outlook.append(
    numberField(
      "Shortest usable window (hours)",
      draft.outlook.min_window_hours,
      (v) => (draft.outlook.min_window_hours = v),
      { min: 1, step: 1 },
    ),
  );
  wrap.append(outlook);

  // --- limits -------------------------------------------------------------
  for (const group of LIMIT_GROUPS) {
    const sec = section(group.title);
    for (const [key, label] of group.fields) {
      sec.append(
        key === "ice_season_start" || key === "ice_season_end"
          ? monthDayField(label, draft.limits[key], (v) => (draft.limits[key] = v))
          : numberField(label, draft.limits[key] as number, (v) => ((draft.limits[key] as number) = v), {
              step: group.step ?? 1,
            }),
      );
    }
    wrap.append(sec);
  }

  // --- notifications ------------------------------------------------------
  const notify = section("Notifications");
  const ntfy = textInput(draft.notify.ntfy_url ?? "", "https://ntfy.sh/your-topic");
  ntfy.addEventListener("input", () => {
    draft.notify.ntfy_url = ntfy.value.trim() === "" ? null : ntfy.value.trim();
  });
  notify.append(labelled("ntfy topic URL", ntfy));
  notify.append(
    el(
      "p",
      "muted small",
      "When this is set, every outlook run posts the outlook summary line to that ntfy topic. " +
        "Leave it empty and nothing leaves the tailnet.",
    ),
  );
  wrap.append(notify);

  // --- read-only facts ----------------------------------------------------
  const facts = section("Schedule");
  const list = el("dl", "fact-grid");
  fact(list, "Time zone", draft.timezone);
  fact(list, "Scheduled runs", draft.schedule.run_times_local.join(", "));
  fact(list, "Next run", nextRun ?? "unknown");
  facts.append(list);
  facts.append(el("p", "muted small", "The run schedule is set in the service, not here."));
  wrap.append(facts);

  // --- actions ------------------------------------------------------------
  const actions = el("div", "brief-settings-actions");
  const save = el("button", "btn btn-primary", "Save settings");
  save.type = "button";
  save.addEventListener("click", () => {
    const local = validate(draft);
    if (local.length > 0) {
      showProblems(problems, local);
      return;
    }
    void (async () => {
      save.disabled = true;
      save.textContent = "Saving…";
      try {
        homeWater.adopt(await putSettings(draft));
        hideProblems(problems);
        toast("Settings saved; a new run is starting");
        close();
      } catch (err) {
        if (err instanceof ValidationError) showProblems(problems, err.problems);
        else showProblems(problems, [err instanceof Error ? err.message : "Could not save."]);
      } finally {
        save.disabled = false;
        save.textContent = "Save settings";
      }
    })();
  });

  const refresh = el("button", "btn", "Refresh now");
  refresh.type = "button";
  refresh.addEventListener("click", () => {
    void (async () => {
      refresh.disabled = true;
      refresh.textContent = "Running…";
      try {
        opts.onBriefing(await refreshBriefing());
        toast("Briefing refreshed");
      } catch (err) {
        showProblems(problems, [err instanceof Error ? err.message : "Refresh failed."]);
      } finally {
        refresh.disabled = false;
        refresh.textContent = "Refresh now";
      }
    })();
  });

  actions.append(refresh, save);
  wrap.append(actions);
  return wrap;
}

// -- limit groups ----------------------------------------------------------

interface LimitGroup {
  title: string;
  fields: Array<[LimitKey, string]>;
  step?: number;
}

/** Units live in the label; the contract's own defaults are in docs/data-contract.md. */
const LIMIT_GROUPS: LimitGroup[] = [
  {
    title: "Wind and gusts",
    fields: [
      ["wind_ok", "Sustained wind, favorable up to (kt)"],
      ["wind_max", "Sustained wind, marginal up to (kt)"],
      ["gust_spread_ok", "Gust spread, favorable up to (kt)"],
      ["gust_spread_max", "Gust spread, marginal up to (kt)"],
    ],
  },
  {
    title: "Crosswind",
    fields: [
      ["xwind_runway_ok", "Runway crosswind, favorable up to (kt)"],
      ["xwind_runway_max", "Runway crosswind, marginal up to (kt)"],
      ["xwind_water_max", "Crosswind on the water, most (kt)"],
    ],
  },
  {
    title: "Ceiling, visibility and fog",
    fields: [
      ["ceiling_ok", "Ceiling, favorable at or above (ft AGL)"],
      ["ceiling_min", "Ceiling, marginal at or above (ft AGL)"],
      ["vis_ok", "Visibility, favorable at or above (sm)"],
      ["vis_min", "Visibility, marginal at or above (sm)"],
      ["fog_spread_f", "Fog risk when temp/dewpoint spread is under (°F)"],
    ],
    step: 1,
  },
  {
    title: "Density altitude and temperature",
    fields: [
      ["da_ok", "Density altitude, favorable up to (ft)"],
      ["da_max", "Density altitude, marginal up to (ft)"],
      ["temp_water_min_f", "Coldest water-ops temperature (°F)"],
    ],
  },
  {
    title: "Water",
    fields: [
      ["wave_ok_in", "Wave height, favorable up to (in)"],
      ["wave_max_in", "Wave height, marginal up to (in)"],
      ["min_run_ft", "Shortest usable run into the wind (ft)"],
    ],
  },
  {
    title: "Ice season",
    fields: [
      ["ice_season_start", "Ice season starts (MM-DD)"],
      ["ice_season_end", "Ice season ends (MM-DD)"],
    ],
  },
];

// -- validation ------------------------------------------------------------

/** Light client-side checks. The service's 422 stays the authority. */
function validate(s: Settings): string[] {
  const out: string[] = [];
  if (!s.home_airport.id.trim()) out.push("Home airport: pick an identifier.");
  if (!(s.radius_nm > 0)) out.push("Search radius must be greater than zero.");
  if (!(s.n_lakes > 0)) out.push("Lakes to rank must be at least one.");
  if (s.outlook.times_local.length === 0) out.push("Add at least one outlook time.");
  for (const t of s.outlook.times_local) {
    if (!isHhmm(t)) out.push(`Outlook time "${t}" is not HH:MM.`);
  }
  if (!isHhmm(s.outlook.morning_end_local)) out.push("Window end is not HH:MM.");
  const start = s.outlook.morning_start;
  if (start !== "sunrise" && start !== "civil_twilight" && !isHhmm(start)) {
    out.push("Window start must be sunrise, civil twilight, or HH:MM.");
  }
  for (const key of ["ice_season_start", "ice_season_end"] as const) {
    if (!/^\d{2}-\d{2}$/.test(s.limits[key])) out.push(`${key} must be MM-DD.`);
  }
  return out;
}

function showProblems(host: HTMLElement, lines: string[]): void {
  host.replaceChildren();
  host.hidden = false;
  const box = el("div", "notice notice--warn");
  const list = el("ul", "brief-problem-list");
  for (const line of lines) list.append(el("li", undefined, line));
  box.append(list);
  host.append(box);
  host.scrollIntoView({ block: "nearest" });
}

function hideProblems(host: HTMLElement): void {
  host.replaceChildren();
  host.hidden = true;
}

// -- field helpers ---------------------------------------------------------

function section(title: string): HTMLElement {
  const sec = el("section", "about-section");
  sec.append(el("h3", "detail-h", title));
  return sec;
}

function fact(list: HTMLElement, label: string, value: string): void {
  list.append(el("dt", undefined, label), el("dd", undefined, value));
}

function labelled(text: string, control: HTMLElement): HTMLElement {
  const label = el("label", "field");
  label.append(el("span", "field-label", text));
  label.append(control);
  return label;
}

function textInput(value: string, placeholder: string): HTMLInputElement {
  const input = document.createElement("input");
  input.type = "text";
  input.className = "field-input";
  input.value = value;
  input.placeholder = placeholder;
  return input;
}

function numberField(
  label: string,
  value: number,
  onChange: (value: number) => void,
  opts: { min?: number; step?: number } = {},
): HTMLElement {
  const input = document.createElement("input");
  input.type = "number";
  input.className = "field-input field-input--number";
  input.value = String(value);
  input.inputMode = "decimal";
  if (opts.min != null) input.min = String(opts.min);
  input.step = String(opts.step ?? 1);
  input.addEventListener("input", () => {
    const n = Number(input.value);
    if (Number.isFinite(n)) onChange(n);
  });
  return labelled(label, input);
}

function timeField(label: string, value: string, onChange: (value: string) => void): HTMLElement {
  const input = timeInput(value, onChange);
  return labelled(label, input);
}

function timeInput(value: string, onChange: (value: string) => void): HTMLInputElement {
  const input = document.createElement("input");
  input.type = "time";
  input.className = "field-input field-input--time";
  input.value = value;
  input.addEventListener("input", () => onChange(input.value));
  return input;
}

function monthDayField(
  label: string,
  value: string,
  onChange: (value: string) => void,
): HTMLElement {
  const input = textInput(value, "12-01");
  input.classList.add("field-input--time");
  input.addEventListener("input", () => onChange(input.value.trim()));
  return labelled(label, input);
}

function checkboxField(
  label: string,
  value: boolean,
  onChange: (value: boolean) => void,
): HTMLElement {
  const row = el("label", "toggle-row");
  const box = document.createElement("input");
  box.type = "checkbox";
  box.checked = value;
  box.addEventListener("change", () => onChange(box.checked));
  row.append(box, el("span", "toggle-text", label));
  return row;
}

/** Editable list of HH:MM, with add and remove. Mutates the array in place. */
function timeListField(times: string[]): HTMLElement {
  const wrap = el("div", "time-list");
  const rows = el("div", "time-list-rows");

  const draw = (): void => {
    rows.replaceChildren();
    times.forEach((value, i) => {
      const row = el("div", "time-list-row");
      row.append(
        timeInput(value, (next) => {
          times[i] = next;
        }),
      );
      const remove = el("button", "icon-btn", "✕");
      remove.type = "button";
      remove.setAttribute("aria-label", `Remove ${value}`);
      remove.addEventListener("click", () => {
        times.splice(i, 1);
        draw();
      });
      row.append(remove);
      rows.append(row);
    });
  };
  draw();

  const add = el("button", "btn", "Add a time");
  add.type = "button";
  add.addEventListener("click", () => {
    times.push("18:00");
    draw();
  });

  wrap.append(el("span", "field-label", "Outlook run times"), rows, add);
  return wrap;
}

/** sunrise | civil_twilight | HH:MM, as a segmented control plus a time input. */
function morningStartField(draft: Settings): HTMLElement {
  const wrap = el("div", "field");
  wrap.append(el("span", "field-label", "Window starts at"));

  const group = el("div", "segmented");
  group.setAttribute("role", "radiogroup");
  group.setAttribute("aria-label", "Window starts at");

  const custom = timeInput(isHhmm(draft.outlook.morning_start) ? draft.outlook.morning_start : "07:00", (v) => {
    draft.outlook.morning_start = v;
  });
  custom.hidden = !isHhmm(draft.outlook.morning_start);

  const choices: Array<[string, string]> = [
    ["sunrise", "Sunrise"],
    ["civil_twilight", "Civil twilight"],
    ["custom", "Set a time"],
  ];
  const current = (): string =>
    isHhmm(draft.outlook.morning_start) ? "custom" : draft.outlook.morning_start;

  for (const [value, label] of choices) {
    const btn = el("button", "segment", label);
    btn.type = "button";
    btn.setAttribute("role", "radio");
    const mark = (): void => {
      const on = current() === value;
      btn.classList.toggle("is-active", on);
      btn.setAttribute("aria-checked", String(on));
    };
    mark();
    btn.addEventListener("click", () => {
      draft.outlook.morning_start = value === "custom" ? custom.value || "07:00" : value;
      custom.hidden = value !== "custom";
      for (const other of group.querySelectorAll<HTMLElement>(".segment")) {
        const on = other === btn;
        other.classList.toggle("is-active", on);
        other.setAttribute("aria-checked", String(on));
      }
    });
    group.append(btn);
  }

  wrap.append(group, custom);
  return wrap;
}

function renderAirport(host: HTMLElement, airport: HomeAirport, justResolved: boolean): void {
  host.replaceChildren();
  host.append(el("div", "brief-airport-id", `${airport.id} — ${airport.name}`));
  host.append(
    el(
      "div",
      "muted small",
      `${airport.lat.toFixed(4)}, ${airport.lon.toFixed(4)} · field elevation ${airport.elev_ft.toLocaleString("en-US")} ft`,
    ),
  );
  const runways = el("div", "chips");
  if (airport.runways.length === 0) {
    runways.append(el("span", "chip", "no runways on file"));
  } else {
    for (const rwy of airport.runways) {
      runways.append(el("span", "chip", `${rwy.id} · ${String(rwy.heading).padStart(3, "0")}°`));
    }
  }
  host.append(runways);
  if (justResolved) {
    host.append(el("div", "muted small", "Save to make this the home airport."));
  }
}
