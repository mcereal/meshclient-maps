import io
import os
import struct
import tempfile
import unittest

from PIL import Image

from meshmaps import mctp


def png(colour, size=256, palette=True):
    image = Image.new("RGB", (size, size), colour)
    if palette:
        image = image.quantize(colors=4)
    out = io.BytesIO()
    image.save(out, "PNG")
    return out.getvalue()


class Pack(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)

    def tile(self, name, data):
        path = os.path.join(self.dir.name, name)
        with open(path, "wb") as handle:
            handle.write(data)
        return path

    def write(self, tiles, **kwargs):
        out = os.path.join(self.dir.name, "out.mctp")
        options = {"name": "Test", "attribution": "(c) test", "generated": 1_700_000_000, **kwargs}
        return out, mctp.write(out, tiles, **options)

    def test_round_trip_keeps_header_and_sorted_index(self):
        sea = self.tile("sea.png", png((0, 0, 255)))
        land = self.tile("land.png", png((0, 255, 0)))
        out, result = self.write({(1, 1, 0): land, (0, 0, 0): sea, (1, 0, 0): sea})
        pack, problems, non_palette = mctp.verify(out)
        self.assertEqual(problems, [])
        self.assertEqual(non_palette, 0)
        self.assertEqual([entry[:3] for entry in pack.entries], [(0, 0, 0), (1, 0, 0), (1, 1, 0)])
        self.assertEqual((pack.name, pack.attribution, pack.generated), ("Test", "(c) test", 1_700_000_000))
        self.assertEqual(result.sha256, mctp.sha256_file(out))
        self.assertEqual(result.bytes, os.path.getsize(out))

    def test_identical_tiles_are_stored_once(self):
        sea_a = self.tile("a.png", png((0, 0, 255)))
        sea_b = self.tile("b.png", png((0, 0, 255)))
        out, result = self.write({(1, 0, 0): sea_a, (1, 0, 1): sea_b, (1, 1, 0): sea_a})
        self.assertEqual((result.tiles, result.blobs), (3, 1))
        pack = mctp.load(out)
        self.assertEqual(len({entry[4] for entry in pack.entries}), 1)
        self.assertEqual(mctp.verify(out)[1], [])

    def test_refuses_what_the_client_cannot_draw(self):
        small = self.tile("small.png", png((1, 2, 3), size=128))
        with self.assertRaisesRegex(mctp.PackError, "128x128"):
            self.write({(0, 0, 0): small})
        text = self.tile("text.png", b"not a png at all, just some bytes padding it out")
        with self.assertRaisesRegex(mctp.PackError, "not a PNG"):
            self.write({(0, 0, 0): text})
        good = self.tile("good.png", png((1, 2, 3)))
        with self.assertRaisesRegex(mctp.PackError, "pyramid"):
            self.write({(1, 2, 0): good})
        with self.assertRaisesRegex(mctp.PackError, "31"):
            self.write({(0, 0, 0): good}, name="x" * 32)
        self.assertFalse(os.path.exists(os.path.join(self.dir.name, "out.mctp")))

    def test_verify_names_an_extent_past_the_end(self):
        good = self.tile("good.png", png((1, 2, 3)))
        out, _ = self.write({(0, 0, 0): good})
        with open(out, "r+b") as handle:
            handle.seek(mctp.HEADER_LEN + 12)
            handle.write(struct.pack("<I", 10_000_000))
        self.assertIn("lies outside the file", mctp.verify(out)[1][0])

    def test_notes_tiles_that_are_not_palette(self):
        rgb = self.tile("rgb.png", png((1, 2, 3), palette=False))
        out, _ = self.write({(0, 0, 0): rgb})
        self.assertEqual(mctp.verify(out)[2], 1)


if __name__ == "__main__":
    unittest.main()
