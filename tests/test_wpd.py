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
    upload_one,
    replace_watch_image,
    list_images,
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

    def upload_file(self, filename, source):
        item = FakeFile(self, filename, Path(source).read_bytes())
        self.children.append(item)


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

    def test_usb_vid_pid_identifies_reader_with_generic_name(self):
        dev = FakeDevice("Digital Paper")
        dev._p_id = r"\\?\USB#VID_1F85&PID_4E16#UNIT123"
        self.assertIs(_choose_device([dev]), dev)
        other = FakeDevice("Other MTP")
        other._p_id = r"\\?\USB#VID_0001&PID_0002#OTHER"
        with self.assertRaises(QuietMtpUnavailable):
            _choose_device([other])

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
            self.assertEqual(img.download_count, 2)  # backup + pre-delete hash recheck
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


class UploadWpdTests(unittest.TestCase):
    def test_upload_and_sha256_verify_after_readback(self):
        payload = png_data()
        dev, sleep, _ = make_device(payload)
        stages = []
        with tempfile.TemporaryDirectory() as tmp:
            local = Path(tmp) / "fresh.png"
            local.write_bytes(payload)
            result = upload_one(
                local, device_source=lambda: [dev],
                progress=lambda stage, details: stages.append(stage),
            )
            self.assertIn("uploaded+sha256-verified", result)
            uploaded = next(x for x in sleep.children if x.name == "fresh.png")
            self.assertEqual(uploaded.payload, payload)
            self.assertEqual(uploaded.download_count, 1)
            self.assertEqual(uploaded.remove_count, 0)
            self.assertEqual(stages[-1], "verified")
            self.assertIn("uploading", stages)
            self.assertIn("checking", stages)
            self.assertIn("verifying", stages)
            self.assertTrue(dev.closed)

    def test_upload_rejects_existing_name_without_modification(self):
        dev, sleep, first = make_device(png_data())
        with tempfile.TemporaryDirectory() as tmp:
            local = Path(tmp) / "001.png"
            local.write_bytes(b"other")
            with self.assertRaises(FileExistsError):
                upload_one(local, device_source=lambda: [dev])
        self.assertEqual(sleep.children, [first])
        self.assertEqual(first.download_count, 0)

    def test_upload_wrong_readback_hash_fails_without_deleting(self):
        dev, sleep, original = make_device(png_data())
        original_upload = sleep.upload_file

        def corrupt(filename, source):
            original_upload(filename, source)
            sleep.children[-1].payload = b"corrupted"

        sleep.upload_file = corrupt
        with tempfile.TemporaryDirectory() as tmp:
            local = Path(tmp) / "new.png"
            local.write_bytes(png_data())
            with self.assertRaisesRegex(RuntimeError, "回讀大小不符|SHA-256"):
                upload_one(local, device_source=lambda: [dev])
        self.assertEqual(sleep.children[0], original)
        self.assertEqual(sleep.children[-1].remove_count, 0)
        self.assertTrue(dev.closed)

    def test_upload_wpd_missing_file_in_sleep_is_not_verified(self):
        dev, sleep, _ = make_device(png_data())
        sleep.upload_file = lambda name, path: None
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "vanish.png"
            path.write_bytes(png_data())
            with patch("mook_mtp_wpd.time.sleep", return_value=None):
                with self.assertRaisesRegex(RuntimeError, "找不到"):
                    upload_one(path, device_source=lambda: [dev])
        self.assertTrue(dev.closed)

    def test_list_images_wpd_contains_new_upload(self):
        dev, sleep, _ = make_device(png_data())
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "new.png"
            path.write_bytes(png_data())
            upload_one(path, device_source=lambda: [dev])
            rows = list_images(device_source=lambda: [dev])
        self.assertEqual([x["name"] for x in rows], ["001.png", "new.png"])

    def test_backend_upload_delegates_to_wpd_not_shell_copyhere(self):
        transport = MooinkLowLevel(transport="usb")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "new.png"
            path.write_bytes(png_data())
            with patch.object(transport, "usb_devices", return_value=["mooInk"]), \
                 patch("mook_mtp_wpd.upload_one",
                       return_value="uploaded+sha256-verified; sleep: new.png") as direct:
                result = transport.add_file(path)
            self.assertIn("uploaded+sha256-verified", result)
            direct.assert_called_once()
            self.assertEqual(direct.call_args.args[0].resolve(), path.resolve())

    def test_backend_upload_reports_every_file_and_duplicate_skips(self):
        transport = MooinkLowLevel(transport="usb")
        with tempfile.TemporaryDirectory() as tmp:
            a, b, c = [Path(tmp) / x for x in ("one.png", "two.png", "three.png")]
            for item in (a, b, c):
                item.write_bytes(png_data())
            calls = []
            updates = []

            def fake_add(path, progress=None):
                calls.append(path.name)
                if path.name == "two.png":
                    raise FileExistsError("same name")
                if progress:
                    progress("uploading", {"message": "mock"})
                return "uploaded+sha256-verified; sleep: " + path.name

            with patch.object(transport, "add_file", side_effect=fake_add):
                manager = SleepScreenManager(io_layer=transport, output_dir=Path(tmp) / "prepared")
                results = manager.manage(
                    files=[a, b, c], operation="add", continue_on_error=True,
                    on_stage=lambda index, total, record: updates.append(
                        (index, record["stage"])),
                )
            self.assertEqual(calls, ["one.png", "two.png", "three.png"])
            self.assertEqual([x["status"] for x in results], ["verified", "skipped", "verified"])
            self.assertIn((1, "uploading"), updates)
            self.assertIn((3, "verified"), updates)

    def test_missing_wpd_backend_blocks_following_uploads(self):
        transport = MooinkLowLevel(transport="usb")
        with tempfile.TemporaryDirectory() as tmp:
            imgs = [Path(tmp) / x for x in ("1.png", "2.png", "3.png")]
            for src in imgs:
                src.write_bytes(png_data())
            with patch.object(transport, "usb_devices", return_value=["mooInk"]), \
                 patch("mook_mtp_wpd.upload_one",
                       side_effect=QuietMtpUnavailable("WPD missing")) as direct:
                result = SleepScreenManager(
                    io_layer=transport, output_dir=Path(tmp) / "prepared").manage(
                        files=imgs, operation="add", continue_on_error=True,
                    )
            self.assertEqual([r["status"] for r in result], ["failed", "blocked", "blocked"])
            direct.assert_called_once()



