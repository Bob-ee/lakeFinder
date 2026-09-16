# seaplane-pipeline

Python (uv, 3.12) pipeline. Stages are subcommands of `seaplane`; every module exposes `run(cfg, args)` and an
optional `add_args(subparser)`. See `docs/design.md` section 5 and `docs/data-contract.md`.

```sh
uv sync
uv run seaplane --help
uv run seaplane --county oakland fetch      # crawl one county + GIS sources
uv run seaplane --county oakland parse-dnr
uv run seaplane all                          # everything, statewide
uv run seaplane review                       # print the review queue
uv run seaplane suggest                      # write data/manual/overrides.suggested.yaml (paste-ready entries)
uv run pytest
```

Data lands in `../data/`: `raw/<date>/` (raw responses), `cache/` (large stable downloads), `work/` (intermediate),
`out/` (served at `/data/`). Set `SEAPLANE_DATA_DIR` to relocate.

LLM extraction (`parse-dnr` second pass) only runs when Anthropic credentials are available; otherwise unparsed entries
are emitted as `restriction_type: other, needs_review: true`.
