import { createReadStream, existsSync, readFileSync, statSync } from "node:fs";
import { createRequire } from "node:module";
import path from "node:path";
import { fileURLToPath } from "node:url";
import type { Connect, Plugin } from "vite";
import { defineConfig } from "vite";

const here = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(here, "..");
const rulesDir = path.resolve(repoRoot, "rules");
const pipelineOut = path.resolve(repoRoot, "data/out");
const devFixtures = path.resolve(here, "dev-fixtures");

/**
 * Real pipeline output if it has been built, otherwise the checked-in fixtures.
 * `SEAPLANE_DATA_DIR` forces a directory, which is handy when the pipeline output is
 * mid-rebuild and you want to work against the fixtures anyway.
 */
function dataDir(): string {
  const override = process.env["SEAPLANE_DATA_DIR"];
  if (override) return path.resolve(here, override);
  return existsSync(path.join(pipelineOut, "pack.json")) ? pipelineOut : devFixtures;
}

const CONTENT_TYPES: Record<string, string> = {
  ".pmtiles": "application/octet-stream",
  ".json": "application/json; charset=utf-8",
  ".geojson": "application/geo+json",
  ".yaml": "text/yaml; charset=utf-8",
  ".txt": "text/plain; charset=utf-8",
};

/**
 * Serves `/data/*` with byte-range support. pmtiles issues HTTP Range requests for the
 * header, the directory tree and every tile; without 206 responses the map stays blank.
 * Production is Caddy, which does this natively. Used by both `vite dev` and `vite preview`.
 */
function dataRangeServer(): Plugin {
  const middleware: Connect.NextHandleFunction = (req, res, next) => {
    const rawUrl = req.url ?? "/";
    if (!rawUrl.startsWith("/data/")) return next();

    const rel = decodeURIComponent(rawUrl.split("?")[0]!.slice("/data/".length));
    const root = dataDir();
    const file = path.resolve(root, rel);
    // Refuse anything that escapes the data directory.
    if (file !== root && !file.startsWith(root + path.sep)) {
      res.statusCode = 403;
      res.end("Forbidden");
      return;
    }

    let stat;
    try {
      stat = statSync(file);
    } catch {
      res.statusCode = 404;
      res.setHeader("Content-Type", "text/plain; charset=utf-8");
      res.end(`Not found in ${root}: ${rel}`);
      return;
    }
    if (!stat.isFile()) {
      res.statusCode = 404;
      res.end("Not found");
      return;
    }

    const size = stat.size;
    const type = CONTENT_TYPES[path.extname(file).toLowerCase()] ?? "application/octet-stream";
    res.setHeader("Content-Type", type);
    res.setHeader("Accept-Ranges", "bytes");
    res.setHeader("Cache-Control", "no-cache");
    res.setHeader("Last-Modified", stat.mtime.toUTCString());

    const method = (req.method ?? "GET").toUpperCase();
    if (method === "OPTIONS") {
      res.statusCode = 204;
      res.setHeader("Allow", "GET, HEAD, OPTIONS");
      res.end();
      return;
    }
    if (method !== "GET" && method !== "HEAD") {
      res.statusCode = 405;
      res.end("Method not allowed");
      return;
    }

    const range = req.headers.range;
    const match = range ? /^bytes=(\d*)-(\d*)$/.exec(range.trim()) : null;

    if (!match) {
      res.statusCode = 200;
      res.setHeader("Content-Length", String(size));
      if (method === "HEAD") {
        res.end();
        return;
      }
      createReadStream(file).pipe(res);
      return;
    }

    const startRaw = match[1] ?? "";
    const endRaw = match[2] ?? "";
    let start: number;
    let end: number;
    if (startRaw === "") {
      // Suffix range: the last N bytes. pmtiles does not use these, but be correct anyway.
      const suffix = Number(endRaw);
      if (!Number.isFinite(suffix) || suffix <= 0) {
        res.statusCode = 416;
        res.setHeader("Content-Range", `bytes */${size}`);
        res.end();
        return;
      }
      start = Math.max(0, size - suffix);
      end = size - 1;
    } else {
      start = Number(startRaw);
      end = endRaw === "" ? size - 1 : Number(endRaw);
    }

    if (!Number.isFinite(start) || !Number.isFinite(end) || start > end || start >= size) {
      res.statusCode = 416;
      res.setHeader("Content-Range", `bytes */${size}`);
      res.end();
      return;
    }
    end = Math.min(end, size - 1);

    res.statusCode = 206;
    res.setHeader("Content-Range", `bytes ${start}-${end}/${size}`);
    res.setHeader("Content-Length", String(end - start + 1));
    if (method === "HEAD") {
      res.end();
      return;
    }
    createReadStream(file, { start, end }).pipe(res);
  };

  return {
    name: "seaplane:data-range-server",
    configureServer(server) {
      server.middlewares.use(middleware);
      server.config.logger.info(`  [data] /data served from ${dataDir()}`);
    },
    configurePreviewServer(server) {
      server.middlewares.use(middleware);
    },
  };
}

