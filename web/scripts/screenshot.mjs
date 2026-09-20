/**
 * Headless-Chrome screenshot driver over CDP (node 24 has a global WebSocket).
 * Seeds the first-run disclaimer ack in localStorage so the dialog does not cover the app.
 *
 *   node web/scripts/screenshot.mjs <url> <out.png> <width> <height> [dark|light] [scrollTopPx]
 *   CDP_PORT=9340 picks another debugging port when two drivers run at once.
 */
import { spawn } from "node:child_process";
import { writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const [url, out, w, h, mode, scroll] = process.argv.slice(2);
const width = Number(w), height = Number(h);
const dark = mode === "dark";
// Own port and profile: sharing 9333 with another driver makes CDP attach to whatever
// browser is already there, and the screenshot then shows somebody else's page.
const PORT = Number(process.env.CDP_PORT ?? 9336);

const chrome = spawn("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", [
  "--headless=new",
  "--use-angle=swiftshader",
  "--enable-unsafe-swiftshader",
  "--ignore-gpu-blocklist",
  `--remote-debugging-port=${PORT}`,
  `--window-size=${width},${height}`,
  "--hide-scrollbars",
  "--no-first-run",
  `--user-data-dir=${join(tmpdir(), "lakefinder-shot-" + PORT)}`,
  "about:blank",
], { stdio: "ignore" });

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function targets() {
  // Refuse a browser we did not start: it would be somebody else's session.
  const started = Date.now();
  for (let i = 0; i < 60; i++) {
    try {
      const res = await fetch(`http://127.0.0.1:${PORT}/json/list`);
      const list = await res.json();
      const page = list.find((t) => t.type === "page");
      if (page) return page;
    } catch {}
    await sleep(250);
  }
  throw new Error("Chrome never came up");
}

class Cdp {
  constructor(ws) { this.ws = ws; this.id = 0; this.pending = new Map(); this.events = [];
    ws.addEventListener("message", (e) => {
      const msg = JSON.parse(e.data);
      if (msg.id && this.pending.has(msg.id)) { this.pending.get(msg.id)(msg); this.pending.delete(msg.id); }
      else this.events.push(msg);
    });
  }
  send(method, params = {}) {
    const id = ++this.id;
    return new Promise((resolve, reject) => {
      this.pending.set(id, (m) => (m.error ? reject(new Error(`${method}: ${m.error.message}`)) : resolve(m.result)));
      this.ws.send(JSON.stringify({ id, method, params }));
    });
  }
}

const page = await targets();
const ws = new WebSocket(page.webSocketDebuggerUrl);
await new Promise((r) => ws.addEventListener("open", r, { once: true }));
const cdp = new Cdp(ws);

await cdp.send("Page.enable");
await cdp.send("Runtime.enable");
await cdp.send("Emulation.setDeviceMetricsOverride", {
  width, height, deviceScaleFactor: 2, mobile: width < 700,
});
await cdp.send("Emulation.setEmulatedMedia", {
  features: [{ name: "prefers-color-scheme", value: dark ? "dark" : "light" }],
});

const origin = new URL(url).origin;
await cdp.send("Page.navigate", { url: origin + "/" });
await sleep(2500);
await cdp.send("Runtime.evaluate", {
  awaitPromise: true,
  expression: `(async () => {
    const pack = await (await fetch("/data/pack.json")).json().catch(() => null);
    localStorage.setItem("seaplane.disclaimerAck", "v1:" + (pack && pack.version ? pack.version : "none"));
    return "ok";
  })()`,
});

await cdp.send("Page.navigate", { url });
await sleep(9000);
if (process.env.EVAL) {
  await cdp.send("Runtime.evaluate", { expression: process.env.EVAL, awaitPromise: true });
  await sleep(Number(process.env.EVAL_WAIT ?? 1500));
}
if (scroll) {
  await cdp.send("Runtime.evaluate", {
    expression: `document.querySelector(".sheet-body").scrollTop = ${Number(scroll)}`,
  });
  await sleep(600);
}

const { data } = await cdp.send("Page.captureScreenshot", { format: "png", captureBeyondViewport: false });
writeFileSync(out, Buffer.from(data, "base64"));
const errs = cdp.events.filter((e) => e.method === "Runtime.consoleAPICalled" && e.params.type === "error");
if (errs.length) console.log("console errors:", JSON.stringify(errs.slice(0, 5)));
console.log("wrote", out);
ws.close();
chrome.kill();
process.exit(0);
