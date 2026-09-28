# meshclient-maps

Offline map packs for [mesh-client](https://github.com/mcereal/mesh-client): the pipeline that
draws them and the catalog the client downloads them from.

A pack is one `.mctp` file: 256 px palette PNG tiles behind a sorted index. The format is
mesh-client's, specified by its reader in `src/map/source_pack.c`. The client opens one with a
binary search and a single `pread` per tile, which is what the Brick's FAT32 card needs. This
repo turns OpenStreetMap into those files, one region at a time, and publishes them to R2.

```
Protomaps planet (OSM as vector tiles, build.protomaps.com)
  -> pmtiles extract, cut to the region's Geofabrik outline
  -> tileserver-gl draws each 256 px tile with build/style/<flavor>
  -> quantised to a 32-colour palette, undithered
  -> meshmaps/mctp.py packs them, storing identical tiles once
  -> dist/<id>/<cut>-<style>.mctp + a sidecar .json
  -> R2: v1/packs/..., then v1/catalog.json rebuilt from every sidecar
```

## Setup

Needs Python 3.12+, Node, Docker and git. `mise install` pins the first two.

```sh
make setup          # .venv, the pmtiles CLI, Protomaps fonts and sprites, the style
make test           # unit tests; no network, no Docker
make check          # regions.toml is valid and every outline resolves (fetches Geofabrik's index once)
```

## Building

```sh
.venv/bin/python -m meshmaps tiles us-washington    # how big a region is, before drawing it
make build REGION=world                              # ~40 s, ~10 MB
make build REGION=us-washington                      # a few minutes
.venv/bin/python -m meshmaps info dist/world/*.mctp
make catalog                                         # dist/catalog.json, for a look
```

A build can be interrupted and run again: the vector extract and every drawn tile are kept
under `build/work/<id>/`, and only what is missing is drawn. `make clean` throws that away.

To look at a pack on the client:

```sh
cd ../mesh-client
./build/debug/meshclient --map-pack ../meshclient-maps/dist/world/<cut>-light.mctp
MESHCLIENT_MAP_PACK=$PWD/../meshclient-maps/dist/world/<cut>-light.mctp MESHCLIENT_UI_BACKEND=sdl ./build/debug/meshclient
```

## Regions

`regions/regions.toml` is the list: groups (headings in the client's tree) and packs (downloads).
A pack's outline is a Geofabrik region, whose outlines are the extract boundaries most OSM users
already know. Only the outline is taken from Geofabrik. Its names and tree are inconsistent
(`us/washington` has parent `north-america`), so names and parents are written here.

An `id` is permanent: it is the pack's path on R2 and how the client recognises an installed
pack when a newer cut appears.

## Zoom and size

The zoom cap is what sets a pack's size. Each level down is four times the tiles:

| Pack | Tiles | Size | Built on an M-series Mac |
|---|---|---|---|
| World base, z0-8 | 87,381 (24,835 distinct) | 128.6 MB | 8 min |
| Washington, z0-13 | 26,883 (22,210 distinct) | 88.7 MB | 16 s extract, 125 s render |
| Texas, z0-13 | 64,592 | ~210 MB (est.) | |
| Germany, z0-13 | 57,304 | ~200-250 MB (est.) | |

About 3.3 KB a tile after the 32-colour palette and deduplication. At that rate every US state
and every European country to z13 fits in a few GB. z14 is roughly four times each figure.

Regions stop at z13. A position on a mesh is rounded to hundreds of metres, z14 roughly
quadruples the file, and the biggest states would approach FAT32's 4 GiB limit. The client
indexes at most 1,000,000 tiles a pack; `build` refuses more.

## Publishing

Packs go to a bucket of their own, with a public custom domain and no Worker in front. Plain
GETs with `Range` are all the client needs. Credentials are environment variables and are never
written to a file:

```sh
export R2_ACCOUNT_ID=... R2_ACCESS_KEY_ID=... R2_SECRET_ACCESS_KEY=... R2_BUCKET=meshclient-maps
make publish REGION=us-washington
.venv/bin/python -m meshmaps prune --keep 2 --dry-run
```

`publish` uploads the pack and then its sidecar, and rebuilds `v1/catalog.json` from every
sidecar in the bucket, so publishing one region never drops another. See
[`docs/catalog.md`](docs/catalog.md) for the layout and the contract the client reads.

The **Build packs** workflow does the same on GitHub Actions for the regions it is given, and
needs the three `R2_*` values as repository secrets.

## Licences

The code is MIT. The packs are drawn from © OpenStreetMap contributors (ODbL) via Protomaps, and
each pack carries that attribution in its header for the client to draw. The label fonts are
Noto Sans (OFL), from Protomaps' basemaps-assets.
