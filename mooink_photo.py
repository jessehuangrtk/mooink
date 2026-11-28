"""Photo import and portrait-first e-paper preparation for mooInk.

Uses the existing sleep manager formats and sizes, but always targets the
physical portrait screen. Rotation of landscape photos is automatic; the
reader itself can be held sideways by the user to view the rotated photo.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import shutil
import uuid

from mook_sleep_screen_manager import IMAGE_EXTS, RESOLUTIONS, safe_file_name


def sha256_file(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as src:
        for chunk in iter(lambda: src.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


@dataclass(frozen=True)
class PhotoPreview:
    source: Path
    source_sha256: str
    source_mtime_ns: int
    image_path: Path
    page_count: int = 1

    def source_unchanged(self):
        try:
            return (self.source.is_file()
                    and self.source.stat().st_mtime_ns == self.source_mtime_ns
                    and sha256_file(self.source) == self.source_sha256)
        except OSError:
            return False


def prepare_photo_preview(source, work_dir):
    """Snapshot a photo for stable manual selection, without editing original."""
    from PIL import Image, ImageOps
    source = Path(source).expanduser().resolve()
    if source.suffix.casefold() not in IMAGE_EXTS or not source.is_file():
        raise ValueError("請選擇 JPG、PNG、WEBP、BMP、TIFF、GIF 或 AVIF 圖片")
    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    mtime = source.stat().st_mtime_ns
    checksum = sha256_file(source)
    snapshot = work_dir / source.name
    shutil.copyfile(source, snapshot)
    if (sha256_file(snapshot) != checksum or source.stat().st_mtime_ns != mtime
            or sha256_file(source) != checksum):
        raise RuntimeError("照片正在變更，請重新選擇")
    try:
        with Image.open(snapshot) as img:
            # Same first-frame behavior as mook_sleep_screen_manager.py.
            img.seek(0)
            oriented = ImageOps.exif_transpose(img)
            if oriented.width < 1 or oriented.height < 1:
                raise ValueError("無效的圖片尺寸")
    except Exception as exc:
        raise ValueError("照片無法解碼：" + str(exc)) from exc
    return PhotoPreview(source, checksum, mtime, snapshot)


def crop_white_border(image, *, threshold=250, required_fraction=None):
    """Conservatively remove only near-white padding around the image.

    Determine the exact bounding rectangle of *every* nonwhite pixel from
    the original pixels; never allow almost-empty rows to erase thin strokes,
    captions or other meaningful edge details. No downsampling for crop
    detection. Pure-white photographs are left unmodified.
    """
    from PIL import ImageChops
    rgb = image.convert("RGB")
    # All three channels must be near-white before a pixel is removable.
    red, green, blue = rgb.split()
    red = red.point([255 if v < threshold else 0 for v in range(256)])
    green = green.point([255 if v < threshold else 0 for v in range(256)])
    blue = blue.point([255 if v < threshold else 0 for v in range(256)])
    nonwhite = ImageChops.lighter(red, ImageChops.lighter(green, blue))
    bounds = nonwhite.getbbox()
    if bounds is None:
        return image.copy()
    left, top, right, bottom = bounds
    # Retain 1 px around the exact nonwhite bounds when possible.
    bounds = (max(0,left-1),max(0,top-1),
              min(image.width,right+1),min(image.height,bottom+1))
    return image.crop(bounds)


def optimize_photo_for_portrait(image, size):
    """Best 90 degree orientation, with ALL meaningful source pixels retained.

    Uses a white letterbox only when the photo's aspect ratio differs from
    the reader's shape. No cover/center-crop. This prioritizes content over
    an aggressively full-bleed appearance.
    """
    from PIL import Image, ImageOps
    if size not in RESOLUTIONS:
        raise ValueError("未知 mooInk 尺寸：" + str(size))
    source = crop_white_border(image)
    width, height = RESOLUTIONS[size]
    aspect = source.width / source.height
    target = width / height
    keep = min(aspect / target, target / aspect)
    turned = 1 / aspect
    rotate = min(turned / target, target / turned) > keep + 1e-8
    if rotate:
        source = source.transpose(Image.Transpose.ROTATE_90)
    # Fit fully inside a portrait canvas; never remove photo edges.
    prepared = ImageOps.contain(source.convert("RGB"), (width, height),
                                Image.Resampling.LANCZOS)
    result = Image.new("RGB", (width, height), "white")
    result.paste(prepared, ((width-prepared.width)//2,
                            (height-prepared.height)//2))
    return result, rotate

def photo_page_image(preview, *, size="10.3"):
    from PIL import Image, ImageOps
    with Image.open(preview.image_path) as image:
        image.seek(0)
        oriented = ImageOps.exif_transpose(image)
        result, _ = optimize_photo_for_portrait(oriented, size)
    return result


def render_photo_png(preview, size, output):
    """Same full-frame image for preview and USB, quantized to 16 grays."""
    from PIL import Image
    image = photo_page_image(preview, size=size).convert("L")
    image = image.point([int(round(value/17)*17) for value in range(256)])
    target = Path(output)
    temporary = target.with_name("." + target.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        image.save(temporary, format="PNG", optimize=True)
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def prepare_photo_batch(sources, output_dir, sizes=("10.3","13.3")):
    """Batch-photo management like SleepScreenManagerUI, without touching USB."""
    from PIL import Image, ImageOps
    root = Path(output_dir)
    prepared = {size:[] for size in sizes}
    for source in sources:
        source = Path(source).expanduser().resolve()
        if source.suffix.casefold() not in IMAGE_EXTS or not source.is_file():
            raise ValueError("無法讀取照片：" + str(source))
        with Image.open(source) as image:
            image.seek(0)
            oriented = ImageOps.exif_transpose(image).copy()
        for size in sizes:
            directory = root / size
            directory.mkdir(parents=True, exist_ok=True)
            suffix = hashlib.sha256(str(source).encode("utf-8")).hexdigest()[:10]
            out = directory / (safe_file_name(source.stem) + "_" + suffix + ".png")
            result, _ = optimize_photo_for_portrait(oriented, size)
            gray = result.convert("L").point([int(round(v/17)*17)
                                              for v in range(256)])
            temp = out.with_name("." + out.name + "." + uuid.uuid4().hex + ".tmp")
            try:
                gray.save(temp, format="PNG", optimize=True)
                os.replace(temp,out)
            finally:
                temp.unlink(missing_ok=True)
            prepared[size].append(out)
    return prepared
