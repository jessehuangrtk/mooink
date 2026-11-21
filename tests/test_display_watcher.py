import tempfile
import unittest
from pathlib import Path
from dataclasses import replace
from mooink_display_watcher import Settings, Processor, sha256

class WatcherTests(unittest.TestCase):
    def test_size_specific_names(self):
        with tempfile.TemporaryDirectory() as tmp:
            for size, tag in (("10.3","10"),("13.3","13")):
                s=Settings(watch_dir=tmp,size=size).validate()
                self.assertEqual([p.name for p in s.paths()],
                                 ["mooink_display_"+tag+".docx","mooink_display_"+tag+".png"])
    def test_validate_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            s=Settings(watch_dir=tmp,wifi_share=r"\\192.168.1.140\shared\sleep")
            self.assertIs(s.validate(),s)
            with self.assertRaises(ValueError):
                replace(s,mooink_ip="192.168.1.141").validate()
            with self.assertRaises(ValueError):
                replace(s,wifi_share=r"\\192.168.1.140\shared\documents").validate()
    def test_local_render_without_share_never_wakes(self):
        with tempfile.TemporaryDirectory() as tmp:
            s=Settings(watch_dir=tmp,size="10.3")
            source,output=s.paths()
            source.write_bytes(b"docx")
            calls=[]
            def render(src,dst,size):
                calls.append(size)
                dst.write_bytes(b"png")
            def forbidden(*args):
                self.fail("Unexpected transfer/wake")
            p=Processor(Path(tmp)/"state.json", render, forbidden, forbidden)
            p.check(s)
            p.check(s)
            self.assertEqual(calls,["10.3"])
            self.assertTrue(output.is_file())
    def test_upload_verified_before_wake_and_no_repeat_on_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            s=Settings(watch_dir=tmp,size="13.3",wifi_share=r"\\192.168.1.140\shared\sleep")
            source,output=s.paths()
            source.write_bytes(b"docx")
            calls=[]
            def render(src,dst,size):
                calls.append(("render",size))
                dst.write_bytes(b"png")
            def upload(settings,src,digest):
                self.assertEqual(sha256(src),digest)
                calls.append(("upload",src.name))
                return str(src)
            def wake(port,log):
                calls.append(("wake",port))
                return "skip"
            state=Path(tmp)/"state.json"
            p=Processor(state,render,upload,wake)
            p.check(s)
            Processor(state,render,upload,wake).check(s)
            self.assertEqual(calls,[("render","13.3"),("upload","mooink_display_13.png"),("wake","COM6")])
    def test_failed_upload_retries_without_wake(self):
        with tempfile.TemporaryDirectory() as tmp:
            s=Settings(watch_dir=tmp,wifi_share=r"\\192.168.1.140\shared\sleep")
            s.paths()[0].write_bytes(b"docx")
            attempts=[]
            def render(src,dst,size):
                dst.write_bytes(b"png")
            def fail(*args):
                attempts.append(1)
                raise OSError("offline")
            def wake(*args):
                self.fail("F12 must not be sent")
            p=Processor(Path(tmp)/"state.json",render,fail,wake)
            for _ in range(2):
                with self.assertRaises(OSError):
                    p.check(s)
            self.assertEqual(len(attempts),2)
            self.assertEqual(p.sent,{})
