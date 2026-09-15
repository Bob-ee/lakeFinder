import { STORAGE_KEYS } from "../config";
import type { MapController } from "../map";
import { OVERLAY_GROUPS, type OverlayGroup } from "../map/style";
import { el } from "./format";
import { icon } from "./icons";
import { readLocal, writeLocal } from "./theme";
import { toast } from "./toast";

export interface LayerPrefs {
  usableWater: boolean;
  overlays: Record<OverlayGroup, boolean>;
}

/** Overlays are off by default except BAS launch points (design.md 7.2). */
export function defaultLayerPrefs(): LayerPrefs {
  return {
    usableWater: false,
    overlays: { federal: false, airspace: false, bas: true, airports: false },
  };
}

export function loadLayerPrefs(): LayerPrefs {
  const base = defaultLayerPrefs();
  const raw = readLocal(STORAGE_KEYS.layers);
  if (!raw) return base;
  try {
    const parsed = JSON.parse(raw) as Partial<LayerPrefs>;
    if (typeof parsed.usableWater === "boolean") base.usableWater = parsed.usableWater;
    if (parsed.overlays) {
      for (const key of Object.keys(base.overlays) as OverlayGroup[]) {
        const v = parsed.overlays[key];
        if (typeof v === "boolean") base.overlays[key] = v;
      }
    }
  } catch {
    // Corrupt preference: fall back to the defaults rather than failing startup.
  }
  return base;
}

export function saveLayerPrefs(prefs: LayerPrefs): void {
  writeLocal(STORAGE_KEYS.layers, JSON.stringify(prefs));
}

const OVERLAY_LABELS: Record<OverlayGroup, string> = {
  federal: "Federal units",
  airspace: "Airspace",
  bas: "Launch sites",
  airports: "Airports",
};

/** Right-edge control stack. 48 px targets, one-handed reach (design.md 7.1). */
export function mountMapControls(
  parent: HTMLElement,
  map: MapController,
  prefs: LayerPrefs,
): HTMLElement {
  const stack = el("div", "map-controls");

  const recenter = el("button", "icon-btn map-btn");
  recenter.type = "button";
  recenter.title = "Recenter (follow-me arrives in phase 2)";
  recenter.setAttribute("aria-label", "Recenter map");
  recenter.innerHTML = icon("recenter");
  recenter.addEventListener("click", () => {
    map.recenter();
    toast("Follow-me arrives in phase 2");
  });

  const layersBtn = el("button", "icon-btn map-btn");
  layersBtn.type = "button";
  layersBtn.title = "Layers";
  layersBtn.setAttribute("aria-label", "Map layers");
  layersBtn.setAttribute("aria-expanded", "false");
  layersBtn.innerHTML = icon("layers");

  const panel = el("div", "layers-panel");
  panel.hidden = true;
  panel.setAttribute("role", "group");
  panel.setAttribute("aria-label", "Map layers");

  const tiles = map.tiles;

  panel.append(
    toggleRow({
      label: "Usable water",
      hint: tiles.usableWater ? "zoom 12+" : "tiles not built",
      checked: prefs.usableWater,
      disabled: !tiles.usableWater,
      onChange: (on) => {
        prefs.usableWater = on;
        map.setUsableWater(on);
        saveLayerPrefs(prefs);
      },
    }),
  );

  for (const group of Object.keys(OVERLAY_GROUPS) as OverlayGroup[]) {
    panel.append(
      toggleRow({
        label: OVERLAY_LABELS[group],
        hint: tiles.overlays ? undefined : "tiles not built",
        checked: prefs.overlays[group],
        disabled: !tiles.overlays,
        onChange: (on) => {
          prefs.overlays[group] = on;
          map.setOverlay(group, on);
          saveLayerPrefs(prefs);
        },
      }),
    );
  }

  layersBtn.addEventListener("click", () => {
    const show = Boolean(panel.hidden);
    panel.hidden = !show;
    layersBtn.setAttribute("aria-expanded", String(show));
    layersBtn.classList.toggle("is-active", show);
  });
  document.addEventListener("pointerdown", (e) => {
    if (panel.hidden) return;
    if (stack.contains(e.target as Node)) return;
    panel.hidden = true;
    layersBtn.setAttribute("aria-expanded", "false");
    layersBtn.classList.remove("is-active");
  });

  stack.append(recenter, layersBtn, panel);
  parent.append(stack);
  return stack;
}

function toggleRow(opts: {
  label: string;
  hint?: string | undefined;
  checked: boolean;
  disabled: boolean;
  onChange: (on: boolean) => void;
}): HTMLElement {
  const row = el("label", "toggle-row");
  const box = document.createElement("input");
  box.type = "checkbox";
  box.checked = opts.checked && !opts.disabled;
  box.disabled = opts.disabled;
  box.addEventListener("change", () => opts.onChange(box.checked));

  const text = el("span", "toggle-text");
  text.append(el("span", undefined, opts.label));
  if (opts.hint) text.append(el("span", "muted small", opts.hint));

  row.append(box, text);
  if (opts.disabled) row.classList.add("is-disabled");
  return row;
}