if __name__ == "__main__":
    unittest.main()


class WatchImageReplacementTests(unittest.TestCase):
    def test_replace_only_reserved_png_and_backup(self):
        previous=png_data()
        dev,sleep,other=make_device(previous)
        old=FakeFile(sleep,"mooink_display_10.png",previous)
        sleep.children.append(old)
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/"mooink_display_10.png"
            Image.new("L",(4,4),0).save(path)
            result=replace_watch_image(path,Path(tmp)/"backup",device_source=lambda:[dev])
            self.assertTrue(result["changed"])
            self.assertEqual(Path(result["backup"]).read_bytes(),previous)
            self.assertEqual(old.remove_count,1)
            self.assertEqual(other.remove_count,0)
            new=[x for x in sleep.children if x.name==path.name][0]
            self.assertEqual(new.payload,path.read_bytes())
            self.assertTrue(dev.closed)

    def test_identical_image_is_not_deleted_or_uploaded(self):
        data=png_data()
        dev,sleep,other=make_device(data)
        old=FakeFile(sleep,"mooink_display_10.png",data)
        sleep.children.append(old)
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/old.name
            path.write_bytes(data)
            result=replace_watch_image(path,Path(tmp)/"backup",device_source=lambda:[dev])
            self.assertFalse(result["changed"])
            self.assertEqual(old.remove_count,0)
            self.assertFalse((Path(tmp)/"backup").exists())

    def test_invalid_old_image_keeps_original(self):
        dev,sleep,other=make_device(png_data())
        old=FakeFile(sleep,"mooink_display_13.png",b"not an image")
        sleep.children.append(old)
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/old.name
            path.write_bytes(png_data())
            with self.assertRaises(Exception):
                replace_watch_image(path,Path(tmp)/"backup",device_source=lambda:[dev])
            self.assertEqual(old.remove_count,0)
            self.assertIn(old,sleep.children)

    def test_no_other_filename_and_no_multiple_reader_write(self):
        dev,sleep,other=make_device(png_data())
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/"001.png"
            path.write_bytes(png_data())
            with self.assertRaises(ValueError):
                replace_watch_image(path,Path(tmp)/"backup",device_source=lambda:[dev])
            target=Path(tmp)/"mooink_display_10.png"
            target.write_bytes(png_data())
            with self.assertRaises(QuietMtpUnavailable):
                replace_watch_image(target,Path(tmp)/"backup",
                                    device_source=lambda:[dev,FakeDevice("mooInk 2")])
            self.assertEqual(other.remove_count,0)
            self.assertEqual(len(sleep.children),1)


