"""Web Mercator tile arithmetic, and which tiles a region needs.

XYZ, not TMS: y counts south from the top of the world, which is what the client reads.
"""

import math

from shapely.geometry import box
from shapely.prepared import prep

LAT_MAX = 85.0511287798


def tile_bounds(z, x, y):
    """(west, south, east, north) of one tile, in degrees."""
    n = 1 << z
    west = x / n * 360.0 - 180.0
    east = (x + 1) / n * 360.0 - 180.0
    north = math.degrees(math.atan(math.sinh(math.pi * (1.0 - 2.0 * y / n))))
    south = math.degrees(math.atan(math.sinh(math.pi * (1.0 - 2.0 * (y + 1) / n))))
    return west, south, east, north


def tile_at(lon, lat, z):
    n = 1 << z
    lat = max(-LAT_MAX, min(LAT_MAX, lat))
    x = int((lon + 180.0) / 360.0 * n)
    y = int((1.0 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2.0 * n)
    return min(max(x, 0), n - 1), min(max(y, 0), n - 1)


def covering(geometry, min_zoom, max_zoom):
    """Every tile from ``min_zoom`` to ``max_zoom`` that touches ``geometry``, sorted.

    Walks down the pyramid from zoom 0 and only descends into tiles that touch the outline, so a
    state costs a few hundred thousand intersection tests rather than one per tile in its
    bounding box. ``geometry`` is None for the whole world.
    """
    if geometry is None:
        return [
            (z, x, y) for z in range(min_zoom, max_zoom + 1) for x in range(1 << z) for y in range(1 << z)
        ]
    shape = prep(geometry)
    found = []
    stack = [(0, 0, 0)]
    while stack:
        z, x, y = stack.pop()
        if not shape.intersects(box(*tile_bounds(z, x, y))):
            continue
        if z >= min_zoom:
            found.append((z, x, y))
        if z < max_zoom:
            stack.extend(((z + 1, 2 * x + dx, 2 * y + dy) for dx in (0, 1) for dy in (0, 1)))
    found.sort()
    return found


def coverage(keys):
    """(south, west, north, east) of a set of tiles: the box the client derives from a pack."""
    west, south, east, north = 180.0, 90.0, -180.0, -90.0
    for z, x, y in keys:
        t_west, t_south, t_east, t_north = tile_bounds(z, x, y)
        west, south = min(west, t_west), min(south, t_south)
        east, north = max(east, t_east), max(north, t_north)
    return south, west, north, east
