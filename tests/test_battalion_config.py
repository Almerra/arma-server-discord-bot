import ast
import json
from pathlib import Path
from types import SimpleNamespace
import unittest

from cogs.ctc import CTC, DEFAULTS
from ctc.units import UnitError


ROOT = Path(__file__).resolve().parents[1]
OFFICES = {
    "server_admin_office", "recruitment_headquarters", "combat_training_centre",
    "field_specialist_office", "mission_development",
}


class BattalionConfigTests(unittest.TestCase):
    def test_template_places_offices_under_am2(self):
        config = json.loads((ROOT / "config.json.template").read_text(encoding="utf-8"))
        discord = config["discord"]
        self.assertTrue(OFFICES.isdisjoint(discord))
        self.assertTrue(OFFICES.issubset(discord["am2"]))
        self.assertEqual(discord["am2"]["name"], "2nd Battalion")
        self.assertNotIn("site_url", discord["am2"])
        self.assertIn("site_url", discord["am2"]["combat_training_centre"])
        self.assertNotIn("units", discord["am2"]["combat_training_centre"])
        self.assertNotIn("primary_unit", discord["am2"]["combat_training_centre"])
        for battalion in ("am1", "am3", "am4"):
            self.assertTrue(OFFICES.isdisjoint(discord[battalion]))

    def test_consumers_do_not_read_legacy_top_level_offices(self):
        paths = [
            *list((ROOT / "cogs").glob("*.py")),
            ROOT / "scripts" / "mission_feedback" / "export.py",
        ]
        for path in paths:
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Subscript) or not isinstance(node.slice, ast.Constant):
                    continue
                if node.slice.value not in OFFICES:
                    continue
                parent = node.value
                if isinstance(parent, ast.Subscript) and isinstance(parent.slice, ast.Constant):
                    self.assertNotEqual(parent.slice.value, "discord", str(path))

    def test_ctc_reads_each_battalion_without_sharing_settings(self):
        settings = {
            "manager_ids": [123],
            "queue_channel_id": 456,
            "instructor_role_id": 789,
            "site_url": "https://am2.example",
        }
        bot = SimpleNamespace(config={"discord": {
            "default_battalion": "am2",
            "am1": {
                "name": "1st Battalion",
                "combat_training_centre": {
                    "queue_channel_id": 999, "catalogue": "am1.json",
                    "site_url": "https://am1.example",
                },
            },
            "am2": {
                "name": "2nd Battalion",
                "combat_training_centre": settings,
            },
            "am3": {"name": "3rd Battalion"},
            "am4": {},
        }})
        cog = CTC(bot)
        self.assertEqual(cog.units.primary, "am2")
        self.assertEqual(cog.units.keys(), ["am1", "am2"])
        self.assertEqual(cog.units.require("am2").settings["queue_channel_id"], 456)
        self.assertEqual(cog.units.require("am2").settings["manager_ids"], [123])
        self.assertEqual(cog.units.require("am2").settings["archive_on_award"], DEFAULTS["archive_on_award"])
        am1 = cog.units.require("am1")
        self.assertEqual(am1.name, "1st Battalion")
        self.assertEqual(am1.settings["site_url"], "https://am1.example")
        self.assertEqual(am1.settings["queue_channel_id"], 999)
        self.assertEqual(am1.settings["manager_ids"], [])
        self.assertEqual(am1.catalogue_path.name, "am1.json")
        self.assertEqual(cog.units.for_channel(999).key, "am1")
        self.assertEqual(cog.units.find("2nd Battalion").key, "am2")
        self.assertNotIn("name", settings)
        self.assertEqual(settings["site_url"], "https://am2.example")
        self.assertEqual(cog.units.require("am2").settings["site_url"], "https://am2.example")

    def test_ctc_missing_offices_retains_existing_unconfigured_behavior(self):
        for bot in (SimpleNamespace(), SimpleNamespace(config={"discord": {"am1": {}}})):
            self.assertEqual(CTC.raw_config(bot), {})

    def test_ctc_single_battalion_keeps_its_key(self):
        cog = CTC(SimpleNamespace(config={"discord": {
            "am3": {"combat_training_centre": {"queue_channel_id": 123}},
        }}))
        self.assertEqual(cog.units.primary, "am3")
        self.assertEqual(cog.units.only.key, "am3")
        self.assertEqual(cog.units.only.name, "AM3")
        self.assertEqual(cog.units.only.settings["site_url"], "")

    def test_ctc_rejects_legacy_or_invalid_settings(self):
        for settings in (None, [], {"units": {"am1": {}}}, {"primary_unit": "am2"}):
            with self.subTest(settings=settings), self.assertRaises(UnitError):
                CTC.raw_config(SimpleNamespace(config={"discord": {
                    "am2": {"combat_training_centre": settings},
                }}))
