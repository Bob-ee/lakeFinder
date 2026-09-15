# Service worker (phase 3)

Empty on purpose. Phase 1 registers no service worker; the folder exists so the offline
work has a home and the import paths do not move later.

## Plan

Design doc section 7.8, phase 3 in section 10.

1. **App shell, cache-first, never network-first.** A dead Tailscale tunnel in the air must
   not hang launch, so the shell is precached at install and served from the cache without
   touching the network. Workbox `precacheAndRoute` over the Vite build manifest, driven by
   `vite-plugin-pwa` or a hand-written `sw.ts` compiled as a second Rollup input.
2. **`/data/*` is not cached by the service worker.** The data pack is far too large for the
   Cache API on iOS. It goes to OPFS instead (step 3).
3. **OPFS data pack.** A settings screen downloads every file listed in `pack.json` into the
   OPFS directory `pack/`, with progress and sha256 verification, and records the pack
   version. On launch, when online, compare against the server manifest and surface
   "update available".
4. **pmtiles OPFS `Source`.** Implement the pmtiles `Source` interface against the OPFS file:
   `FileSystemSyncAccessHandle` inside a worker where available, `File.slice()` otherwise.
   Register it so `pmtiles://` URLs resolve to the local copy when the pack is present and
   fall back to `/data/` when it is not.
5. **`index.json`, `restrictions.json`, `rules.json` ship in the pack**, so search and
   classification stay fully client-side offline. They are small enough for the Cache API,
   but keeping them in OPFS with everything else means one version to reason about.
6. **Install prompt.** Durable storage on iOS needs an installed PWA, so show the
   Add to Home Screen hint on first run and before a pack download.

## Not in scope here

Wind (7.9) is online-only and degrades silently; its last response is cached in IndexedDB
(store `wind`), not by the service worker.
