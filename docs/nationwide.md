# Going nationwide

Added 2026-09-19. Bobby intends to release this to the seaplane community, so it has to work in every state.
Michigan is the pilot state, not the product. This file records what that changes so new work does not dig the
Michigan assumptions deeper. Nothing here is scheduled yet.

## The rule for new work

**State-agnostic by construction.** New code takes its region from configuration, not constants: no hard-coded
`America/Detroit`, EPSG:3078, Michigan bounding boxes, county lists, or KPTK outside a region config and the user's
settings. Existing Michigan-specific code is left alone until a second state forces the refactor; do not rewrite what
works on speculation.

## What is already national

| layer | source | note |
|---|---|---|
| weather, waves, sun | aviationweather.gov, Open-Meteo, NWS, NDBC, local solar math | the briefing has no Michigan logic beyond default settings |
| federal land and water | NPS, FWS boundaries | national services, currently clipped to Michigan |
| airspace, airports, runways | FAA | national |
| rules engine | `rules/` data-driven JSON | rule *content* is Michigan; the engine is not |
| wave field design | `docs/big-water-design.md` | geometry only; no hand-drawn areas by design |

## What is Michigan-only today, and the national shape of it

| piece | today | national |
|---|---|---|
| water polygons | Michigan Hydrography Polygons (no permanent id, inland only) | USGS NHD / 3DHP: national, public domain, carries a permanent identifier and GNIS name. Also fixes the hashed-id weakness. Recon is comparing detail against the state layer. |
| county, township, PLSS | Michigan Geographic Framework, Michigan PLSS | Census TIGER counties and county subdivisions; BLM PLSS where a state's rules cite sections (30 states have PLSS, the rest do not) |
| restrictions | Michigan DNR local watercraft controls, 83 county pages, a Michigan-specific parser | **a regulation adapter per state**: each adapter yields the contract's restriction records from whatever that state publishes. This is the real cost of going national and cannot be automated away. A state with no adapter must read as "no data for this state", never as clear. |
| seaplane-specific law | R 259.401 and the MAC record | per-state aeronautics rules (several states and many municipalities restrict seaplane operations outright); belongs in the same adapter |
| public access | Michigan BAS | per-state boating access datasets, or none |
| projection | EPSG:3078 | a local equal-area or UTM CRS chosen per water body |
| verdict wording | tuned to Michigan's legal model (design section 2) | the four verdicts hold; the reasons and citations are per state |

Community-sourced knowledge (pilot reports per lake, the SPA Water Landing Directory model) is the realistic way to
cover states before an adapter exists. It needs accounts and moderation, so it is a product decision, not a task.

## Scale

- Michigan is 10.8k water bodies and a 4 MB `index.json`. The country is on the order of millions of NHD
  waterbodies. One index file does not survive that: packs become per state or per region, search needs a tiled or
  server-side index, and the basemap is per region.
- The briefing generates one file for one pilot on one machine. A public release needs either the same self-hosted
  model packaged for anyone to run, or a shared service with per-user settings. On a shared service the forecast
  fetches must be cached by grid cell across users, and Open-Meteo's free tier is non-commercial and rate limited, so
  a public service needs their paid plan or NWS gridded data instead.
- Settings move from one server-side `settings.json` to per user (client-side storage is enough until there are
  accounts).

## Release concerns that are not code

- The disclaimer posture matters more with strangers using it: never "legal", never "safe", always the citation and
  the date of the data.
- Source licenses: everything used so far is public domain or open government data. OpenStreetMap data is ODbL
  (share-alike, attribution); avoid building core layers on it without deciding to accept that.
- A stale regulation is worse than none. Each adapter needs a last-verified date shown in the app and a refresh job.

## Suggested sequence

1. Finish Michigan: rivers, Lake St. Clair, the wave field, deployment, offline.
2. Move water polygons to NHD for Michigan alone and confirm nothing regresses; this is the change that makes
   every later state cheap on the geometry side.
3. Pull the Michigan constants into a region config.
4. Add a second state with a very different regulatory shape (Minnesota, Florida, Washington, and Alaska are the big
   seaplane states) to find out what the adapter interface really needs.
5. Decide hosting and accounts before inviting anyone.
