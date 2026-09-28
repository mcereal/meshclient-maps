import os
import tempfile
import unittest

from meshmaps import regions

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class Regions(unittest.TestCase):
    def load_text(self, text):
        with tempfile.NamedTemporaryFile("w", suffix=".toml", delete=False) as handle:
            handle.write(text)
        self.addCleanup(os.unlink, handle.name)
        return regions.load(handle.name)

    def test_the_committed_list_loads(self):
        groups, packs = regions.load(os.path.join(ROOT, "regions", "regions.toml"))
        self.assertIn("world", packs)
        self.assertIsNone(packs["world"].geofabrik)

    def test_refuses_a_parent_that_is_not_a_group(self):
        with self.assertRaisesRegex(regions.RegionError, "not a group"):
            self.load_text('[[pack]]\nid = "a"\nname = "A"\nparent = "nowhere"\nmax_zoom = 5\n')

    def test_refuses_a_name_the_pack_header_cannot_hold(self):
        with self.assertRaises(Exception):
            self.load_text(f'[[pack]]\nid = "a"\nname = "{"x" * 40}"\nmax_zoom = 5\n')

    def test_refuses_an_id_that_is_not_a_path_segment(self):
        with self.assertRaisesRegex(regions.RegionError, "lowercase"):
            self.load_text('[[pack]]\nid = "us/washington"\nname = "W"\nmax_zoom = 5\n')

    def test_refuses_duplicates_and_zoom_out_of_range(self):
        with self.assertRaisesRegex(regions.RegionError, "twice"):
            self.load_text('[[pack]]\nid = "a"\nname = "A"\nmax_zoom = 5\n' * 2)
        with self.assertRaisesRegex(regions.RegionError, "zoom"):
            self.load_text('[[pack]]\nid = "a"\nname = "A"\nmax_zoom = 19\n')

    def test_refuses_a_group_loop(self):
        with self.assertRaisesRegex(regions.RegionError, "loop"):
            self.load_text(
                '[[group]]\nid = "a"\nname = "A"\nparent = "b"\n[[group]]\nid = "b"\nname = "B"\nparent = "a"\n'
            )


    def test_refuses_a_group_the_client_cannot_show(self):
        text = '[[group]]\nid = "us"\nname = "US"\n'
        for i in range(regions.GROUP_PACKS_MAX + 1):
            text += f'[[pack]]\nid = "us-{i}"\nname = "S{i}"\nparent = "us"\nmax_zoom = 13\n'
        with self.assertRaisesRegex(regions.RegionError, "us holds"):
            self.load_text(text)

    def test_refuses_more_top_packs_than_the_client_shows(self):
        text = "".join(
            f'[[pack]]\nid = "top-{i}"\nname = "T{i}"\nmax_zoom = 6\n'
            for i in range(regions.TOP_PACKS_MAX + 1)
        )
        with self.assertRaisesRegex(regions.RegionError, "at the top"):
            self.load_text(text)


if __name__ == "__main__":
    unittest.main()
