"""The catalog: the one file the client reads to learn what it can download.

Every built pack has a small JSON sidecar beside it describing that one file. The catalog is
rebuilt from the sidecars plus regions.toml each time, never edited in place. So a run that
builds one region cannot lose another's entry, and two runs publishing at once cannot
overwrite each other's work. See docs/catalog.md for the contract.
"""

import json
from datetime import datetime, timezone

from . import tiles as tile_math

FORMAT = 1
ATTRIBUTION = "© OpenStreetMap contributors"
PREFIX = "v1"


def pack_key(region_id, cut, flavor):
    """A pack's path under the prefix. Immutable: a new cut is a new path, never an overwrite,
    so a client resuming a download with a Range request is always resuming the same bytes."""
    return f"packs/{region_id}/{cut}-{flavor}.mctp"


def sidecar_key(region_id, cut, flavor):
    return f"packs/{region_id}/{cut}-{flavor}.json"


def sidecar(region, *, flavor, cut, generated, keys, result):
    # The box of the deepest zoom only. A region pack also holds the few shallow tiles above it,
    # and zoom 0 is one tile covering the whole world, so the box of every tile says nothing
    # about where the region is.
    deepest = max(key[0] for key in keys)
    south, west, north, east = tile_math.coverage([key for key in keys if key[0] == deepest])
    return {
        "id": region.id,
        "style": flavor,
        "cut": cut,
        "generated": generated,
        "min_zoom": min(key[0] for key in keys),
        "max_zoom": max(key[0] for key in keys),
        "bbox": [round(south, 7), round(west, 7), round(north, 7), round(east, 7)],
        "tiles": result.tiles,
        "bytes": result.bytes,
        "sha256": result.sha256,
        "url": pack_key(region.id, cut, flavor),
    }


def assemble(groups, packs, sidecars, *, now=None):
    """The catalog for the regions in ``packs``, from the newest sidecar of each (id, style).

    A region with no sidecar yet is left out, and so is a sidecar whose region has been removed
    from regions.toml. Names and parents come from regions.toml, not the sidecar, so renaming
    a region or moving it in the tree does not need a rebuild.
    """
    newest = {}
    for entry in sidecars:
        slot = (entry["id"], entry["style"])
        if slot not in newest or entry["cut"] > newest[slot]["cut"]:
            newest[slot] = entry
    listed = []
    # The top of the tree first, then by id. A client before mesh-client#415 reads the first 96
    # entries, and the world base is what it draws everywhere else.
    ordered = sorted(
        newest.items(),
        key=lambda item: (packs[item[0][0]].parent is not None if item[0][0] in packs else True, item[0]),
    )
    for (region_id, _style), entry in ordered:
        region = packs.get(region_id)
        if region is None:
            continue
        listed.append({"id": region.id, "name": region.name, "parent": region.parent, **{
            key: entry[key]
            for key in ("style", "cut", "min_zoom", "max_zoom", "bbox", "tiles", "bytes", "sha256", "url")
        }})  # fmt: skip
    used_groups = _groups_in_use(groups, listed)
    stamp = (now or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {
        "format": FORMAT,
        "generated": stamp,
        "attribution": ATTRIBUTION,
        "groups": [
            {"id": group.id, "name": group.name, "parent": group.parent}
            for group in groups.values()
            if group.id in used_groups
        ],
        "packs": listed,
    }


def _groups_in_use(groups, listed):
    """Only groups with a pack somewhere beneath them: an empty heading is a dead end on a
    screen with no pointer to back out of it quickly."""
    used = set()
    for entry in listed:
        parent = entry["parent"]
        while parent is not None and parent not in used:
            used.add(parent)
            parent = groups[parent].parent
    return used


def dumps(catalog):
    return json.dumps(catalog, ensure_ascii=False, indent=1) + "\n"
