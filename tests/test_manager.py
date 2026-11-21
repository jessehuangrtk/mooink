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



class MtpBackupRegressionTests(unittest.TestCase):
    def test_duplicate_backup_uses_separate_folder_with_original_filename(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            backup_dir = root / "backup"
            backup_dir.mkdir()
            (backup_dir / "repeat.jpg").write_bytes(b"OLD")
            copied_paths = []
            deleted = []
            item = SimpleNamespace(
                Name="repeat.jpg",
                InvokeVerb=lambda verb: deleted.append(verb),
            )

            class FakeLocalFolder:
                def __init__(self, dest):
                    self.dest = Path(dest)

                def CopyHere(self, source_item, flags):
                    actual_path = self.dest / source_item.Name
                    copied_paths.append(actual_path)
                    actual_path.write_bytes(b"NEW!")

            io = MooinkLowLevel(transport="usb")
            io._target = lambda write=False: SimpleNamespace(Items=lambda: [item])
            io.list_sleep = lambda: [{"name": "repeat.jpg", "size": 4}]
            io._shell = SimpleNamespace(Namespace=lambda dest: FakeLocalFolder(dest))
            actual = io.backup_and_delete(
                "repeat.jpg", backup_dir, delete_after=False,
                confirm_delete=False, backup_timeout=5,
            )

            self.assertIn("backup-only-size-verified", actual)
            self.assertEqual((backup_dir / "repeat.jpg").read_bytes(), b"OLD")
            self.assertEqual(len(copied_paths), 1)
            self.assertEqual(copied_paths[0].name, "repeat.jpg")
            self.assertNotEqual(copied_paths[0].parent, backup_dir)
            self.assertEqual(copied_paths[0].read_bytes(), b"NEW!")
            self.assertEqual(deleted, [])

    def test_mtp_copy_not_started_halts_batch_without_deleting_anything(self):
        from mook_sleep_screen_manager import MtpBackupNotStartedError
        with tempfile.TemporaryDirectory() as tmp:
            copied, deleted = [], []
            item = SimpleNamespace(
                Name="first.jpg",
                InvokeVerb=lambda verb: deleted.append(verb),
            )
            io = MooinkLowLevel(transport="usb")
            io._target = lambda write=False: SimpleNamespace(Items=lambda: [item])
            io.list_sleep = lambda: [{"name": "first.jpg", "size": 100}]
            io._shell = SimpleNamespace(
                Namespace=lambda path: SimpleNamespace(
                    CopyHere=lambda source, flags: copied.append(source.Name)
                )
            )
            manager = SleepScreenManager(output_dir=str(Path(tmp) / "prepared"), io_layer=io)
            # The first monotonic call constructs the copy deadline (0 + 5);
            # the second immediately expires it without a five-second sleep.
            with patch("mook_sleep_screen_manager.time.monotonic", side_effect=[0.0, 8.0]):
                rows = manager.manage(
                    files=["first.jpg", "second.jpg", "third.jpg"],
                    operation="delete", backup_dir=str(Path(tmp) / "backups"),
                    confirm=False, continue_on_error=True, backup_timeout=5,
                    stop_on_mtp_stall=True,
                )
            self.assertEqual(copied, ["first.jpg"])
            self.assertEqual(deleted, [])
            self.assertEqual([x["status"] for x in rows],
                             ["failed", "blocked", "blocked"])
            self.assertIn("MtpBackupNotStartedError", rows[0]["detail"])

    def test_mtp_unconfirmed_delete_halts_following_files(self):
        class Device:
            transport = "usb"
            def __init__(self):
                self.items = []
            def backup_and_delete(self, name, path, *, confirm_delete=True, **kwargs):
                self.items.append(name)
                return "Windows Shell delete requested; verify manually"

        with tempfile.TemporaryDirectory() as tmp:
            d = Device()
            manager = SleepScreenManager(io_layer=d, output_dir=tmp)
            rows = manager.manage(
                files=["one.jpg", "two.jpg"], operation="delete", backup_dir=tmp,
                confirm=False, continue_on_error=True, stop_on_mtp_stall=True,
            )
            self.assertEqual(d.items, ["one.jpg"])
            self.assertEqual([x["status"] for x in rows], ["pending", "blocked"])



class EmptyBackupFolderRegressions(unittest.TestCase):
    def test_missing_mtp_sizes_no_longer_create_empty_directories(self):
        from mook_sleep_screen_manager import MtpUnknownSizeError
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            device_item = SimpleNamespace(Name="001.jpg")
            io = MooinkLowLevel(transport="usb")
            io._target = lambda write=False: SimpleNamespace(Items=lambda: [device_item])
            io.list_sleep = lambda: [{"name": "001.jpg", "size": None}]
            copy_requested = []
            io._shell = SimpleNamespace(Namespace=lambda _: copy_requested.append("copy"))
            manager = SleepScreenManager(output_dir=base / "prepared", io_layer=io)
            destination = base / "backups"
            rows = manager.manage(
                files=["001.jpg"] + [str(i) + ".jpg" for i in range(2, 23)],
                operation="delete", backup_dir=destination, confirm=False,
                continue_on_error=True, stop_on_mtp_stall=True,
            )
            self.assertEqual(len(rows), 22)
            self.assertEqual([x["status"] for x in rows], ["failed"] + ["blocked"] * 21)
            self.assertIn("MtpUnknownSizeError", rows[0]["detail"])
            self.assertFalse(destination.exists())
            self.assertEqual(copy_requested, [])

    def test_cleanup_only_removes_directly_nested_empty_autogenerated_directories(self):
        from mook_sleep_screen_manager import cleanup_empty_backup_dirs
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            empty = base / "mooink_20261121_154433_001"
            empty.mkdir()
            nonempty = base / "mooink_20261121_154433_002"
            nonempty.mkdir()
            (nonempty / "saved.jpg").write_bytes(b"never remove")
            other = base / "my_other_folder"
            other.mkdir()
            nested = other / "mooink_20261121_154433_003"
            nested.mkdir()
            ignored = base / "mooink_invalid_folder_004"
            ignored.mkdir()
            removed = cleanup_empty_backup_dirs(base)
            self.assertEqual(removed, [str(empty)])
            self.assertFalse(empty.exists())
            self.assertTrue((nonempty / "saved.jpg").exists())
            self.assertTrue(other.exists())
            self.assertTrue(nested.exists())
            self.assertTrue(ignored.exists())

    def test_mtp_size_uses_extended_property_when_plain_size_is_missing(self):
        item = SimpleNamespace(
            Size=None,
            ExtendedProperty=lambda name: 456 if name == "System.Size" else None,
        )
        self.assertEqual(MooinkLowLevel._mtp_size(item), 456)

    def test_backup_only_works_without_device_size_and_never_deletes(self):
        import io
        from PIL import Image
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with io.BytesIO() as stream:
                Image.new("RGB", (2, 2), "white").save(stream, format="PNG")
                pixels = stream.getvalue()
            requested = []
            device_item = SimpleNamespace(
                Name="unknown.png",
                InvokeVerb=lambda verb: requested.append(verb),
            )

            class FakeLocalFolder:
                def __init__(self, destination):
                    self.destination = Path(destination)

                def CopyHere(self, item, options):
                    (self.destination / item.Name).write_bytes(pixels)

            device = MooinkLowLevel(transport="usb")
            device._target = lambda write=False: SimpleNamespace(Items=lambda: [device_item])
            device.list_sleep = lambda: [{"name": "unknown.png", "size": None}]
            device._shell = SimpleNamespace(Namespace=lambda dst: FakeLocalFolder(dst))
            result = device.backup_and_delete(
                "unknown.png", root / "backup", delete_after=False,
                confirm_delete=False, backup_timeout=8,
            )
            self.assertIn("backup-only-unverified-source-size", result)
            self.assertEqual((root / "backup" / "unknown.png").read_bytes(), pixels)
            self.assertEqual(requested, [])


if __name__ == "__main__":
    unittest.main()
