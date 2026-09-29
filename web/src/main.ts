import type { PaddingOptions } from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import "./styles/base.css";
import "./styles/map.css";
import "./styles/search.css";
import "./styles/sheet.css";
import "./styles/briefing.css";
import "./styles/waves.css";
import "./styles/location.css";
import "./styles/wind.css";
import "./styles/timebar.css";

import { BriefingController, readBriefingParam } from "./briefing";
import { minRunFt, waveLimits } from "./briefing/home-water";
import { WAVES } from "./config";
import { ForecastWaves } from "./forecast";
import { Tabs, detailPlaceholder } from "./lists";
import { NearestList, mountNearestButton } from "./lists/nearest";
import { LocationService } from "./location";
import { FollowController } from "./location/follow";
import { MapController } from "./map";
import { SearchBox } from "./search/ui";
import { Sheet } from "./sheet";
import { renderDetail, renderPeek, type DetailInput } from "./sheet/detail";
import { AppState, readUrlParam, type Selection } from "./state";
import { waveGeo } from "./waves/geo";
import { WaveLegend } from "./waves/legend";
import { applyWaveBandVars } from "./waves/ramp";
import { WaveFieldLoader } from "./waves/load";
import { WaterSection, type WaveState } from "./waves/section";
import { defaultWindFor, forecastWindFor } from "./waves/wind";
import { maybeShowFirstRun, openAbout } from "./ui/about";
import { el } from "./ui/format";
import { loadLayerPrefs, mountMapControls } from "./ui/controls";
import { Theme } from "./ui/theme";
import { toast } from "./ui/toast";
import { Wind } from "./wind";

