# `api/` — briefing service

FastAPI service that writes `data/out/briefing.json`: a deterministic daily flying briefing for the
SeaRey, with an evening outlook for tomorrow morning. **No LLM, no tokens** — every number comes
from a public feed and an algorithm in `seaplane_api/briefing/`.

Algorithm and reasoning: `docs/briefing-design.md`. Schemas and endpoints: `docs/data-contract.md`
("Briefing"), which is authoritative.

## Run

```
cd api
uv run seaplane-api                      # uvicorn on 127.0.0.1:8000, scheduler in-process
uv run seaplane-api --host 0.0.0.0 --port 8080
uv run seaplane-api briefing --once      # one run, no server (for launchd or cron)
uv run pytest -q                         # 218 tests, all on recorded fixtures
uv run ruff check .
```

A run takes about 1.5 s and makes eight HTTP requests: METAR, TAF, one Open-Meteo call for the
airport plus two batched calls for the lakes, NWS alerts, NWS `/points`, NWS hourly. The lake count
does not change that: 309 candidates within 40 nm of KPTK snap to 78 forecast points on a 0.1°
grid, which is two calls of 50.

| endpoint | behavior |
|---|---|
| `GET /api/health` | `{"ok", "briefing_generated_at", "next_run_local"}` |
| `GET /api/settings` | the settings object |
| `PUT /api/settings` | full object, 422 on bad input, written atomically, triggers a background `manual` run |
| `GET /api/airports/{ident}` | resolves an identifier to the `home_airport` shape; 404 when unknown |
| `POST /api/briefing/refresh` | runs now, returns the new `briefing.json` body |

`/api/wind/*` (design 7.9) is a documented empty slot in `seaplane_api/wind.py`; it is not mounted,
so those paths 404.

## Environment

| variable | default | what |
|---|---|---|
| `SEAPLANE_DATA_OUT` | `../data/out` | reads `index.json` and `lake_extents.json`, writes `briefing.json` |
| `SEAPLANE_DATA_MANUAL` | `../data/manual` | reads and writes `settings.json` |

`settings.json` is gitignored and is created with the contract defaults the first time the service
starts. Unknown keys are rejected (422); missing keys are filled, so an older file keeps working.

No API keys. Every feed is keyless. The service identifies itself to all of them as
`lakeFinder (https://github.com/Bob-ee/lakeFinder)` — api.weather.gov requires a User-Agent. **No
email address goes into any header, URL, or payload**, and a test enforces that.

## How the scheduler works

An asyncio task wakes every 30 s and asks `scheduler.due_times(now, last_run, times, tz)`: *has a
wall-clock time in `schedule.run_times_local ∪ outlook.times_local` passed since the last run?* It
deliberately does not sleep until the next scheduled time, because the deploy target is a MacBook
that sleeps. Consequences:

- A missed time older than **90 minutes** is skipped, not run late. A laptop that wakes at 09:05
  after a day asleep runs 09:00 once and ignores the eight times it slept through.
- All comparisons happen on the UTC timeline, so DST is correct: 22:00 EST to 06:00 EDT is seven
  real hours, and the fall-back 01:30 that happens twice still runs once.
- It also runs at **startup** when `briefing.json` is missing or older than 3 hours, and immediately
  when `PUT /api/settings` changes anything.
- `run_kind` is `"outlook"` when the triggering time is in `outlook.times_local`, otherwise
  `"scheduled"`; `"manual"` for a refresh or a CLI run, `"startup"` for the startup catch-up.

`due_times` is pure and tested directly, including both DST change days and the sleep/wake case.

## How to add a fetcher

1. New module in `seaplane_api/fetch/`. One public coroutine per endpoint, returning
   `(payload | None, error | None)` — **never raise**. Use `fetch.http.get_json` / `get_text`, which
   already carry the User-Agent, a 10 s timeout, and one retry (4xx is not retried).
2. Put the observed live shape and units in the module docstring. The upstream feeds disagree with
   their own documentation often enough that this is the only reliable record.
3. Add a field to `briefing.generate.Feeds` and fetch it in `collect_feeds`, appending any error to
   `feeds.errors` and leaving the field `None`. A failure must degrade exactly one input.
4. Consume it in a **pure** function under `seaplane_api/briefing/`. Nothing there may open a socket
   or read a file.
5. Record one real response into `tests/fixtures/` (trimmed to a few KB) and test the parsing against
   it, plus a failure path through `httpx.MockTransport`.
6. If it becomes a `sources` entry, add it to `generate._sources` and to the key set in
   `tests/test_schema.py`.

## Deploy on the headless MacBook (launchd)

`deploy/push.sh <host>` from the dev Mac, which runs `deploy/install.sh` on the server: it writes and loads
`com.lakefinder.api` (this service under `caffeinate -is`, `uv run --frozen --no-dev seaplane-api` on
127.0.0.1:8000, `SEAPLANE_DATA_OUT` / `SEAPLANE_DATA_MANUAL` pointed at the checkout) and `com.lakefinder.web`
(caddy). See `deploy/README.md`; `deploy/install.sh --dry-run <dir>` prints the plists.

**The Mac must not sleep, or the 18:00/20:00/22:00 outlook runs will not happen.** A sleeping laptop
wakes with all three times more than 90 minutes old, and the scheduler skips them by design — the
evening outlook would simply never be there in the morning.

```
sudo pmset -a sleep 0 disablesleep 1      # mains-powered, lid closed, never sleeps
```

The installed plist already runs the service under `caffeinate -is`, which covers idle sleep on mains power but
not a closed lid; `pmset -g` shows what is currently set. Display sleep is fine; system sleep is not.

`docker-compose.yml` has an `api` service (profile `api`) using the `Dockerfile` here if you would
rather run it in Docker; it mounts the same data volume Caddy serves.
