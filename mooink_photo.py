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


def crop_white_border(image, *, threshold=245, required_fraction=0.985):
    """Trim contiguous nearly-white outer rows/columns, not all white objects.

    A row/column is a margin only if >=98.5% of its pixels are near-white.
    Edges become tight without blindly cropping a mostly-white photograph.
    """
    from PIL import Image
    gray = image.convert("L")
    # No numpy dependency: sample the outer rows and columns.
    width, height = gray.size
    if width < 2 or height < 2:
        return image.copy()
    # Downsample only for detecting border thickness; crop original pixels.
    sample = gray.copy()
    sample.thumbnail((640, 640), Image.Resampling.BILINEAR)
    sw, sh = sample.size
    data = sample.load()
    # Guard against nearly-white photos: only trim if there is a true
    # nonwhite interior. Minimum 2% dark content avoids losing white scenes.
    dark = sum(data[x,y] < threshold for y in range(sh) for x in range(sw))
    if dark < sw * sh * 0.002:
        return image.copy()
    allowed_x = max(0, int((1-required_fraction)*sh))
    allowed_y = max(0, int((1-required_fraction)*sw))
    left, right, top, bottom = 0, sw, 0, sh
    def col_is_white(x):
        return sum(data[x,y] < threshold for y in range(sh)) <= allowed_x
    def row_is_white(y):
        return sum(data[x,y] < threshold for x in range(sw)) <= allowed_y
    while left < right-1 and col_is_white(left):
        left += 1
    while right > left+1 and col_is_white(right-1):
        right -= 1
    while top < bottom-1 and row_is_white(top):
        top += 1
    while bottom > top+1 and row_is_white(bottom-1):
        bottom -= 1
    if left == 0 and right == sw and top == 0 and bottom == sh:
        return image.copy()
    rect = (round(left*width/sw), round(top*height/sh),
            round(right*width/sw), round(bottom*height/sh))
    # Preserve at least 10% of the photo; don't crop entirely near-white art.
    if (rect[2]-rect[0])*(rect[3]-rect[1]) < width*height*0.1:
        return image.copy()
    candidate = image.crop(rect)
    # The 640-pixel mask is approximate: upscaling its crop box can
    # leave a 1-2 pixel white halo. Refine only a thin strip against the
    # ORIGINAL pixels so that bright real content is not aggressively cut.
    refined = candidate.convert("L").point(
        [255 if value < threshold else 0 for value in range(256)]).getbbox()
    if refined:
        x0,y0,x1,y1=refined
        slack_x=max(3,round(width*0.005))
        slack_y=max(3,round(height*0.005))
        if (x0<=slack_x and y0<=slack_y
                and candidate.width-x1<=slack_x
                and candidate.height-y1<=slack_y):
            return candidate.crop(refined)
    return candidate


def optimize_photo_for_portrait(image, size):
    """Trim white borders, rotate to best portrait fit, and cover the panel.

    Landscape source -> 90 degree physical rotation in file, so the long
    reader screen is filled. The user can rotate the reader to view it.
    Cover may crop content near the edges but never stretches the image.
    """
    from PIL import Image, ImageOps
    if size not in RESOLUTIONS:
        raise ValueError("未知 mooInk 尺寸：" + str(size))
    source = crop_white_border(image)
    width, height = RESOLUTIONS[size]
    aspect = source.width/source.height
    target = width/height
    keep = min(aspect/target, target/aspect)
    turned = 1/aspect
    rotate = min(turned/target,target/turned) > keep + 1e-8
    if rotate:
        source = source.transpose(Image.Transpose.ROTATE_90)
    # Maximize screen coverage, unlike contain which leaves wide empty bands.
    return ImageOps.fit(source.convert("RGB"), (width,height),
                        method=Image.Resampling.LANCZOS, centering=(0.5,0.5)), rotate


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
