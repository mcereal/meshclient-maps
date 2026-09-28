"""meshmaps: build, check and publish mesh-client's map packs.

    python -m meshmaps check                      regions.toml, and every outline it names
    python -m meshmaps tiles us-washington        how many tiles a region is, per zoom
    python -m meshmaps build us-washington        extract, render, pack, verify -> dist/
    python -m meshmaps info dist/.../x.mctp       what a pack says about itself
    python -m meshmaps verify dist/.../x.mctp     everything the client would refuse it for
    python -m meshmaps catalog                    dist/catalog.json from what is in dist/
    python -m meshmaps publish us-washington      upload a built pack, rebuild the remote catalog
    python -m meshmaps prune --keep 2             drop old cuts from R2
"""

import argparse
import glob
import json
import os
import sys
import time
from collections import Counter

from . import catalog, mctp, publish, regions, render, tiles

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REGIONS = os.path.join(ROOT, "regions", "regions.toml")
CACHE = os.path.join(ROOT, "build", "cache")
WORK = os.path.join(ROOT, "build", "work")
DIST = os.path.join(ROOT, "dist")


def fail(message):
    print(f"meshmaps: {message}", file=sys.stderr)
    sys.exit(1)


def load_regions():
    try:
        return regions.load(REGIONS)
    except (regions.RegionError, mctp.PackError) as error:
        fail(f"regions.toml: {error}")


def pick(packs, region_id):
    if region_id not in packs:
        fail(f"no pack {region_id!r} in regions.toml")
    return packs[region_id]


def cmd_check(args):
    groups, packs = load_regions()
    for region in packs.values():
        regions.outline(region, CACHE)
    print(f"{len(groups)} groups, {len(packs)} packs; every outline resolves")


def cmd_tiles(args):
    _, packs = load_regions()
    region = pick(packs, args.region)
    keys = tiles.covering(regions.outline(region, CACHE), region.min_zoom, args.max_zoom or region.max_zoom)
    per_zoom = Counter(key[0] for key in keys)
    running = 0
    for z in sorted(per_zoom):
        running += per_zoom[z]
        print(f"  z{z:<2} {per_zoom[z]:>9} {running:>10}")
    print(f"{region.id}: {len(keys)} tiles")


def cmd_build(args):
    _, packs = load_regions()
    region = pick(packs, args.region)
    if args.planet:
        planet_url = args.planet
        cut, generated = render.planet_cut(planet_url)
    else:
        planet_url, cut, generated = render.latest_planet()
    print(f"{region.id}: planet {cut}, style {args.flavor}, z{region.min_zoom}-{region.max_zoom}")

    work = os.path.join(WORK, region.id)
    os.makedirs(work, exist_ok=True)
    geometry = regions.outline(region, CACHE)
    outline_path = None
    if geometry is not None:
        outline_path = os.path.join(work, "outline.geojson")
        with open(outline_path, "w") as handle:
            json.dump(regions.outline_geojson(region, CACHE), handle)

    vector = os.path.join(work, f"{cut}.pmtiles")
    started = time.monotonic()
    render.extract(ROOT, planet_url, outline_path, region.max_zoom, vector)
    print(f"  extracted in {time.monotonic() - started:.0f}s ({os.path.getsize(vector) / 1e6:.1f} MB)")

    keys = tiles.covering(geometry, region.min_zoom, region.max_zoom)
    if len(keys) > mctp.TILES_MAX:
        fail(f"{len(keys)} tiles; the client holds {mctp.TILES_MAX}. Lower max_zoom")
    tiles_dir = os.path.join(work, f"tiles-{cut}-{args.flavor}")
    started = time.monotonic()
    with render.tileserver(ROOT, vector, args.flavor) as url:
        render.render(url, keys, tiles_dir, colours=args.colours, workers=args.workers)
    print(f"  rendered {len(keys)} tiles in {time.monotonic() - started:.0f}s")

    out_dir = os.path.join(DIST, region.id)
    os.makedirs(out_dir, exist_ok=True)
    pack_path = os.path.join(out_dir, f"{cut}-{args.flavor}.mctp")
    result = mctp.write(
        pack_path,
        {key: render.tile_path(tiles_dir, key) for key in keys},
        name=region.name,
        attribution=catalog.ATTRIBUTION,
        generated=generated,
    )
    _, problems, _ = mctp.verify(pack_path)
    if problems:
        fail(f"{pack_path} does not verify: {problems[:5]}")
    meta = catalog.sidecar(region, flavor=args.flavor, cut=cut, generated=generated, keys=keys, result=result)
    with open(pack_path[: -len(".mctp")] + ".json", "w") as handle:
        json.dump(meta, handle, indent=1)
    print(
        f"  {pack_path}: {result.tiles} tiles, {result.blobs} distinct, "
        f"{result.bytes / 1e6:.1f} MB, sha256 {result.sha256[:12]}"
    )


