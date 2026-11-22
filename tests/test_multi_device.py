import hashlib
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace

from mooink_multi_device import DeviceRegistry, MultiDeviceController
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


if __name__ == "__main__":
    unittest.main()
