"""Photo import, automatic rotation, border cropping and USB isolation."""
import tempfile
from pathlib import Path
import unittest

from PIL import Image, ImageDraw

from mooink_photo import (
    optimize_photo_for_portrait, crop_white_border, prepare_photo_preview,
    photo_page_image, render_photo_png, prepare_photo_batch,
)
from mooink_docx_pages import (
    prepare_media_preview, render_document_page, render_selected_page_png,
)
from mooink_multi_device import DeviceRegistry, update_selected_docx


class PortraitPhotoTests(unittest.TestCase):
    def test_landscape_photo_rotates_into_portrait_not_small_letterbox(self):
        photo = Image.new("RGB", (1200, 600), (55, 65, 75))
        filled, turned = optimize_photo_for_portrait(photo, "10.3")
        self.assertTrue(turned)
        self.assertEqual(filled.size, (1404,1872))
        self.assertEqual(filled.getpixel((0,0)), (255,255,255))
        self.assertEqual(filled.getpixel((1403,1871)), (255,255,255))
        self.assertEqual(filled.getpixel((702,936)), (55,65,75))

    def test_portrait_photo_keeps_orientation(self):
        photo = Image.new("RGB", (600, 1000), (10, 20, 30))
        result, turned = optimize_photo_for_portrait(photo, "13.3")
        self.assertFalse(turned)
        self.assertEqual(result.size, (1650,2200))
        self.assertEqual(result.getpixel((0,0)), (255,255,255))
        self.assertEqual(result.getpixel((825,1100)), (10,20,30))

    def test_real_whitespace_border_trimmed(self):
        photo = Image.new("RGB",(850,550),"white")
        ImageDraw.Draw(photo).rectangle((100,75,749,474), fill=(18,29,38))
        cropped = crop_white_border(photo)
        self.assertLess(cropped.width, photo.width-150)
        self.assertLess(cropped.height, photo.height-120)
        result, turned = optimize_photo_for_portrait(photo,"10.3")
        self.assertTrue(turned)
        # The source is kept intact: near-white padding is removed but
        # nonmatching aspect ratios produce safe white side bars, not cropping.
        self.assertEqual(result.getpixel((0,0)), (255,255,255))
        self.assertTrue(all(channel < 100 for channel in result.getpixel((702,936))))


    def test_important_edge_corners_survive_no_cover_crop(self):
        photo = Image.new("RGB", (800,400), (60,60,60))
        draw = ImageDraw.Draw(photo)
        marks = [(255,0,0),(0,255,0),(0,0,255),(255,255,0)]
        for box,color in zip([(0,0,80,80),(719,0,799,80),
                              (0,319,80,399),(719,319,799,399)],marks):
            draw.rectangle(box,fill=color)
        portrait, turned = optimize_photo_for_portrait(photo,"10.3")
        self.assertTrue(turned)
        # Downsampled sampling is enough to find all four *edge* markers.
        samples=list(portrait.resize((120,160), Image.Resampling.NEAREST).getdata())
        for mark in marks:
            self.assertTrue(any(sum(abs(px[i]-mark[i]) for i in range(3)) < 12
                                for px in samples),
                            "Edge marker disappeared: " + str(mark))

    def test_thin_dark_line_at_photo_edge_preserved(self):
        photo = Image.new("RGB",(500,700),"white")
        draw=ImageDraw.Draw(photo)
        draw.line((1,1,1,699),fill="black",width=1)
        draw.rectangle((200,220,300,320),fill=(20,30,40))
        cropped=crop_white_border(photo)
        self.assertEqual(cropped.getpixel((1,1)),(0,0,0))
        self.assertEqual(cropped.height,700)

    def test_white_photo_and_tiny_photo_do_not_crop_to_empty(self):
        white = Image.new("RGB",(400,700),"white")
        self.assertEqual(crop_white_border(white).size, white.size)
        tiny = Image.new("RGB",(1,2),"white")
        self.assertEqual(crop_white_border(tiny).size,tiny.size)
        for img in (white, tiny):
            full, _ = optimize_photo_for_portrait(img,"10.3")
            self.assertEqual(full.size,(1404,1872))

    def test_selected_png_and_preview_use_same_orientation(self):
        with tempfile.TemporaryDirectory() as tmp:
            source=Path(tmp)/"landscape photo.png"
            Image.new("RGB",(700,250),(60,70,80)).save(source)
            orig=source.read_bytes()
            preview=prepare_media_preview(source,Path(tmp)/"working")
            self.assertEqual(preview.page_count,1)
            self.assertTrue(preview.source_unchanged())
            shown=render_document_page(preview,1,size="10.3")
            self.assertEqual(shown.size,(1404,1872))
            output=Path(tmp)/"mooink_display_10.png"
            render_selected_page_png(preview,1,"10.3",output)
            with Image.open(output) as transferred:
                self.assertEqual(transferred.size,shown.size)
                self.assertLessEqual(len(transferred.getcolors(maxcolors=1000) or []),16)
            self.assertEqual(source.read_bytes(),orig)
            with self.assertRaisesRegex(ValueError,"一頁"):
                render_selected_page_png(preview,2,"10.3",output)
            source.write_bytes(b"changed")
            self.assertFalse(preview.source_unchanged())

    def test_batch_photo_management_both_sizes_without_changing_originals(self):
        with tempfile.TemporaryDirectory() as tmp:
            sources=[Path(tmp)/"photo1.jpg",Path(tmp)/"photo2.png"]
            Image.new("RGB",(800,300),(20,30,40)).save(sources[0])
            Image.new("RGB",(250,800),(80,90,100)).save(sources[1])
            originals=[p.read_bytes() for p in sources]
            result=prepare_photo_batch(sources, Path(tmp)/"prepared")
            self.assertEqual(len(result["10.3"]),2)
            self.assertEqual(len(result["13.3"]),2)
            for size, shape in (("10.3",(1404,1872)),("13.3",(1650,2200))):
                for output in result[size]:
                    self.assertTrue(output.is_file())
                    with Image.open(output) as image:
                        self.assertEqual(image.size,shape)
            self.assertEqual([p.read_bytes() for p in sources],originals)

    def test_manual_photo_delivers_per_device_size_and_keeps_auto_word(self):
        with tempfile.TemporaryDirectory() as tmp:
            source=Path(tmp)/"photo.png"
            Image.new("RGB",(800,300),(15,50,70)).save(source)
            automatic=Path(tmp)/"mooink_display_10.docx"
            automatic.write_bytes(b"automatic DOCX stays unchanged")
            reg=DeviceRegistry(Path(tmp)/"profiles.json")
            rows=[{"id":"a"*64,"name":"A","ready":True},
                  {"id":"b"*64,"name":"B","ready":True}]
            reg.assign(rows[0]["id"],"10.3","A")
            reg.assign(rows[1]["id"],"13.3","B")
            preview=prepare_photo_preview(source,Path(tmp)/"snapshot")
            delivered=[]
            def upload(rid,path,size,log):
                with Image.open(path) as img:
                    delivered.append((rid,size,img.size))
                return {"changed":True}
            result=update_selected_docx(
                source,registry=reg,discover=lambda:rows,
                render=lambda src,png,size:render_photo_png(preview,size,png),
                upload=upload)
            self.assertEqual(len(result["successful"]),2)
            self.assertEqual(delivered,[
                (rows[0]["id"],"10.3",(1404,1872)),
                (rows[1]["id"],"13.3",(1650,2200))])
            self.assertEqual(automatic.read_bytes(),b"automatic DOCX stays unchanged")
            self.assertEqual(reg.lookup(rows[0]["id"])["word_name"],"photo.png")

    def test_invalid_photo_reports_decoder_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/"corrupt.png"
            path.write_bytes(b"not a picture")
            with self.assertRaisesRegex(ValueError,"無法解碼"):
                prepare_photo_preview(path,Path(tmp)/"snapshot")


if __name__ == "__main__":
    unittest.main()
