"""From vector tiles to the palette PNGs a pack holds.

The source is a Protomaps planet build (OpenStreetMap as vector tiles). ``pmtiles extract``
cuts the region out of it over HTTP range requests, tileserver-gl draws each raster tile from
that with the style under build/style, and each tile is quantised to a small palette here: the
Brick decodes a palette PNG in about 60% of the time of a 24-bit one, and it is a third the size.
"""

import contextlib
import io
import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

from PIL import Image

from . import http

PLANET_BUILDS = "https://build-metadata.protomaps.dev/builds.json"
PLANET_BASE = "https://build.protomaps.com/"

# The planet's own deepest zoom. Anything deeper is drawn by overzooming z15 data.
PLANET_MAX_ZOOM = 15

TILESERVER_IMAGE = "maptiler/tileserver-gl:v5.6.0"

# Room the renderer draws around each tile, so a label crossing an edge is drawn whole on both
# sides instead of cut off or dropped.
TILE_MARGIN = 64

DEFAULT_COLOURS = 32


class RenderError(Exception):
    pass


def latest_planet():
    """(url, cut date as YYYYMMDD, epoch seconds it was built) of the newest planet build."""
    builds = json.loads(http.get(PLANET_BUILDS, timeout=60))
    newest = max(builds, key=lambda build: build["key"])
    uploaded = datetime.fromisoformat(newest["uploaded"].replace("Z", "+00:00"))
    return PLANET_BASE + newest["key"], newest["key"].split(".")[0], int(uploaded.timestamp())


def planet_cut(url):
    """(cut date, epoch seconds) for a planet URL named YYYYMMDD.pmtiles."""
    stem = os.path.basename(url).split(".")[0]
    try:
        when = datetime.strptime(stem, "%Y%m%d").replace(tzinfo=timezone.utc)
    except ValueError as error:
        raise RenderError(f"{url}: not a dated planet build") from error
    return stem, int(when.timestamp())


def pmtiles_tool(root):
    local = os.path.join(root, "build", "tools", "pmtiles")
    if os.path.exists(local):
        return local
    found = shutil.which("pmtiles")
    if found is None:
        raise RenderError("no pmtiles tool: run `make tools`")
    return found


def extract(root, planet_url, region_geojson, max_zoom, output):
    """Cuts a region's vector tiles out of the planet into ``output``. Skipped if already there.

    ``region_geojson`` is a path to a GeoJSON outline, or None for the whole world.
    """
    if os.path.exists(output):
        return
    command = [pmtiles_tool(root), "extract", planet_url, output + ".part"]
    command.append(f"--maxzoom={min(max_zoom, PLANET_MAX_ZOOM)}")
    if region_geojson is None:
        command.append("--bbox=-180,-85.05,180,85.05")
    else:
        command.append(f"--region={region_geojson}")
    subprocess.run(command, check=True)
    os.replace(output + ".part", output)


def _free_port():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


@contextlib.contextmanager
def tileserver(root, vector_path, flavor):
    """A tileserver-gl container drawing ``vector_path`` in ``flavor``; yields its tile URL."""
    style_dir = os.path.join(root, "build", "style")
    fonts_dir = os.path.join(root, "build", "assets", "fonts")
    if not os.path.exists(os.path.join(style_dir, flavor, "style.json")):
        raise RenderError(f"no {flavor} style: run `make style`")
    if not os.path.isdir(fonts_dir):
        raise RenderError("no fonts: run `make assets`")

    region_dir = os.path.dirname(os.path.abspath(vector_path))
    config = {
        "options": {
            "paths": {"root": "/data", "fonts": "fonts", "styles": "styles", "pmtiles": "region"},
            "tileMargin": TILE_MARGIN,
        },
        "styles": {flavor: {"style": f"{flavor}/style.json"}},
        "data": {"protomaps": {"pmtiles": os.path.basename(vector_path)}},
    }
    with open(os.path.join(region_dir, "tileserver.json"), "w") as handle:
        json.dump(config, handle)

    port = _free_port()
    name = f"meshmaps-{os.getpid()}-{port}"
    subprocess.run(
        [
            "docker", "run", "-d", "--rm", "--name", name,
            "-p", f"127.0.0.1:{port}:8080",
            "-v", f"{os.path.abspath(style_dir)}:/data/styles:ro",
            "-v", f"{os.path.abspath(fonts_dir)}:/data/fonts:ro",
            "-v", f"{region_dir}:/data/region:ro",
            TILESERVER_IMAGE, "--config", "/data/region/tileserver.json",
        ],
        check=True,
        stdout=subprocess.DEVNULL,
    )  # fmt: skip
    base = f"http://127.0.0.1:{port}"
    try:
        deadline = time.monotonic() + 120
        while True:
            try:
                with urllib.request.urlopen(base + "/health", timeout=5):
                    break
            except (urllib.error.URLError, ConnectionError):
                if time.monotonic() > deadline:
                    logs = subprocess.run(["docker", "logs", name], capture_output=True, text=True)
                    raise RenderError(f"tileserver did not start:\n{logs.stdout}{logs.stderr}")
                time.sleep(1)
        yield f"{base}/styles/{flavor}/256/{{z}}/{{x}}/{{y}}.png"
    finally:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True)


def quantise(png, colours):
    """A rendered tile as a palette PNG of at most ``colours`` entries, undithered.

    Undithered because a dithered flat fill is noise to the PNG filter and costs more bytes than
    the colours it saves, and a map is flat fills.
    """
    image = Image.open(io.BytesIO(png)).convert("RGB")
    palette = image.quantize(colors=colours, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE)
    out = io.BytesIO()
    palette.save(out, "PNG", optimize=True)
    return out.getvalue()


def tile_path(tiles_dir, key):
    z, x, y = key
    return os.path.join(tiles_dir, str(z), str(x), f"{y}.png")


def _render_one(url_template, tiles_dir, key, colours):
    path = tile_path(tiles_dir, key)
    if os.path.exists(path):
        return
    z, x, y = key
    url = url_template.format(z=z, x=x, y=y)
    for attempt in range(4):
        try:
            with urllib.request.urlopen(url, timeout=120) as response:
                png = response.read()
            break
        except (urllib.error.URLError, ConnectionError):
            if attempt == 3:
                raise
            time.sleep(2**attempt)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path + ".part", "wb") as handle:
        handle.write(quantise(png, colours))
    os.replace(path + ".part", path)


def render(url_template, keys, tiles_dir, *, colours=DEFAULT_COLOURS, workers=8):
    """Draws every tile in ``keys`` into ``tiles_dir``. Tiles already there are kept, so an
    interrupted render picks up where it stopped."""
    total = len(keys)
    done = 0
    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(_render_one, url_template, tiles_dir, key, colours) for key in keys]
        for future in as_completed(futures):
            future.result()
            done += 1
            if done % 1000 == 0 or done == total:
                rate = done / max(time.monotonic() - started, 1e-6)
                print(f"  rendered {done}/{total} ({rate:.0f}/s)", file=sys.stderr)
