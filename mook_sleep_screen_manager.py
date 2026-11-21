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

class MtpUnknownSizeError(RuntimeError):
    """MTP does not report the source size; automatic deletion is unsafe."""


class MtpBackupNotStartedError(RuntimeError):
    """MTP Shell copy did not materialize a local backup (systemic blocker)."""


class MtpDeletionUnconfirmedError(RuntimeError):
    """A Shell delete request was issued but has not been observed as complete."""



def question(prompt: str, default: bool = False) -> bool:
    s = input(prompt + (" [Y/n]: " if default else " [y/N]: ")).strip().lower()
    return default if not s else s in ("y", "yes", "是", "好")


def safe_file_name(text: str) -> str:
    import re
    out = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", text).strip(" .")
    return out[:90] or "image"


def cleanup_empty_backup_dirs(backup_dir):
    """Remove only directly nested EMPTY mooink_YYYYMMDD_HHMMSS_NNN folders.

    Never walk recursively, never delete contents or files, never follow
    symlinks. Do not call during an in-flight Shell.CopyHere transfer.
    """
    import re
    base = Path(backup_dir)
    if not base.is_dir():
        return []
    removed = []
    for child in base.iterdir():
        if not re.fullmatch(r"mooink_\d{8}_\d{6}_\d{3}", child.name):
            continue
        if child.is_symlink() or not child.is_dir():
            continue
        try:
            child.rmdir()  # Succeeds only for a truly empty directory.
        except OSError:
            continue
        removed.append(str(child))
    return removed


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

    @staticmethod
    def _mtp_size(item):
        """Read size without assuming ShellItem.Size is supplied by WPD."""
        for getter in (
            lambda: getattr(item, "Size"),
            lambda: item.ExtendedProperty("System.Size"),
        ):
            try:
                value = getter()
                if value is not None and not isinstance(value, bool):
                    number = int(value)
                    if number > 0:
                        return number
            except (AttributeError, TypeError, ValueError, OverflowError, OSError):
                continue
            except Exception:
                # COM may return a failing HRESULT for unsupported properties.
                continue
        return None

    def list_sleep(self):
        target = self._target()
        if isinstance(target, Path):
            return [{"name": x.name, "size": x.stat().st_size} for x in sorted(target.iterdir())
                    if x.is_file() and x.suffix.lower() in OUTPUT_EXTS]
        items = []
        for item in target.Items():
            n = str(item.Name)
            if not bool(item.IsFolder) and Path(n).suffix.lower() in OUTPUT_EXTS:
                size = self._mtp_size(item)
                items.append({"name": n, "size": size})
        return sorted(items, key=lambda x: x["name"].lower())

    def add_file(self, path: Path, overwrite=False, progress=None):
        """Upload to sleep, reporting stages through the optional callback.

        Direct WPD replaces Shell.CopyHere for USB: it confirms success by
        reading back the uploaded file and comparing SHA256, not by trusting
        asynchronous Shell requests or missing MTP file-size properties.
        """
        path = Path(path).resolve()
        if not path.is_file() or path.suffix.lower() not in OUTPUT_EXTS:
            raise ValueError("只接受已產生的 JPG/PNG 檔案")

        def notify(stage, **fields):
            if progress:
                try:
                    progress(stage, fields)
                except Exception:
                    pass

        if self.transport == "usb":
            if len(self.usb_devices()) != 1:
                raise RuntimeError("USB 上傳僅允許同時連接一台 mooInk；請確認裝置")
            if overwrite:
                raise ValueError("USB 不允許無備份覆寫已有檔案")
            from mook_mtp_wpd import upload_one
            return upload_one(path, progress=progress)

        target = self._target(write=True)
        if not isinstance(target, Path):
            raise RuntimeError("Wi-Fi 上傳需要已掛載的 sleep 資料夾")
        names = {p["name"].casefold() for p in self.list_sleep()}
        if path.name.casefold() in names and not overwrite:
            raise FileExistsError("sleep 已有同名檔案：" + path.name)
        destination = target / path.name
        notify("uploading", message="正在複製到已掛載的 sleep 資料夾",
               copied=0, total=path.stat().st_size)
        shutil.copy2(path, destination)
        notify("verifying", message="檢查共享資料夾複製後的 SHA-256",
               copied=destination.stat().st_size, total=path.stat().st_size)
        if destination.stat().st_size != path.stat().st_size:
            raise RuntimeError("共享 sleep 檔案大小不符，不宣稱成功")
        def sha256(src):
            digest = hashlib.sha256()
            with src.open("rb") as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(block)
            return digest.digest()
        if sha256(path) != sha256(destination):
            raise RuntimeError("共享 sleep 檔案 SHA-256 不一致，不宣稱成功")
        notify("verified", message="已掛載 sleep 共享檔案 SHA-256 驗證成功",
               copied=path.stat().st_size, total=path.stat().st_size)
        return "uploaded+sha256-verified; sleep: " + path.name

    def backup_and_delete(self, filename: str, backup_dir: Path, *,
                          confirm_delete=True, progress=None, backup_timeout=50.0,
                          delete_after=True, quiet_wpd=False):
        """Back up one file and verify before requesting deletion.

        MTP CopyHere has no true byte-progress API. progress(stage, details)
        receives observed local backup bytes, not a guaranteed transfer rate.
        Errors never trigger deletion. Caller may supply a non-Tk callback.
        """
        def notify(stage, **details):
            if progress is not None:
                try:
                    progress(stage, details)
                except Exception:
                    # Progress presentation cannot change delete safety.
                    pass

        notify("locate", message="確認裝置及圖片")
        if Path(filename).name != filename or Path(filename).suffix.lower() not in OUTPUT_EXTS:
            raise ValueError("只能刪除 sleep 根目錄下的 JPG/PNG，且不可使用路徑")
        if quiet_wpd:
            if self.transport != "usb":
                raise RuntimeError("靜默 WPD 刪除僅限 USB；Wi-Fi 共享資料夾不需此模式")
            # Cross-check Windows Shell's recognized mooInk count before WPD
            # opens any device. Never silently fall back to Explorer verbs.
            if len(self.usb_devices()) != 1:
                raise RuntimeError("靜默 WPD 模式必須僅連接一台 mooInk USB 裝置")
            from mook_mtp_wpd import backup_and_delete_one
            return backup_and_delete_one(
                filename, backup_dir, delete_after=delete_after, progress=progress
            )
        target = self._target(write=True)
        found = [x for x in self.list_sleep() if x["name"] == filename]
        if not found:
            raise FileNotFoundError(filename)
        # Do NOT create even the backup root here. MTP metadata validation
        # must run first; otherwise 22 missing sizes create 22 empty folders.
        backup_dir = Path(backup_dir).resolve()

        def reserve_backup_path():
            backup_dir.mkdir(parents=True, exist_ok=True)
            path = backup_dir / filename
            if not path.exists():
                return path
            # Shell.CopyHere always writes the original source item name;
            # put duplicates in a new destination directory, not a new name.
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            counter = 1
            while True:
                location = backup_dir / ("mooink_%s_%03d" % (stamp, counter))
                try:
                    location.mkdir()
                    return location / filename
                except FileExistsError:
                    counter += 1
        if isinstance(target, Path):
            src = target / filename
            expected_size = src.stat().st_size
            backup = reserve_backup_path()
            notify("backup_target", message="本機備份位置：" + str(backup))
            notify("copying", message="複製共享資料夾圖片", copied=0, total=expected_size)
            shutil.copy2(src, backup)
            notify("verifying", message="確認備份大小", copied=backup.stat().st_size,
                   total=expected_size)
            if not backup.exists() or backup.stat().st_size != src.stat().st_size:
                raise RuntimeError("備份驗證失敗，拒絕刪除")
            if not delete_after:
                notify("verified", message="備份完成，不刪除原檔")
                return "backup-only-verified; backup: " + str(backup)
            notify("deleting", message="備份已驗證，正在刪除")
            src.unlink()
            if src.exists():
                raise RuntimeError("共享資料夾刪除結果未確認，請手動檢查；備份已保留：" + str(backup))
            notify("deleted", message="已確認刪除")
            return "deleted+verified; backup: " + str(backup)

        # Windows MTP Shell.CopyHere may finish asynchronously, show a
        # transfer dialog, or only publish the destination at the end.
        # Do not infer success from CopyHere returning.
        item = next((x for x in target.Items() if str(x.Name) == filename), None)
        if item is None:
            raise FileNotFoundError(filename)
        src_size = self._mtp_size(item) or found[0].get("size")
        if src_size is None or src_size <= 0:
            if delete_after:
                notify("failed", message="MTP 無法取得來源大小；拒絕自動刪除，請先單張備份測試")
                raise MtpUnknownSizeError(
                    "USB MTP 未提供來源大小（Size 與 System.Size 都不可用），"
                    "自動刪除不可驗證；尚未建立任何備份資料夾。"
                    "請使用「單張備份測試（不刪除）」診斷。")
            notify("copying", message="MTP 來源大小未知：只測試複製，絕不刪除")
            src_size = None
        if not 5 <= backup_timeout <= 300:
            raise ValueError("單張 MTP 備份等待秒數必須介於 5 至 300")
        backup = reserve_backup_path()
        notify("backup_target", message="本機備份位置：" + str(backup))
        backup_dir_shell = self._shell.Namespace(str(backup.parent))
        if backup_dir_shell is None:
            # Only cleanup the newly created backup directory if no copy
            # operation was submitted; never remove a file-filled folder.
            if backup.parent != backup_dir:
                try:
                    backup.parent.rmdir()
                except OSError:
                    pass
            raise RuntimeError("Windows Shell 無法開啟備份資料夾")
        notify("copying", message="要求 Windows 複製 USB 圖片", copied=0,
               total=src_size, timeout=backup_timeout)
        deadline = time.monotonic() + backup_timeout
        backup_dir_shell.CopyHere(item, 0)
        stable = 0
        last_size = None
        current_size = 0
        while time.monotonic() < deadline:
            try:
                current_size = backup.stat().st_size if backup.exists() else 0
            except OSError:
                current_size = 0
            notify("copying", message="等待 Windows MTP 備份完成",
                   copied=current_size, total=src_size, timeout=backup_timeout)
            if src_size is None:
                # Backup-only fallback: no original size, so require an
                # unchanged nonempty local file over several observations.
                # This is diagnostic evidence, NOT proof of source equality.
                stable = stable + 1 if current_size > 0 and last_size == current_size else 0
                if stable >= 6:
                    break
            elif current_size == src_size:
                stable = stable + 1 if last_size == current_size else 1
                if stable >= 2:
                    break
            else:
                stable = 0
                if current_size > src_size:
                    raise RuntimeError(
                        "備份比裝置宣告檔案更大（%d > %d），保留原檔" %
                        (current_size, src_size))
            last_size = current_size
            time.sleep(0.5)
        else:
            if current_size == 0:
                raise MtpBackupNotStartedError(
                    "USB MTP CopyHere 等待 %.0f 秒後，目的資料夾仍未出現備份檔；"
                    "可能是 Windows Shell 傳輸未啟動、遭提示視窗阻擋，"
                    "或 MTP 裝置未支援此複製方式。停止連續嘗試以免空等。"
                    "裝置宣告大小=%s bytes；預期本機位置=%s" %
                    (backup_timeout, src_size if src_size is not None else "未知", backup))
            raise RuntimeError(
                "USB MTP 備份逾時或大小不符；不刪除原檔"
                "（等待 %.0f 秒，裝置檔案大小=%s，本機備份大小=%d，位置=%s）" %
                (backup_timeout, src_size if src_size is not None else "未知", current_size, backup))
        if src_size is None:
            if delete_after:
                raise MtpUnknownSizeError("來源大小未知，保留原檔")
            # Verify the local image decodes. Still NOT sufficient evidence
            # to delete the device source: no source byte count/hash.
            try:
                from PIL import Image
                with Image.open(backup) as image:
                    image.load()
            except Exception as exc:
                raise RuntimeError("來源大小未知且備份圖片無法完整解碼；原檔保留：" + str(exc))
            notify("verified", message="本機圖片可解碼，但來源大小未知，原檔保留",
                   copied=current_size, total=None)
            return "backup-only-unverified-source-size; backup: " + str(backup)
        notify("verifying", message="本機備份大小一致（連續兩次確認，尚非雜湊比對）",
               copied=current_size, total=src_size)
        if not delete_after:
            notify("verified", message="備份檔案大小驗證完成，不刪除原檔")
            return "backup-only-size-verified; backup: " + str(backup)
        if confirm_delete and not question("確定刪除 mooInk 的 %s？已備份到 %s" % (filename, backup)):
            return "cancelled-after-backup: " + str(backup)
        notify("deleting", message="備份已完成，送出 MTP 刪除要求")
        item.InvokeVerb("delete")
        # This Shell verb is asynchronous. Observe the device rather than
        # reporting a successful deletion just because InvokeVerb returned.
        notify("deleting", message="已送出刪除要求，正在重新檢查裝置清單")
        deadline = time.monotonic() + 8.0
        while time.monotonic() < deadline:
            time.sleep(0.8)
            if filename not in {row["name"] for row in self.list_sleep()}:
                notify("deleted", message="MTP 清單確認原檔已消失")
                return "deleted+verified; backup: " + str(backup)
        notify("pending", message="MTP 刪除未確認；不會接著刪除其他檔案")
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

    def _pdf_pages(self, source: Path, temporary_dir: Path, render_scale=1.5):
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
                pix = page.get_pixmap(matrix=fitz.Matrix(render_scale, render_scale), alpha=False)
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

    def manage(self, files=None, operation="list", backup_dir="mooink_backup",
               *, confirm=True, continue_on_error=False, on_result=None,
               on_stage=None, backup_timeout=None, stop_on_mtp_stall=False,
               quiet_wpd=False):
        """Manage sleep files. Batch deletion can report every file separately.

        continue_on_error=True returns a list of records:
          {name, status=deleted|pending|skipped|failed, detail}.
        Each file is independently backed up and verified before deletion.
        on_result(index, total, record) is called after each attempt.
        on_stage(index, total, record) reports intermediate copy/wait phases.
        All callbacks execute on the caller's worker thread, not the Tk thread.
        stop_on_mtp_stall=True halts after a copy that never starts, or after
        an unconfirmed MTP delete, to avoid many long useless retries.
        Without continue_on_error the original tuple return format remains.
        """
        if self.io is None:
            raise RuntimeError("尚未指定 USB 或已掛載的 Wi-Fi sleep 目錄")
        if operation == "list":
            return self.io.list_sleep()
        if operation == "add":
            if not files:
                raise ValueError("新增需指定檔案")
            if not continue_on_error:
                return [(str(path), self.io.add_file(Path(path))) for path in files]

            # Upload one file at a time. Unlike Shell.CopyHere, WPD reports
            # verified results only after reading the device copy back.
            results = []
            blocked_reason = None
            total = len(files)
            for index, raw in enumerate(files, 1):
                name = Path(raw).name

                def emit(stage, details=None):
                    if on_stage is None:
                        return
                    event = {"name": name, "stage": stage}
                    if details:
                        event.update(details)
                    try:
                        on_stage(index, total, event)
                    except Exception:
                        pass

                if blocked_reason is not None:
                    record = {
                        "name": name, "status": "blocked",
                        "detail": "未執行，前一檔 WPD 裝置問題：" + blocked_reason,
                    }
                else:
                    emit("locate", {"message": "準備上傳 %s" % name})
                    try:
                        progress = emit if on_stage else None
                        detail = str(self.io.add_file(Path(raw), progress=progress))
                        if not detail.startswith("uploaded+sha256-verified;"):
                            raise RuntimeError(
                                "上傳未得到 SHA-256 驗證成功回覆：" + detail
                            )
                        record = {"name": name, "status": "verified", "detail": detail}
                    except FileExistsError as exc:
                        record = {
                            "name": name, "status": "skipped",
                            "detail": "同名檔案已存在，未覆寫：" + str(exc),
                        }
                    except Exception as exc:
                        from mook_mtp_wpd import QuietMtpUnavailable
                        if isinstance(exc, QuietMtpUnavailable):
                            blocked_reason = str(exc)
                        record = {
                            "name": name, "status": "failed",
                            "detail": type(exc).__name__ + ": " + str(exc),
                        }
                results.append(record)
                emit(record["status"], {"message": record["detail"]})
                if on_result:
                    try:
                        on_result(index, total, record)
                    except Exception:
                        pass
            return results
        if operation in ("delete", "clear"):
            # For explicit selections, let each individual backup_and_delete
            # check the device. A failed preliminary listing must not abort
            # the entire batch before any per-file errors can be recorded.
            if operation == "delete" and files:
                names = [str(f) for f in files]
            elif operation == "clear":
                names = [f["name"] for f in self.io.list_sleep()]
            else:
                names = []
            if not names:
                return []
            # The GUI already confirmed through its dialog; do not print
            # to a missing pythonw stdout or call input() on a worker thread.
            if confirm:
                print("將先備份每個 JPG/PNG，然後刪除以下 sleep 圖片（不動其他檔案）：")
                for name in names:
                    print("  " + name)
                if not question("確定執行？", default=False):
                    return ["cancelled"]
            if not continue_on_error:
                return [(name, self.io.backup_and_delete(
                    name, Path(backup_dir), confirm_delete=confirm)) for name in names]

            # One file at a time: BACKUP -> VERIFY -> DELETE, then next.
            # A failed backup never requests a delete for that file.
            results = []
            total = len(names)
            blocked_reason = None
            for index, name in enumerate(names, 1):
                def emit(stage, details=None):
                    if on_stage is None:
                        return
                    event = {"name": name, "stage": stage}
                    if details:
                        event.update(details)
                    try:
                        on_stage(index, total, event)
                    except Exception:
                        pass

                if blocked_reason is not None:
                    record = {
                        "name": name, "status": "blocked",
                        "detail": "未執行；前一張發生系統性 MTP 問題：" + blocked_reason,
                    }
                    results.append(record)
                    emit("blocked", {"message": record["detail"]})
                    if on_result is not None:
                        try:
                            on_result(index, total, record)
                        except Exception:
                            pass
                    continue
                emit("locate", {"message": "開始處理第 %d/%d 張" % (index, total)})
                try:
                    options = {}
                    if on_stage is not None:
                        options["progress"] = emit
                    if backup_timeout is not None:
                        options["backup_timeout"] = float(backup_timeout)
                    if quiet_wpd:
                        options["quiet_wpd"] = True
                    detail = str(self.io.backup_and_delete(
                        name, Path(backup_dir), confirm_delete=confirm, **options))
                    if detail.startswith("deleted+verified;"):
                        status = "deleted"
                    elif detail.startswith("backup-only-"):
                        status = "backed_up"
                    elif detail.startswith("cancelled-after-backup:"):
                        status = "skipped"
                    else:
                        # Windows Shell/MTP deletion is asynchronous. A
                        # completed call is NOT proof that the file disappeared.
                        status = "pending"
                        if stop_on_mtp_stall and getattr(self.io, "transport", None) == "usb":
                            blocked_reason = "已送出刪除命令，但無法確認裝置已刪除"
                    record = {"name": name, "status": status, "detail": detail}
                except (MtpBackupNotStartedError, MtpUnknownSizeError) as exc:
                    if stop_on_mtp_stall:
                        blocked_reason = str(exc)
                    record = {
                        "name": name, "status": "failed",
                        "detail": type(exc).__name__ + ": " + str(exc),
                    }
                except Exception as exc:
                    # A missing direct-WPD backend or ambiguous device
                    # identity is systemic, not an independent per-file error.
                    from mook_mtp_wpd import QuietMtpUnavailable
                    if stop_on_mtp_stall and isinstance(exc, QuietMtpUnavailable):
                        blocked_reason = str(exc)
                    record = {
                        "name": name, "status": "failed",
                        "detail": type(exc).__name__ + ": " + str(exc),
                    }
                emit(record["status"], {"message": record["detail"]})
                results.append(record)
                if on_result is not None:
                    try:
                        on_result(index, total, record)
                    except Exception:
                        # A broken progress reporter must not interrupt a
                        # safety-critical batch or cause a repeated deletion.
                        pass
            return results
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



def convert_both_sizes(paths, *, orientation="auto", fit="smart",
                       output_dir="mooink_prepared"):
    """Convert each input for both reader sizes, using one shared algorithm.

    Files are separated into <output_dir>/10.3 and <output_dir>/13.3.
    Each subfolder has its own manifest.json. This never communicates
    with a connected reader; device-size selection only affects uploads.
    """
    root = Path(output_dir).expanduser().resolve()
    if not str(root):
        raise ValueError("請指定圖片輸出根目錄")
    source_paths = list(paths)
    outputs = {}
    logs = []
    for size in ("10.3", "13.3"):
        manager = SleepScreenManager(
            size=size, orientation=orientation, fit=fit,
            output_dir=root / size,
        )
        outputs[size] = manager.convert(source_paths)
        logs.extend("[%s 吋] %s" % (size, line) for line in manager.logs)
    return outputs, logs


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