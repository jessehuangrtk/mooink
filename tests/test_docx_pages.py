import os
from pathlib import Path
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from mooink_docx_pages import (prepare_docx_preview, render_document_page,
                               render_selected_page_png)


@unittest.skipUnless(sys.version_info >= (3, 10), "PDF preview supports Python 3.10+")
class MultipleWordPageTests(unittest.TestCase):
    def generate_preview(self, path, work):
        import pymupdf
        from PIL import Image
        from mook_sleep_screen_manager import SleepScreenManager, safe_file_name

        def pdf_pages(self, source, directory, render_scale=0.5):
            doc = pymupdf.open()
            for number in range(1, 5):
                page = doc.new_page(width=400, height=560)
                page.draw_rect(pymupdf.Rect(180, 160, 280, 210),
                               color=(0,0,0), fill=(0,0,0))
                page.insert_text((195, 195), str(number),
                                 fontsize=18, color=(1,1,1))
            pdf = Path(directory)/(safe_file_name(source.stem)+".pdf")
            doc.save(pdf)
            doc.close()
            yield 1, Image.new("RGB", (100, 140), "white")

        fake_com = SimpleNamespace(CoInitialize=lambda: None, CoUninitialize=lambda: None)
        with patch.object(SleepScreenManager, "_pdf_pages", pdf_pages), \
             patch.dict(sys.modules, {"pythoncom": fake_com}):
            return prepare_docx_preview(path,work)

    def test_multiple_pages_selected_and_exact_page_count(self):
        from PIL import Image
        with tempfile.TemporaryDirectory() as tmp:
            source=Path(tmp)/"chapters.docx"
            source.write_bytes(b"docx snapshot")
            preview=self.generate_preview(source,Path(tmp)/"pdf")
            self.assertEqual(preview.page_count,4)
            self.assertTrue(preview.source_unchanged())
            first=render_document_page(preview,1)
            fourth=render_document_page(preview,4)
            self.assertEqual(first.size,fourth.size)
            self.assertGreater(first.height,first.width)
            self.assertEqual(first.getpixel((0,0)),(255,255,255))
            self.assertNotEqual(first.tobytes(),fourth.tobytes())
            png=Path(tmp)/"mooink_display_10.png"
            render_selected_page_png(preview,3,"10.3",png)
            with Image.open(png) as img:
                self.assertEqual(img.size,(1404,1872))
                self.assertEqual(img.getpixel((0,0)),255)
                self.assertEqual(img.getpixel((1403,1871)),255)
            with self.assertRaisesRegex(ValueError,"頁碼"):
                render_document_page(preview,5)
            source.write_bytes(b"changed since preview")
            self.assertFalse(preview.source_unchanged())

    def test_empty_page_and_one_page_document(self):
        import pymupdf
        from PIL import Image
        from mook_sleep_screen_manager import SleepScreenManager, safe_file_name
        with tempfile.TemporaryDirectory() as tmp:
            source=Path(tmp)/"single.docx"
            source.write_bytes(b"single page")
            def single_pages(self, docx, directory, render_scale=0.5):
                pdf=pymupdf.open()
                pdf.new_page(width=612,height=792)
                pdf.save(Path(directory)/(safe_file_name(docx.stem)+".pdf"))
                pdf.close()
                yield 1, Image.new("RGB",(100,100),"white")
            fake=SimpleNamespace(CoInitialize=lambda:None,CoUninitialize=lambda:None)
            with patch.object(SleepScreenManager,"_pdf_pages",single_pages), \
                 patch.dict(sys.modules,{"pythoncom":fake}):
                preview=prepare_docx_preview(source,Path(tmp)/"out")
            self.assertEqual(preview.page_count,1)
            self.assertEqual(render_document_page(preview,1).getpixel((0,0)),
                             (255,255,255))
            output=Path(tmp)/"mooink_display_13.png"
            render_selected_page_png(preview,1,"13.3",output)
            with Image.open(output) as img:
                self.assertTrue(img.getbbox())
                self.assertEqual(img.getpixel((0,0)),255)


if __name__ == "__main__":
    unittest.main()
