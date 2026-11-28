"""One-time DOCX to PDF conversion with full-page, selectable page previews.

Manual rendering uses the very same cached PDF used for the on-screen preview.
No automatic monitored DOCX files or PNG files are modified.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
import shutil


@dataclass(frozen=True)
class PagePreview:
    source: Path
    source_sha256: str
    source_mtime_ns: int
    pdf: Path
    page_count: int

    def source_unchanged(self):
        try:
            return (self.source.stat().st_mtime_ns == self.source_mtime_ns
                    and _checksum(self.source) == self.source_sha256)
        except OSError:
            return False


def _checksum(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as inp:
        for part in iter(lambda: inp.read(1024 * 1024), b""):
            h.update(part)
    return h.hexdigest()


def prepare_docx_preview(source, work_dir):
    """Convert all DOCX pages to one PDF, once. Return their true page count.

    Must be called in a worker thread on Windows; it initializes COM for that
    thread. The caller owns work_dir for the lifetime of page browsing/upload.
    """
    import os
    import pymupdf
    from mook_sleep_screen_manager import SleepScreenManager, safe_file_name
    source = Path(source).expanduser().resolve()
    work_dir = Path(work_dir)
    if source.suffix.casefold() != ".docx" or not source.is_file():
        raise ValueError("請先選擇存在的 DOCX 檔案")
    work_dir.mkdir(parents=True, exist_ok=True)
    original_mtime = source.stat().st_mtime_ns
    original_sha = _checksum(source)
    snapshot = work_dir / source.name
    shutil.copyfile(source, snapshot)
    if _checksum(snapshot) != original_sha or not (
            source.stat().st_mtime_ns == original_mtime
            and _checksum(source) == original_sha):
        raise RuntimeError("Word 正在儲存，請完成後重新選擇檔案")

    com = None
    if os.name == "nt":
        import pythoncom
        com = pythoncom
        com.CoInitialize()
    try:
        manager = SleepScreenManager(
            size="10.3", orientation="portrait", fit="contain", output_dir=work_dir)
        pages = manager._pdf_pages(snapshot, work_dir, render_scale=0.5)
        try:
            if next(pages, None) is None:
                raise RuntimeError("Word 沒有任何可顯示頁面")
        finally:
            pages.close()
    finally:
        if com is not None:
            com.CoUninitialize()
    pdf = work_dir / (safe_file_name(snapshot.stem) + ".pdf")
    if not pdf.is_file():
        raise RuntimeError("Word 轉換後沒有產生 PDF")
    with pymupdf.open(pdf) as doc:
        count = doc.page_count
    if count < 1:
        raise RuntimeError("Word 沒有任何可顯示頁面")
    if source.stat().st_mtime_ns != original_mtime or _checksum(source) != original_sha:
        raise RuntimeError("Word 已修改，請重新選擇檔案")
    return PagePreview(source, original_sha, original_mtime, pdf, count)


def prepare_media_preview(source, work_dir):
    """Word uses its multi-page PDF, photographs use photo preparation."""
    from mook_sleep_screen_manager import IMAGE_EXTS
    if Path(source).suffix.casefold() in IMAGE_EXTS:
        from mooink_photo import prepare_photo_preview
        return prepare_photo_preview(source, work_dir)
    return prepare_docx_preview(source, work_dir)


def render_document_page(preview, page_number, *, scale=1.25, size="10.3"):

    """Return full PDF page or an auto-rotated portrait photo preview."""
    from PIL import Image
    from PIL import Image
    if not 1 <= int(page_number) <= preview.page_count:
        raise ValueError("指定頁碼不在檔案的頁面範圍")
    from mooink_photo import PhotoPreview, photo_page_image
    if isinstance(preview, PhotoPreview):
        return photo_page_image(preview, size=size)
    import pymupdf
    with pymupdf.open(preview.pdf) as doc:
        page = doc[int(page_number) - 1]
        ratio = min(float(scale), 2600/max(page.rect.width, page.rect.height))
        pixmap = page.get_pixmap(matrix=pymupdf.Matrix(ratio, ratio), alpha=False)
        return Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)


def render_selected_page_png(preview, page_number, size, output):
    """Selected DOCX page or portrait-first photograph -> reader PNG."""
    from mooink_photo import PhotoPreview, render_photo_png
    if isinstance(preview, PhotoPreview):
        if int(page_number) != 1:
            raise ValueError("照片只有一頁")
        return render_photo_png(preview, size, output)
    import uuid
    from PIL import Image
    from mook_sleep_screen_manager import RESOLUTIONS
    page = render_document_page(preview, page_number, scale=3.5)
    gray = page.convert("L")
    mask = gray.point([255 if value < 245 else 0 for value in range(256)])
    bounds = mask.getbbox()
    if bounds:
        gray = gray.crop(bounds)
    width, height = RESOLUTIONS[size]
    border = max(4, round(min(width, height)*0.008))
    inner_width, inner_height = width-2*border, height-2*border
    from PIL import ImageOps
    fitted = ImageOps.pad(gray, (inner_width, inner_height),
                          method=Image.Resampling.LANCZOS, color=255)
    out = Image.new("L", (width,height),255)
    out.paste(fitted,(border,border))
    out = out.point([round(i/17)*17 for i in range(256)])
    output = Path(output)
    temp = output.with_name("." + output.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        out.save(temp,format="PNG",optimize=True)
        temp.replace(output)
    finally:
        temp.unlink(missing_ok=True)
