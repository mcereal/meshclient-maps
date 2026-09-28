"""The ``.mctp`` raster tile pack: writer, reader and verifier.

The format belongs to mesh-client. Its reader, and the authoritative layout, is
``src/map/source_pack.c`` there, and ``devtools/map_pack/map_pack.py`` is the host tool that
first wrote it. This is a second writer for the same bytes. It streams from tiles on disk
instead of holding a region in memory, and it stores identical tiles once.

Little-endian throughout:

    0    8   "MCTPACK2"
    8    2   tile size in pixels
    10   1   tile format (1 = PNG)
    11   1   reserved, zero
    12   4   index offset from the start of the file
    16   4   tile count
    20   4   reserved, zero
    24   8   generated: seconds since the epoch, 0 when unknown
    32   64  attribution, UTF-8, NUL padded
    96   32  name, UTF-8, NUL padded
    128      index: count 24-byte entries sorted by (z, x, y), then the tile bytes

An entry is (u8 zoom, 3 pad, u32 x, u32 y, u32 length, u64 offset). The client checks each
entry's extent lies inside the file and never that extents are disjoint, so two entries may
name the same bytes. That is what makes open ocean cost one tile, not thousands.
"""

import hashlib
import os
import struct
from dataclasses import dataclass, field

MAGIC = b"MCTPACK2"
HEADER = struct.Struct("<8sHBBIII")
HEADER_LEN = 128
ENTRY = struct.Struct("<BxxxIIIQ")
ENTRY_LEN = 24
ATTRIBUTION_MAX = 64
NAME_MAX = 32
TILE_SIZE = 256
FORMAT_PNG = 1
TILE_BYTES_MAX = 1024 * 1024
TILES_MAX = 1_000_000
ZOOM_MAX = 18

# FAT32 cannot hold a file of 4 GiB or more, and the Brick's card is FAT32.
PACK_BYTES_MAX = (1 << 32) - 1

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
PNG_PALETTE = 3


class PackError(Exception):
    pass


def text_field(value, cap, label):
    encoded = value.encode("utf-8")
    if len(encoded) > cap - 1:
        raise PackError(f"{label} is {len(encoded)} bytes; the pack holds {cap - 1}")
    return encoded + b"\0" * (cap - len(encoded))


def png_shape(data):
    """(width, height, colour type) from a PNG's IHDR, or None when it is not a PNG."""
    if len(data) < 33 or not data.startswith(PNG_SIGNATURE) or data[12:16] != b"IHDR":
        return None
    width, height = struct.unpack(">II", data[16:24])
    return width, height, data[25]


def check_key(key):
    z, x, y = key
    if z > ZOOM_MAX or x >= (1 << z) or y >= (1 << z):
        raise PackError(f"tile {key} is not a place in the pyramid")


def check_tile(key, data):
    check_key(key)
    if not data or len(data) > TILE_BYTES_MAX:
        raise PackError(f"tile {key} is {len(data)} bytes")
    shape = png_shape(data)
    if shape is None:
        raise PackError(f"tile {key} is not a PNG")
    if shape[:2] != (TILE_SIZE, TILE_SIZE):
        raise PackError(f"tile {key} is {shape[0]}x{shape[1]}; the client draws {TILE_SIZE}")


@dataclass
class WriteResult:
    tiles: int
    blobs: int
    bytes: int
    sha256: str


