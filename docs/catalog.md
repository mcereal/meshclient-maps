# The catalog contract

What mesh-client reads to learn which map packs it can download. This page is the contract: a
change that breaks a client already in the field needs a new `format` and a new prefix, not an
edit.

## Where things are

Everything is under one prefix on the bucket's public hostname:

```
<base>/v1/catalog.json                          what exists; rebuilt on every publish
<base>/v1/packs/<id>/<cut>-<style>.mctp         one pack; never overwritten
<base>/v1/packs/<id>/<cut>-<style>.json         that pack's sidecar; the catalog is built from these
```

A pack's path contains its cut date, so the bytes at a URL never change. A client that loses its
connection halfway can resume with an HTTP `Range` request and know it is continuing the same
file, and the pack is served `immutable`. The catalog is served with a five-minute cache.

The newest two cuts of each pack are kept (`meshmaps prune --keep 2`), so a download that began
before a new cut was published can still finish.

## catalog.json

```json
{
 "format": 1,
 "generated": "2026-09-28T12:00:00Z",
 "attribution": "© OpenStreetMap contributors",
 "groups": [
  {"id": "north-america", "name": "North America", "parent": null},
  {"id": "us", "name": "United States", "parent": "north-america"}
 ],
 "packs": [
  {
   "id": "us-washington",
   "name": "Washington",
   "parent": "us",
   "style": "light",
   "cut": "20260927",
   "min_zoom": 0,
   "max_zoom": 13,
   "bbox": [45.5, -124.9, 49.1, -116.8],
   "tiles": 26883,
   "bytes": 281000000,
   "sha256": "…",
   "url": "packs/us-washington/20260927-light.mctp"
  }
 ]
}
```

| Field | Meaning |
|---|---|
| `format` | `1`. A client refuses a catalog whose format it does not know, and does not guess. |
| `generated` | When this catalog was written, UTC. |
| `attribution` | The credit every pack carries. Also inside each pack's header. |
| `groups[]` | Headings in the region tree. `parent` is a group id or `null`. Only groups with a pack somewhere beneath them are listed. |
| `packs[].id` | Stable forever. It is how a client recognises an installed pack as the same region when a newer cut appears. |
| `packs[].name` | At most 31 bytes of UTF-8, the same as the pack header's name. |
| `packs[].parent` | A group id, or `null` for the top level (the world base). |
| `packs[].style` | The style the tiles were drawn in. Only `light` is built today; one id may appear once per style. |
| `packs[].cut` | `YYYYMMDD` of the OpenStreetMap data it was drawn from. A larger cut than the installed one means an update is available. |
| `packs[].min_zoom`, `max_zoom` | The zooms the pack holds. |
| `packs[].bbox` | `[south, west, north, east]` in degrees: the union of the corners of the pack's *deepest-zoom* tiles, i.e. where the region is. A regional pack also holds its few shallow ancestors, and the zoom-0 tile covers the whole world, so a box over every tile would say nothing. |
| `packs[].tiles` | Tiles in the index. The index is held in RAM at 24 bytes a tile. |
| `packs[].bytes` | The file's exact size: what to check free space against and what `Content-Length` says. |
| `packs[].sha256` | Of the whole file. Check it before renaming a download into place. |
| `packs[].url` | Relative to the directory `catalog.json` is in. |

Packs are listed sorted by `(id, style)`. A client builds its own tree from `parent`, not from
the order.

mesh-client shows the packs at the top of the tree and one row per group that holds packs, and a
group's packs on a screen of their own. Its screens are bounded, so `regions.toml` is too, and
`meshmaps check` refuses more than: 4 packs at the top, 16 groups holding packs, 60 packs in one
group. A region list that grows past these splits a group (`us-west`, `us-east`) rather than
raising them.

## What a client should do

- Fetch `catalog.json` when the download screen opens, not on a timer.
- Download to `<name>.part`, resume with `Range: bytes=<have>-`, check `bytes` and `sha256`, and
  only then rename into place. A partial file never has the `.mctp` name.
- Compare an installed pack to the catalog by `id` and `cut`, not by file name.
- Keep the world base (`id` `world`) as the fallback under every regional pack.
