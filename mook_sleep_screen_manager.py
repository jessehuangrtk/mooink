#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""mooInk first-generation sleep-screen manager (Windows / Python 3.9+).

Two public classes:
  MooinkLowLevel       -- explicit portable USB MTP / local mounted share operations
  SleepScreenManager   -- image, Excel, Word conversion + interactive functions

Image sizes: 10.3" 1404x1872, 13.3" 1650x2200. Landscape output is
rotated canvas dimensions; user rotates the actual reader for viewing.

Native Sony-compatible Wi-Fi API is verified for *Document* management,
not for the external sleep folder. This tool NEVER pretends that a Sony
Document API call writes to /sleep. Wi-Fi sleep CRUD requires a verified
OS-visible mounted network folder: --wifi-share \\server\\share\\sleep.
Without it, Wi-Fi only does a read-only connection check to 8080/8443.

Files are prepared locally before transfer; the device will not necessarily
refresh e-ink display until a real sleep transition.

Install: python -m pip install pillow pymupdf pywin32
For Excel and Word conversion: Microsoft Office OR LibreOffice installed.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from typing import Optional

RESOLUTIONS = {"10.3": (1404, 1872), "13.3": (1650, 2200)}
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff", ".gif", ".avif"}
OUTPUT_EXTS = {".jpg", ".jpeg", ".png"}
OFFICE_EXTS = {".xlsx", ".xls", ".xlsm", ".xlsb", ".ods", ".docx", ".doc", ".odt"}
MAX_JPG_SIZE = 990_000  # below Readmoo's recommended 1 MB


def question(prompt: str, default: bool = False) -> bool:
    s = input(prompt + (" [Y/n]: " if default else " [y/N]: ")).strip().lower()
    return default if not s else s in ("y", "yes", "是", "好")


def safe_file_name(text: str) -> str:
    import re
    out = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", text).strip(" .")
    return out[:90] or "image"