/**
 * `virtual:rules-engine` re-exports ../rules/engine/index.js when that file exists and a
 * null-shaped stub when it does not, so the client builds before the rules agent lands its
 * engine and picks it up automatically once it does.
 */
function rulesEngineShim(): Plugin {
  const id = "virtual:rules-engine";
  const resolved = "\0" + id;
  const enginePath = path.join(rulesDir, "engine/index.js");
  return {
    name: "seaplane:rules-engine-shim",
    resolveId(source) {
      return source === id ? resolved : null;
    },
    load(loadId) {
      if (loadId !== resolved) return null;
      if (!existsSync(enginePath)) {
        return [
          "// rules/engine/index.js was not present at build time.",
          "export const engineAvailable = false;",
          "export const evaluateLake = null;",
          "export const evaluateAll = null;",
          "export const loadRules = null;",
          "export const normalizeName = null;",
        ].join("\n");
      }
      return [
        'import * as impl from "@rules/engine/index.js";',
        "export const engineAvailable = true;",
        "export const evaluateLake = impl.evaluateLake ?? null;",
        "export const evaluateAll = impl.evaluateAll ?? null;",
        "export const loadRules = impl.loadRules ?? null;",
        "export const normalizeName = impl.normalizeName ?? null;",
      ].join("\n");
    },
  };
}

const require = createRequire(import.meta.url);
const pkg = require("./package.json") as { version: string };

/** Where the maplibre trio is published in the build output. */
const MAPLIBRE_DIR = "vendor/maplibre";
const MAPLIBRE_FILES = [
  "maplibre-gl.mjs",
  "maplibre-gl-shared.mjs",
  "maplibre-gl-worker.mjs",
];

/**
 * maplibre-gl v6 ships as three sibling ESM files and computes its worker URL at runtime
 * from `import.meta.url`, so the worker must sit next to the module the browser actually
 * loaded. Bundling it into one chunk leaves the worker URL pointing at a file that does
 * not exist: the worker never starts, every tile stays in "loading", and the map renders
 * blank with no error.
 *
 * So maplibre is left external and its own dist files are copied out verbatim. The worker
 * then finds its siblings, and the 500 KB shared chunk is fetched once and served to the
 * worker from the HTTP cache instead of being duplicated into the app bundle.
 */
function maplibreAssets(): Plugin {
  const dist = path.dirname(require.resolve("maplibre-gl/dist/maplibre-gl.mjs"));
  return {
    name: "seaplane:maplibre-assets",
    apply: "build",
    generateBundle() {
      for (const name of MAPLIBRE_FILES) {
        this.emitFile({
          type: "asset",
          fileName: `${MAPLIBRE_DIR}/${name}`,
          source: readFileSync(path.join(dist, name)),
        });
      }
    },
  };
}

export default defineConfig({
  root: here,
  optimizeDeps: {
    // maplibre-gl v6 ships as three sibling ESM files and derives its worker URL from
    // import.meta.url at runtime. Pre-bundling it into .vite/deps breaks that lookup, the
    // worker 404s, and every tile stays stuck in "loading" with a blank map. See
    // maplibreAssets() below for the matching production arrangement.
    exclude: ["maplibre-gl"],
  },
  resolve: {
    alias: {
      "@rules": rulesDir,
    },
  },
  server: {
    fs: {
      // The @rules alias points outside the Vite root.
      allow: [here, rulesDir],
    },
  },
  build: {
    target: "es2022",
    sourcemap: true,
    rollupOptions: {
      // See maplibreAssets(): the library is served as its own files, not bundled.
      external: (id) => id === "maplibre-gl",
      output: {
        paths: { "maplibre-gl": `/${MAPLIBRE_DIR}/maplibre-gl.mjs` },
      },
    },
  },
  plugins: [dataRangeServer(), rulesEngineShim(), maplibreAssets()],
  define: {
    __APP_VERSION__: JSON.stringify(pkg.version),
  },
});
