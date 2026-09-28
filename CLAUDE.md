# CLAUDE.md

Builds mesh-client's offline map packs and publishes them to R2. README.md says how;
docs/catalog.md is the contract the client reads and is not changed casually.

## Commands

```sh
make setup                         # .venv, pmtiles CLI, fonts/sprites, style
make test                          # unit tests - run before every push
make check                         # regions.toml + every Geofabrik outline resolves
make build REGION=<id>             # needs Docker running
.venv/bin/python -m meshmaps info|verify dist/<id>/<file>.mctp
```

## Rules

- **The `.mctp` format is mesh-client's.** Its reader, `src/map/source_pack.c`, is the spec, and
  `meshmaps/mctp.py` writes it. A pack is checked against that reader
  (`meshclient --map-pack <file>`) before a format change is trusted, not just against `verify` here.
- **Shared blobs are legal.** The client checks each extent lies in the file, never that extents
  are disjoint, and identical tiles are written once. Do not "fix" duplicate offsets.
- **An id in regions.toml is permanent** - it is an R2 path and a client's key for an installed pack.
- **A published pack path is never overwritten.** A new cut is a new path; clients resume with Range.
- **The catalog is derived, never edited**: rebuilt from every sidecar in the bucket plus regions.toml.
- **R2 credentials are env vars only** (`R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`).
  Never write them to a file in this repo.
- Deleting from the bucket (`prune` without `--dry-run`) needs the user's go-ahead.