class MooinkLowLevel:
    """Low-level transport: Windows Shell MTP or an explicitly mounted share.

    No device serial is hard-coded. With two devices connected, Shell shows
    both; for write/delete of indistinguishable USB devices, disconnect one.
    """

    def __init__(self, transport="usb", ip: Optional[str] = None,
                 wifi_share: Optional[str] = None, usb_index: Optional[int] = None):
        self.transport = transport
        self.ip = ip
        self.usb_index = usb_index
        self.wifi_share = Path(wifi_share) if wifi_share else None
        self._shell = None
        self._usb_devices = None
        self._folder = None
        if transport not in ("usb", "wifi"):
            raise ValueError("transport must be usb or wifi")
        if self.wifi_share and self.wifi_share.name.lower() != "sleep":
            raise ValueError("--wifi-share 必須直接指向名稱為 sleep 的資料夾")

    def wifi_probe(self):
        if not self.ip:
            return {"status": "no_ip"}
        import ipaddress
        ip = ipaddress.ip_address(self.ip)
        if ip.version != 4 or not (ip.is_private or ip.is_link_local):
            raise ValueError("Wi-Fi IP 必須是區域網路 IPv4")
        result = {}
        for port in (8080, 8443):
            try:
                with socket.create_connection((str(ip), port), timeout=3):
                    result[str(port)] = "connected"
            except OSError as exc:
                result[str(port)] = type(exc).__name__
        return result

    def usb_devices(self):
        if os.name != "nt":
            raise RuntimeError("USB/MTP Shell 模式僅支援 Windows")
        import win32com.client
        if self._shell is None:
            self._shell = win32com.client.Dispatch("Shell.Application")
        this_pc = self._shell.Namespace(17)  # This PC / ssfDRIVES
        if this_pc is None:
            raise RuntimeError("Windows Shell 找不到『本機』")
        devices = []
        for item in this_pc.Items():
            name = str(item.Name)
            if "mooink" in name.lower() and bool(item.IsFolder):
                devices.append(item)
        self._usb_devices = devices
        return [str(item.Name) for item in devices]

    def _choose_usb(self):
        # usb_devices() returns display names but caches Shell COM items.
        # Always select from cached objects, never from name strings.
        if self._usb_devices is None:
            self.usb_devices()
        devices = self._usb_devices
        if not devices:
            raise RuntimeError("Windows 本機中沒有可存取的 mooInk MTP 裝置")
        if self.usb_index is None:
            if len(devices) == 1:
                self.usb_index = 1
            else:
                print("偵測到以下 USB 裝置（Shell 可能沒有提供可辨識序號）：")
                for i, item in enumerate(devices, 1):
                    print("  %d. %s" % (i, item.Name))
                self.usb_index = int(input("選擇 USB 裝置編號：").strip())
        if not 1 <= self.usb_index <= len(devices):
            raise ValueError("USB 裝置編號錯誤")
        return devices[self.usb_index - 1]

    @staticmethod
    def _find_child(folder, wanted):
        for item in folder.Items():
            if str(item.Name).lower() == wanted.lower() and bool(item.IsFolder):
                return item.GetFolder
        return None

    def _usb_sleep_folder(self):
        if self._folder is not None:
            return self._folder
        selected = self._choose_usb()
        root = selected.GetFolder
        if root is None:
            raise RuntimeError("Windows 無法讀取選定 mooInk 的 MTP 根目錄")
        # Some MTP drivers show sleep directly, others below "Internal storage".
        queue = [(root, 0)]
        while queue:
            folder, depth = queue.pop(0)
            child = self._find_child(folder, "sleep")
            if child is not None:
                self._folder = child
                return child
            if depth < 3:
                for item in folder.Items():
                    if bool(item.IsFolder):
                        name = str(item.Name).lower()
                        if depth == 0 or any(s in name for s in ("storage", "shared", "internal", "儲存")):
                            try:
                                queue.append((item.GetFolder, depth + 1))
                            except Exception:
                                pass
        raise RuntimeError("MTP 找不到 sleep 資料夾；沒有自動建立或猜測目錄")

    def _target(self, write=False):
        if self.transport == "usb":
            if write and len(self.usb_devices()) > 1:
                raise RuntimeError("同時接了兩台同名 mooInk；為避免刪錯／傳錯，請先拔除另一台再寫入")
            return self._usb_sleep_folder()
        if self.wifi_share and self.wifi_share.is_dir():
            return self.wifi_share
        raise NotImplementedError(
            "Sony Wi-Fi API 目前只驗證 Document，沒有驗證 sleep 資料夾的 API。"
            "請使用 USB，或明確掛載並提供 --wifi-share \\server\\share\\sleep。")

    def list_sleep(self):
        target = self._target()
        if isinstance(target, Path):
            return [{"name": x.name, "size": x.stat().st_size} for x in sorted(target.iterdir())
                    if x.is_file() and x.suffix.lower() in OUTPUT_EXTS]
        items = []
        for item in target.Items():
            n = str(item.Name)
            if not bool(item.IsFolder) and Path(n).suffix.lower() in OUTPUT_EXTS:
                try:
                    size = int(item.Size)
                except Exception:
                    size = None
                items.append({"name": n, "size": size})
        return sorted(items, key=lambda x: x["name"].lower())

    def add_file(self, path: Path, overwrite=False):
        path = Path(path).resolve()
        if not path.is_file() or path.suffix.lower() not in OUTPUT_EXTS:
            raise ValueError("只接受已產生的 JPG/PNG 檔案")
        target = self._target(write=True)
        existing = {p["name"].lower() for p in self.list_sleep()}
        if path.name.lower() in existing and not overwrite:
            raise FileExistsError("sleep 已有同名檔案：" + path.name)
        if isinstance(target, Path):
            dest = target / path.name
            shutil.copy2(path, dest)
            return "copied+verified" if dest.stat().st_size == path.stat().st_size else "copy-size-mismatch"
        if path.name.lower() in existing:
            raise FileExistsError("MTP 模式不自動覆寫同名檔；請先備份、刪除原檔")
        # CopyHere is asynchronous on WPD: never claim immediate success.
        target.CopyHere(str(path), 0)  # Shell manages transfer / prompts
        deadline = time.monotonic() + 50
        seen_stable = 0
        previous = None
        while time.monotonic() < deadline:
            entry = next((x for x in self.list_sleep() if x["name"].lower() == path.name.lower()), None)
            if entry:
                n = entry.get("size")
                if n == path.stat().st_size:
                    seen_stable = seen_stable + 1 if previous == n else 1
                    if seen_stable >= 2:
                        return "appeared+size-verified"
                elif n is None:
                    return "appeared-unverified-size"
                previous = n
            time.sleep(0.8)
        return "copy-requested-verification-pending"

    def backup_and_delete(self, filename: str, backup_dir: Path, *, confirm_delete=True):
        if Path(filename).name != filename or Path(filename).suffix.lower() not in OUTPUT_EXTS:
            raise ValueError("只能刪除 sleep 根目錄下的 JPG/PNG，且不可使用路徑")
        target = self._target(write=True)
        found = [x for x in self.list_sleep() if x["name"] == filename]
        if not found:
            raise FileNotFoundError(filename)
        backup_dir = Path(backup_dir).resolve()
        backup_dir.mkdir(parents=True, exist_ok=True)
        backup = backup_dir / filename
        if backup.exists():
            backup = backup_dir / (datetime.now().strftime('%Y%m%d_%H%M%S_') + filename)
        if isinstance(target, Path):
            src = target / filename
            shutil.copy2(src, backup)
            if not backup.exists() or backup.stat().st_size != src.stat().st_size:
                raise RuntimeError("備份驗證失敗，拒絕刪除")
            src.unlink()
            return str(backup)
        # Windows MTP Shell manages the actual delete. It may show UI.
        item = next((x for x in target.Items() if str(x.Name) == filename), None)
        if item is None:
            raise FileNotFoundError(filename)
        backup_dir_shell = self._shell.Namespace(str(backup_dir))
        if backup_dir_shell is None:
            raise RuntimeError("Windows Shell 無法開啟備份資料夾")
        backup_dir_shell.CopyHere(item, 0)
        deadline = time.monotonic() + 50
        src_size = found[0].get("size")
        while time.monotonic() < deadline:
            if backup.exists() and backup.stat().st_size > 0 and (
                    src_size is None or backup.stat().st_size == src_size):
                break
            time.sleep(0.8)
        else:
            raise RuntimeError("USB MTP 備份未完成或大小不符；不刪除原檔")
        if confirm_delete and not question("確定刪除 mooInk 的 %s？已備份到 %s" % (filename, backup)):
            return "cancelled-after-backup: " + str(backup)
        item.InvokeVerb("delete")
        # In MTP mode delete can be asynchronous and/or require Explorer UI.
        return "Windows Shell delete requested; verify manually; backup: " + str(backup)