class ReaderProbeTests(unittest.TestCase):
    def test_reader_probe_validates_sleep_and_closes_session(self):
        from mook_mtp_wpd import probe_reader
        device, _, _ = make_device(png_data())
        device._p_id = "reader-id"
        self.assertEqual(probe_reader(device_source=lambda: [device]), "reader-id")
        self.assertTrue(device.closed)

    def test_reader_probe_rejects_missing_sleep(self):
        from mook_mtp_wpd import probe_reader
        device = FakeDevice()
        with self.assertRaises(QuietMtpUnavailable):
            probe_reader(device_source=lambda: [device])
        self.assertTrue(device.closed)


class MultipleReaderTests(unittest.TestCase):
    def test_two_identical_named_readers_have_distinct_ids(self):
        from mook_mtp_wpd import list_readers
        d1, _, _ = make_device(png_data())
        d2, _, _ = make_device(png_data())
        d1._p_id = r"USB\\VID_1F85&PID_4E16\\SERIAL_A"
        d2._p_id = r"USB\\VID_1F85&PID_4E16\\SERIAL_B"
        rows = list_readers(device_source=lambda: [d1, d2])
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(row["ready"] for row in rows))
        self.assertEqual(len({row["id"] for row in rows}), 2)
        self.assertTrue(d1.closed and d2.closed)

    def test_targeted_write_does_not_touch_other_reader(self):
        from mook_mtp_wpd import _reader_id
        d1, f1, old1 = make_device(png_data())
        d2, f2, old2 = make_device(png_data())
        d1._p_id = r"USB\\VID_1F85&PID_4E16\\SERIAL_A"
        d2._p_id = r"USB\\VID_1F85&PID_4E16\\SERIAL_B"
        before2 = [x.payload for x in f2.children]
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "mooink_display_10.png"
            target.write_bytes(png_data())
            result = replace_watch_image(target, Path(tmp)/"backups",
                                         device_source=lambda: [d1, d2],
                                         reader_id=_reader_id(d1))
        self.assertTrue(result["changed"])
        self.assertEqual([x.payload for x in f2.children], before2)
        self.assertEqual(old2.remove_count, 0)
        self.assertEqual(len(f1.children), 2)  # existing 001.png plus new reserved PNG
        self.assertEqual(len(f2.children), 1)

    def test_multi_without_target_must_refuse_write(self):
        d1, _, _ = make_device(png_data())
        d2, _, _ = make_device(png_data())
        d1._p_id = "reader_a"
        d2._p_id = "reader_b"
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/"mooink_display_10.png"
            path.write_bytes(png_data())
            with self.assertRaises(QuietMtpUnavailable):
                replace_watch_image(path,Path(tmp)/"backup",
                                    device_source=lambda: [d1,d2])

    def test_unknown_id_refuses_targeted_write(self):
        d1, folder, _ = make_device(png_data())
        d1._p_id = "reader_a"
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/"mooink_display_10.png"
            path.write_bytes(png_data())
            with self.assertRaises(QuietMtpUnavailable):
                replace_watch_image(path,Path(tmp)/"backup",
                                    device_source=lambda:[d1], reader_id="0"*64)
            self.assertEqual(len(folder.children), 1)


class ListReaderDiagnosticTests(unittest.TestCase):
    def test_not_ready_reader_still_appears_with_error(self):
        from mook_mtp_wpd import list_readers
        device = FakeDevice("mooInk Pro")
        device._p_id = "WPD_MOOINK_DIAGNOSTIC_01"
        output = list_readers(device_source=lambda: [device])
        self.assertEqual(len(output), 1)
        self.assertFalse(output[0]["ready"])
        self.assertIn("sleep", output[0]["error"])
        self.assertTrue(device.closed)


