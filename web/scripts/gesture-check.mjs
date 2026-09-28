/**
 * Drives the sheet, the iPad panel and the wave key with CDP touch input in headless
 * Chrome (touch emulation, mobile metrics) and prints what happened after each step.
 * CDP touch is Chrome's input pipeline, not a real iPhone: it checks the logic, not iOS
 * Safari's scroll arbitration.
 *
 *   node web/scripts/gesture-check.mjs [origin] [outDir]
 *   defaults: http://localhost:5203  and the current directory. CDP_PORT picks the port.
 */
import { spawn } from "node:child_process";
import { writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const origin = process.argv[2] ?? "http://localhost:5203";
const outDir = process.argv[3] ?? ".";
const PORT = Number(process.env.CDP_PORT ?? 9352);
const SMALL = 1900195525; // Cass Lake
const BIG = 657423876; // Lake St. Clair (has a wave field)

const chrome = spawn("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", [
  "--headless=new", "--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist",
  `--remote-debugging-port=${PORT}`, "--window-size=1180,900", "--hide-scrollbars", "--no-first-run",
  `--user-data-dir=${join(tmpdir(), "lakefinder-gesture-" + PORT)}`, "about:blank",
], { stdio: "ignore" });

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
let failures = 0;

async function pageTarget() {
  for (let i = 0; i < 60; i++) {
    try {
      const list = await (await fetch(`http://127.0.0.1:${PORT}/json/list`)).json();
      const page = list.find((t) => t.type === "page");
      if (page) return page;
    } catch {}
    await sleep(250);
  }
  throw new Error("Chrome never came up");
}

const page = await pageTarget();
const ws = new WebSocket(page.webSocketDebuggerUrl);
await new Promise((r) => ws.addEventListener("open", r, { once: true }));
let seq = 0;
const pending = new Map();
ws.addEventListener("message", (e) => {
  const m = JSON.parse(e.data);
  if (m.id && pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id); }
});
const send = (method, params = {}) => new Promise((resolve, reject) => {
  const id = ++seq;
  pending.set(id, (m) => (m.error ? reject(new Error(`${method}: ${m.error.message}`)) : resolve(m.result)));
  ws.send(JSON.stringify({ id, method, params }));
});
const js = async (expression) => {
  const r = await send("Runtime.evaluate", { expression, awaitPromise: true, returnByValue: true });
  if (r.exceptionDetails) throw new Error(`${expression}: ${r.exceptionDetails.text}`);
  return r.result.value;
};
const shot = async (name) => {
  const { data } = await send("Page.captureScreenshot", { format: "png" });
  const path = join(outDir, name);
  writeFileSync(path, Buffer.from(data, "base64"));
  console.log("   screenshot", path);
};
const snap = () => js("seaplane.sheet.current");
const rect = (sel) => js(`(() => { const r = document.querySelector(${JSON.stringify(sel)})?.getBoundingClientRect();
  return r ? { x: r.x, y: r.y, w: r.width, h: r.height, b: r.bottom, r: r.right } : null })()`);
function check(label, got, want) {
  const ok = JSON.stringify(got) === JSON.stringify(want);
  if (!ok) failures++;
  console.log(`${ok ? "ok  " : "FAIL"} ${label}: ${JSON.stringify(got)}${ok ? "" : ` (want ${JSON.stringify(want)})`}`);
}

const touch = (type, x, y) => send("Input.dispatchTouchEvent", {
  type, touchPoints: type === "touchEnd" ? [] : [{ x, y, radiusX: 4, radiusY: 4, force: 1 }],
});
async function tap(x, y) {
  await touch("touchStart", x, y);
  await sleep(60);
  await touch("touchEnd", x, y);
  await sleep(400);
}
/** Finger from (x, y0) to (x, y1) in `steps` moves `stepMs` apart, holding `holdMs` before lifting. */
async function drag(x, y0, y1, { steps = 20, stepMs = 16, holdMs = 150, dx = 0 } = {}) {
  await touch("touchStart", x, y0);
  for (let i = 1; i <= steps; i++) {
    await sleep(stepMs);
    await touch("touchMove", x + (dx * i) / steps, y0 + ((y1 - y0) * i) / steps);
  }
  if (holdMs) {
    await sleep(holdMs);
    await touch("touchMove", x + dx, y1); // still finger: release velocity ~0
  }
  await touch("touchEnd", x + dx, y1);
  await sleep(450);
}

