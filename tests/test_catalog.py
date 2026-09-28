import unittest
from datetime import datetime, timezone

from meshmaps import catalog
from meshmaps.regions import Group, Region

GROUPS = {
    "na": Group("na", "North America", None),
    "us": Group("us", "United States", "na"),
    "eu": Group("eu", "Europe", None),
}
PACKS = {
    "wa": Region("wa", "Washington", "us", "us/washington", 13),
    "de": Region("de", "Germany", "eu", "germany", 13),
    "world": Region("world", "World", None, None, 8),
}


def side(region_id, cut, style="light"):
    return {
        "id": region_id, "style": style, "cut": cut, "generated": 0, "min_zoom": 0, "max_zoom": 13,
        "bbox": [1, 2, 3, 4], "tiles": 10, "bytes": 100, "sha256": "ab" * 32,
        "url": catalog.pack_key(region_id, cut, style),
    }  # fmt: skip


class Catalog(unittest.TestCase):
    def assemble(self, sidecars):
        return catalog.assemble(GROUPS, PACKS, sidecars, now=datetime(2026, 9, 28, tzinfo=timezone.utc))

    def test_newest_cut_of_each_style_wins(self):
        built = self.assemble([side("wa", "20260901"), side("wa", "20260927"), side("wa", "20260801", "dark")])
        self.assertEqual([(p["style"], p["cut"]) for p in built["packs"]], [("dark", "20260801"), ("light", "20260927")])
        self.assertEqual(built["packs"][1]["url"], "packs/wa/20260927-light.mctp")

    def test_names_and_parents_come_from_the_region_list(self):
        entry = self.assemble([side("wa", "20260927")])["packs"][0]
        self.assertEqual((entry["name"], entry["parent"]), ("Washington", "us"))

    def test_the_top_of_the_tree_comes_first(self):
        built = self.assemble([side("de", "20260927"), side("wa", "20260927"), side("world", "20260927")])
        self.assertEqual([p["id"] for p in built["packs"]], ["world", "de", "wa"])

    def test_a_removed_region_drops_out(self):
        self.assertEqual(self.assemble([side("gone", "20260927")])["packs"], [])

    def test_only_groups_with_a_pack_beneath_them_are_listed(self):
        built = self.assemble([side("wa", "20260927")])
        self.assertEqual([g["id"] for g in built["groups"]], ["na", "us"])
        self.assertEqual(built["format"], catalog.FORMAT)
        self.assertEqual(built["generated"], "2026-09-28T00:00:00Z")

    def test_sidecar_box_is_the_region_not_the_world(self):
        from meshmaps.mctp import WriteResult

        keys = [(0, 0, 0), (13, 1307, 2860)]
        entry = catalog.sidecar(PACKS["wa"], flavor="light", cut="20260927", generated=0, keys=keys,
                                result=WriteResult(tiles=2, blobs=2, bytes=1, sha256="0" * 64))
        south, west, north, east = entry["bbox"]
        self.assertTrue(47 < south < north < 48 and -123 < west < east < -122)
        self.assertEqual((entry["min_zoom"], entry["max_zoom"]), (0, 13))


if __name__ == "__main__":
    unittest.main()
