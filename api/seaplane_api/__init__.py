"""`seaplane_api`: the FastAPI service that writes `data/out/briefing.json`.

Two halves, deliberately separated so the algorithm is testable without a network:

- `seaplane_api.briefing.*` -- pure functions over plain data (dicts, floats, `datetime`).
  Nothing in there opens a socket or reads a file.
- `seaplane_api.fetch.*` -- one module per upstream feed. Every call returns either a payload or
  `None` plus an error string; a failure degrades exactly one input.

`service.run_briefing` glues them together, `scheduler` decides when, `app` serves it. `wavefield`
sits with `paths` and `settings` on the file-reading side: it turns the pipeline's `wave_points`
pack into the sample points that `briefing.wave` aggregates into regions.
"""

__all__ = ["__version__"]

__version__ = "0.1.0"