def write(output, tiles, *, name, attribution, generated):
    """Writes a pack from ``tiles``, a mapping of (z, x, y) to a path holding that tile's PNG.

    Reads every tile twice, first to hash and then to copy, so a region never has to fit in
    memory. Written to ``output + ".part"`` and renamed, so a failed build leaves no pack behind.
    """
    if not tiles:
        raise PackError("nothing to pack")
    if len(tiles) > TILES_MAX:
        raise PackError(f"{len(tiles)} tiles; the reader holds {TILES_MAX}")

    order = sorted(tiles)
    tiles_at = HEADER_LEN + ENTRY_LEN * len(order)
    blobs = {}  # digest -> (offset, length, path), in first-seen order
    entries = []
    offset = tiles_at
    for key in order:
        with open(tiles[key], "rb") as handle:
            data = handle.read()
        check_tile(key, data)
        digest = hashlib.sha256(data).digest()
        if digest not in blobs:
            blobs[digest] = (offset, len(data), tiles[key])
            offset += len(data)
        blob_offset, length, _ = blobs[digest]
        entries.append(ENTRY.pack(key[0], key[1], key[2], length, blob_offset))
    if offset > PACK_BYTES_MAX:
        raise PackError(f"pack would be {offset} bytes; FAT32 holds {PACK_BYTES_MAX}")

    part = output + ".part"
    whole = hashlib.sha256()
    with open(part, "wb") as out:

        def emit(chunk):
            out.write(chunk)
            whole.update(chunk)

        emit(HEADER.pack(MAGIC, TILE_SIZE, FORMAT_PNG, 0, HEADER_LEN, len(order), 0))
        emit(struct.pack("<q", int(generated)))
        emit(text_field(attribution, ATTRIBUTION_MAX, "attribution"))
        emit(text_field(name, NAME_MAX, "name"))
        emit(b"".join(entries))
        for blob_offset, length, path in blobs.values():
            with open(path, "rb") as handle:
                data = handle.read()
            if len(data) != length:
                raise PackError(f"{path} changed while the pack was being written")
            emit(data)
        written = out.tell()
    os.replace(part, output)
    return WriteResult(tiles=len(order), blobs=len(blobs), bytes=written, sha256=whole.hexdigest())


@dataclass
class Pack:
    path: str
    size: int
    tile_size: int
    format: int
    index_at: int
    generated: int
    attribution: str
    name: str
    entries: list = field(default_factory=list)  # (z, x, y, length, offset)


def load(path):
    """A pack's header and index, checked the way the client checks them at open."""
    size = os.path.getsize(path)
    with open(path, "rb") as handle:
        raw = handle.read(HEADER_LEN)
        if len(raw) < HEADER_LEN:
            raise PackError(f"{path} is too short to be a pack")
        magic, tile_size, fmt, _, index_at, count, _ = HEADER.unpack(raw[: HEADER.size])
        if magic != MAGIC:
            raise PackError(f"{path} is not a tile pack")
        if count == 0 or count > TILES_MAX:
            raise PackError(f"{path} declares {count} tiles")
        if index_at < HEADER_LEN:
            raise PackError(f"{path} puts its index inside its own header")
        handle.seek(index_at)
        index_raw = handle.read(ENTRY_LEN * count)
    if len(index_raw) != ENTRY_LEN * count:
        raise PackError(f"{path} declares {count} tiles and holds fewer")
    return Pack(
        path=path,
        size=size,
        tile_size=tile_size,
        format=fmt,
        index_at=index_at,
        generated=struct.unpack("<q", raw[24:32])[0],
        attribution=raw[32:96].split(b"\0", 1)[0].decode("utf-8", "replace"),
        name=raw[96:128].split(b"\0", 1)[0].decode("utf-8", "replace"),
        entries=[ENTRY.unpack_from(index_raw, i * ENTRY_LEN) for i in range(count)],
    )


def verify(path):
    """Every problem the client would refuse the pack for, plus tiles that are not palette PNGs.

    Returns (pack, problems, non_palette_count). Reads each distinct blob once.
    """
    pack = load(path)
    problems = []
    if pack.tile_size != TILE_SIZE:
        problems.append(f"tile size {pack.tile_size}, not {TILE_SIZE}")
    if pack.format != FORMAT_PNG:
        problems.append(f"tile format {pack.format}, not PNG")
    tiles_at = pack.index_at + len(pack.entries) * ENTRY_LEN
    previous = None
    seen = {}
    non_palette = 0
    with open(path, "rb") as handle:
        for z, x, y, length, offset in pack.entries:
            key = (z, x, y)
            if previous is not None and key <= previous:
                problems.append(f"tile {key} is out of order after {previous}")
            previous = key
            try:
                check_key(key)
            except PackError as error:
                problems.append(str(error))
            if offset < tiles_at or offset > pack.size or pack.size - offset < length:
                problems.append(f"tile {key} lies outside the file")
                continue
            if (offset, length) not in seen:
                handle.seek(offset)
                data = handle.read(length)
                try:
                    check_tile(key, data)
                    seen[(offset, length)] = png_shape(data)[2]
                except PackError as error:
                    problems.append(str(error))
                    seen[(offset, length)] = None
                    continue
            if seen[(offset, length)] != PNG_PALETTE:
                non_palette += 1
    return pack, problems, non_palette


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()