async function boot(): Promise<void> {
  const theme = new Theme();
  applyWaveBandVars(theme.resolved);
  const app = document.getElementById("app");
  if (!app) throw new Error("#app missing from index.html");

  const mapEl = el("div", "map");
  mapEl.id = "map";
  app.append(mapEl);

  const state = new AppState();
  /** Bumped on every selection change; a late wave-field load checks it before painting. */
  let waterToken = 0;
  const sheet = new Sheet(app);
  const tabs = new Tabs(sheet.slots.tabs, sheet.slots.body);
  const detailPanel = tabs.panel("detail");

  const prefs = loadLayerPrefs();

  // Map padding keeps the selected lake clear of the sheet on a phone and of the docked
  // panel on an iPad. Clamped so fitBounds never gets padding larger than the viewport.
  const padding = (): PaddingOptions => {
    const w = mapEl.clientWidth || window.innerWidth;
    const h = mapEl.clientHeight || window.innerHeight;
    const clampV = (v: number) => Math.max(16, Math.min(v, Math.floor(h / 2) - 24));
    const clampH = (v: number) => Math.max(16, Math.min(v, Math.floor(w / 2) - 24));
    if (sheet.isPanel) {
      return { top: clampV(96), bottom: clampV(48), left: clampH(48), right: clampH(sheet.widthPx + 32) };
    }
    return { top: clampV(96), bottom: clampV(sheet.heightPx + 32), left: clampH(32), right: clampH(32) };
  };

  const [map] = await Promise.all([
    MapController.create(mapEl, theme.resolved, prefs, {
      padding,
      onLakeTap: (hit) => {
        const id = hit.id ?? state.resolveHit(hit.name, hit.county);
        if (id == null || !state.selectLake(id, "map")) {
          toast(hit.name ? `${hit.name} is not in the index` : "That lake is not in the index");
        }
      },
      onBackgroundTap: () => state.clearSelection(),
    }),
    state.load(),
  ]);

  // Flight-mode wind: stations on the map (a layers-panel toggle) and a block in the sheet.
  const wind = new Wind(map, () => theme.resolved);

  // Location (phase 2): the recenter button resumes follow-me once there is a fix.
  const location = new LocationService();
  let follow: FollowController | null = null;
  const controls = mountMapControls(app, map, prefs, () => follow?.recenter(), [wind.layerRow()]);
  follow = new FollowController({
    map,
    location,
    padding,
    controls,
    // A `?lake=` link owns the camera at startup; the pilot can still tap recenter.
    autoFollow: () => state.current == null,
  });
  controls.append(wind.layer.statusElement);

  // The wave field: one index fetch, then one Range request per water body. Everything
  // about it degrades to "this water body has no wave field", which is also the honest
  // answer for every lake too small for the pipeline to sample.
  const waves = new WaveFieldLoader();
  const legend = new WaveLegend(app);

  // The briefing lives in the sheet because that is the one surface that is already a
  // bottom sheet on a phone and a docked panel on an iPad. The chip on the map keeps it
  // one tap away from the default view.
  const briefing = new BriefingController({
    show: (grow) => {
      tabs.select("briefing");
      // The pilot tapped the chip (or opened ?briefing=1): the card needs room, so raise
      // the sheet to full. `grow` never lowers a sheet and brings a collapsed panel back.
      if (grow) sheet.grow("full");
    },
    hide: () => sheet.setSnap("hidden"),
    hasSelection: () => state.current != null,
    lookupLake: (id) => state.search.get(id) ?? null,
    // Picking a row leaves the sheet where the pilot put it (no auto-shrink from full).
    selectLake: (id) => {
      if (!state.selectLake(id, "list")) toast("That lake is not in this data pack");
    },
    selectRegion: (id, lat, lon) => {
      if (!state.selectLake(id, "list")) {
        toast("That water body is not in this data pack");
        return;
      }
      // After the selection's own fitBounds has run, so the region wins the camera.
      map.map.once("moveend", () => map.flyToRegion(lon, lat));
    },
  });
  briefing.mountPanel(tabs.panel("briefing"));
  briefing.mountPeek(sheet.slots.peek);
  briefing.mountChip(app);
  briefing.start();

  // Waves over time: the map's time bar and the Water section's forecast mode.
  const forecastWaves = new ForecastWaves(app, { sheet, timeline: () => briefing.store.briefing?.timeline });

  const search = new SearchBox(app, {
    index: state.search,
    onSelect: (id) => {
      if (!state.selectLake(id, "search")) toast("That lake is not in the index");
    },
    onOpenAbout: () => openAbout(state, theme, () => briefing.openSettings()),
  });

  const nearest = new NearestList({
    lakes: () => state.search.all(),
    location,
    mapCenter: () => {
      const c = map.map.getCenter();
      return { lat: c.lat, lon: c.lng };
    },
    onSelect: (id) => {
      if (!state.selectLake(id, "list")) toast("That lake is not in this data pack");
    },
    minRunFt: () => minRunFt(WAVES.defaultMinRunFt),
  });
  tabs.panel("nearest").append(nearest.element);
  tabs.addListener((id) => nearest.setVisible(id === "nearest"));
  map.map.on("moveend", () => nearest.onMapMoved());
  mountNearestButton(controls, () => {
    tabs.select("nearest");
    nearest.setVisible(true);
    // An explicit ask for the list: raise to at least half so rows are visible.
    sheet.grow("half");
  });

  theme.onChange((resolved) => {
    map.setTheme(resolved);
    applyWaveBandVars(resolved);
  });
  // The wave key shows as its chip while a phone sheet covers half the map or more.
  const compactLegend = () =>
    legend.setCompact(!sheet.isPanel && (sheet.current === "half" || sheet.current === "full"));
  sheet.onSnapChange(() => {
    map.resize();
    compactLegend();
    forecastWaves.place();
  });
  window.addEventListener("resize", () => {
    map.resize();
    forecastWaves.place();
  });

  state.onSelection((selection) => {
    if (!selection) {
      map.clearSelection();
      legend.hide();
      forecastWaves.detach();
      wind.select(null, null, null);
      waterToken++;
      // Clearing a lake that was picked from the briefing falls back to the briefing
      // rather than closing the sheet out from under it.
      if (briefing.isOpen) {
        detailPanel.replaceChildren(detailPlaceholder());
        briefing.reveal(false);
        return;
      }
      // Nothing left to show: the pilot tapped empty map to clear the selection.
      sheet.setSnap("hidden");
      return;
    }
    // Selecting a lake needs the camera; recenter resumes following.
    follow?.pause();
    renderSelection(selection);
    map.select(selection.lake);
    tabs.select("detail");
    // No auto-popup: a visible sheet stays where the pilot left it; a hidden one shows
    // peek (or the stored snap on the first show after a reload). A collapsed iPad panel
    // stays collapsed; its tab picks up the new name from the peek row.
    sheet.reveal();
  });

  function renderSelection(selection: Selection): void {
    const input: DetailInput = {
      lake: selection.lake,
      evaluation: selection.evaluation,
      restrictions: selection.restrictions,
      pack: state.pack,
    };
    renderPeek(input, sheet.slots.peek);
    const detail = renderDetail(input);
    wind.select(selection.lake, detail.element, sheet.slots.peek, detail.water);
    detailPanel.replaceChildren(detail.element);
    detailPanel.scrollTop = 0;
    void mountWaterSection(selection, detail.water);
  }

  /**
   * Loads this water body's wave field and mounts the Water section, which then owns the
   * wind and pushes every change at the map. The token guards against a slow Range request
   * landing after the pilot has already tapped a different lake.
   */
  async function mountWaterSection(selection: Selection, slot: HTMLElement): Promise<void> {
    const token = ++waterToken;
    map.setWaveField(null);
    legend.hide();
    forecastWaves.detach();

    const field = await waves.field(selection.lake.id);
    if (token !== waterToken || !field) return;
    const [minRun, limits] = await Promise.all([
      minRunFt(WAVES.defaultMinRunFt),
      waveLimits(WAVES.defaultLimits),
    ]);
    if (token !== waterToken || !slot.isConnected) return;

    const briefed = briefing.store.briefing;
    const paint = (waveState: WaveState): void => {
      if (token !== waterToken) return;
      map.setWaveField(waveGeo(waveState));
      legend.show(waveState);
    };

    const section = new WaterSection({
      lake: selection.lake,
      field,
      minRunFt: minRun,
      limits,
      initialWind: defaultWindFor(briefed, selection.lake.id),
      forecast: forecastWindFor(briefed, selection.lake.id),
      onChange: paint,
      onRegionTap: (_region, point) => map.flyToRegion(point.lon, point.lat),
    });
    slot.replaceChildren(section.element);
    paint(section.current);
    void forecastWaves.attach(section, field, () => token === waterToken);
  }

  if (state.search.size === 0) {
    toast("No lake index found under /data. Run the fixtures script or the pipeline.");
  }

  maybeShowFirstRun(state);

  // `?briefing=1` first, so `?lake=` still wins the sheet when both are present.
  if (readBriefingParam()) briefing.reveal();

  const fromUrl = readUrlParam();
  if (fromUrl != null) {
    if (!state.selectLake(fromUrl, "url")) {
      toast(`Lake ${fromUrl} is not in this data pack`);
    }
  }

  // Deliberately global: handy from the console and from the phase-3 service worker.
  Object.assign(window, {
    seaplane: { state, map, sheet, search, theme, briefing, location, follow, nearest, tabs, wind },
  });

  // Last, so the permission prompt comes after the map and sheet are up.
  location.start();
}

void boot().catch((err: unknown) => {
  console.error("[boot] failed", err);
  const app = document.getElementById("app");
  if (app) {
    const fatal = el("div", "fatal");
    fatal.append(
      el("h1", undefined, "Could not start"),
      el("p", undefined, err instanceof Error ? err.message : String(err)),
    );
    app.append(fatal);
  }
});
