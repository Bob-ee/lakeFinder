import type { PaddingOptions } from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import "./styles/base.css";
import "./styles/map.css";
import "./styles/search.css";
import "./styles/sheet.css";

import { Tabs } from "./lists";
import { MapController } from "./map";
import { SearchBox } from "./search/ui";
import { Sheet } from "./sheet";
import { renderDetail, renderPeek, type DetailInput } from "./sheet/detail";
import { AppState, readUrlParam, type Selection } from "./state";
import { maybeShowFirstRun, openAbout } from "./ui/about";
import { el } from "./ui/format";
import { loadLayerPrefs, mountMapControls } from "./ui/controls";
import { Theme } from "./ui/theme";
import { toast } from "./ui/toast";

async function boot(): Promise<void> {
  const theme = new Theme();
  const app = document.getElementById("app");
  if (!app) throw new Error("#app missing from index.html");

  const mapEl = el("div", "map");
  mapEl.id = "map";
  app.append(mapEl);

  const state = new AppState();
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

  mountMapControls(app, map, prefs);

  const search = new SearchBox(app, {
    index: state.search,
    onSelect: (id) => {
      if (!state.selectLake(id, "search")) toast("That lake is not in the index");
    },
    onOpenAbout: () => openAbout(state, theme),
  });

  theme.onChange((resolved) => map.setTheme(resolved));
  sheet.onSnapChange(() => map.resize());
  window.addEventListener("resize", () => map.resize());

  state.onSelection((selection) => {
    if (!selection) {
      sheet.setSnap("hidden");
      map.clearSelection();
      return;
    }
    renderSelection(selection);
    map.select(selection.lake);
    tabs.select("detail");
    // Re-open at half on a new selection; leave a sheet already at full alone.
    if (sheet.current !== "full") sheet.setSnap("half");
  });

  function renderSelection(selection: Selection): void {
    const input: DetailInput = {
      lake: selection.lake,
      evaluation: selection.evaluation,
      restrictions: selection.restrictions,
      pack: state.pack,
    };
    renderPeek(input, sheet.slots.peek);
    detailPanel.replaceChildren(renderDetail(input));
    detailPanel.scrollTop = 0;
  }

  if (state.search.size === 0) {
    toast("No lake index found under /data. Run the fixtures script or the pipeline.");
  }

  maybeShowFirstRun(state);

  const fromUrl = readUrlParam();
  if (fromUrl != null) {
    if (!state.selectLake(fromUrl, "url")) {
      toast(`Lake ${fromUrl} is not in this data pack`);
    }
  }

  // Deliberately global: handy from the console and from the phase-3 service worker.
  Object.assign(window, { seaplane: { state, map, sheet, search, theme } });
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
