# Deploy without Docker (brew caddy + launchd)

For the headless MacBook on the tailnet. The dev Mac builds the client and the data pack and pushes them; the
server runs two launchd jobs and needs only `uv` and `caddy`.

```
dev Mac                                    server Mac
pipeline ─▶ data/out ─┐                    com.lakefinder.web   caddy :8080 on 127.0.0.1 ─ /, /data/*, /api/* ─┐
web build ─▶ web/dist ┴─ deploy/push.sh ─▶ com.lakefinder.api   caffeinate + seaplane-api on 127.0.0.1:8000 ◀──┘
                                           tailscale serve ─▶ https://<host>.<tailnet>.ts.net
```

## First time

On the server, once: `brew install uv caddy`, Tailscale signed in, ssh (Remote Login) on.

From the dev Mac:

```
deploy/push.sh <host> lakeFinder -- --daemon --serve
```

That builds `web/dist`, rsyncs the checkout + `web/dist` + `data/out` (about 175 MB the first time), then runs
`deploy/install.sh` on the server, which writes the two plists, starts them, waits for `/api/health`,
`/data/pack.json` and the app shell to answer through caddy, and turns on `tailscale serve`.

- `--daemon` installs LaunchDaemons that run as your user from boot, with nobody logged in. It asks for sudo once.
  Without it the jobs are LaunchAgents: no sudo, but they only run while you are logged in at the console, so a
  headless Mac then needs auto-login (and over ssh with nobody logged in the install fails and says so).
- `--serve` runs `tailscale serve --bg 8080`. The PWA needs HTTPS to install to a home screen; caddy itself listens
  on 127.0.0.1 only, so nothing is exposed to the LAN in plain HTTP. `LAKEFINDER_BIND=0.0.0.0` changes that.
- **Sleep.** The api runs under `caffeinate -is`, which stops idle sleep on mains power. A closed lid still sleeps
  the Mac: `sudo pmset -a disablesleep 1` once on the server. The installer prints a note while that is unset. A
  sleeping Mac skips the 18:00 / 20:00 / 22:00 outlook runs by design (see `api/README.md`).

## After that

| what changed | do |
|---|---|
| code, client, or data pack | `deploy/push.sh <host>` (reinstalls and restarts; a few seconds of downtime) |
| data pack only | `NO_INSTALL=1 SKIP_BUILD=1 deploy/push.sh <host>` (caddy serves files from disk; the api rereads the pack each run) |
| remove | on the server: `deploy/install.sh --uninstall` (add `--daemon` if installed that way) |

`push.sh` never sends and never deletes the server's own `data/manual/settings.json` and `data/out/briefing.json`.
Settings made in the app on the server stay there; the dev Mac's settings file is not the deployed one.

Logs: `~/Library/Logs/lakefinder-api.log`, `~/Library/Logs/lakefinder-web.log`.
State: `launchctl print system/com.lakefinder.api` (or `gui/$(id -u)/…` for agents).
Check the rendered plists without touching anything: `deploy/install.sh --dry-run /tmp/plists`.

## Not here yet

The weekly pipeline run on the server. It needs the whole toolchain there (GDAL, tippecanoe, pmtiles, node) and
DNR rules change a few times a year, so for now rerun the pipeline on the dev Mac and push the pack.
