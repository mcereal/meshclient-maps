import unittest

from shapely.geometry import box

from meshmaps import tiles


class Tiles(unittest.TestCase):
    def test_world_is_every_tile(self):
        self.assertEqual(len(tiles.covering(None, 0, 3)), 1 + 4 + 16 + 64)

    def test_tile_at_and_bounds_agree(self):
        x, y = tiles.tile_at(-122.52, 47.63, 13)
        self.assertEqual((x, y), (1307, 2860))
        west, south, east, north = tiles.tile_bounds(13, x, y)
        self.assertTrue(west <= -122.52 < east and south <= 47.63 < north)

    def test_covering_follows_the_outline_not_its_box(self):
        # Two small squares far apart: their bounding box is most of a continent, the tiles are not.
        both = box(-122.6, 47.5, -122.5, 47.6).union(box(-80.2, 25.7, -80.1, 25.8))
        keys = tiles.covering(both, 10, 10)
        self.assertLess(len(keys), 10)
        self.assertEqual(keys, sorted(keys))
        self.assertIn((10,) + tiles.tile_at(-122.55, 47.55, 10), keys)

    def test_covering_includes_every_ancestor_down_to_min_zoom(self):
        keys = tiles.covering(box(-122.6, 47.5, -122.5, 47.6), 0, 6)
        self.assertEqual(sorted({key[0] for key in keys}), list(range(7)))

    def test_coverage_is_the_union_of_tile_corners(self):
        south, west, north, east = tiles.coverage([(1, 0, 0), (1, 1, 1)])
        self.assertAlmostEqual(west, -180.0)
        self.assertAlmostEqual(east, 180.0)
        self.assertAlmostEqual(north, tiles.LAT_MAX, places=6)
        self.assertAlmostEqual(south, -tiles.LAT_MAX, places=6)


if __name__ == "__main__":
    unittest.main()
