import tempfile
import unittest
from pathlib import Path
from mooink_display_watcher import (Settings, Processor, sha256,
                                    initialize_workfiles, TEMPLATE_DIR, WORK_FILES)

def fake_pdf(png, pdf, size):
    pdf.write_bytes(b"%PDF-test " + png.read_bytes())


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
            p=Processor(state, render, fake_pdf)
            self.assertIn("尚未啟用", p.check(s))
            self.assertIn("無更新", p.check(s))
            self.assertIn("無更新", Processor(state, render, fake_pdf).check(s))
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
            p=Processor(Path(tmp)/"state.json", fail, fake_pdf)
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
            p = Processor(Path(tmp) / "state.json", render, fake_pdf)
            p.check(s)
            output.write_bytes(b"placeholder PNG")
            p.check(s)
            self.assertEqual(attempts, ["10.3", "10.3"])
            self.assertEqual(output.read_bytes(), b"rendered PNG")


    def test_delivery_order_and_retry(self):
        from unittest.mock import Mock
        from dataclasses import replace
        with tempfile.TemporaryDirectory() as tmp:
            s = Settings(watch_dir=tmp, enable_wifi_cover=True)
            src, png = s.paths()
            src.write_bytes(b"Word")
            actions = []
            client = Mock()
            def render(src, dst, size):
                actions.append("render")
                dst.write_bytes(b"png")
            def upload(client, pdf, log):
                self.assertTrue(pdf.read_bytes().startswith(b"%PDF-"))
                actions.append("verify-upload")
                return "doc-id"
            def wake(port, log):
                actions.append("wake")
                return "F12 已送出"
            def open_pdf(client, doc_id):
                actions.append("open")
                return 200
            p = Processor(Path(tmp)/"state.json", render, fake_pdf,
                          connect=lambda ip: (client, "reader1"),
                          identify=lambda ip: "reader1", upload=upload,
                          open_pdf=open_pdf, wake=wake,
                          delay=lambda seconds: actions.append("delay"))
            self.assertIn("HTTP 200", p.check(s))
            self.assertEqual(actions, ["render","verify-upload","wake","delay","open"])
            self.assertIn("無更新", p.check(s))
            self.assertEqual(len(actions),5)
            self.assertTrue(png.with_suffix(".pdf").is_file())
            # New device at same IP must not reuse old delivery state.
            p.identify=lambda ip: "reader2"
            p.connect=lambda ip: (client, "reader2")
            p.check(s)
            self.assertEqual(actions[-4:], ["verify-upload","wake","delay","open"])

    def test_upload_failure_never_opens_or_wakes(self):
        from unittest.mock import Mock
        with tempfile.TemporaryDirectory() as tmp:
            s = Settings(watch_dir=tmp, enable_wifi_cover=True)
            s.paths()[0].write_bytes(b"Word")
            client=Mock()
            render=lambda src,dst,size: dst.write_bytes(b"png")
            upload=Mock(side_effect=OSError("offline"))
            wake, open_pdf=Mock(), Mock()
            p=Processor(Path(tmp)/"state.json",render,fake_pdf,
                        connect=lambda ip:(client,"reader1"),identify=lambda ip:"reader1",
                        upload=upload,open_pdf=open_pdf,wake=wake)
            for _ in range(2):
                with self.assertRaises(OSError):
                    p.check(s)
            self.assertEqual(upload.call_count,2)
            wake.assert_not_called()
            open_pdf.assert_not_called()
            self.assertEqual(client.session.close.call_count,2)

    def test_missing_pico_still_attempts_open(self):
        from unittest.mock import Mock
        with tempfile.TemporaryDirectory() as tmp:
            s = Settings(watch_dir=tmp, enable_wifi_cover=True, pico_port="")
            s.paths()[0].write_bytes(b"Word")
            client=Mock()
            open_pdf=Mock(return_value=204)
            delay=Mock()
            p=Processor(Path(tmp)/"state.json",lambda src,dst,size: dst.write_bytes(b"png"),fake_pdf,
                        connect=lambda ip:(client,"reader1"),identify=lambda ip:"reader1",
                        upload=lambda *args:"doc-id",open_pdf=open_pdf,
                        wake=lambda *args:"未設定 Pico，略過 F12",delay=delay)
            self.assertIn("HTTP 204",p.check(s))
            open_pdf.assert_called_once_with(client,"doc-id")
            delay.assert_not_called()
