"""Reserved slot for the wind proxy (`docs/design.md` 7.9). Deliberately empty.

The design gives `/api/wind/*` to this service:

- `GET /api/wind/stations?bbox=` merges Synoptic Mesonet, aviationweather METARs by bbox, and NDBC
  buoys into `{id, source, name, lat, lon, dir_deg, speed_kt, gust_kt, obs_time}`, cached 5 min per
  bbox tile. Synoptic needs `SYNOPTIC_TOKEN`, which `docker-compose.yml` already passes in.
- `GET /api/wind/point?lat=&lon=` returns Open-Meteo current wind, cached 15 min.

Two pieces are already here for it: `fetch/ndbc.py` parses `latest_obs.txt` by bbox, and
`fetch/aviationweather.py` talks to the METAR endpoint. Adding the router means writing the Synoptic
fetcher, a bbox-tile cache, and the merge -- and then including this router in `app.py`.

Nothing is mounted until then, so `/api/wind/*` returns 404 rather than an empty promise.
"""
from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/api/wind", tags=["wind"])
