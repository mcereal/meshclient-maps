"""meshmaps: build, check and publish mesh-client's map packs.

    python -m meshmaps plan                       regions.toml from regions/plan.toml and Geofabrik
    python -m meshmaps check                      regions.toml, and every outline it names
    python -m meshmaps tiles us-washington        how many tiles a region is, per zoom
    python -m meshmaps build us-washington        extract, render, pack, verify -> dist/
    python -m meshmaps info dist/.../x.mctp       what a pack says about itself
    python -m meshmaps verify dist/.../x.mctp     everything the client would refuse it for
    python -m meshmaps catalog                    dist/catalog.json from what is in dist/
    python -m meshmaps publish us-washington      upload a built pack, rebuild the remote catalog
    python -m meshmaps publish-catalog            rebuild the remote catalog from its sidecars
    python -m meshmaps shards 40 all              regions split into balanced batches, as JSON
    python -m meshmaps prune --keep 2             drop old cuts from R2
"""

import argparse
import glob
import json
import os
import sys
import time
from collections import Counter

from . import catalog, mctp, plan, publish, regions, render, tiles

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REGIONS = os.path.join(ROOT, "regions", "regions.toml")
PLAN = os.path.join(ROOT, "regions", "plan.toml")
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


def cmd_plan(args):
    features = regions.geofabrik_index(CACHE)
    index = {gid: feature["properties"] for gid, feature in features.items()}
    count = plan.counter(features, os.path.join(CACHE, f"tiles-z{plan.COUNT_ZOOM}.json"))
    try:
        result = plan.make(plan.load(PLAN), index, count)
    except plan.PlanError as error:
        fail(str(error))
    per_group = Counter(pack.parent for pack in result.packs)
    for group in result.groups:
        print(f"  {group.name:<32} {per_group.get(group.id, 0):>3} packs")
    print(f"split into subdivisions: {', '.join(result.split) or 'none'}")
    print(f"drawn shallower to fit: {', '.join(f'{gid} z{zoom}' for gid, zoom in result.shallow) or 'none'}")
    print(f"left to the world base: {', '.join(result.dropped) or 'none'}")
    text = plan.render(result)
    if args.dry_run:
        print(f"{len(result.groups)} groups, {len(result.packs)} packs (not written)")
        return
    with open(REGIONS + ".part", "w") as handle:
        handle.write(text)
    try:
        regions.load(REGIONS + ".part")
    except (regions.RegionError, mctp.PackError) as error:
        os.unlink(REGIONS + ".part")
        fail(f"the plan does not make a valid regions.toml: {error}")
    os.replace(REGIONS + ".part", REGIONS)
    print(f"{len(result.groups)} groups, {len(result.packs)} packs -> regions/regions.toml")


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
        if args.no_catalog:
            return
        text = catalog.dumps(catalog.assemble(groups, packs, publish.remote_sidecars(s3)))
        publish.upload_catalog(s3, text)
    except publish.PublishError as error:
        fail(str(error))
    print("catalog updated")


def cmd_publish_catalog(args):
    groups, packs = load_regions()
    try:
        s3 = publish.client()
        built = catalog.assemble(groups, packs, publish.remote_sidecars(s3))
        publish.upload_catalog(s3, catalog.dumps(built))
    except publish.PublishError as error:
        fail(str(error))
    print(f"catalog updated: {len(built['packs'])} packs in {len(built['groups'])} groups")


def cmd_shards(args):
    """Regions in ``args.count`` batches of about equal work, longest first into the lightest -
    what the build workflow runs one job per batch of, since a matrix holds 256 jobs."""
    _, packs = load_regions()
    wanted = list(packs) if args.regions == ["all"] else args.regions
    for region_id in wanted:
        pick(packs, region_id)
    features = regions.geofabrik_index(CACHE)
    count = plan.counter(features, os.path.join(CACHE, f"tiles-z{plan.COUNT_ZOOM}.json"))

    def work(region_id):
        region = packs[region_id]
        if region.geofabrik is None:
            return sum(4**z for z in range(region.max_zoom + 1))
        return plan.estimate(count(region.geofabrik), region.max_zoom)

    shards = [[] for _ in range(max(1, min(args.count, len(wanted))))]
    loads = [0] * len(shards)
    for region_id in sorted(wanted, key=work, reverse=True):
        lightest = loads.index(min(loads))
        shards[lightest].append(region_id)
        loads[lightest] += work(region_id)
    print(json.dumps([" ".join(shard) for shard in shards if shard]))


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

    planned = commands.add_parser("plan")
    planned.add_argument("--dry-run", action="store_true", help="print the plan, write nothing")
    planned.set_defaults(run=cmd_plan)

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
    published.add_argument("--no-catalog", action="store_true", help="upload only; publish-catalog after")
    published.set_defaults(run=cmd_publish)

    commands.add_parser("publish-catalog").set_defaults(run=cmd_publish_catalog)

    sharded = commands.add_parser("shards")
    sharded.add_argument("count", type=int)
    sharded.add_argument("regions", nargs="+", help="region ids, or all")
    sharded.set_defaults(run=cmd_shards)

    pruned = commands.add_parser("prune")
    pruned.add_argument("--keep", type=int, default=2)
    pruned.add_argument("--dry-run", action="store_true")
    pruned.set_defaults(run=cmd_prune)

    args = parser.parse_args()
    args.run(args)


if __name__ == "__main__":
    main()
