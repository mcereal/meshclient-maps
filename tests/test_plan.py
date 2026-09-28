import unittest

from meshmaps import plan

PLAN = {
    "budget_tiles": 400000,
    "max_zoom": 13,
    "min_zoom": 10,
    "exclude": ["both"],
    "top": [{"id": "world", "name": "World", "max_zoom": 8}],
    "continent": [{"geofabrik": "land", "id": "land", "name": "Land"}],
    "split_by_prefix": {"us": {"id": "us", "name": "United States"}},
    "names": {"us/new-york": "New York State"},
    "zoom": {"small": 14},
}

# Tiles at plan.COUNT_ZOOM. z13 fits the budget up to 18,750; z12 up to 75,000; z10 up to 1.2M.
INDEX = {
    "land": ({"name": "Land"}, 0),
    "small": ({"name": "Small", "parent": "land"}, 100),
    "big": ({"name": "Big", "parent": "land"}, 50000),
    "north": ({"name": "North", "parent": "big"}, 9000),
    "south": ({"name": "South", "parent": "big"}, 30000),
    "wide": ({"name": "Wide", "parent": "land"}, 60000),
    "vast": ({"name": "Vast", "parent": "land"}, 5000000),
    "both": ({"name": "Small and Wide", "parent": "land"}, 100),
    "us": ({"name": "United States", "parent": "land"}, 200000),
    "us/new-york": ({"name": "us/new-york", "parent": "land"}, 800),
    "us/district-of-columbia": ({"name": "us/district-of-columbia", "parent": "land"}, 4),
}


def make(**changes):
    index = {gid: properties | {"id": gid} for gid, (properties, _) in INDEX.items()}
    return plan.make(PLAN | changes, index, lambda gid: INDEX[gid][1])


class Plan(unittest.TestCase):
    def test_every_region_is_a_pack_a_group_or_named_as_left_out(self):
        result = make()
        packs = {pack.id: pack for pack in result.packs}
        self.assertEqual(packs["world"].max_zoom, 8)
        self.assertEqual(packs["small"].max_zoom, 14, "a zoom override goes deeper")
        self.assertNotIn("big", packs, "too big and subdivided: split")
        self.assertEqual(packs["big-north"].parent, "big")
        self.assertEqual(packs["big-north"].max_zoom, 13)
        self.assertEqual(packs["big-south"].max_zoom, 12, "a subdivision that still does not fit is drawn shallower")
        self.assertEqual(packs["wide"].max_zoom, 12, "too big and not subdivided: shallower")
        self.assertEqual(result.dropped, ["vast"], "too big at every zoom the plan allows")
        self.assertNotIn("both", packs, "an excluded union is not listed")
        self.assertEqual([g.id for g in result.groups], ["land", "big", "us"])

    def test_the_us_is_its_states_named_from_their_paths(self):
        packs = {pack.id: pack for pack in make().packs}
        self.assertNotIn("us", packs)
        self.assertEqual(packs["us-new-york"].parent, "us")
        self.assertEqual(packs["us-new-york"].geofabrik, "us/new-york")
        self.assertEqual(packs["us-new-york"].name, "New York State", "an override wins")
        self.assertEqual(packs["us-district-of-columbia"].name, "District of Columbia")

    def test_a_name_the_header_cannot_hold_is_refused(self):
        with self.assertRaisesRegex(plan.PlanError, "small"):
            make(names={"small": "x" * 32})

    def test_the_estimate_scales_by_four_a_level(self):
        self.assertEqual(plan.estimate(3, plan.COUNT_ZOOM), 4)
        self.assertEqual(plan.estimate(3, plan.COUNT_ZOOM + 1), 16)
        self.assertEqual(plan.fit_zoom(100, 400000, 13, 10), 13)
        self.assertIsNone(plan.fit_zoom(10**9, 400000, 13, 10))

    def test_the_written_file_loads(self):
        import os
        import tempfile

        from meshmaps import regions

        with tempfile.NamedTemporaryFile("w", suffix=".toml", delete=False) as handle:
            handle.write(plan.render(make()))
        self.addCleanup(os.unlink, handle.name)
        groups, packs = regions.load(handle.name)
        self.assertIn("us-new-york", packs)
        self.assertEqual(groups["us"].parent, "land")


if __name__ == "__main__":
    unittest.main()
