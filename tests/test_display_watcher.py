import tempfile
import unittest
from pathlib import Path
from mooink_display_watcher import (Settings, Processor, sha256,
                                    initialize_workfiles, TEMPLATE_DIR, WORK_FILES)

class WatcherTests(unittest.TestCase):
    def test_size_specific_names(self):
        with tempfile.TemporaryDirectory() as tmp:
            for size, tag in (("10.3","10"),("13.3","13")):
                s=Settings(watch_dir=tmp,size=size).validate()
                self.assertEqual([p.name for p in s.paths()],
                                 ["mooink_display_"+tag+".docx","mooink_display_"+tag+".png"])
    def test_local_render_and_restart_skip_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            s=Settings(watch_dir=tmp,size="10.3")
            source,output=s.paths()
            source.write_bytes(b"docx")
            calls=[]
            def render(src,dst,size):
                calls.append(size)
                dst.write_bytes(b"png")
            state=Path(tmp)/"state.json"
            p=Processor(state, render)
            self.assertIn("尚未啟用", p.check(s))
            self.assertIn("無更新", p.check(s))
            self.assertIn("無更新", Processor(state, render).check(s))
            self.assertEqual(calls,["10.3"])
            self.assertTrue(output.is_file())

    def test_render_failure_retries_without_marking_done(self):
        with tempfile.TemporaryDirectory() as tmp:
            s=Settings(watch_dir=tmp)
            s.paths()[0].write_bytes(b"docx")
            attempts=[]
            def fail(*args):
                attempts.append(1)
                raise OSError("render failed")
            p=Processor(Path(tmp)/"state.json", fail)
            for _ in range(2):
                with self.assertRaises(OSError):
                    p.check(s)
            self.assertEqual(len(attempts),2)
            self.assertEqual(p.rendered,{})

    def test_workfiles_copied_from_tracked_templates_without_overwriting(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            existing = root / "mooink_display_10.docx"
            existing.write_bytes(b"my edited Word file")
            created = initialize_workfiles(root)
            self.assertEqual(set(created), set(WORK_FILES) - {existing.name})
            self.assertEqual(existing.read_bytes(), b"my edited Word file")
            for name in created:
                self.assertEqual((root / name).read_bytes(),
                                 (TEMPLATE_DIR / name).read_bytes())
            self.assertEqual(initialize_workfiles(root), [])
            self.assertEqual(existing.read_bytes(), b"my edited Word file")

    def test_all_four_templates_valid(self):
        import zipfile
        from PIL import Image
        for tag, size in (("10", (1404, 1872)), ("13", (1650, 2200))):
            with zipfile.ZipFile(TEMPLATE_DIR / ("mooink_display_" + tag + ".docx")) as doc:
                self.assertIsNone(doc.testzip())
                self.assertIn("word/document.xml", doc.namelist())
            with Image.open(TEMPLATE_DIR / ("mooink_display_" + tag + ".png")) as img:
                self.assertEqual(img.size, size)
                img.load()

    def test_template_folder_is_not_a_work_folder(self):
        with self.assertRaises(ValueError):
            initialize_workfiles(TEMPLATE_DIR)
        with self.assertRaises(ValueError):
            Settings(watch_dir=str(TEMPLATE_DIR)).validate()

    def test_recreated_png_is_not_mistaken_for_current_render(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = Settings(watch_dir=tmp)
            source, output = s.paths()
            source.write_bytes(b"docx")
            attempts = []
            def render(src, dst, size):
                attempts.append(size)
                dst.write_bytes(b"rendered PNG")
            p = Processor(Path(tmp) / "state.json", render)
            p.check(s)
            output.write_bytes(b"placeholder PNG")
            p.check(s)
            self.assertEqual(attempts, ["10.3", "10.3"])
            self.assertEqual(output.read_bytes(), b"rendered PNG")


    def test_usb_upload_before_wake_and_unchanged_skip(self):
        from unittest.mock import Mock
        with tempfile.TemporaryDirectory() as tmp:
            s=Settings(watch_dir=tmp,enable_usb=True)
            s.paths()[0].write_bytes(b"Word")
            actions=[]
            def render(src,dst,size):
                dst.write_bytes(b"png")
            def upload(settings, source, log):
                actions.append("upload")
                self.assertEqual(source.read_bytes(),b"png")
                return {"changed":actions.count("upload")==1}
            def wake(port, log):
                actions.append("wake")
                return "Space 已送出且 CDC 回顯一致"
            p=Processor(Path(tmp)/"state.json",render,upload,wake,probe=lambda: None)
            self.assertIn("已回讀驗證",p.check(s))
            self.assertIn("無更新",p.check(s))
            self.assertEqual(actions,["upload","wake"])
            p.check(s,force=True)
            self.assertEqual(actions[-2:],["upload","wake"])

    def test_upload_failure_never_wakes(self):
        from unittest.mock import Mock
        with tempfile.TemporaryDirectory() as tmp:
            s=Settings(watch_dir=tmp,enable_usb=True)
            s.paths()[0].write_bytes(b"Word")
            wake=Mock()
            upload=Mock(side_effect=OSError("offline"))
            p=Processor(Path(tmp)/"state.json",lambda src,dst,size:dst.write_bytes(b"png"),upload,wake,probe=lambda: None)
            for _ in range(2):
                with self.assertRaises(OSError):
                    p.check(s)
            wake.assert_not_called()
            self.assertEqual(upload.call_count,2)


    def test_watcher_wake_uses_plain_space(self):
        from unittest.mock import patch
        from types import SimpleNamespace
        from mooink_display_watcher import pico_wake
        with patch("pico2w_usb2bluetooth.ports",return_value=[SimpleNamespace(device="COM6")]), \
             patch("pico2w_usb2bluetooth.send_space",return_value=True) as send:
            self.assertIn("CDC 回顯一致",pico_wake("COM6",lambda msg:None))
            send.assert_called_once_with("COM6")

    def test_watcher_wake_error_is_not_success(self):
        from unittest.mock import patch
        from types import SimpleNamespace
        from mooink_display_watcher import pico_wake
        with patch("pico2w_usb2bluetooth.ports",return_value=[SimpleNamespace(device="COM6")]), \
             patch("pico2w_usb2bluetooth.send_space",side_effect=TimeoutError("no echo")):
            self.assertIn("傳送未確認",pico_wake("COM6",lambda msg:None))


    def test_hotplug_forces_render_even_without_document_change(self):
        from unittest.mock import Mock
        with tempfile.TemporaryDirectory() as tmp:
            s = Settings(watch_dir=tmp, enable_usb=True, pico_port="")
            src, dst = s.paths()
            src.write_bytes(b"Word")
            online = False
            def probe():
                if not online:
                    raise OSError("not connected")
            render = Mock(side_effect=lambda src,dst,size: dst.write_bytes(b"png"))
            upload = Mock(return_value={"changed": False})
            wake = Mock(return_value="Pico disabled")
            delivered = []
            p = Processor(Path(tmp)/"state.json", render, upload, wake, probe)
            self.assertIn("等待", p.check(s, on_success=delivered.append))
            self.assertEqual(render.call_count, 1)
            upload.assert_not_called()
            online = True
            p.check(s, scheduled=False, on_success=delivered.append)
            self.assertEqual(render.call_count, 2)
            self.assertEqual(upload.call_count, 1)
            wake.assert_not_called()  # explicitly disabled Pico
            self.assertEqual(delivered[0]["png_bytes"], b"png")
            self.assertEqual(delivered[0]["word_mtime"], src.stat().st_mtime)
            self.assertIsNone(p.check(s, scheduled=False, on_success=delivered.append))
            online = False
            self.assertIn("離線", p.check(s, scheduled=False))
            online = True
            p.check(s, scheduled=False, on_success=delivered.append)
            self.assertEqual(render.call_count, 3)
            self.assertEqual(upload.call_count, 2)
            self.assertEqual(len(delivered), 2)

    def test_hotplug_failed_transfer_retries_without_false_success(self):
        from unittest.mock import Mock
        with tempfile.TemporaryDirectory() as tmp:
            s = Settings(watch_dir=tmp, enable_usb=True)
            s.paths()[0].write_bytes(b"Word")
            upload = Mock(side_effect=[OSError("disconnected"), {"changed": False}])
            delivered = Mock()
            wake = Mock(return_value="Pico absent")
            p = Processor(Path(tmp)/"state.json",
                          lambda src,dst,size: dst.write_bytes(b"png"),
                          upload, wake, lambda: None)
            with self.assertRaises(OSError):
                p.check(s, on_success=delivered)
            delivered.assert_not_called()
            wake.assert_not_called()
            p.check(s, scheduled=False, on_success=delivered)
            delivered.assert_called_once()
            wake.assert_called_once()


    def test_quick_replug_event_forces_update_with_same_identity(self):
        from unittest.mock import Mock
        with tempfile.TemporaryDirectory() as tmp:
            s = Settings(watch_dir=tmp, enable_usb=True)
            s.paths()[0].write_bytes(b"Word")
            render = Mock(side_effect=lambda src,dst,size: dst.write_bytes(b"png"))
            upload = Mock(return_value={"changed": False})
            wake = Mock(side_effect=OSError("Pico removed"))
            path = r"\\?\usb#vid_1f85&pid_4e16#reader#{guid}"
            p = Processor(Path(tmp)/"state.json", render, upload, wake, lambda: path)
            delivered = []
            p.check(s, on_success=delivered.append)
            self.assertIsNone(p.check(s, scheduled=False, device_events=["pico"]))
            self.assertIn("已回讀驗證", p.check(s, scheduled=False,
                device_events=[path], on_success=delivered.append))
            self.assertEqual(render.call_count, 2)
            self.assertEqual(len(delivered), 2)
            self.assertEqual(wake.call_count, 2)

    def test_changed_word_with_same_png_updates_word_timestamp(self):
        from unittest.mock import Mock
        import os
        with tempfile.TemporaryDirectory() as tmp:
            s = Settings(watch_dir=tmp, enable_usb=True, pico_port="")
            source = s.paths()[0]
            source.write_bytes(b"Word")
            p = Processor(Path(tmp)/"state.json",
                          lambda src,dst,size: dst.write_bytes(b"same png"),
                          Mock(return_value={"changed": False}), Mock(), lambda: "reader")
            delivered = []
            p.check(s, on_success=delivered.append)
            stamp = source.stat().st_mtime + 100
            os.utime(source, (stamp, stamp))
            p.check(s, on_success=delivered.append)
            self.assertEqual(len(delivered), 2)
            self.assertEqual(delivered[-1]["word_mtime"], source.stat().st_mtime)
            p.wake.assert_not_called()


    def test_trigger_configuration_defaults_and_validation(self):
        with tempfile.TemporaryDirectory() as tmp:
            default = Settings(watch_dir=tmp, enable_usb=True).validate()
            self.assertTrue(default.update_on_connect)
            self.assertTrue(default.update_on_file_change)
            for plug, poll in ((True, False), (False, True), (True, True)):
                Settings(watch_dir=tmp, enable_usb=True,
                         update_on_connect=plug,
                         update_on_file_change=poll).validate()
            with self.assertRaises(ValueError):
                Settings(watch_dir=tmp, enable_usb=True,
                         update_on_connect=False,
                         update_on_file_change=False).validate()

    def test_hotplug_only_ignores_word_polling(self):
        from unittest.mock import Mock
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "mooink_display_10.docx"
            src.write_bytes(b"initial")
            online = False
            def probe():
                if not online:
                    raise OSError("offline")
                return "device-a"
            render = Mock(side_effect=lambda source, output, size: output.write_bytes(b"PNG"))
            upload = Mock(return_value={"changed": False})
            success = []
            settings = Settings(watch_dir=tmp, enable_usb=True, pico_port="",
                                update_on_connect=True, update_on_file_change=False)
            p = Processor(Path(tmp)/"state.json", render, upload, Mock(), probe)
            # Not even an initial Word check until a USB arrival.
            self.assertIsNone(p.check(settings, scheduled=True, on_success=success.append))
            render.assert_not_called()
            online = True
            p.check(settings, scheduled=False, on_success=success.append)
            self.assertEqual((render.call_count, upload.call_count), (1, 1))
            src.write_bytes(b"edited while still attached")
            self.assertIsNone(p.check(settings, scheduled=True, on_success=success.append))
            self.assertEqual((render.call_count, upload.call_count), (1, 1))
            online = False
            p.check(settings, scheduled=False)
            online = True
            p.check(settings, scheduled=False, on_success=success.append)
            self.assertEqual((render.call_count, upload.call_count), (2, 2))
            self.assertEqual(len(success), 2)

    def test_file_poll_only_never_uploads_on_reconnect_without_change(self):
        from unittest.mock import Mock
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "mooink_display_10.docx"
            src.write_bytes(b"initial")
            online = False
            def probe():
                if not online:
                    raise OSError("offline")
                return "reader"
            render = Mock(side_effect=lambda source, output, size: output.write_bytes(b"PNG"))
            upload = Mock(return_value={"changed": False})
            successes = []
            settings = Settings(watch_dir=tmp, enable_usb=True, pico_port="",
                                update_on_connect=False, update_on_file_change=True)
            state = Path(tmp)/"state.json"
            p = Processor(state, render, upload, Mock(), probe)
            p.check(settings, scheduled=True)
            self.assertEqual(render.call_count, 1)
            online = True
            self.assertIsNone(p.check(settings, scheduled=False))
            upload.assert_not_called()  # insertion alone must not transmit
            p.check(settings, scheduled=True, on_success=successes.append)
            self.assertEqual(upload.call_count, 1)
            p.check(settings, scheduled=True, on_success=successes.append)
            self.assertEqual(upload.call_count, 1)
            online = False
            p.check(settings, scheduled=False)
            online = True
            self.assertIsNone(p.check(settings, scheduled=False))
            p.check(settings, scheduled=True, on_success=successes.append)
            self.assertEqual(upload.call_count, 1)
            # Restart still remembers the last verified Word version.
            restarted = Processor(state, render, upload, Mock(), probe)
            restarted.check(settings, scheduled=True, on_success=successes.append)
            self.assertEqual(upload.call_count, 1)
            src.write_bytes(b"edited")
            restarted.check(settings, scheduled=True, on_success=successes.append)
            self.assertEqual(upload.call_count, 2)
            self.assertEqual(len(successes), 2)

    def test_both_triggers_independent_and_failed_upload_retries(self):
        from unittest.mock import Mock
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp)/"mooink_display_10.docx"
            src.write_bytes(b"first")
            online = True
            def probe():
                if not online:
                    raise OSError("offline")
                return "reader"
            upload = Mock(side_effect=[{"changed": True}, OSError("link gone"),
                                       {"changed": False}, {"changed": False}])
            render = Mock(side_effect=lambda source, output, size: output.write_bytes(b"PNG"))
            p = Processor(Path(tmp)/"state.json", render, upload, Mock(), probe)
            settings = Settings(watch_dir=tmp, enable_usb=True, pico_port="",
                                update_on_connect=True, update_on_file_change=True)
            successes = []
            p.check(settings, scheduled=False, on_success=successes.append)
            src.write_bytes(b"updated")
            with self.assertRaises(OSError):
                p.check(settings, scheduled=True, on_success=successes.append)
            self.assertEqual(len(successes), 1)  # no false confirmation
            # Retry on recovery (without waiting for file interval).
            p.check(settings, scheduled=False, on_success=successes.append)
            self.assertEqual(len(successes), 2)
            online = False
            p.check(settings, scheduled=False)
            online = True
            p.check(settings, scheduled=False, on_success=successes.append)
            self.assertEqual(len(successes), 3)
            self.assertEqual(upload.call_count, 4)

    def test_same_vid_different_serial_does_not_trigger_other_reader(self):
        from mooink_display_watcher import reader_event_matches
        arrival = r"\\?\USB#VID_1F85&PID_4E16#SERIAL_A#{guid}"
        reader_a = r"USB\VID_1F85&PID_4E16\SERIAL_A"
        reader_b = r"USB\VID_1F85&PID_4E16\SERIAL_B"
        self.assertTrue(reader_event_matches(arrival, reader_a))
        self.assertFalse(reader_event_matches(arrival, reader_b))
        self.assertFalse(reader_event_matches(arrival, ""))