class DevicePhotoManagementTests(unittest.TestCase):
    def _two_readers(self):
        from mook_mtp_wpd import _reader_id
        a, a_folder, a_image = make_device(png_data(), device_name="mooInk")
        b, b_folder, b_image = make_device(png_data(), device_name="mooInk")
        a._p_id = r"USB\VID_1F85&PID_4E16\MANAGER_A"
        b._p_id = r"USB\VID_1F85&PID_4E16\MANAGER_B"
        return (a, a_folder, a_image, _reader_id(a)), (b, b_folder, b_image, _reader_id(b))

    def test_list_each_connected_reader_photo_independently(self):
        (a, folder_a, _, id_a), (b, folder_b, _, id_b) = self._two_readers()
        folder_b.children[0].name = "second.jpg"
        first = list_images(reader_id=id_a, device_source=lambda:[a,b])
        second = list_images(reader_id=id_b, device_source=lambda:[a,b])
        self.assertEqual([f["name"] for f in first],["001.png"])
        self.assertEqual([f["name"] for f in second],["second.jpg"])

    def test_backup_only_targeted_reader_never_deletes(self):
        (a, fa, ia, id_a), (b, fb, ib, id_b) = self._two_readers()
        with tempfile.TemporaryDirectory() as tmp:
            result=backup_and_delete_one("001.png",tmp,reader_id=id_b,
                              device_source=lambda:[a,b],delete_after=False)
            self.assertIn("backup-only-wpd-verified",result)
            self.assertEqual(ia.download_count,0)
            self.assertEqual(ia.remove_count,0)
            self.assertEqual(ib.download_count,1)
            self.assertEqual(ib.remove_count,0)
            self.assertEqual(len(fa.children),1)
            self.assertEqual(len(fb.children),1)

    def test_delete_targeted_reader_always_preserves_other_reader(self):
        (a, fa, ia, id_a), (b, fb, ib, id_b) = self._two_readers()
        with tempfile.TemporaryDirectory() as tmp:
            result=backup_and_delete_one("001.png",tmp,reader_id=id_a,
                             device_source=lambda:[a,b],delete_after=True)
            self.assertIn("deleted+verified",result)
            self.assertEqual(ia.remove_count,1)
            self.assertEqual(ib.remove_count,0)
            self.assertEqual(len(fa.children),0)
            self.assertEqual(len(fb.children),1)
            self.assertTrue((Path(tmp)/"001.png").exists())

    def test_upload_targeted_reader_no_overwrite(self):
        (a, fa, ia, id_a), (b, fb, ib, id_b) = self._two_readers()
        with tempfile.TemporaryDirectory() as tmp:
            photo=Path(tmp)/"new_picture.png"
            photo.write_bytes(png_data())
            result=upload_one(photo,reader_id=id_b,device_source=lambda:[a,b])
            self.assertIn("uploaded+sha256-verified",result)
            self.assertEqual(len(fa.children),1)
            self.assertEqual(len(fb.children),2)
            with self.assertRaises(FileExistsError):
                upload_one(photo,reader_id=id_b,device_source=lambda:[a,b])
            self.assertEqual(len(fb.children),2)

    def test_invalid_reader_id_cannot_backup_delete_upload(self):
        (a, fa, ia, id_a), (b, fb, ib, id_b) = self._two_readers()
        with tempfile.TemporaryDirectory() as tmp:
            photo=Path(tmp)/"new_picture.png"
            photo.write_bytes(png_data())
            for action in (
                lambda:backup_and_delete_one("001.png",tmp,reader_id="f"*64,
                                             device_source=lambda:[a,b]),
                lambda:upload_one(photo,reader_id="f"*64,
                                  device_source=lambda:[a,b]),
            ):
                with self.assertRaises(QuietMtpUnavailable):
                    action()
            self.assertEqual(ia.remove_count,0)
            self.assertEqual(ib.remove_count,0)
            self.assertEqual(len(fa.children),1)
            self.assertEqual(len(fb.children),1)

    def test_same_size_image_content_changed_during_backup_not_deleted(self):
        class ChangingFile(FakeFile):
            def __init__(self, *args,**kwargs):
                super().__init__(*args,**kwargs)
                self.old_payload=self.payload
            def download_file(self,path):
                super().download_file(path)
                if self.download_count == 1:
                    # Same length: avoid size-only deletion check.
                    from PIL import Image
                    alt=bytearray(self.old_payload)
                    alt[-10] = (alt[-10]+1)%255
                    self.payload=bytes(alt)
        a, folder, old = make_device(png_data())
        a._p_id = "WPD_WATCHER_TEST"
        folder.children.clear()
        image=ChangingFile(folder,"001.png",png_data())
        folder.children.append(image)
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(RuntimeError,"已改變"):
                backup_and_delete_one("001.png",tmp,reader_id=None,
                                      device_source=lambda:[a],delete_after=True)
            self.assertEqual(image.remove_count,0)
            self.assertEqual(len(folder.children),1)


