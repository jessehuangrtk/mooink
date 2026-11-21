# -*- coding: utf-8 -*-
"""Backend regression tests; no real mooInk reader or Windows COM required."""

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from mook_sleep_screen_manager import MooinkLowLevel, SleepScreenManager


class FakeTransport:
    def __init__(self):
        self.events = []

    def list_sleep(self):
        return [{"name": "sample.jpg", "size": 10}]

    def backup_and_delete(self, filename, backup_dir, *, confirm_delete=True):
        self.events.append((filename, Path(backup_dir), confirm_delete))
        return "backup-created"


class MooinkManagerTests(unittest.TestCase):
    def test_usb_choose_single_com_object_not_name(self):
        item = SimpleNamespace(Name="mooInk Pro")
        io = MooinkLowLevel(transport="usb", usb_index=1)

        def scan():
            io._usb_devices = [item]
            return [item.Name]  # Public return is names; private cache is objects.

        with patch.object(io, "usb_devices", side_effect=scan):
            self.assertIs(io._choose_usb(), item)

    def test_usb_choose_previously_cached_com_object(self):
        items = [SimpleNamespace(Name="mooInk Pro"), SimpleNamespace(Name="mooInk Pro")]
        io = MooinkLowLevel(transport="usb", usb_index=2)
        io._usb_devices = items
        self.assertIs(io._choose_usb(), items[1])

    def test_invalid_usb_index(self):
        io = MooinkLowLevel(transport="usb", usb_index=3)
        io._usb_devices = [SimpleNamespace(Name="mooInk Pro")]
        with self.assertRaises(ValueError):
            io._choose_usb()

    def test_two_devices_cannot_write(self):
        io = MooinkLowLevel(transport="usb", usb_index=1)
        with patch.object(io, "usb_devices", return_value=["mooInk Pro", "mooInk Pro"]):
            with self.assertRaisesRegex(RuntimeError, "同時接了兩台"):
                io._target(write=True)

    def test_wifi_share_must_be_named_sleep(self):
        with self.assertRaises(ValueError):
            MooinkLowLevel(transport="wifi", wifi_share="not_a_sleep_folder")

    def test_wifi_share_list_only_images(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "sleep"
            folder.mkdir()
            (folder / "one.JPG").write_bytes(b"123")
            (folder / "two.png").write_bytes(b"12")
            (folder / "readme.txt").write_text("skip", encoding="utf-8")
            io = MooinkLowLevel(transport="wifi", wifi_share=str(folder))
            self.assertEqual([x["name"] for x in io.list_sleep()], ["one.JPG", "two.png"])

    def test_manager_preconfirmed_delete_skips_console_prompt(self):
        with tempfile.TemporaryDirectory() as tmp:
            io = FakeTransport()
            manager = SleepScreenManager(output_dir=tmp, io_layer=io)
            with patch("mook_sleep_screen_manager.question",
                       side_effect=AssertionError("GUI must not prompt through stdin")):
                result = manager.manage(files=["sample.jpg"], operation="delete",
                                        backup_dir=tmp, confirm=False)
            self.assertEqual(len(result), 1)
            self.assertEqual(io.events[0][0], "sample.jpg")
            self.assertFalse(io.events[0][2])

    def test_cli_delete_requires_confirmation(self):
        with tempfile.TemporaryDirectory() as tmp:
            io = FakeTransport()
            manager = SleepScreenManager(output_dir=tmp, io_layer=io)
            with patch("mook_sleep_screen_manager.question", return_value=False):
                result = manager.manage(files=["sample.jpg"], operation="delete",
                                        backup_dir=tmp)
            self.assertEqual(result, ["cancelled"])
            self.assertEqual(io.events, [])

    def test_add_refuses_duplicate_name_wifi_share(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "sleep"
            folder.mkdir()
            (folder / "one.jpg").write_bytes(b"original")
            source = Path(tmp) / "one.jpg"
            source.write_bytes(b"replacement")
            io = MooinkLowLevel(transport="wifi", wifi_share=str(folder))
            with self.assertRaises(FileExistsError):
                io.add_file(source)
            self.assertEqual((folder / "one.jpg").read_bytes(), b"original")

    def test_manager_convert_invalid_size(self):
        with self.assertRaises(ValueError):
            SleepScreenManager(size="9.7")


class BatchDeleteTests(unittest.TestCase):
    def test_failure_is_skipped_and_next_file_deleted_with_verified_backup(self):
        import shutil
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            device = root / "sleep"
            device.mkdir()
            backup_dir = root / "backup"
            (device / "bad.jpg").write_bytes(b"original-bad")
            (device / "good.jpg").write_bytes(b"original-good")
            real_copy = shutil.copy2
            events = []

            def copy_spy(src, dst, *args, **kwargs):
                events.append(("copy", Path(src).name))
                if Path(src).name == "bad.jpg":
                    raise OSError("simulated backup failure")
                return real_copy(src, dst, *args, **kwargs)

            def progress(index, total, record):
                events.append(("progress", index, record["status"]))

            io = MooinkLowLevel(transport="wifi", wifi_share=str(device))
            manager = SleepScreenManager(output_dir=str(root / "prepared"), io_layer=io)
            with patch("mook_sleep_screen_manager.shutil.copy2", side_effect=copy_spy):
                rows = manager.manage(
                    files=["bad.jpg", "good.jpg"], operation="delete",
                    backup_dir=str(backup_dir), confirm=False,
                    continue_on_error=True, on_result=progress,
                )
            self.assertEqual([x["status"] for x in rows], ["failed", "deleted"])
            self.assertEqual([x["name"] for x in rows], ["bad.jpg", "good.jpg"])
            self.assertTrue((device / "bad.jpg").exists(), "Never delete a file whose backup failed")
            self.assertFalse((device / "good.jpg").exists())
            self.assertEqual((backup_dir / "good.jpg").read_bytes(), b"original-good")
            self.assertEqual(events, [
                ("copy", "bad.jpg"), ("progress", 1, "failed"),
                ("copy", "good.jpg"), ("progress", 2, "deleted"),
            ])

    def test_mtp_delete_request_marked_pending_not_success(self):
        class FakeMtp:
            def backup_and_delete(self, name, directory, *, confirm_delete=True):
                return "Windows Shell delete requested; verify manually; backup: " + name

        with tempfile.TemporaryDirectory() as tmp:
            manager = SleepScreenManager(output_dir=tmp, io_layer=FakeMtp())
            rows = manager.manage(
                files=["one.jpg", "two.png"], operation="delete",
                backup_dir=tmp, confirm=False, continue_on_error=True,
            )
        self.assertEqual([x["status"] for x in rows], ["pending", "pending"])

    def test_failure_and_skipped_status_do_not_stop_batch(self):
        class FakeMixed:
            def __init__(self):
                self.visited = []

            def backup_and_delete(self, name, directory, *, confirm_delete=True):
                self.visited.append(name)
                if name == "a.jpg":
                    raise RuntimeError("USB MTP 備份未完成或大小不符；不刪除原檔")
                if name == "b.jpg":
                    return "cancelled-after-backup: C:\\backup\\b.jpg"
                return "Windows Shell delete requested; verify manually; backup: c.jpg"

        with tempfile.TemporaryDirectory() as tmp:
            io = FakeMixed()
            manager = SleepScreenManager(output_dir=tmp, io_layer=io)
            rows = manager.manage(
                files=["a.jpg", "b.jpg", "c.jpg"], operation="delete",
                backup_dir=tmp, confirm=False, continue_on_error=True,
            )
        self.assertEqual(io.visited, ["a.jpg", "b.jpg", "c.jpg"])
        self.assertEqual([x["status"] for x in rows], ["failed", "skipped", "pending"])
        self.assertIn("備份未完成", rows[0]["detail"])

    def test_progress_callback_error_does_not_repeat_deletion(self):
        class FakeDevice:
            def __init__(self):
                self.visited = []

            def backup_and_delete(self, name, directory, *, confirm_delete=True):
                self.visited.append(name)
                return "Windows Shell delete requested; verify manually"

        with tempfile.TemporaryDirectory() as tmp:
            io = FakeDevice()
            manager = SleepScreenManager(output_dir=tmp, io_layer=io)
            rows = manager.manage(
                files=["1.jpg", "2.jpg"], operation="delete", confirm=False,
                continue_on_error=True, on_result=lambda *args: 1 / 0,
            )
        self.assertEqual(len(rows), 2)
        self.assertEqual(io.visited, ["1.jpg", "2.jpg"])




class LiveBackupProgressTests(unittest.TestCase):
    def test_live_phases_on_shared_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            device = root / "sleep"
            device.mkdir()
            (device / "first.jpg").write_bytes(b"A" * 1024)
            io = MooinkLowLevel(transport="wifi", wifi_share=str(device))
            manager = SleepScreenManager(output_dir=str(root / "prepared"), io_layer=io)
            stages = []
            rows = manager.manage(
                files=["first.jpg"], operation="delete",
                backup_dir=str(root / "backups"), confirm=False,
                continue_on_error=True, backup_timeout=15,
                on_stage=lambda index, total, row: stages.append(
                    (index, total, row["stage"], row["name"])
                ),
            )
            self.assertEqual(rows[0]["status"], "deleted")
            self.assertEqual(stages[0], (1, 1, "locate", "first.jpg"))
            self.assertIn("copying", [x[2] for x in stages])
            self.assertIn("verifying", [x[2] for x in stages])
            self.assertIn("deleting", [x[2] for x in stages])
            self.assertEqual(stages[-1][2], "deleted")

    def test_unknown_mtp_size_never_requests_delete_or_copy(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            events = []
            item = SimpleNamespace(Name="unknown.jpg")
            folder = SimpleNamespace(Items=lambda: [item])
            io = MooinkLowLevel(transport="usb")
            io._target = lambda write=False: folder
            io.list_sleep = lambda: [{"name": "unknown.jpg", "size": None}]
            io._shell = SimpleNamespace(Namespace=lambda p: events.append("namespace"))
            rows = SleepScreenManager(output_dir=str(root / "prepared"), io_layer=io).manage(
                files=["unknown.jpg"], operation="delete", backup_dir=str(root / "backup"),
                continue_on_error=True, confirm=False,
                on_stage=lambda i, n, row: events.append(row["stage"]),
            )
            self.assertEqual(rows[0]["status"], "failed")
            self.assertIn("未提供", rows[0]["detail"])
            self.assertNotIn("namespace", events)
            self.assertNotIn("deleting", events)

    def test_mock_mtp_copy_reports_observed_bytes_and_wait_budget(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            payload = b"x" * 2048
            device_item = SimpleNamespace(Name="check.jpg", Size=len(payload))
            requests = []
            def fake_delete(verb):
                requests.append(verb)
            device_item.InvokeVerb = fake_delete

            class FakeBackupFolder:
                def CopyHere(self, item, options):
                    requests.append("copy")
                    (root / "backup" / item.Name).write_bytes(payload)

            io = MooinkLowLevel(transport="usb")
            io._target = lambda write=False: SimpleNamespace(Items=lambda: [device_item])
            io.list_sleep = lambda: [{"name": "check.jpg", "size": len(payload)}]
            io._shell = SimpleNamespace(Namespace=lambda _: FakeBackupFolder())
            stages = []
            record = io.backup_and_delete(
                "check.jpg", root / "backup", confirm_delete=False,
                progress=lambda stage, details: stages.append((stage, details)),
                backup_timeout=5,
            )
            self.assertIn("delete requested", record)
            self.assertEqual(requests, ["copy", "delete"])
            self.assertTrue(any(stage == "copying" and data.get("copied") == len(payload)
                                for stage, data in stages))
            self.assertEqual(stages[-1][0], "pending")



if __name__ == "__main__":
    unittest.main()
