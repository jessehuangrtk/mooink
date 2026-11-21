# -*- coding: utf-8 -*-
"""Tests for optional WPD direct USB transfer, without a physical reader."""
import io
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from PIL import Image
from mook_mtp_wpd import (
    QuietMtpUnavailable,
    _choose_device,
    _sleep_directory,
    backup_and_delete_one,
)
from mook_sleep_screen_manager import MooinkLowLevel, SleepScreenManager


class FakeFile:
    def __init__(self, folder, name, payload, size=None):
        self.folder = folder
        self.name = name
        self.content_type = 2
        self.size = len(payload) if size is None else size
        self.payload = payload
        self.download_count = 0
        self.remove_count = 0

    def download_file(self, path):
        self.download_count += 1
        Path(path).write_bytes(self.payload)

    def remove(self):
        self.remove_count += 1
        self.folder.children.remove(self)


class FakeFolder:
    def __init__(self, name, content_type, children=None):
        self.name = name
        self.content_type = content_type
        self.children = children or []

    def get_children(self):
        return list(self.children)


class FakeDevice:
    def __init__(self, name="mooInk Pro", content=None):
        self.devicename = name
        self.content = content or []
        self.closed = False

    def get_content(self):
        return list(self.content)

    def close(self):
        self.closed = True


def make_device(payload, *, source_size=None, device_name="mooInk Pro"):
    sleep = FakeFolder("sleep", 1)
    image = FakeFile(sleep, "001.png", payload, size=source_size)
    sleep.children.append(image)
    storage = FakeFolder("Internal storage", 0, children=[sleep])
    device = FakeDevice(device_name, [storage])
    return device, sleep, image


def png_data():
    stream = io.BytesIO()
    Image.new("RGB", (4, 4), "white").save(stream, format="PNG")
    return stream.getvalue()


class QuietWpdTests(unittest.TestCase):
    def test_unique_compatible_device_only(self):
        self.assertIs(_choose_device([FakeDevice("other"), FakeDevice("mooInk Pro")]).devicename,
                      "mooInk Pro")
        with self.assertRaises(QuietMtpUnavailable):
            _choose_device([FakeDevice("other")])
        with self.assertRaises(QuietMtpUnavailable):
            _choose_device([FakeDevice("mooInk 1"), FakeDevice("mooInk 2")])

    def test_one_sleep_directory_required(self):
        dev, sleep, _ = make_device(png_data())
        self.assertIs(_sleep_directory(dev), sleep)
        dev.content.append(FakeFolder("sleep", 1))
        with self.assertRaises(QuietMtpUnavailable):
            _sleep_directory(dev)

    def test_wpd_download_verify_delete_no_prompt(self):
        dev, sleep, img = make_device(png_data())
        with tempfile.TemporaryDirectory() as tmp:
            with patch("builtins.input", side_effect=AssertionError("no console prompts")):
                result = backup_and_delete_one(
                    "001.png", Path(tmp) / "backups",
                    device_source=lambda: [dev], delete_after=True,
                )
            self.assertIn("deleted+verified", result)
            self.assertEqual(img.download_count, 1)
            self.assertEqual(img.remove_count, 1)
            self.assertEqual(sleep.get_children(), [])
            self.assertTrue(dev.closed)
            self.assertEqual((Path(tmp) / "backups" / "001.png").read_bytes(),
                             img.payload)

    def test_backup_only_never_deletes(self):
        dev, sleep, img = make_device(png_data())
        with tempfile.TemporaryDirectory() as tmp:
            result = backup_and_delete_one(
                "001.png", tmp, device_source=lambda: [dev],
                delete_after=False,
            )
            self.assertIn("backup-only-wpd-verified", result)
            self.assertEqual(img.remove_count, 0)
            self.assertEqual(len(sleep.get_children()), 1)

    def test_wrong_source_size_preserves_device_image(self):
        dev, sleep, img = make_device(png_data(), source_size=987654)
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(RuntimeError, "備份大小不符"):
                backup_and_delete_one(
                    "001.png", tmp, device_source=lambda: [dev],
                    delete_after=True,
                )
            self.assertEqual(img.remove_count, 0)
            self.assertEqual(len(sleep.get_children()), 1)

    def test_ambiguous_device_identity_never_copies(self):
        dev1, _, img1 = make_device(png_data(), device_name="mooInk Pro")
        dev2, _, img2 = make_device(png_data(), device_name="mooInk Pro")
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(QuietMtpUnavailable):
                backup_and_delete_one(
                    "001.png", tmp, device_source=lambda: [dev1, dev2],
                )
        self.assertEqual(img1.download_count + img2.download_count, 0)
        self.assertEqual(img1.remove_count + img2.remove_count, 0)
        self.assertTrue(dev1.closed)
        self.assertTrue(dev2.closed)

    def test_backend_quiet_wpd_bypasses_shell_invoke_verb(self):
        io_device = MooinkLowLevel(transport="usb")
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(io_device, "usb_devices", return_value=["mooInk"]), \
                 patch("mook_mtp_wpd.backup_and_delete_one",
                       return_value="deleted+verified; backup: tested") as direct:
                response = io_device.backup_and_delete(
                    "001.png", Path(tmp), quiet_wpd=True, confirm_delete=False
                )
            self.assertIn("deleted+verified", response)
            direct.assert_called_once()
            self.assertTrue(direct.call_args.kwargs["delete_after"])

    def test_batch_stops_on_unavailable_quiet_wpd_backend(self):
        dev = MooinkLowLevel(transport="usb")
        manager = SleepScreenManager(io_layer=dev)
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(dev, "usb_devices", return_value=["mooInk"]), \
                 patch("mook_mtp_wpd.backup_and_delete_one",
                       side_effect=QuietMtpUnavailable("需要安裝 WPD")):
                result = manager.manage(
                    files=["001.png", "002.png", "003.png"],
                    operation="delete", confirm=False,
                    backup_dir=tmp, continue_on_error=True,
                    quiet_wpd=True, stop_on_mtp_stall=True,
                )
        self.assertEqual([r["status"] for r in result],
                         ["failed", "blocked", "blocked"])


if __name__ == "__main__":
    unittest.main()