class CleanupOtherSleepImagesTests(unittest.TestCase):
    def _setup(self):
        from mook_mtp_wpd import _reader_id
        a, folder_a, _ = make_device(png_data())
        b, folder_b, _ = make_device(png_data())
        a._p_id = r"USB\\VID_1F85&PID_4E16\\CLEAN_A"
        b._p_id = r"USB\\VID_1F85&PID_4E16\\CLEAN_B"
        folder_a.children[0].name = "mooink_display_10.png"
        folder_b.children[0].name = "other_reader.png"
        other_a = FakeFile(folder_a, "holiday.jpg", png_data())
        folder_a.children.append(other_a)
        return a, folder_a, other_a, _reader_id(a), b, folder_b, _reader_id(b)

    def test_opt_in_cleanup_only_target_reader_and_backups_all_other_pictures(self):
        from mook_mtp_wpd import cleanup_other_sleep_images
        a, fa, other, id_a, b, fb, id_b = self._setup()
        extra=FakeFile(fa,"vacation.png",png_data())
        fa.children.append(extra)
        fa.children.append(FakeFolder("documents", 1))
        fa.children.append(FakeFile(fa,"notes.txt",b"leave me"))
        with tempfile.TemporaryDirectory() as tmp:
            removed=cleanup_other_sleep_images(
                "mooink_display_10.png",Path(tmp),reader_id=id_a,
                device_source=lambda:[a,b])
            self.assertEqual(removed,["holiday.jpg","vacation.png"])
            self.assertEqual(other.remove_count,1)
            self.assertEqual(extra.remove_count,1)
            self.assertEqual([x.name for x in fa.children],
                             ["mooink_display_10.png","documents","notes.txt"])
            self.assertEqual([x.name for x in fb.children],["other_reader.png"])
            self.assertEqual((Path(tmp)/id_a/"holiday.jpg").read_bytes(),other.payload)
            self.assertEqual((Path(tmp)/id_a/"vacation.png").read_bytes(),extra.payload)
            self.assertEqual(cleanup_other_sleep_images(
                "mooink_display_10.png",Path(tmp),reader_id=id_a,
                device_source=lambda:[a,b]),[])

    def test_refuses_cleanup_without_verified_keep_image(self):
        from mook_mtp_wpd import cleanup_other_sleep_images
        a,fa,other,id_a,b,fb,id_b = self._setup()
        with tempfile.TemporaryDirectory() as tmp:
            for keep in ("mooink_display_13.png","holiday.jpg"):
                with self.assertRaises((ValueError,QuietMtpUnavailable)):
                    cleanup_other_sleep_images(
                        keep,Path(tmp),reader_id=id_a,device_source=lambda:[a,b])
            self.assertEqual(other.remove_count,0)
            self.assertEqual(len(fa.children),2)

    def test_no_reader_id_never_deletes_anything(self):
        from mook_mtp_wpd import cleanup_other_sleep_images
        a,fa,other,id_a,b,fb,id_b = self._setup()
        with tempfile.TemporaryDirectory() as tmp:
            for rid in (None,"f"*64,"abc"):
                with self.assertRaises((ValueError,QuietMtpUnavailable)):
                    cleanup_other_sleep_images("mooink_display_10.png",tmp,
                        reader_id=rid,device_source=lambda:[a,b])
            self.assertEqual(other.remove_count,0)

    def test_corrupt_backup_stops_other_deletions(self):
        from mook_mtp_wpd import cleanup_other_sleep_images
        a,fa,other,id_a,b,fb,id_b = self._setup()
        extra=FakeFile(fa,"later.png",png_data())
        fa.children.append(extra)
        other.size += 1
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(RuntimeError):
                cleanup_other_sleep_images("mooink_display_10.png",tmp,
                    reader_id=id_a,device_source=lambda:[a,b])
            self.assertEqual(other.remove_count,0)
            self.assertEqual(extra.remove_count,0)
