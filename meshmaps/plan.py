"""regions.toml from regions/plan.toml and Geofabrik's index.

Every region under a continent the plan names becomes one pack, drawn as deep as the plan's
``max_zoom`` if its tiles fit ``budget_tiles``. One that does not fit and that Geofabrik subdivides
becomes a group of its subdivisions (one level). One that does not fit and cannot be split is
drawn shallower, down to ``min_zoom``, and below that is left to the world base.

A region's size is counted once, at COUNT_ZOOM, and scaled: a level down is four times the
tiles, and every level above the deepest adds a third again. The build counts exactly and
refuses a pack past what the client indexes, so an estimate a little off costs nothing worse
than a pack a level shallower than it could have been.
"""

import json
import os
import tomllib
from collections import defaultdict
from dataclasses import dataclass

from shapely.geometry import shape

from . import mctp, tiles

COUNT_ZOOM = 11


@dataclass(frozen=True)
class Planned:
    id: str
    name: str
    parent: str | None
    geofabrik: str | None = None
    max_zoom: int = 0


@dataclass
class Plan:
    groups: list
    packs: list
    split: list  # Geofabrik ids that became groups
    shallow: list  # (Geofabrik id, zoom) drawn above max_zoom to fit
    dropped: list  # Geofabrik ids no zoom from min_zoom up fits


class PlanError(Exception):
    pass


def load(path):
    with open(path, "rb") as handle:
        return tomllib.load(handle)


def estimate(count, zoom):
    """Every tile from zoom 0 to ``zoom`` of a region with ``count`` tiles at COUNT_ZOOM."""
    if zoom >= COUNT_ZOOM:
        deepest = count * 4 ** (zoom - COUNT_ZOOM)
    else:
        deepest = max(1, count // 4 ** (COUNT_ZOOM - zoom))
    return deepest * 4 // 3


def fit_zoom(count, budget, deepest, shallowest):
    """The deepest zoom from ``deepest`` up to ``shallowest`` whose pack fits, or None."""
    for zoom in range(deepest, shallowest - 1, -1):
        if estimate(count, zoom) <= budget:
            return zoom
    return None


def title(geofabrik_id):
    """A name for a region Geofabrik names by its path: ``us/new-york`` is "New York"."""
    words = geofabrik_id.rsplit("/", 1)[-1].split("-")
    small = {"of", "and", "the", "da"}
    return " ".join(w if i and w in small else w.capitalize() for i, w in enumerate(words))


def make(plan, index, count):
    """The plan for ``index`` (Geofabrik id -> properties), sizing each region by ``count``."""
    budget, deepest, shallowest = plan["budget_tiles"], plan["max_zoom"], plan["min_zoom"]
    excluded = set(plan.get("exclude", []))
    by_prefix = plan.get("split_by_prefix", {})
    names = plan.get("names", {})
    zooms = plan.get("zoom", {})

    children = defaultdict(list)
    for gid, properties in index.items():
        if gid not in excluded:
            children[properties.get("parent")].append(gid)
    prefixed = {gid for gid in index if "/" in gid and gid.split("/", 1)[0] in by_prefix and gid not in excluded}

    result = Plan(groups=[], packs=[], split=[], shallow=[], dropped=[])
    long_names = []

    def name_of(gid):
        name = names.get(gid) or index[gid].get("name") or gid
        if "/" in name:
            name = title(gid)
        if len(name.encode("utf-8")) > mctp.NAME_MAX - 1:
            long_names.append(f"{gid}: {name}")
        return name

    def zoom_of(gid):
        cap = zooms.get(gid, deepest)
        zoom = fit_zoom(count(gid), budget, cap, shallowest)
        if zoom is not None and zoom < deepest:
            result.shallow.append((gid, zoom))
        return zoom

    def add_pack(gid, pack_id, parent):
        zoom = zoom_of(gid)
        if zoom is None:
            result.dropped.append(gid)
            return
        result.packs.append(Planned(pack_id, name_of(gid), parent, gid, zoom))

    for top in plan.get("top", []):
        result.packs.append(Planned(top["id"], top["name"], None, top.get("geofabrik"), top["max_zoom"]))

    for continent in plan["continent"]:
        result.groups.append(Planned(continent["id"], continent["name"], None))
        splits = []
        for gid in sorted(children[continent["geofabrik"]]):
            if gid in prefixed:
                continue
            if gid in by_prefix:
                members = sorted(member for member in prefixed if member.startswith(gid + "/"))
                splits.append((gid, by_prefix[gid]["id"], by_prefix[gid]["name"], members))
                continue
            fits = fit_zoom(count(gid), budget, zooms.get(gid, deepest), deepest) is not None
            if not fits and children[gid]:
                splits.append((gid, gid.replace("/", "-"), name_of(gid), sorted(children[gid])))
                continue
            add_pack(gid, gid.replace("/", "-"), continent["id"])
        for gid, group_id, group_name, members in splits:
            result.split.append(gid)
            result.groups.append(Planned(group_id, group_name, continent["id"]))
            for member in members:
                pack_id = member.replace("/", "-")
                if not pack_id.startswith(group_id + "-"):
                    pack_id = f"{group_id}-{pack_id}"
                add_pack(member, pack_id, group_id)

    if long_names:
        raise PlanError("names past the pack header's limit; add them to [names]: " + "; ".join(long_names))
    return result


def counter(index_features, cache_path):
    """``count(gid)``: a region's tiles at COUNT_ZOOM, kept in ``cache_path`` between runs."""
    cached = {}
    if os.path.exists(cache_path):
        with open(cache_path) as handle:
            cached = json.load(handle)

    def count(gid):
        if gid not in cached:
            geometry = index_features[gid].get("geometry")
            if geometry is None:
                raise PlanError(f"Geofabrik has no outline for {gid!r}")
            cached[gid] = len(tiles.covering(shape(geometry), COUNT_ZOOM, COUNT_ZOOM))
            with open(cache_path + ".part", "w") as handle:
                json.dump(cached, handle, sort_keys=True)
            os.replace(cache_path + ".part", cache_path)
        return cached[gid]

    return count


def render(result):
    """regions.toml's text."""
    out = [
        "# Written by `meshmaps plan` from regions/plan.toml and Geofabrik's index - edit plan.toml",
        "# and run `make plan` rather than changing this. Committed, so a build does not depend on",
        "# the index having stayed the same.",
        "#",
        "# An id is permanent: it is the pack's path on R2 and how the client recognises an installed",
        "# pack when a newer cut appears.",
        "",
    ]
    for group in result.groups:
        out += ["[[group]]", f'id = "{group.id}"', f"name = {json.dumps(group.name, ensure_ascii=False)}"]
        if group.parent is not None:
            out.append(f'parent = "{group.parent}"')
        out.append("")
    for pack in result.packs:
        out += ["[[pack]]", f'id = "{pack.id}"', f"name = {json.dumps(pack.name, ensure_ascii=False)}"]
        if pack.parent is not None:
            out.append(f'parent = "{pack.parent}"')
        if pack.geofabrik is not None:
            out.append(f'geofabrik = "{pack.geofabrik}"')
        out += [f"max_zoom = {pack.max_zoom}", ""]
    return "\n".join(out)