class SleepScreenManager:
    """High-level batch conversion, orientation, preview and sleep manager."""

    def __init__(self, size="13.3", orientation="auto", fit="smart",
                 output_dir="mooink_prepared", io_layer=None):
        if size not in RESOLUTIONS:
            raise ValueError("size needs to be 10.3 or 13.3")
        if orientation not in ("auto", "portrait", "landscape"):
            raise ValueError("orientation needs auto/portrait/landscape")
        if fit not in ("cover", "contain", "smart"):
            raise ValueError("fit needs cover/contain/smart")
        self.size, self.orientation, self.fit = size, orientation, fit
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.io = io_layer
        self.logs = []

    def _pdf_pages(self, source: Path, temporary_dir: Path):
        """Render each Office document page with Office COM or LibreOffice."""
        import fitz
        pdf = temporary_dir / (safe_file_name(source.stem) + ".pdf")
        completed = False
        if os.name == "nt":
            try:
                import win32com.client
                if source.suffix.lower() in (".xls", ".xlsx", ".xlsm", ".xlsb"):
                    app = win32com.client.DispatchEx("Excel.Application")
                    book = None
                    try:
                        app.Visible = False
                        app.DisplayAlerts = False
                        app.AutomationSecurity = 3  # ForceDisable macros
                        book = app.Workbooks.Open(str(source.resolve()), UpdateLinks=0, ReadOnly=True)
                        book.ExportAsFixedFormat(0, str(pdf.resolve()))
                        completed = True
                    finally:
                        if book is not None:
                            book.Close(False)
                        app.Quit()
                elif source.suffix.lower() in (".doc", ".docx"):
                    app = win32com.client.DispatchEx("Word.Application")
                    doc = None
                    try:
                        app.Visible = False
                        app.DisplayAlerts = 0
                        app.AutomationSecurity = 3
                        doc = app.Documents.Open(str(source.resolve()), ReadOnly=True,
                                                 AddToRecentFiles=False, Visible=False)
                        doc.ExportAsFixedFormat(str(pdf.resolve()), 17)
                        completed = True
                    finally:
                        if doc is not None:
                            doc.Close(False)
                        app.Quit()
            except Exception as exc:
                self.logs.append("Office COM conversion unavailable: %s" % type(exc).__name__)
        if not completed:
            soffice = shutil.which("soffice") or shutil.which("libreoffice")
            if not soffice and os.name == "nt":
                guess = Path(os.environ.get("PROGRAMFILES", r"C:\Program Files")) / "LibreOffice" / "program" / "soffice.exe"
                if guess.is_file():
                    soffice = str(guess)
            if not soffice:
                raise RuntimeError("文件轉圖片需要已安裝的 Microsoft Office（pywin32）或 LibreOffice")
            profile = (temporary_dir / "lo_profile").resolve().as_uri()
            result = subprocess.run([soffice, "--headless", "-env:UserInstallation=" + profile,
                                     "--convert-to", "pdf", "--outdir", str(temporary_dir),
                                     str(source.resolve())],
                                    capture_output=True, text=True, timeout=120)
            if result.returncode != 0 or not pdf.exists():
                raise RuntimeError("LibreOffice PDF 轉換失敗：" + result.stderr[:200])
        with fitz.open(pdf) as doc:
            for index, page in enumerate(doc):
                if index >= 100:
                    self.logs.append("Page limit 100 reached: " + source.name)
                    break
                # Limit large PDF render output memory; final resize to device.
                pix = page.get_pixmap(matrix=fitz.Matrix(1.5, 1.5), alpha=False)
                from PIL import Image
                image = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
                yield index + 1, image

    def convert(self, paths):
        from PIL import Image, ImageOps, ImageEnhance
        results = []
        for raw in paths:
            source = Path(raw).expanduser().resolve()
            if not source.is_file():
                self.logs.append("SKIP missing file: " + str(source))
                continue
            ext = source.suffix.lower()
            with tempfile.TemporaryDirectory(prefix="mooink_conv_") as temp:
                if ext in IMAGE_EXTS:
                    with Image.open(source) as img:
                        # GIF/TIFF multi-frame semantics are not screen page
                        # semantics; use first frame unless exported to PDF.
                        items = [(1, ImageOps.exif_transpose(img).convert("RGB"))]
                elif ext in OFFICE_EXTS:
                    items = self._pdf_pages(source, Path(temp))
                elif ext == ".pdf":
                    # Optional convenience: PDF input (same rendering pipeline)
                    import fitz
                    def page_images():
                        with fitz.open(source) as pdf:
                            for i, page in enumerate(pdf):
                                if i >= 100:
                                    break
                                pix = page.get_pixmap(matrix=fitz.Matrix(1.5, 1.5), alpha=False)
                                yield i+1, Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
                    items = page_images()
                else:
                    self.logs.append("SKIP unsupported: " + str(source))
                    continue
                for page_index, img in items:
                    if img.width <= 0 or img.height <= 0:
                        continue
                    landscape = (img.width >= img.height) if self.orientation == "auto" else self.orientation == "landscape"
                    width, height = RESOLUTIONS[self.size]
                    if landscape:
                        width, height = height, width
                    # If user locks portrait and supplies landscape, rotate
                    # the *content* so it fills the portrait panel; vice versa.
                    changed = False
                    if (img.width > img.height) != landscape and img.width != img.height:
                        img = img.transpose(Image.Transpose.ROTATE_90)
                        changed = True
                    mode = self.fit
                    if mode == "smart":
                        source_aspect = img.width / img.height
                        dest_aspect = width / height
                        crop_fraction = 1.0 - min(source_aspect/dest_aspect, dest_aspect/source_aspect)
                        # Office sheets/Word should be readable and never cropped.
                        mode = "contain" if ext in OFFICE_EXTS or crop_fraction > 0.10 else "cover"
                    img = ImageEnhance.Contrast(ImageOps.grayscale(img)).enhance(1.06)
                    if mode == "cover":
                        out = ImageOps.fit(img, (width, height), method=Image.Resampling.LANCZOS,
                                           centering=(.5, .5))
                    else:
                        out = Image.new("L", (width, height), 255)
                        scaled = ImageOps.contain(img, (width, height), Image.Resampling.LANCZOS)
                        out.paste(scaled, ((width-scaled.width)//2, (height-scaled.height)//2))
                    # 16 gray levels are a closer match to first-gen e-paper.
                    out = out.point([int(round(v / 17) * 17) for v in range(256)])
                    digest = hashlib.sha1(str(source).encode("utf-8")).hexdigest()[:7]
                    dest = self.output_dir / (safe_file_name(source.stem) +
                                               "_p%03d_%s_%s.jpg" % (page_index, self.size.replace('.',''), digest))
                    for quality in (92, 85, 76, 65, 54, 43, 33, 25):
                        out.save(dest, format="JPEG", quality=quality, optimize=True)
                        if dest.stat().st_size <= MAX_JPG_SIZE:
                            break
                    if dest.stat().st_size > MAX_JPG_SIZE:
                        dest.unlink(missing_ok=True)
                        raise RuntimeError("輸出大於 1MB：" + source.name)
                    self.logs.append("Converted %s page %d -> %s (%dx%d, %s, rotated=%s, %d bytes)" %
                                     (source.name, page_index, dest.name, width, height,
                                      mode, changed, dest.stat().st_size))
                    results.append(dest)
        self._save_manifest(results)
        return results

    def _save_manifest(self, images):
        payload = {"created": datetime.now().isoformat(), "size": self.size,
                   "orientation": self.orientation, "fit": self.fit,
                   "files": [str(x) for x in images], "log": self.logs,
                   "note": "Images may not refresh until reader transitions through real sleep."}
        (self.output_dir / "manifest.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def manage(self, files=None, operation="list", backup_dir="mooink_backup", *, confirm=True):
        if self.io is None:
            raise RuntimeError("尚未指定 USB 或已掛載的 Wi-Fi sleep 目錄")
        if operation == "list":
            return self.io.list_sleep()
        if operation == "add":
            if not files:
                raise ValueError("新增需指定檔案")
            result = []
            for path in files:
                result.append((str(path), self.io.add_file(Path(path))))
            return result
        if operation in ("delete", "clear"):
            current = self.io.list_sleep()
            names = ([str(f) for f in files] if operation == "delete" and files else
                     [f["name"] for f in current] if operation == "clear" else [])
            if not names:
                return []
            print("將先備份每個 JPG/PNG，然後刪除以下 sleep 圖片（不動其他檔案）：")
            for name in names:
                print("  " + name)
            # A GUI may confirm on the main thread, then pass confirm=False.
            # CLI callers keep the original interactive confirmation prompts.
            if confirm and not question("確定執行？", default=False):
                return ["cancelled"]
            return [(name, self.io.backup_and_delete(
                name, Path(backup_dir), confirm_delete=confirm)) for name in names]
        raise ValueError("Unsupported operation: " + operation)

    def interactive(self):
        while True:
            print("\nmooInk sleep 畫面管理（%s / %s / %s）" %
                  (self.size, self.orientation, self.fit))
            print("1. 查看裝置 sleep 圖片")
            print("2. 批次轉換圖片、Excel、Word、PDF")
            print("3. 將已產生圖片新增到 sleep")
            print("4. 備份並刪除指定圖片")
            print("5. 備份並清空 sleep 圖片")
            print("6. Wi-Fi API 連線探測（唯讀）")
            print("0. 離開")
            choice = input("請輸入編號：").strip()
            try:
                if choice == "0":
                    return
                if choice == "1":
                    for f in self.manage(operation="list"):
                        print("  %-60s %s" % (f["name"], f.get("size")))
                elif choice == "2":
                    raw = input("請輸入檔案路徑（多個檔案可用 | 分隔）：").strip()
                    files = [x.strip().strip('"') for x in raw.split("|") if x.strip()]
                    results = self.convert(files)
                    for p in results:
                        print("  " + str(p))
                    print("完成 %d 張，輸出位置：%s" % (len(results), self.output_dir.resolve()))
                elif choice == "3":
                    candidates = sorted(self.output_dir.glob("*.jpg"))
                    print("準備上傳 %d 個已產生 JPG" % len(candidates))
                    if candidates and question("確認新增？"):
                        print(self.manage(candidates, operation="add"))
                elif choice == "4":
                    name = input("輸入欲刪除的完整檔名：").strip()
                    print(self.manage([name], operation="delete"))
                elif choice == "5":
                    print(self.manage(operation="clear"))
                elif choice == "6":
                    print("已驗證公開服務端口：" + str(self.io.wifi_probe()))
                else:
                    print("未知選項")
            except (OSError, RuntimeError, ValueError, FileNotFoundError, NotImplementedError) as exc:
                print("操作未完成：" + str(exc))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--size", choices=RESOLUTIONS, default="13.3")
    parser.add_argument("--orientation", choices=("auto","portrait","landscape"), default="auto")
    parser.add_argument("--fit", choices=("smart","cover","contain"), default="smart")
    parser.add_argument("--transport", choices=("usb", "wifi"), default="usb")
    parser.add_argument("--ip", help="mooInk 的 LAN IP；只用於探測，不假設可存取 sleep")
    parser.add_argument("--wifi-share", help="已掛載、確定可存取的遠端 sleep 資料夾；非 Sony 原生 API")
    parser.add_argument("--usb-index", type=int, help="USB 順序編號；兩台同名設備的寫入仍會被禁止")
    parser.add_argument("--input", nargs="+", help="批次轉檔：圖片、Excel、Word、PDF")
    parser.add_argument("--output-dir", default="mooink_prepared")
    parser.add_argument("--action", choices=("menu", "convert", "list", "add", "delete", "clear", "probe"), default="menu")
    parser.add_argument("--files", nargs="*", help="新增／刪除指定檔案或檔名")
    parser.add_argument("--backup-dir", default="mooink_backup")
    args = parser.parse_args(argv)
    io_layer = MooinkLowLevel(transport=args.transport, ip=args.ip, wifi_share=args.wifi_share,
                             usb_index=args.usb_index)
    mgr = SleepScreenManager(size=args.size, orientation=args.orientation,
                             fit=args.fit, output_dir=args.output_dir, io_layer=io_layer)
    try:
        if args.action == "menu":
            mgr.interactive()
        elif args.action == "convert":
            for f in mgr.convert(args.input or []):
                print(f)
        elif args.action == "probe":
            print(json.dumps(io_layer.wifi_probe(), ensure_ascii=False, indent=2))
        else:
            print(json.dumps(mgr.manage(files=args.files, operation=args.action,
                                        backup_dir=args.backup_dir), ensure_ascii=False, indent=2))
    except (OSError, ValueError, RuntimeError, NotImplementedError) as exc:
        print("ERROR:", exc, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())