def cmd_info(args):
    try:
        pack = mctp.load(args.pack)
    except mctp.PackError as error:
        fail(str(error))
    keys = [entry[:3] for entry in pack.entries]
    south, west, north, east = tiles.coverage(keys)
    per_zoom = Counter(key[0] for key in keys)
    blobs = len({(entry[3], entry[4]) for entry in pack.entries})
    when = time.strftime("%Y-%m-%d", time.gmtime(pack.generated)) if pack.generated else "unset"
    print(f"name         {pack.name or '(unset)'}")
    print(f"attribution  {pack.attribution or '(unset)'}")
    print(f"generated    {when}")
    print(f"tiles        {len(keys)} ({blobs} distinct) in {pack.size / 1e6:.1f} MB")
    for z in sorted(per_zoom):
        print(f"  z{z:<2}        {per_zoom[z]}")
    print(f"coverage     {south:.5f},{west:.5f} to {north:.5f},{east:.5f}")
    print(f"index        {len(keys) * mctp.ENTRY_LEN / 1e6:.2f} MB held in the client's RAM")


def cmd_verify(args):
    try:
        pack, problems, non_palette = mctp.verify(args.pack)
    except mctp.PackError as error:
        fail(str(error))
    for problem in problems:
        print(f"  {problem}")
    print(f"{args.pack}: {len(pack.entries)} tiles, {len(problems)} problem(s)")
    if non_palette:
        print(f"  note: {non_palette} tiles are not palette PNGs")
    if problems:
        sys.exit(1)


def local_sidecars():
    found = []
    for path in glob.glob(os.path.join(DIST, "*", "*.json")):
        with open(path) as handle:
            found.append(json.load(handle))
    return found


def cmd_catalog(args):
    groups, packs = load_regions()
    text = catalog.dumps(catalog.assemble(groups, packs, local_sidecars()))
    os.makedirs(DIST, exist_ok=True)
    with open(os.path.join(DIST, "catalog.json"), "w") as handle:
        handle.write(text)
    print(text, end="")


def cmd_publish(args):
    groups, packs = load_regions()
    wanted = set(args.regions)
    for region_id in wanted:
        pick(packs, region_id)
    try:
        s3 = publish.client()
        for meta in local_sidecars():
            if meta["id"] not in wanted:
                continue
            path = os.path.join(DIST, meta["id"], os.path.basename(meta["url"]))
            if mctp.sha256_file(path) != meta["sha256"]:
                fail(f"{path} does not match its sidecar")
            print(f"uploading {meta['url']} ({meta['bytes'] / 1e6:.1f} MB)")
            publish.upload_pack(s3, path, meta)
        text = catalog.dumps(catalog.assemble(groups, packs, publish.remote_sidecars(s3)))
        publish.upload_catalog(s3, text)
    except publish.PublishError as error:
        fail(str(error))
    print("catalog updated")


def cmd_prune(args):
    try:
        s3 = publish.client()
        doomed = publish.prune(s3, args.keep, dry_run=args.dry_run)
    except publish.PublishError as error:
        fail(str(error))
    for key in doomed:
        print(("would delete " if args.dry_run else "deleted ") + key)


def main():
    parser = argparse.ArgumentParser(prog="meshmaps", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)

    commands.add_parser("check").set_defaults(run=cmd_check)

    counted = commands.add_parser("tiles")
    counted.add_argument("region")
    counted.add_argument("--max-zoom", type=int, help="count as if the region went this deep")
    counted.set_defaults(run=cmd_tiles)

    built = commands.add_parser("build")
    built.add_argument("region")
    built.add_argument("--flavor", default="light", help="the style under build/style")
    built.add_argument("--planet", help="a dated planet URL; default is the newest build")
    built.add_argument("--colours", type=int, default=render.DEFAULT_COLOURS)
    built.add_argument("--workers", type=int, default=8)
    built.set_defaults(run=cmd_build)

    for name, run in (("info", cmd_info), ("verify", cmd_verify)):
        shown = commands.add_parser(name)
        shown.add_argument("pack")
        shown.set_defaults(run=run)

    commands.add_parser("catalog").set_defaults(run=cmd_catalog)

    published = commands.add_parser("publish")
    published.add_argument("regions", nargs="+")
    published.set_defaults(run=cmd_publish)

    pruned = commands.add_parser("prune")
    pruned.add_argument("--keep", type=int, default=2)
    pruned.add_argument("--dry-run", action="store_true")
    pruned.set_defaults(run=cmd_prune)

    args = parser.parse_args()
    args.run(args)


if __name__ == "__main__":
    main()