async function device(width, height, mobile) {
  await send("Emulation.setDeviceMetricsOverride", { width, height, deviceScaleFactor: Number(process.env.DSF ?? 1), mobile });
  await send("Emulation.setTouchEmulationEnabled", { enabled: true, maxTouchPoints: 5 });
  await send("Emulation.setEmitTouchEventsForMouse", { enabled: true, configuration: mobile ? "mobile" : "desktop" });
}
async function load(path, wait = 7000) {
  await send("Page.navigate", { url: origin + path });
  await sleep(wait);
}

await send("Page.enable");
await send("Runtime.enable");
await send("Emulation.setEmulatedMedia", { features: [{ name: "prefers-color-scheme", value: "light" }] });

// -- phone ------------------------------------------------------------------
const W = 390, H = 844;
await device(W, H, true);
await load("/", 2500);
await js(`(async () => {
  const pack = await (await fetch("/data/pack.json")).json().catch(() => null);
  localStorage.clear();
  localStorage.setItem("seaplane.disclaimerAck", "v1:" + (pack && pack.version ? pack.version : "none"));
})()`);
await load("/");
console.log("phone 390x844");
check("1. on load, nothing selected", await snap(), "hidden");
await js(`seaplane.state.selectLake(${SMALL}, "search")`);
await sleep(700);
check("2. select a lake from hidden", await snap(), "peek");
await shot("sheet-1-peek.png");

// Drag the peek row slowly up to about half.
let peek = await rect(".sheet-peek");
await drag(W / 2, peek.y + 30, peek.y + 30 - (H * 0.5 - 92), { steps: 24 });
check("3. drag header up to half", await snap(), "half");
await shot("sheet-2-half.png");

// Quick flick up on the tab strip.
const tabs = await rect(".sheet-tabs");
if (process.env.DEBUG) await js(`window.__log = []; for (const t of ["pointerdown","pointermove","pointerup","pointercancel","touchstart","touchmove","touchend"]) document.querySelector(".sheet").addEventListener(t, (e) => __log.push(t + " " + Math.round(e.timeStamp) + " " + (e.clientY ?? e.touches?.[0]?.clientY ?? "") + " " + (e.target.className||"")), true)`);
// 150 px from half: by position alone that settles back at half; only the speed says full.
await drag(W / 2, tabs.y + 20, tabs.y + 20 - 150, { steps: 3, stepMs: 0, holdMs: 0 });
check("4. flick header up (150 px, fast)", await snap(), "full");
if (process.env.DEBUG) console.log(await js("__log.join('\\n')"));
check("   flick did not switch the tab", await js("seaplane.tabs.current"), "detail");
const nearestTab = await rect("#tab-nearest");
await tap(nearestTab.x + nearestTab.w / 2, nearestTab.y + nearestTab.h / 2);
check("   a plain tap on a tab still works", [await js("seaplane.tabs.current"), await snap()], ["nearest", "full"]);
const detailTab = await rect("#tab-detail");
await tap(detailTab.x + detailTab.w / 2, detailTab.y + detailTab.h / 2);

// Body scrolled down: a downward drag scrolls the body, the sheet stays.
await js(`document.querySelector(".sheet-body").scrollTop = 200`);
await sleep(200);
let body = await rect(".sheet-body");
await drag(W / 2, body.y + 150, body.y + 290, { steps: 12 });
const scrolled = await js(`document.querySelector(".sheet-body").scrollTop`);
check("5. body scrolled: drag down scrolls, sheet stays", [await snap(), scrolled < 200], ["full", true]);

// Body at the top: a downward drag moves the sheet.
await js(`document.querySelector(".sheet-body").scrollTop = 0`);
await sleep(200);
body = await rect(".sheet-body");
await drag(W / 2, body.y + 60, body.y + 60 + (H * 0.92 - H * 0.5), { steps: 24 });
check("6. body at top, drag down", await snap(), "half");
await shot("sheet-3-body-drag-to-half.png");

// Half: an upward drag in the body raises the sheet.
body = await rect(".sheet-body");
await drag(W / 2, body.y + 200, body.y + 200 - (H * 0.92 - H * 0.5), { steps: 24 });
check("7. half, body drag up", await snap(), "full");
await drag(W / 2, (await rect(".sheet-body")).y + 40, (await rect(".sheet-body")).y + 40 + 360, { steps: 20 });
check("   and back down", await snap(), "half");

// Tap the handle: straight to peek.
let handle = await rect(".sheet-handle");
await tap(W / 2, handle.y + handle.h / 2);
check("8. tap handle", await snap(), "peek");
await shot("sheet-4-tap-to-peek.png");

// Another lake: the sheet stays at peek.
await js(`seaplane.state.selectLake(${BIG}, "search")`);
await sleep(2500);
check("9. select another lake at peek", await snap(), "peek");
await shot("sheet-5-second-lake-peek.png");

