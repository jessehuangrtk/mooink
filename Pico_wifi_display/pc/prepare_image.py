"""Prepare a stable grayscale PNG in a Windows SMB share. No background sender."""
import argparse
import hashlib
import os
from pathlib import Path
import tempfile
from PIL import Image, ImageOps

SIZES={"10.3":(1404,1872),"13.3":(1650,2200)}

def prepare(src, share, size):
    target=Path(share)/"mooink_display.png"
    if not target.parent.is_dir():
        raise ValueError("SMB share directory does not exist")
    with Image.open(src) as original:
        image=ImageOps.pad(ImageOps.exif_transpose(original).convert("L"),SIZES[size],method=Image.Resampling.LANCZOS,color=255)
        image=image.point([round(i/17)*17 for i in range(256)])
    fd,tmp=tempfile.mkstemp(prefix=".mooink_",suffix=".png",dir=target.parent)
    try:
        with os.fdopen(fd,"wb") as f:
            image.save(f,format="PNG",optimize=True)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp,target)
    finally:
        Path(tmp).unlink(missing_ok=True)
    with target.open("rb") as f:
        digest=hashlib.sha256()
        for block in iter(lambda:f.read(65536),b""):digest.update(block)
    return target,digest.hexdigest()

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("image")
    p.add_argument("share")
    p.add_argument("--size",choices=SIZES,default="10.3")
    a=p.parse_args()
    path,digest=prepare(a.image,a.share,a.size)
    print(path)
    print("SHA256",digest)

if __name__=="__main__":
    main()
