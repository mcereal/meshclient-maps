"""The region list in regions/regions.toml, and the outline each pack is cut to."""

import json
import os
import re
import tomllib
from dataclasses import dataclass

from shapely.geometry import shape

from . import http, mctp

GEOFABRIK_INDEX = "https://download.geofabrik.de/index-v1.json"
ID_PATTERN = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")

# What mesh-client's Settings > Maps can show. It lists the packs at the top of the tree and one row
# per group on its first screen, and a group's packs on a screen of their own, inside a section of
# at most 64 rows - so a catalog past these would have packs nobody could reach. See
# docs/catalog.md.
TOP_PACKS_MAX = 4
GROUPS_WITH_PACKS_MAX = 16
GROUP_PACKS_MAX = 60


@dataclass(frozen=True)
class Group:
    id: str
    name: str
    parent: str | None


@dataclass(frozen=True)
class Region:
    id: str
    name: str
    parent: str | None
    geofabrik: str | None
    max_zoom: int
    min_zoom: int = 0


class RegionError(Exception):
    pass


def load(path):
    """(groups, packs) from a regions file, each keyed by id, with every rule checked."""
    with open(path, "rb") as handle:
        raw = tomllib.load(handle)
    groups = {}
    for row in raw.get("group", []):
        group = Group(id=row["id"], name=row["name"], parent=row.get("parent"))
        _check_common(group.id, group.name, groups)
        groups[group.id] = group
    packs = {}
    for row in raw.get("pack", []):
        region = Region(
            id=row["id"],
            name=row["name"],
            parent=row.get("parent"),
            geofabrik=row.get("geofabrik"),
            max_zoom=int(row["max_zoom"]),
            min_zoom=int(row.get("min_zoom", 0)),
        )
        _check_common(region.id, region.name, packs)
        if region.id in groups:
            raise RegionError(f"{region.id} is both a group and a pack")
        if not 0 <= region.min_zoom <= region.max_zoom <= mctp.ZOOM_MAX:
            raise RegionError(f"{region.id}: zoom {region.min_zoom}-{region.max_zoom}")
        packs[region.id] = region
    for item in [*groups.values(), *packs.values()]:
        if item.parent is not None and item.parent not in groups:
            raise RegionError(f"{item.id}: parent {item.parent} is not a group")
    per_parent = {}
    for region in packs.values():
        per_parent[region.parent] = per_parent.get(region.parent, 0) + 1
    if per_parent.get(None, 0) > TOP_PACKS_MAX:
        raise RegionError(f"{per_parent[None]} packs at the top; the client shows {TOP_PACKS_MAX}")
    with_packs = [parent for parent in per_parent if parent is not None]
    if len(with_packs) > GROUPS_WITH_PACKS_MAX:
        raise RegionError(f"{len(with_packs)} groups hold packs; the client shows {GROUPS_WITH_PACKS_MAX}")
    for parent in with_packs:
        if per_parent[parent] > GROUP_PACKS_MAX:
            raise RegionError(f"{parent} holds {per_parent[parent]} packs; the client shows {GROUP_PACKS_MAX}")
    for group in groups.values():
        seen = set()
        at = group
        while at.parent is not None:
            if at.id in seen:
                raise RegionError(f"{group.id}: its parents loop")
            seen.add(at.id)
            at = groups[at.parent]
    return groups, packs


def _check_common(item_id, name, existing):
    if not ID_PATTERN.match(item_id):
        raise RegionError(f"{item_id!r} is not a lowercase-hyphen id")
    if item_id in existing:
        raise RegionError(f"{item_id} is listed twice")
    mctp.text_field(name, mctp.NAME_MAX, f"{item_id} name")


def geofabrik_index(cache_dir):
    """Geofabrik's region index, fetched once and kept under ``cache_dir``."""
    path = os.path.join(cache_dir, "geofabrik-index-v1.json")
    if not os.path.exists(path):
        os.makedirs(cache_dir, exist_ok=True)
        data = http.get(GEOFABRIK_INDEX)
        with open(path + ".part", "wb") as handle:
            handle.write(data)
        os.replace(path + ".part", path)
    with open(path, "rb") as handle:
        return {feature["properties"]["id"]: feature for feature in json.load(handle)["features"]}


def outline(region, cache_dir):
    """The region's outline as a shapely geometry, or None for the whole world."""
    if region.geofabrik is None:
        return None
    index = geofabrik_index(cache_dir)
    feature = index.get(region.geofabrik)
    if feature is None or feature.get("geometry") is None:
        raise RegionError(f"{region.id}: Geofabrik has no outline for {region.geofabrik!r}")
    return shape(feature["geometry"])


def outline_geojson(region, cache_dir):
    """The same outline as a GeoJSON Feature, which is what ``pmtiles extract --region`` takes."""
    feature = geofabrik_index(cache_dir)[region.geofabrik]
    return {"type": "Feature", "properties": {}, "geometry": feature["geometry"]}
