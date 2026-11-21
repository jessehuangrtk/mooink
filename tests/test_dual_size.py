# -*- coding: utf-8 -*-
"""Device size persistence and dual-size output regression tests."""

import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image
from mook_sleep_screen_manager import convert_both_sizes
from mook_sleep_screen_manager_ui import load_device_size, save_device_size


class DeviceSizeSettingsTests(unittest.TestCase):
    def test_default_size_for_new_or_broken_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "mooink" / "settings.json"
            self.assertEqual(load_device_size(config), "13.3")
            config.parent.mkdir()
            config.write_text("broken json", encoding="utf-8")
            self.assertEqual(load_device_size(config), "13.3")

    def test_device_size_persists_and_restores(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "nested" / "settings.json"
            save_device_size(config, "10.3")
            self.assertEqual(load_device_size(config), "10.3")
            self.assertEqual(json.loads(config.read_text(encoding="utf-8")),
                             {"device_size": "10.3"})
            save_device_size(config, "13.3")
            self.assertEqual(load_device_size(config), "13.3")
            self.assertFalse((config.parent / "settings.json.tmp").exists())

    def test_invalid_size_rejected_and_existing_config_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "settings.json"
            save_device_size(config, "10.3")
            with self.assertRaises(ValueError):
                save_device_size(config, "9.7")
            self.assertEqual(load_device_size(config), "10.3")


class BothSizesConversionTests(unittest.TestCase):
    def test_generates_two_separate_directories_with_correct_resolution(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "family_photo.png"
            Image.new("RGB", (72, 110), "white").save(source)
            out = root / "result"
            groups, logs = convert_both_sizes(
                [source], orientation="portrait", fit="contain",
                output_dir=out,
            )
            self.assertEqual(set(groups), {"10.3", "13.3"})
            self.assertEqual([len(groups[k]) for k in ("10.3", "13.3")], [1, 1])
            self.assertEqual(groups["10.3"][0].parent, out / "10.3")
            self.assertEqual(groups["13.3"][0].parent, out / "13.3")
            self.assertNotEqual(groups["10.3"][0], groups["13.3"][0])
            for size, expected in (("10.3", (1404, 1872)),
                                   ("13.3", (1650, 2200))):
                with Image.open(groups[size][0]) as image:
                    self.assertEqual(image.size, expected)
                metadata = json.loads((out / size / "manifest.json").read_text(
                    encoding="utf-8"))
                self.assertEqual(metadata["size"], size)
                self.assertEqual(len(metadata["files"]), 1)
            self.assertTrue(any("[10.3 吋]" in line for line in logs))
            self.assertTrue(any("[13.3 吋]" in line for line in logs))

    def test_empty_batch_creates_separate_manifests(self):
        with tempfile.TemporaryDirectory() as tmp:
            groups, logs = convert_both_sizes([], output_dir=Path(tmp) / "outputs")
            self.assertEqual(groups, {"10.3": [], "13.3": []})
            self.assertEqual(logs, [])
            self.assertTrue((Path(tmp) / "outputs" / "10.3" / "manifest.json").exists())
            self.assertTrue((Path(tmp) / "outputs" / "13.3" / "manifest.json").exists())


if __name__ == "__main__":
    unittest.main()