// Wave key: minimized by default, tap expands, no overlap with the buttons or chip.
const overlap = (a, b) => !!a && !!b && a.x < b.r && b.x < a.r && a.y < b.b && b.y < a.b;
check("10. wave key minimized by default", await js(`document.querySelector(".wave-key").className`), "wave-key is-min");
let key = await rect(".wave-key-chip");
check("    chip height <= 48", key.h <= 48, true);
const ctl = await rect(".map-controls"), brief = await rect(".brief-chip");
check("    chip clear of buttons / briefing chip", [overlap(key, ctl), overlap(key, brief)], [false, false]);
await shot("sheet-legend-min.png");
await tap(key.x + key.w / 2, key.y + key.h / 2);
check("11. tap chip expands", await js(`document.querySelector(".wave-key").className`), "wave-key");
const card = await rect(".wave-key-card");
check("    card clear of buttons / briefing chip", [overlap(card, ctl), overlap(card, brief)], [false, false]);
await shot("sheet-legend-open.png");

// Tap the handle again: back to the last open height (half).
handle = await rect(".sheet-handle");
await tap(W / 2, handle.y + handle.h / 2);
check("12. tap handle from peek", await snap(), "half");
check("    wave key compact while the sheet is at half", await js(`document.querySelector(".wave-key").className`), "wave-key is-min");

await load(`/?lake=${BIG}`, 8000);
check("13. reload: snap restored", await snap(), "half");
await drag(W / 2, (await rect(".sheet-handle")).y + 10, (await rect(".sheet-handle")).y + 10 + 330, { steps: 20 });
check("    lower to peek", await snap(), "peek");
check("    wave key still expanded after reload", await js(`document.querySelector(".wave-key").className`), "wave-key");
const head = await rect(".wave-key-head");
await tap(head.x + head.w / 2, head.y + head.h / 2);
await load(`/?lake=${BIG}`, 8000);
check("14. minimized, reload: still minimized", await js(`document.querySelector(".wave-key").className`), "wave-key is-min");

// Drag below peek: dismissed; selection and outline stay.
peek = await rect(".sheet-peek");
await drag(W / 2, peek.y + 20, peek.y + 110, { steps: 10 });
check("15. drag below peek", [await snap(), await js("seaplane.state.current?.lake.id")], ["hidden", BIG]);
await shot("sheet-6-dismissed.png");
await js(`seaplane.state.selectLake(${BIG}, "map")`);
await sleep(700);
check("16. re-select from hidden", await snap(), "peek");

// -- iPad ---------------------------------------------------------------------
console.log("iPad 1180x820");
await device(1180, 820, false);
await load(`/?lake=${SMALL}`, 8000);
check("17. panel open", [await snap(), await js("seaplane.sheet.widthPx")], ["peek", 380]);
await shot("sheet-ipad-open.png");
const col = await rect(".sheet-collapse");
check("    collapse button >= 48 px", col.h >= 48 && col.w >= 48, true);
await tap(col.x + col.w / 2, col.y + col.h / 2);
await sleep(300);
check("18. collapse", [await js("seaplane.sheet.isCollapsed"), await js("seaplane.sheet.widthPx")], [true, 0]);
check("    tab text", await js(`document.querySelector(".sheet-restore").innerText.replace(/\\s+/g, " ").trim()`), "Cass Lake Conditional");
check("    map buttons moved to the edge", (await rect(".map-controls")).r, 1180 - 12);
await shot("sheet-ipad-collapsed.png");
await js(`seaplane.state.selectLake(${BIG}, "search")`);
await sleep(1500);
check("19. select another lake: still collapsed", [await js("seaplane.sheet.isCollapsed"), await js(`document.querySelector(".sheet-restore .sheet-restore-name").textContent`)], [true, "Lake St. Clair"]);
await shot("sheet-ipad-collapsed-second.png");
const tab = await rect(".sheet-restore");
await tap(tab.x + tab.w / 2, tab.y + tab.h / 2);
check("20. tap tab restores", [await js("seaplane.sheet.isCollapsed"), await js("Math.round(seaplane.sheet.widthPx)")], [false, 380]);
const ph = await rect(".sheet-peek");
await drag(ph.x + 80, ph.y + 30, ph.y + 34, { steps: 10, dx: 140, holdMs: 0 });
check("21. swipe right on the panel header", await js("seaplane.sheet.isCollapsed"), true);
await load(`/?lake=${SMALL}`, 8000);
check("22. reload: still collapsed", await js("seaplane.sheet.isCollapsed"), true);

console.log(failures ? `${failures} check(s) failed` : "all checks passed");
ws.close();
chrome.kill();
process.exit(failures ? 1 : 0);
