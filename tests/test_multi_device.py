import hashlib
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace

from mooink_multi_device import (DeviceRegistry, MultiDeviceController,
                                 update_selected_docx, ManualReaderUnavailable,
                                 manual_reader_diagnostic)
from mooink_display_watcher import Settings


class DeviceProfilesTest(unittest.TestCase):
    def test_id_to_size_persists_between_restarts(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "profiles.json"
            reg = DeviceRegistry(path)
            id10 = "a" * 64
            id13 = "b" * 64
            reg.assign(id10, "10.3", "Readmoo")
            reg.assign(id13, "13.3", "Readmoo")
            new = DeviceRegistry(path)
            self.assertEqual(new.lookup(id10)["size"], "10.3")
            self.assertEqual(new.lookup(id13)["size"], "13.3")
            self.assertNotEqual(new.lookup(id10), new.lookup(id13))

    def test_unknown_device_requires_explicit_assignment(self):
        with tempfile.TemporaryDirectory() as tmp:
            reg = DeviceRegistry(Path(tmp) / "profiles.json")
            calls = []
            readers = [
                {"id": "a"*64,"name": "mooInk","ready":True,"pnp":"reader a"},
                {"id": "b"*64,"name": "mooInk","ready":True,"pnp":"reader b"},
            ]
            reg.assign("a"*64, "10.3", "mooInk")
            class FakeProcessor:
                def check(self, settings, **kwargs):
                    calls.append((settings.size, settings.pico_port))
                    return "verified"
            manager = MultiDeviceController(reg, lambda rid: FakeProcessor(),
                                            lambda: readers, lambda event,pnp:False)
            statuses = {}
            manager.check(Settings(watch_dir=tmp, enable_usb=True, size="13.3", pico_port="COM6"),
                          status=lambda k,m: statuses.__setitem__(k,m))
            self.assertEqual(calls, [("10.3","")])
            self.assertIn("尚未配對", statuses["b"*64])

    def test_two_registered_readers_use_two_correct_sizes(self):
        with tempfile.TemporaryDirectory() as tmp:
            reg = DeviceRegistry(Path(tmp)/"profiles.json")
            ids = ["a"*64, "b"*64]
            reg.assign(ids[0], "10.3", "mooInk")
            reg.assign(ids[1], "13.3", "mooInk")
            rows = [{"id": rid,"name":"mooInk","ready":True,"pnp":rid} for rid in ids]
            calls = []
            class FakeProcessor:
                def __init__(self, rid): self.rid = rid
                def check(self, settings, **kwargs):
                    calls.append((self.rid, settings.size, settings.pico_port))
                    return "verified"
            manager = MultiDeviceController(reg, lambda rid: FakeProcessor(rid),
                                            lambda: rows, lambda event,pnp:False)
            manager.check(Settings(watch_dir=tmp, enable_usb=True, size="10.3", pico_port="COM6"))
            self.assertEqual(calls, [(ids[0],"10.3",""),(ids[1],"13.3","")])
            rows.pop()
            manager.check(Settings(watch_dir=tmp, enable_usb=True, size="10.3", pico_port="COM6"))
            self.assertNotIn(ids[1],manager.processors)
            rows.append({"id":ids[1], "name":"mooInk","ready":True,"pnp":ids[1]})
            manager.check(Settings(watch_dir=tmp, enable_usb=True, size="10.3", pico_port="COM6"))
            self.assertEqual(calls[-1], (ids[1],"13.3",""))

    def test_verified_metadata_persists(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/"profiles.json"
            rid = "a"*64
            r = DeviceRegistry(path)
            r.assign(rid, "10.3", "mooInk")
            r.success(rid, {"updated_at":12345.0,"word_mtime":12300.0,
                            "word_name":"mooink_display_10.docx",
                            "png_name":"mooink_display_10.png",
                            "png_bytes":b"PNG mock data"}, "10.3", "mooInk")
            loaded = DeviceRegistry(path)
            self.assertEqual(loaded.lookup(rid)["word_mtime"], 12300.0)
            self.assertEqual(loaded.preview_file(rid).read_bytes(), b"PNG mock data")
            loaded.assign(rid, "13.3", "mooInk")
            self.assertNotIn("last_updated", loaded.lookup(rid))
            self.assertFalse(loaded.preview_file(rid).exists())


if __name__ == "__main__":
    unittest.main()


    def test_multi_device_forwards_independent_trigger_settings(self):
        with tempfile.TemporaryDirectory() as tmp:
            reg = DeviceRegistry(Path(tmp)/"profiles.json")
            ids = ("a"*64, "b"*64)
            reg.assign(ids[0], "10.3", "A")
            reg.assign(ids[1], "13.3", "B")
            seen = []
            rows = [{"id": rid, "name":rid[:4], "ready":True, "pnp":rid} for rid in ids]
            class Fake:
                def __init__(self, rid): self.rid=rid
                def check(self, settings, **kwargs):
                    seen.append((self.rid, settings.size,
                                 settings.update_on_connect, settings.update_on_file_change))
                    return "ok"
            mgr = MultiDeviceController(reg, lambda rid: Fake(rid),
                                        lambda: rows, lambda a,b:False)
            cfg = Settings(watch_dir=tmp, enable_usb=True,
                           update_on_connect=False, update_on_file_change=True)
            mgr.check(cfg)
            self.assertEqual(seen, [(ids[0], "10.3",False,True),
                                    (ids[1], "13.3",False,True)])


    def test_selected_docx_shared_across_independent_reader_sizes(self):
        with tempfile.TemporaryDirectory() as tmp:
            custom = Path(tmp) / "weekly-report.docx"
            custom.write_bytes(b"document")
            reg = DeviceRegistry(Path(tmp) / "profiles.json")
            ids = ("a" * 64, "b" * 64)
            reg.assign(ids[0], "10.3", "A")
            reg.assign(ids[1], "13.3", "B")
            seen = []
            class Fake:
                def __init__(self, rid):
                    self.rid = rid
                def check(self, settings, **kwargs):
                    seen.append((settings.paths()[0], settings.paths()[1].name))
                    return "ok"
            readers = [{"id": rid, "name": "Mooink", "ready": True, "pnp": rid}
                       for rid in ids]
            controller = MultiDeviceController(reg, Fake, lambda: readers,
                                               lambda path, identity: False)
            # Factory accepts reader ID and Fake stores it; source remains shared.
            controller.check(Settings(watch_dir=tmp, enable_usb=True,
                                      custom_docx=str(custom)))
            self.assertEqual(seen, [(custom, "mooink_display_10.png"),
                                    (custom, "mooink_display_13.png")])


class ManualSelectedDocxTests(unittest.TestCase):
    def test_manual_file_is_separate_from_auto_and_uses_size_specific_pngs(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            auto = directory / "mooink_display_10.docx"
            manual = directory / "completely-different.docx"
            auto.write_bytes(b"automatic document")
            manual.write_bytes(b"manual document")
            auto_png = directory / "mooink_display_10.png"
            auto_png.write_bytes(b"automatic PNG preserved")
            reg = DeviceRegistry(directory / "profiles.json")
            readers = [{"id":"a"*64,"name":"A","ready":True},
                       {"id":"b"*64,"name":"B","ready":True}]
            reg.assign(readers[0]["id"],"10.3","A")
            reg.assign(readers[1]["id"],"13.3","B")
            rendered, uploaded, delivered = [], [], []
            def render(src, target, size):
                rendered.append((src.read_bytes(), target.name, size))
                target.write_bytes(("manual-PNG-" + size).encode())
            def upload(reader_id, png, size, log):
                uploaded.append((reader_id, png.name, png.read_bytes()))
                return {"changed":True}
            summary = update_selected_docx(
                manual, registry=reg, discover=lambda:readers, render=render,
                upload=upload, on_success=lambda rid,data:delivered.append((rid,data)))
            self.assertEqual(len(summary["successful"]), 2)
            self.assertEqual(len(summary["failed"]), 0)
            self.assertEqual(rendered,
                             [(b"manual document","mooink_display_10.png","10.3"),
                              (b"manual document","mooink_display_13.png","13.3")])
            self.assertEqual([(x[0],x[1]) for x in uploaded],
                             [(readers[0]["id"],"mooink_display_10.png"),
                              (readers[1]["id"],"mooink_display_13.png")])
            self.assertEqual(len(delivered),2)
            self.assertEqual(auto.read_bytes(),b"automatic document")
            self.assertEqual(manual.read_bytes(),b"manual document")
            self.assertEqual(auto_png.read_bytes(),b"automatic PNG preserved")
            self.assertEqual(Settings(watch_dir=tmp).paths()[0], auto)
            self.assertEqual(reg.lookup(readers[1]["id"])["word_name"],manual.name)

    def test_manual_update_works_independently_of_usb_auto_switches(self):
        with tempfile.TemporaryDirectory() as tmp:
            manual = Path(tmp)/"one-off.docx"
            manual.write_bytes(b"oneoff")
            reg = DeviceRegistry(Path(tmp)/"profiles.json")
            rid = "a"*64
            reg.assign(rid,"10.3","reader")
            cfg = Settings(watch_dir=tmp,enable_usb=False,
                           update_on_connect=False,update_on_file_change=False)
            cfg.validate()
            result = update_selected_docx(
                manual,registry=reg,
                discover=lambda:[{"id":rid,"name":"reader","ready":True}],
                render=lambda src,dst,size:dst.write_bytes(b"png"),
                upload=lambda rid,png,size,log:{"changed":False})
            self.assertEqual(result["successful"],[rid])

    def test_unpaired_reader_is_not_written(self):
        with tempfile.TemporaryDirectory() as tmp:
            manual = Path(tmp)/"one-off.docx"
            manual.write_bytes(b"docx")
            reg = DeviceRegistry(Path(tmp)/"profiles.json")
            called = []
            with self.assertRaisesRegex(ManualReaderUnavailable,"尺寸配對"):
                update_selected_docx(manual,registry=reg,
                    discover=lambda:[{"id":"a"*64,"name":"reader","ready":True}],
                    render=lambda *args:called.append("render"),
                    upload=lambda *args:called.append("upload"))
            self.assertEqual(called,[])

    def test_manual_failure_does_not_record_false_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            manual = Path(tmp)/"one-off.docx"
            manual.write_bytes(b"docx")
            reg = DeviceRegistry(Path(tmp)/"profiles.json")
            rid = "a"*64
            reg.assign(rid,"13.3","reader")
            results = update_selected_docx(
                manual,registry=reg,
                discover=lambda:[{"id":rid,"name":"reader","ready":True}],
                render=lambda src,dst,size:dst.write_bytes(b"png"),
                upload=lambda rid,png,size,log: (_ for _ in ()).throw(OSError("USB lost")))
            self.assertEqual(results["successful"],[])
            self.assertEqual(results["failed"],[rid])
            self.assertNotIn("last_updated",reg.lookup(rid))

    def test_manual_document_changed_during_render_blocks_transfer(self):
        with tempfile.TemporaryDirectory() as tmp:
            manual = Path(tmp)/"one-off.docx"
            manual.write_bytes(b"first")
            reg = DeviceRegistry(Path(tmp)/"profiles.json")
            rid = "a"*64
            reg.assign(rid,"10.3","reader")
            uploaded = []
            def render(src,dst,size):
                dst.write_bytes(b"png")
                manual.write_bytes(b"second")
            with self.assertRaisesRegex(RuntimeError,"轉圖期間被修改"):
                update_selected_docx(manual,registry=reg,
                    discover=lambda:[{"id":rid,"name":"reader","ready":True}],
                    render=render,
                    upload=lambda *args:uploaded.append(1))
            self.assertEqual(uploaded,[])
            self.assertNotIn("last_updated",reg.lookup(rid))

    def test_manual_source_validation(self):
        with tempfile.TemporaryDirectory() as tmp:
            reg = DeviceRegistry(Path(tmp)/"profiles.json")
            wrong = Path(tmp)/"something.txt"
            wrong.write_bytes(b"bad")
            for selected in (wrong,Path(tmp)/"missing.docx"):
                with self.assertRaises(ValueError):
                    update_selected_docx(selected,registry=reg,
                        discover=lambda:[],render=lambda *args:None,
                        upload=lambda *args:None)


    def test_manual_diagnostic_when_wpd_has_no_reader(self):
        with tempfile.TemporaryDirectory() as tmp:
            reg = DeviceRegistry(Path(tmp)/"profiles.json")
            error = manual_reader_diagnostic([], reg)
            self.assertEqual(error.reason, "not_found")
            self.assertIn("WPD", str(error))
            selected = Path(tmp)/"source.docx"
            selected.write_bytes(b"test document")
            with self.assertRaises(ManualReaderUnavailable) as raised:
                update_selected_docx(selected, registry=reg,
                    discover=lambda:[], render=lambda *args: None,
                    upload=lambda *args: None)
            self.assertEqual(raised.exception.reason, "not_found")

    def test_manual_diagnostic_unpaired_device_proposes_pairing(self):
        with tempfile.TemporaryDirectory() as tmp:
            reg = DeviceRegistry(Path(tmp)/"profiles.json")
            manual = Path(tmp)/"selected.docx"
            manual.write_bytes(b"word")
            readers = [{"id":"a"*64,"name":"Mooink","ready":True}]
            error = manual_reader_diagnostic(readers, reg)
            self.assertEqual(error.reason, "unpaired")
            self.assertEqual(error.readers, readers)
            with self.assertRaises(ManualReaderUnavailable) as raised:
                update_selected_docx(manual, registry=reg, discover=lambda:readers,
                    render=lambda *args:None,upload=lambda *args:None)
            self.assertEqual(raised.exception.reason, "unpaired")
            reg.assign("a"*64, "10.3", "Mooink")
            self.assertIsNone(manual_reader_diagnostic(readers, reg))
            delivered = update_selected_docx(manual, registry=reg, discover=lambda:readers,
                render=lambda src,dst,size:dst.write_bytes(b"png"),
                upload=lambda *args:None)
            self.assertEqual(delivered["successful"],["a"*64])

    def test_manual_diagnostic_unready_reader_shows_actual_wpd_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            reg = DeviceRegistry(Path(tmp)/"profiles.json")
            readers = [{"id":"a"*64,"name":"Mooink","ready":False,
                        "error":"sleep 資料夾不存在"}]
            error = manual_reader_diagnostic(readers, reg)
            self.assertEqual(error.reason, "not_ready")
            self.assertIn("sleep 資料夾不存在", str(error))

    def test_manual_with_registered_reader_and_another_unpaired_still_succeeds(self):
        with tempfile.TemporaryDirectory() as tmp:
            reg = DeviceRegistry(Path(tmp)/"profiles.json")
            reg.assign("a"*64, "10.3", "A")
            docx = Path(tmp)/"manual.docx"
            docx.write_bytes(b"docx")
            devices = [{"id":"a"*64,"name":"A","ready":True},
                       {"id":"b"*64,"name":"B","ready":True}]
            result = update_selected_docx(docx,registry=reg,discover=lambda:devices,
                    render=lambda src,dst,size:dst.write_bytes(b"png"),
                    upload=lambda *args:None)
            self.assertEqual(result["successful"],["a"*64])
            self.assertEqual(result["failed"],[])
            self.assertNotIn("size",reg.lookup("b"*64))


    def test_manually_selected_page_is_recorded_without_pollution(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "multi.docx"
            source.write_bytes(b"three-page-file")
            reg = DeviceRegistry(Path(tmp)/"paired.json")
            rid="a"*64
            reg.assign(rid,"10.3","A")
            captured=[]
            result = update_selected_docx(
                source,registry=reg,discover=lambda:[{"id":rid,"name":"A","ready":True}],
                render=lambda src,dst,size:dst.write_bytes(b"page 3 png"),
                upload=lambda *args:None, page_number=3,
                on_success=lambda _,data:captured.append(data))
            self.assertEqual(result["successful"],[rid])
            self.assertEqual(captured[0]["page_number"],3)
            self.assertIn("第3頁",reg.lookup(rid)["word_name"])
            self.assertEqual(reg.lookup(rid)["png_name"],"mooink_display_10.png")

