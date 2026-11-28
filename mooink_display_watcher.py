#!/usr/bin/env python3
"""Windows watcher: DOCX -> grayscale PNG -> USB MTP sleep -> optional Pico Space.
Only the reserved image for the selected size can be replaced.
"""
from __future__ import annotations
import hashlib
import io
import time
from datetime import datetime
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import queue
import re
import shutil
import tempfile
import threading
from dataclasses import dataclass, asdict, replace
import uuid

from mook_sleep_screen_manager import SleepScreenManager, RESOLUTIONS
from mooink_multi_device import (DeviceRegistry, MultiDeviceController,
                                 update_selected_docx, ManualReaderUnavailable)
from mooink_docx_pages import prepare_media_preview, render_document_page, render_selected_page_png
from mooink_photo import prepare_photo_batch

CONFIG_DIR = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "mooink"
CONFIG = CONFIG_DIR / "mooink_display_watcher.json"
MANUAL_SELECTION = CONFIG_DIR / "mooink_manual_docx.json"
PHOTO_LIBRARY = CONFIG_DIR / "mooink_photo_library.json"
STATE = CONFIG_DIR / "mooink_display_watcher_state.json"
LOG = CONFIG_DIR / "mooink_display_watcher.log"
PROJECT_DIR = Path(__file__).resolve().parent
TEMPLATE_DIR = PROJECT_DIR / "template"
USB_POLL_SEC = 1.0  # Independent of the document check interval.
WORK_FILES = tuple("mooink_display_" + tag + ext
                   for tag in ("10", "13") for ext in (".docx", ".png"))


def initialize_workfiles(directory=PROJECT_DIR, template_dir=TEMPLATE_DIR):
    """Seed both size pairs once, without replacing existing local files.

    Exclusive creation is intentional: another process or user may create
    a file after the initial existence check. Templates are read-only inputs.
    """
    directory = Path(directory).resolve()
    template_dir = Path(template_dir).resolve()
    if directory == template_dir or template_dir in directory.parents:
        raise ValueError("template 是原始範本資料夾，不能作為監控目錄")
    if not directory.is_dir():
        raise ValueError("工作目錄不存在：" + str(directory))
    created = []
    for name in WORK_FILES:
        dst = directory / name
        if dst.exists() or dst.is_symlink():
            continue
        src = template_dir / name
        data = src.read_bytes()
        try:
            stream = dst.open("xb")
        except FileExistsError:
            continue
        try:
            with stream:
                stream.write(data)
        except BaseException:
            dst.unlink(missing_ok=True)
            raise
        created.append(name)
    return created


@dataclass(frozen=True)
class Settings:
    watch_dir: str = str(Path(__file__).resolve().parent)
    size: str = "10.3"
    interval_sec: int = 60
    pico_port: str = "COM6"  # blank disables Pico
    enable_usb: bool = False  # explicit opt-in for USB image replacement
    delete_other_sleep_images: bool = False  # opt-in; backup before cleanup
    update_on_connect: bool = True  # initial USB connection and reconnection
    update_on_file_change: bool = True  # scheduled DOCX checks
    custom_docx: str = ""  # blank: size-specific DOCX in watch_dir
    stay_resident: bool = False  # closing window quits unless explicitly enabled

    def validate(self):
        if self.enable_usb and not (self.update_on_connect or self.update_on_file_change):
            raise ValueError("啟用 USB 自動更新時，至少選擇「插拔更新」或「Word 定時更新」一項")
        if self.custom_docx:
            source = Path(self.custom_docx).expanduser()
            if source.suffix.casefold() != ".docx" or not source.is_file():
                raise ValueError("請選擇實際存在的 DOCX 檔案")
        if self.size not in RESOLUTIONS:
            raise ValueError("尺寸必須為 10.3 或 13.3")
        if not 5 <= self.interval_sec <= 86400:
            raise ValueError("檢查秒數必須為 5～86400")
        if not Path(self.watch_dir).is_dir():
            raise ValueError("監控目錄不存在")
        watch_dir = Path(self.watch_dir).resolve()
        if watch_dir == TEMPLATE_DIR.resolve() or TEMPLATE_DIR.resolve() in watch_dir.parents:
            raise ValueError("template 是原始範本資料夾，不能作為監控目錄")
        if self.pico_port and not re.fullmatch(r"COM[1-9][0-9]*", self.pico_port, re.I):
            raise ValueError("Pico COM 埠格式為 COM6 等，空白表示停用")
        return self

    def paths(self):
        stem = "mooink_display_" + ("10" if self.size == "10.3" else "13")
        root = Path(self.watch_dir)
        source = (Path(self.custom_docx).expanduser() if self.custom_docx
                  else root / (stem + ".docx"))
        return source, root / (stem + ".png")

    def render_key(self):
        source, _ = self.paths()
        return json.dumps(["render-v5", str(Path(self.watch_dir).resolve()), self.size,
                           str(source.resolve()), bool(self.custom_docx)],
                          ensure_ascii=False)

def read_json(path, default):
    try:
        result = json.loads(Path(path).read_text(encoding="utf-8"))
        return result if isinstance(result, dict) else default
    except (OSError, ValueError):
        return default

def write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)

def load_settings():
    data = read_json(CONFIG, {})
    try:
        # Previous draft used a separate enable_pico_wake flag.
        if data.get("enable_pico_wake") is False:
            data["pico_port"] = ""
        return Settings(**{k:v for k,v in data.items() if k in Settings.__dataclass_fields__}).validate()
    except (ValueError, TypeError):
        return Settings()

def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024*1024), b""):
            digest.update(block)
    return digest.hexdigest()

def crop_document_content(image, *, threshold=245):
    """Remove mostly-white PDF page margins without cutting nonwhite content.

    Thresholding before resize keeps faint text, lines and inserted images.
    If the document is completely white, retain the full page.
    """
    from PIL import Image
    grayscale = image.convert("L")
    mask = grayscale.point([255 if value < threshold else 0
                            for value in range(256)], mode="L")
    bounds = mask.getbbox()
    return grayscale.crop(bounds) if bounds else grayscale


def fit_minimal_margin(image, size, *, border_fraction=0.008):
    """Scale cropped content to the e-paper canvas with a thin white safety edge."""
    from PIL import Image, ImageOps
    width, height = RESOLUTIONS[size]
    border = max(4, round(min(width, height) * border_fraction))
    inner = (width - 2 * border, height - 2 * border)
    prepared = ImageOps.pad(image.convert("L"), inner,
                            method=Image.Resampling.LANCZOS, color=255)
    final = Image.new("L", (width, height), 255)
    final.paste(prepared, (border, border))
    return final


def render_png(source, output, size, *, crop_margins=False):
    """Render a one-page Word document to grayscale PNG.

    Custom DOCX mode removes the rendered page's outer whitespace before
    fitting the content to the full resolution. Default templates retain
    their original document margins for backward compatibility.
    Multi-page files remain refused instead of silently hiding pages.
    """
    from PIL import Image, ImageOps
    com = None
    if os.name == "nt":
        import pythoncom
        com = pythoncom
        com.CoInitialize()
    try:
        with tempfile.TemporaryDirectory(prefix="mooink_watch_") as tmp:
            work = Path(tmp)
            mgr = SleepScreenManager(size=size, orientation="portrait", fit="contain",
                                     output_dir=work)
            pages = mgr._pdf_pages(Path(source), work, render_scale=3.5)
            try:
                first = next(pages, None)
                if first is None:
                    raise RuntimeError("Word 沒有可轉換頁面")
                if next(pages, None) is not None:
                    raise RuntimeError("Word 超過一頁；請調整到單頁，避免漏掉內容")
                if crop_margins:
                    # Trim the high-resolution PDF raster BEFORE downsampling.
                    content = crop_document_content(first[1])
                    out = fit_minimal_margin(content, size)
                else:
                    out = ImageOps.pad(first[1].convert("L"), RESOLUTIONS[size],
                                       method=Image.Resampling.LANCZOS, color=255)
            finally:
                pages.close()
            out = out.point([round(i/17)*17 for i in range(256)])
            temp = Path(output).with_name("." + Path(output).name + "." +
                                          uuid.uuid4().hex + ".tmp")
            try:
                out.save(temp, format="PNG", optimize=True)
                os.replace(temp, output)
            finally:
                temp.unlink(missing_ok=True)
    finally:
        if com is not None:
            com.CoUninitialize()


def render_cropped_png(source, output, size):
    return render_png(source, output, size, crop_margins=True)


def usb_upload(settings, source, log, reader_id=None):
    import pythoncom
    from mook_mtp_wpd import replace_watch_image, cleanup_other_sleep_images
    pythoncom.CoInitialize()
    try:
        result = replace_watch_image(
            source, CONFIG_DIR / "watcher_backup",
            progress=lambda stage, detail: log(detail.get("message", stage)),
            reader_id=reader_id)
        if settings.delete_other_sleep_images:
            # Only after WPD has SHA256-verified the target. Keep this
            # destructive opt-in isolated to the SAME identified reader.
            if reader_id is None:
                raise RuntimeError("刪除其他 sleep 圖片需要指定已配對的裝置 ID")
            removed = cleanup_other_sleep_images(
                Path(source).name, CONFIG_DIR / "other_sleep_backup",
                reader_id=reader_id,
                progress=lambda stage, detail: log(detail.get("message", stage)))
            result["other_sleep_deleted"] = len(removed)
        return result
    finally:
        pythoncom.CoUninitialize()


def usb_list(reader_id=None):
    import pythoncom
    from mook_mtp_wpd import list_images
    pythoncom.CoInitialize()
    try:
        return list_images(reader_id=reader_id)
    finally:
        pythoncom.CoUninitialize()


def usb_probe(reader_id=None):
    import pythoncom
    from mook_mtp_wpd import probe_reader
    pythoncom.CoInitialize()
    try:
        return probe_reader(reader_id=reader_id)
    finally:
        pythoncom.CoUninitialize()


def usb_readers():
    import pythoncom
    from mook_mtp_wpd import list_readers
    pythoncom.CoInitialize()
    try:
        return list_readers()
    finally:
        pythoncom.CoUninitialize()


def reader_event_matches(path, identity):
    """Match a WPD notification to the SAME reader, not merely its VID/PID."""
    path = str(path or "").casefold()
    identity = str(identity or "").casefold()
    if not path or not identity:
        return False
    def canonical(value):
        # Notification: \\?\USB#VID_...#SERIAL#{interface-GUID}
        # WPD/PnP ID:   USB\VID_...\SERIAL
        value = value.split("#{", 1)[0]
        value = value.removeprefix("\\\\?\\")
        return value.replace("#", "\\")
    return canonical(path) == canonical(identity)


def watch_device_events(stop, notify, log):
    """Windows WPD arrival/removal events accelerate polling; poll is fallback."""
    if os.name != "nt":
        return
    import ctypes
    import uuid as _uuid
    from ctypes import wintypes
    hwnd = None
    handle = None
    atom = None
    try:
        import win32gui
        import win32api
        import win32con
        class DeviceFilter(ctypes.Structure):
            _fields_ = [("size", wintypes.DWORD), ("kind", wintypes.DWORD),
                        ("reserved", wintypes.DWORD), ("guid", ctypes.c_ubyte * 16),
                        ("name", wintypes.WCHAR * 1)]
        def proc(hwnd, message, wp, lp):
            if message == 0x0219:  # WM_DEVICECHANGE
                path = ""
                if wp in (0x8000, 0x8004) and lp:  # interface arrival/removal
                    data = DeviceFilter.from_address(lp)
                    offset = DeviceFilter.name.offset
                    if data.kind == 5 and offset < data.size <= 65536:
                        count = (data.size - offset) // ctypes.sizeof(wintypes.WCHAR)
                        path = ctypes.wstring_at(lp + offset, count).split("\0", 1)[0]
                if wp in (7, 0x8000, 0x8004):
                    notify(path)
                return 1
            return win32gui.DefWindowProc(hwnd, message, wp, lp)
        cls = win32gui.WNDCLASS()
        cls.hInstance = win32api.GetModuleHandle(None)
        cls.lpszClassName = "mooInkWatcher_" + _uuid.uuid4().hex
        cls.lpfnWndProc = proc
        atom = win32gui.RegisterClass(cls)
        hwnd = win32gui.CreateWindow(atom, "", 0, 0, 0, 0, 0, 0, 0, cls.hInstance, None)
        filt = DeviceFilter()
        filt.size = ctypes.sizeof(filt)
        filt.kind = 5  # DBT_DEVTYP_DEVICEINTERFACE
        filt.guid[:] = _uuid.UUID("6ac27878-a6fa-4155-ba85-f98f491d4f33").bytes_le
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        register = user32.RegisterDeviceNotificationW
        register.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD]
        register.restype = wintypes.HANDLE
        handle = register(hwnd, ctypes.byref(filt), 0)
        if not handle:
            log("WPD 插拔事件註冊失敗；保留每秒輪詢")
        while not stop.is_set():
            win32gui.PumpWaitingMessages()
            stop.wait(0.1)
    except Exception as exc:
        log("Windows 插拔事件不可用，使用每秒輪詢：" + str(exc))
    finally:
        if handle:
            unregister = user32.UnregisterDeviceNotification
            unregister.argtypes = [wintypes.HANDLE]
            unregister.restype = wintypes.BOOL
            unregister(handle)
        if hwnd:
            win32gui.DestroyWindow(hwnd)
        if atom:
            win32gui.UnregisterClass(cls.lpszClassName, cls.hInstance)


def pico_wake(port, log):
    if not port:
        return "未設定 Pico，略過 Space"
    try:
        from pico2w_usb2bluetooth import send_space, ports
        if not any(p.device.casefold() == port.casefold() for p in ports()):
            return "找不到 " + port + "，略過 Space"
        log("Pico " + port + "：直接傳送 ASCII 0x20（Space）")
        send_space(port)
        return "Space 已送出且 CDC 回顯一致；實際喚醒仍須觀察"
    except Exception as exc:
        return "Pico Space 傳送未確認：" + str(exc)

class Processor:
    """Check WPD readiness separately from scheduled document renders."""
    def __init__(self, state_path=STATE, render=render_png, upload=usb_upload,
                 wake=pico_wake, probe=usb_probe, cropped_render=None):
        self.state_path = state_path
        self.rendered = read_json(state_path, {})
        self.render, self.upload, self.wake, self.probe = render, upload, wake, probe
        self.cropped_render = (cropped_render if cropped_render is not None
                               else render_cropped_png if render is render_png else render)
        self.usb_ready = False
        self.reconnect_pending = False
        self.usb_key = None
        self.reader_identity = None

    def check(self, settings, force=False, log=lambda msg: None, *,
              scheduled=True, on_success=lambda result: None,
              on_connection=lambda status: None, device_events=()):
        settings.validate()
        key = settings.render_key()
        usb_key = key if settings.enable_usb else None
        if self.usb_key != usb_key:
            self.usb_key = usb_key
            self.usb_ready = False
            self.reconnect_pending = False
            self.reader_identity = None
        was_ready = self.usb_ready
        usb_error = None
        if settings.enable_usb:
            if settings.update_on_connect and any(
                    reader_event_matches(path, self.reader_identity)
                    for path in device_events):
                self.reconnect_pending = True
            try:
                identity = self.probe()
                if (settings.update_on_connect and self.reader_identity is not None
                        and identity != self.reader_identity):
                    self.reconnect_pending = True
                self.reader_identity = identity
                self.usb_ready = True
            except Exception as exc:
                self.usb_ready = False
                usb_error = str(exc)
            if self.usb_ready and not was_ready and settings.update_on_connect:
                self.reconnect_pending = True
                log("mooInk USB 已連線，依「插拔更新」設定同步")
            on_connection("mooInk USB：已連線" if self.usb_ready else "mooInk USB：等待連線")
        else:
            on_connection("mooInk USB：自動更新未啟用")

        # Manual updates always override both automatic trigger switches.
        hotplug_due = (settings.enable_usb and settings.update_on_connect
                       and self.usb_ready and self.reconnect_pending)
        force_render = force or hotplug_due
        file_poll = scheduled and (not settings.enable_usb or settings.update_on_file_change)
        if not force_render and not file_poll:
            if was_ready and not self.usb_ready:
                return "mooInk USB 已離線，等待重新連線"
            return None

        src, dst = settings.paths()
        if not src.is_file():
            return "等待 " + src.name
        source_stat = src.stat()
        digest = sha256(src)
        record = self.rendered.get(key)
        if not isinstance(record, dict):
            record = {}
        current = (record.get("source") == digest
                   and record.get("word_mtime_ns") == source_stat.st_mtime_ns
                   and dst.is_file() and record.get("png") == sha256(dst))
        rendered_now = force_render or not current
        if rendered_now:
            with tempfile.TemporaryDirectory(prefix="mooink_source_") as directory:
                snapshot = Path(directory) / src.name
                shutil.copyfile(src, snapshot)
                if (sha256(snapshot) != digest or sha256(src) != digest
                        or src.stat().st_mtime_ns != source_stat.st_mtime_ns):
                    return "Word 正在儲存，稍後重試"
                log("正在轉換 " + src.name)
                renderer = self.cropped_render if settings.custom_docx else self.render
                renderer(snapshot, dst, settings.size)
            if sha256(src) != digest or src.stat().st_mtime_ns != source_stat.st_mtime_ns:
                return "Word 已再更新，稍後重試最新版本"
            record = {**record, "source": digest, "png": sha256(dst),
                      "word_mtime": source_stat.st_mtime,
                      "word_mtime_ns": source_stat.st_mtime_ns}
            self.rendered[key] = record
            write_json(self.state_path, self.rendered)
            log("本機 PNG 已輸出：" + dst.name)

        if not settings.enable_usb:
            return ("無更新：" + src.name if current and not force else
                    dst.name + " 已輸出；USB 自動傳送尚未啟用")

        # Delivery is tracked per device, separate from local render caching.
        # Connecting with hotplug disabled MUST NOT resend unchanged content.
        pending_document = (file_poll and
                            (record.get("delivered_source") != digest
                             or record.get("delivered_word_mtime_ns") != source_stat.st_mtime_ns
                             or record.get("delivered_png") != record.get("png")))
        upload_due = force_render or pending_document
        if not upload_due:
            if was_ready and not self.usb_ready:
                return "mooInk USB 已離線，等待重新連線"
            return "無更新：" + src.name
        if not self.usb_ready:
            return "等待 mooInk USB 連線；下次允許的觸發事件將重試：" + (usb_error or "")

        # The immutable transfer snapshot is also the source of the UI preview.
        with tempfile.TemporaryDirectory(prefix="mooink_transfer_") as directory:
            snapshot = Path(directory) / dst.name
            shutil.copyfile(dst, snapshot)
            if (sha256(snapshot) != record["png"] or sha256(src) != digest
                    or src.stat().st_mtime_ns != source_stat.st_mtime_ns):
                return "本機檔案已更新，暫不傳送；稍後重試"
            try:
                result = self.upload(settings, snapshot, log)
            except Exception:
                self.usb_ready = False
                if settings.update_on_connect and hotplug_due:
                    self.reconnect_pending = True
                on_connection("mooInk USB：傳送失敗，等待重試")
                raise
            if (sha256(src) != digest or src.stat().st_mtime_ns != source_stat.st_mtime_ns
                    or sha256(dst) != record["png"]):
                return "Word 已再更新，稍後重試最新版本"
            delivery = {"updated_at": time.time(),
                        "word_mtime": source_stat.st_mtime,
                        "word_name": src.name, "png_name": dst.name,
                        "png_bytes": snapshot.read_bytes()}

        # Commit the verified delivery marker only after the WPD readback
        # passed. A failed upload leaves the pending file change intact.
        record.update({"delivered_source": digest,
                       "delivered_word_mtime_ns": source_stat.st_mtime_ns,
                       "delivered_png": record["png"]})
        self.rendered[key] = record
        write_json(self.state_path, self.rendered)
        self.reconnect_pending = False
        on_success(delivery)
        try:
            wake_result = (self.wake(settings.pico_port, log) if settings.pico_port
                           else "未設定 Pico，略過 Space")
        except Exception as exc:
            wake_result = "Pico 不可用，略過 Space：" + str(exc)
        log(wake_result)
        return "USB sleep/" + dst.name + " 已回讀驗證；" + wake_result + "；請觀察休眠畫面"


def main():
    import tkinter as tk
    from tkinter import ttk, filedialog, messagebox
    import pystray
    from PIL import Image, ImageDraw, ImageTk
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("mooink_watcher")
    logger.setLevel(logging.INFO)
    handler = RotatingFileHandler(LOG, maxBytes=1024*1024, backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
    logger.addHandler(handler)
    settings = load_settings()
    root = tk.Tk()
    root.withdraw()
    root.title("mooInk Display Watcher")
    root.resizable(True, True)
    events = queue.Queue()
    stop, trigger = threading.Event(), threading.Event()
    hotplug_events = queue.Queue()
    manual_requests = queue.Queue()
    preview_storage = tempfile.TemporaryDirectory(prefix="mooink_page_preview_")
    def notify_device(path):
        hotplug_events.put(path)
        trigger.set()
    settings_lock = threading.Lock()
    operation_lock = threading.Lock()  # serialize USB and manual Space
    force_pending = False
    status_text = tk.StringVar(value="等待首次檢查")
    connection_text = tk.StringVar(value="mooInk USB：偵測中")
    processor = Processor()
    registry = DeviceRegistry(CONFIG_DIR / "mooink_device_sizes.json")
    def make_reader_processor(reader_id):
        return Processor(
            state_path=CONFIG_DIR / ("watcher_" + reader_id + ".json"),
            upload=lambda cfg, source, log: usb_upload(cfg, source, log, reader_id),
            probe=lambda: usb_probe(reader_id=reader_id),
            wake=pico_wake)
    multiple = MultiDeviceController(registry, make_reader_processor,
                                     usb_readers, reader_event_matches)
    device_info = {}
    device_status = {}
    success_text = tk.StringVar(value="尚無已驗證的 USB 更新")
    word_text = tk.StringVar(value="使用的 Word：—")
    icon = Image.new("RGB", (64,64), "white")
    draw = ImageDraw.Draw(icon)
    draw.rounded_rectangle((8,5,56,59), radius=7, outline="black", width=4)
    for y in (20,30,40):
        draw.line((17,y,47,y), fill="black", width=3)

    def publish(message):
        logger.info(message)
        events.put(("status", message))

    def worker():
        nonlocal force_pending
        previous = None
        previous_settings = None
        next_check = 0.0
        initialized = set()
        while not stop.is_set():
            trigger.clear()
            with settings_lock:
                current, force = settings, force_pending
                force_pending = False
            paths = []
            while True:
                try:
                    paths.append(hotplug_events.get_nowait())
                except queue.Empty:
                    break
            scheduled = (current != previous_settings or time.monotonic() >= next_check)
            previous_settings = current
            if scheduled:
                next_check = time.monotonic() + current.interval_sec
            try:
                directory = Path(current.watch_dir).resolve()
                if directory not in initialized:
                    created = initialize_workfiles(directory)
                    if created:
                        publish("已建立本機工作檔：" + "、".join(created))
                    initialized.add(directory)
                with operation_lock:
                    if current.enable_usb:
                        # Always keep the local Word->PNG feature running even
                        # while all USB readers are disconnected/unpaired.
                        local_result = processor.check(replace(current, enable_usb=False),
                            force, publish,
                            scheduled=scheduled and current.update_on_file_change)
                        results = multiple.check(
                            current, force=force, scheduled=scheduled, device_events=paths,
                            log=publish,
                            inventory=lambda rows: events.put(("inventory", rows)),
                            status=lambda rid, msg: events.put(("device_status", (rid, msg))),
                            success=lambda rid, data: events.put(("device_success", (rid, data))))
                        result = results[-1] if results else (local_result or "裝置掃描完成（未配對或無裝置）")
                    else:
                        result = processor.check(current, force, publish,
                            scheduled=scheduled and current.update_on_file_change)
            except Exception as exc:
                result = "失敗，下次重試：" + str(exc)
            if result is not None and (result != previous or force):
                publish(result)
                previous = result
            # Explicit manual updates run after auto checks. This way any
            # current scheduled update cannot immediately replace the selected
            # one-off document on the reader.
            while not stop.is_set():
                try:
                    manual_request = manual_requests.get_nowait()
                except queue.Empty:
                    break
                selected, chosen_preview, chosen_page = manual_request
                try:
                    if (chosen_preview is None
                            or chosen_preview.source != selected
                            or not chosen_preview.source_unchanged()):
                        raise RuntimeError("來源檔自預覽後已變更，請重新選擇檔案及頁面")
                    with operation_lock:
                        summary = update_selected_docx(
                            selected, registry=registry, discover=usb_readers,
                            page_number=chosen_page,
                            render=lambda docx, png, size: render_selected_page_png(
                                chosen_preview, chosen_page, size, png),
                            upload=lambda rid, png, size, logger: usb_upload(
                                replace(current, size=size), png, logger, rid),
                            on_success=lambda rid, data: events.put(
                                ("device_success", (rid, data))),
                            on_status=lambda rid, msg: events.put(
                                ("device_status", (rid, msg))),
                            log=publish)
                    info = "手動檔案更新：%d 台成功、%d 台失敗" % (
                        len(summary["successful"]), len(summary["failed"]))
                    publish(info)
                    events.put(("manual_docx_done", (info, bool(summary["failed"]))))
                except ManualReaderUnavailable as exc:
                    publish("手動 DOCX 等待裝置：" + str(exc))
                    events.put(("manual_reader_help",
                                (manual_request, exc.reason, exc.readers, str(exc))))
                except Exception as exc:
                    info = "手動檔案更新失敗：" + str(exc)
                    publish(info)
                    events.put(("manual_docx_done", (info, True)))
            remaining = max(0.0, next_check - time.monotonic())
            trigger.wait(min(USB_POLL_SEC, remaining) if current.enable_usb else remaining)

    # Two-column layout. The left pane exists in both modes; all settings are
    # on the right and are not packed until advanced mode is selected.
    style = ttk.Style(root)
    style.theme_use("clam")
    layout = ttk.Frame(root)
    layout.pack(fill="both", expand=True)
    left = ttk.Frame(layout, padding=12, width=520, height=735)
    left.pack(side="left", fill="both", expand=True)
    left.pack_propagate(False)
    frame = ttk.Frame(layout, padding=12)
    fields = {}
    mode_advanced = False

    saved_selection = read_json(MANUAL_SELECTION, {})
    saved_manual = saved_selection.get("file", saved_selection.get("docx", ""))
    manual_docx = tk.StringVar(value=saved_manual if isinstance(saved_manual, str) else "")
    manual_docx_busy = False
    preview_loading = False
    preview_generation = 0
    preview_session = None
    preview_page = 1
    preview_total = 0
    preview_draw_job = None
    preview_photo = None

    toolbar = ttk.Frame(left)
    toolbar.pack(side="top", fill="x", pady=(0, 8))
    selected_name = tk.StringVar(value="尚未選擇 Word 或照片")
    page_label = tk.StringVar(value="0/0 頁")
    ttk.Label(left, textvariable=selected_name, anchor="center",
              wraplength=490).pack(fill="x", pady=(4, 5))
    ttk.Label(left, textvariable=page_label, anchor="center").pack(pady=(0, 4))
    page_scale = ttk.Scale(left, from_=1, to=1, orient="horizontal")
    page_scale.pack(fill="x", padx=15, pady=(0, 10))
    page_scale.state(["disabled"])
    preview_canvas = tk.Canvas(left, width=490, height=610, highlightthickness=0)
    preview_canvas.pack(fill="both", expand=True)
    preview_message = "選擇 Word 或照片以預覽完整頁面"
    preview_canvas.bind("<Configure>", lambda _: draw_preview_placeholder()
                        if preview_photo is None else show_preview_photo())
    preview_status = tk.StringVar(value="")
    ttk.Label(left, textvariable=preview_status, anchor="center",
              wraplength=490).pack(fill="x", pady=(6, 0))

    def draw_preview_placeholder():
        preview_canvas.delete("all")
        preview_canvas.create_text(max(preview_canvas.winfo_width()//2, 200),
                                   max(preview_canvas.winfo_height()//2, 240),
                                   text=preview_message, fill="#9ca3af",
                                   font=("Segoe UI", 11))
    def show_preview_photo():
        preview_canvas.delete("all")
        if preview_photo is not None:
            preview_canvas.create_image(
                preview_canvas.winfo_width()//2, preview_canvas.winfo_height()//2,
                image=preview_photo, anchor="center")
        else:
            draw_preview_placeholder()

    def load_page_preview():
        nonlocal preview_draw_job
        preview_draw_job = None
        if preview_session is None or preview_loading:
            return
        selected_page = preview_page
        generation = preview_generation
        current = preview_session
        try:
            page_image = render_document_page(current, selected_page, scale=1.45,
                                              size=settings.size)
            target = (max(1, preview_canvas.winfo_width()-18),
                      max(1, preview_canvas.winfo_height()-18))
            page_image.thumbnail(target, Image.Resampling.LANCZOS)
            events.put(("page_image", (generation, selected_page, page_image)))
        except Exception as exc:
            events.put(("preview_error", (generation, str(exc))))

    def schedule_page_preview():
        nonlocal preview_draw_job
        if preview_draw_job is not None:
            root.after_cancel(preview_draw_job)
        preview_draw_job = root.after(110, load_page_preview)

    def page_moved(value):
        nonlocal preview_page
        if not preview_total or preview_loading:
            return
        requested = max(1, min(preview_total, round(float(value))))
        if requested == preview_page:
            return
        preview_page = requested
        page_label.set("%d/%d 頁" % (preview_page, preview_total))
        schedule_page_preview()
    page_scale.configure(command=page_moved)

    def select_manual_docx(path=None):
        nonlocal preview_generation, preview_session, preview_loading
        nonlocal preview_total, preview_page, preview_photo, preview_message
        path = path or filedialog.askopenfilename(
            parent=root, title="選擇 Word 或照片",
            filetypes=[("Word 與照片", "*.docx *.jpg *.jpeg *.png *.webp *.bmp *.tif *.tiff *.gif *.avif"),
                       ("Word DOCX", "*.docx"),
                       ("照片", "*.jpg *.jpeg *.png *.webp *.bmp *.tif *.tiff *.gif *.avif"),
                       ("所有檔案", "*.*")])
        if not path:
            return
        source = Path(path).expanduser().resolve()
        from mook_sleep_screen_manager import IMAGE_EXTS
        if source.suffix.casefold() not in IMAGE_EXTS | {".docx"} or not source.is_file():
            messagebox.showerror("檔案錯誤", "請選擇存在的 DOCX 或照片", parent=root)
            return
        manual_docx.set(str(source))
        try:
            write_json(MANUAL_SELECTION, {"file": str(source)})
        except OSError as exc:
            publish("記憶最近手動選檔失敗：" + str(exc))
        preview_generation += 1
        generation = preview_generation
        preview_loading = True
        preview_session = None
        preview_total = 0
        preview_page = 1
        preview_photo = None
        preview_message = "正在讀取檔案預覽…"
        page_label.set("0/0 頁")
        selected_name.set(source.name)
        preview_status.set("正在準備圖片預覽…" if source.suffix.casefold()
                           in IMAGE_EXTS else "正在轉換 DOCX，建立頁面預覽…")
        page_scale.state(["disabled"])
        update_button.configure(state="disabled")
        draw_preview_placeholder()
        def prepare():
            try:
                folder = Path(preview_storage.name) / uuid.uuid4().hex
                session = prepare_media_preview(source, folder)
                events.put(("preview_ready", (generation, session)))
            except Exception as exc:
                events.put(("preview_error", (generation, str(exc))))
        threading.Thread(target=prepare, daemon=True).start()

    choose_button = ttk.Button(toolbar, text="選擇檔案", command=select_manual_docx)
    choose_button.pack(side="left", padx=(0, 5))
    # manual_update_click is defined after page controls, but the button
    # callback resolves it only on user interaction.
    update_button = ttk.Button(toolbar, text="立即更新",
                               command=lambda: manual_update_click())
    update_button.pack(side="left", padx=(0, 5))
    mode_button = ttk.Button(toolbar, text="模式:精簡",
                             command=lambda: set_mode(not mode_advanced))
    mode_button.pack(side="left")
    update_button.configure(state="disabled")

    # Advanced settings reuse the existing controls; no copy of their state.
    for index,(name,label) in enumerate([
        ("watch_dir","監控資料夾"), ("size","mooInk 尺寸"),
        ("interval_sec","檢查秒數"),
        ("pico_port","Pico CDC COM（空白停用）")]):
        ttk.Label(frame,text=label).grid(row=index,column=0,sticky="w",pady=5)
        fields[name] = tk.StringVar(value=str(getattr(settings,name)))
        if name=="size":
            widget=ttk.Combobox(frame,textvariable=fields[name],
                                values=("10.3","13.3"),state="readonly",width=44)
        else:
            widget=ttk.Entry(frame,textvariable=fields[name],width=47)
        widget.grid(row=index,column=1,sticky="ew",padx=8)
    ttk.Button(frame,text="瀏覽",command=lambda: fields["watch_dir"].set(
        filedialog.askdirectory() or fields["watch_dir"].get())).grid(row=0,column=2)
    ttk.Label(frame,text="自動更新 DOCX（選填）").grid(row=4,column=0,sticky="w",pady=5)
    custom_docx = tk.StringVar(value=settings.custom_docx)
    ttk.Entry(frame, textvariable=custom_docx, width=47).grid(
        row=4,column=1,sticky="ew",padx=8)
    docx_actions = ttk.Frame(frame)
    docx_actions.grid(row=4,column=2,sticky="w")
    ttk.Button(docx_actions, text="選檔",command=lambda: custom_docx.set(
        filedialog.askopenfilename(title="選擇自動監控的 Word DOCX",
            filetypes=[("Word DOCX", "*.docx")]) or custom_docx.get())).pack(side="left")
    ttk.Button(docx_actions, text="清除",command=lambda: custom_docx.set("")).pack(side="left")

    resident_setting = tk.BooleanVar(value=settings.stay_resident)
    ttk.Checkbutton(frame, text="常駐模式（關閉視窗仍在系統匣運作；預設關閉）",
                    variable=resident_setting).grid(
        row=5,column=0,columnspan=3,sticky="w",pady=5)
    def manual_update_click():
        nonlocal manual_docx_busy
        if manual_docx_busy or preview_loading:
            return
        if preview_session is None or not preview_session.source_unchanged():
            messagebox.showerror("頁面預覽失效",
                                 "照片/Word 已修改或尚未載入預覽，請重新選擇檔案。",
                                 parent=root)
            return
        selected = preview_session.source
        selected_page = preview_page
        manual_docx_busy = True
        update_button.configure(state="disabled")
        preview_status.set("正在傳送第 %d/%d 頁…" % (selected_page, preview_total))
        publish("手動更新：" + selected.name + " 第 %d/%d 頁" %
                (selected_page, preview_total))
        manual_requests.put((selected, preview_session, selected_page))
        trigger.set()

    enable_usb = tk.BooleanVar(value=settings.enable_usb)
    ttk.Checkbutton(frame,text="啟用 USB 自動更新 sleep 圖片",
                    variable=enable_usb).grid(row=6,column=0,columnspan=3,sticky="w",pady=5)
    update_on_connect = tk.BooleanVar(value=settings.update_on_connect)
    update_on_file_change = tk.BooleanVar(value=settings.update_on_file_change)
    modes = ttk.LabelFrame(frame, text="USB 更新選項", padding=6)
    modes.grid(row=7,column=0,columnspan=3,sticky="ew",pady=4)
    ttk.Checkbutton(modes, text="插拔更新：USB 新連線／重新插入時立即同步",
                    variable=update_on_connect).pack(anchor="w")
    ttk.Checkbutton(modes, text="Polling file update：依設定秒數檢查 Word，變更後同步",
                    variable=update_on_file_change).pack(anchor="w")
    delete_other_sleep = tk.BooleanVar(value=settings.delete_other_sleep_images)
    ttk.Separator(modes, orient="horizontal").pack(fill="x", pady=(6,4))
    ttk.Checkbutton(
        modes, text="更新成功時刪除其他 sleep 圖片（先備份；預設不勾選）",
        variable=delete_other_sleep).pack(anchor="w")
    ttk.Label(frame,text="手動選檔後按「立即更新」會裁白邊並傳送，不更動自動監控檔案。\n自動更新 DOCX 清空時依尺寸使用預設 Word。\n依尺寸產生 mooink_display_10 / 13 的 PNG。\nUSB MTP 備份舊同名 PNG → 上傳 → SHA-256 回讀驗證 → Pico Space（選用）。\n可同時連接多台 mooInk，先以裝置 ID 配對尺寸。\n多裝置 USB 更新不會自動傳送 Pico Space。",wraplength=530).grid(row=9,column=0,columnspan=3,sticky="w",pady=8)
    ttk.Label(frame,textvariable=connection_text).grid(row=8,column=0,columnspan=3,sticky="w")
    ttk.Label(frame,textvariable=status_text,wraplength=550).grid(row=10,column=0,columnspan=3,sticky="w",pady=5)
    def save():
        nonlocal settings, force_pending
        try:
            values={k:v.get().strip() for k,v in fields.items()}
            values["interval_sec"]=int(values["interval_sec"])
            values["custom_docx"] = custom_docx.get().strip()
            if values["custom_docx"]:
                values["custom_docx"] = str(Path(values["custom_docx"]).expanduser().resolve())
            values["enable_usb"] = bool(enable_usb.get())
            values["update_on_connect"] = bool(update_on_connect.get())
            values["update_on_file_change"] = bool(update_on_file_change.get())
            values["stay_resident"] = bool(resident_setting.get())
            values["delete_other_sleep_images"] = bool(delete_other_sleep.get())
            new=Settings(**values).validate()
            if new.delete_other_sleep_images and not settings.delete_other_sleep_images:
                if not messagebox.askyesno(
                    "確認啟用清理其他 sleep 圖片",
                    "啟用後，每次手動或自動更新圖片並回讀驗證成功，"
                    "都會逐張備份並刪除該台 Mooink sleep 內的其他 JPG／PNG。\n"
                    "不會刪除目前更新的圖片，也不會影響其他 Mooink。\n"
                    "備份／刪除失敗時會停止後續清理。確定要啟用？",
                    parent=root):
                    return
            if new.enable_usb and (not settings.enable_usb or new.size != settings.size):
                if not messagebox.askyesno("啟用 USB 更新",
                    "可同時接多台 Mooink，每台需先以裝置 ID 指定尺寸。\n"
                    "將自動備份並替換 sleep 內的專用 PNG；其他圖片是否清理取決於進階勾選設定。"
                    "確定啟用？",parent=root):
                    return
            write_json(CONFIG,asdict(new))
            with settings_lock:
                # Explicitly selecting another Word document requests an update
                # right away, regardless of which automatic triggers are enabled.
                # Toggling trigger switches alone must not force an upload.
                force_pending = force_pending or (
                    new.enable_usb and new.custom_docx != settings.custom_docx)
                settings=new
            trigger.set()
            preview_status.set("進階設定已儲存")
        except (ValueError,OSError) as exc:
            messagebox.showerror("設定錯誤", str(exc), parent=root)
    usb_box = ttk.LabelFrame(frame, text="USB MTP / Pico 測試", padding=8)
    usb_box.grid(row=11,column=0,columnspan=3,sticky="ew",pady=(4,8))
    usb_status = tk.StringVar(value="請接上 mooInk USB 資料線")
    ttk.Label(usb_box,textvariable=usb_status,wraplength=540).pack(anchor="w")
    buttons = ttk.Frame(usb_box)
    buttons.pack(fill="x",pady=(6,0))
    action_buttons = []
    manual_busy = False

    def manual_job(action):
        nonlocal manual_busy
        if manual_busy:
            return
        port = fields["pico_port"].get().strip()
        if action == "wake":
            if not re.fullmatch(r"COM[1-9][0-9]*", port, re.I):
                messagebox.showerror("COM 錯誤","請輸入 COM6 等有效 COM 埠",parent=root)
                return
            if not messagebox.askyesno("測試 Space","將送出 Space 至 Pico 已配對的閱讀器，請確認目標裝置。",parent=root):
                return
        manual_busy = True
        for button in action_buttons:
            button.configure(state="disabled")
        usb_status.set("正在檢查…")
        def run():
            try:
                with operation_lock:
                    if action in ("list", "pair"):
                        readers = usb_readers()
                        if action == "pair":
                            result = readers
                        else:
                            result = [(reader, usb_list(reader["id"]) if reader["ready"] else [])
                                      for reader in readers]
                    else:
                        result = pico_wake(port, logger.info)
                events.put(("manual_result",(action,result,None)))
            except Exception as exc:
                events.put(("manual_result",(action,None,str(exc))))
        threading.Thread(target=run,daemon=True).start()
    for label, action in (("管理裝置／尺寸配對","pair"),
                          ("測試 USB／查詢 sleep","list"),("測試喚醒 Space","wake")):
        button = ttk.Button(buttons,text=label,command=lambda a=action: manual_job(a))
        button.pack(side="left",padx=3)
        action_buttons.append(button)
    ttk.Button(buttons,text="裝置內照片管理",command=lambda: open_device_photo_manager()).pack(
        side="left",padx=3)
    ttk.Button(buttons,text="本機照片庫",command=lambda: open_photo_manager()).pack(
        side="left",padx=3)
    save_button = ttk.Button(frame,text="儲存並開始監控",command=save)
    save_button.grid(row=12,column=1,sticky="e",pady=10)
    action_buttons.append(save_button)

    preview_box = ttk.LabelFrame(frame, text="最後一次 USB SHA-256 驗證成功的圖片", padding=8)
    preview_box.grid(row=13,column=0,columnspan=3,sticky="ew",pady=5)
    ttk.Label(preview_box,textvariable=success_text,wraplength=550).pack(anchor="w")
    ttk.Label(preview_box,textvariable=word_text,wraplength=550).pack(anchor="w")
    tree_columns = ("size", "state", "updated", "wordtime")
    device_tree = ttk.Treeview(preview_box, columns=tree_columns, show="tree headings",
                               height=5, selectmode="browse")
    device_tree.heading("#0", text="裝置（ID 前 12 碼）")
    device_tree.column("#0", width=170, stretch=False)
    for column, label, width in (("size","尺寸",55), ("state","USB / 更新",165),
                                 ("updated","成功時間",145), ("wordtime","Word 修改時間",145)):
        device_tree.heading(column, text=label)
        device_tree.column(column, width=width, stretch=False)
    device_tree.pack(fill="x",pady=5)
    preview = ttk.Label(preview_box,text="尚無圖片")
    preview.pack(pady=4)

    def stamp(value):
        try:
            return datetime.fromtimestamp(float(value)).strftime("%Y-%m-%d %H:%M:%S")
        except (TypeError, ValueError, OverflowError, OSError):
            return "—"

    def show_preview(device_id):
        row = registry.lookup(device_id)
        success_text.set("成功更新：" + stamp(row.get("last_updated")) +
                         "  " + row.get("png_name", ""))
        word_text.set("使用的 Word：" + row.get("word_name", "—") +
                      "  修改時間 " + stamp(row.get("word_mtime")))
        try:
            path = registry.preview_file(device_id)
            with Image.open(path) as image:
                image.thumbnail((280, 340), Image.Resampling.LANCZOS)
                preview.image = ImageTk.PhotoImage(image, master=root)
            preview.configure(image=preview.image, text="")
        except Exception:
            preview.image = None
            preview.configure(image="", text="尚無驗證成功的 PNG 預覽")

    def populate_tree():
        ids = set(device_info) | set(registry.entries())
        for rid in sorted(ids):
            info = device_info.get(rid, {})
            meta = registry.lookup(rid)
            values = (meta.get("size", "未配對"),
                      device_status.get(rid, "已連線" if info.get("ready") else "未連線"),
                      stamp(meta.get("last_updated")), stamp(meta.get("word_mtime")))
            title = (info.get("name") or meta.get("name") or "mooInk") + " [" + rid[:12] + "]"
            if device_tree.exists(rid):
                device_tree.item(rid, text=title, values=values)
            else:
                device_tree.insert("", "end", iid=rid, text=title, values=values)

    def on_select_device(event=None):
        selection = device_tree.selection()
        if selection:
            show_preview(selection[0])
    device_tree.bind("<<TreeviewSelect>>", on_select_device)
    populate_tree()

    def open_device_pairing(readers, *, retry_manual=None):
        nonlocal manual_docx_busy
        if not readers:
            messagebox.showinfo("裝置配對", "目前未偵測到 Mooink。插入 USB 後再試一次。", parent=root)
            return
        dialog = tk.Toplevel(root)
        dialog.title("依裝置 ID 設定尺寸")
        dialog.geometry("650x315")
        dialog.transient(root)
        ttk.Label(dialog,text="每台裝置只需設定一次尺寸；之後依 ID 自動套用，不會寫入未配對的裝置。",
                  wraplength=615).pack(anchor="w",padx=10,pady=8)
        labels = [(r["name"] + "  ID " + r["id"][:16] +
                   ("  [就緒]" if r["ready"] else "  [等待 WPD]")) for r in readers]
        picker = ttk.Combobox(dialog, values=labels, width=75, state="readonly")
        picker.pack(padx=10,pady=6,fill="x")
        selected_size = tk.StringVar(value="")
        line = ttk.Frame(dialog)
        line.pack(anchor="w",padx=10,pady=8)
        ttk.Label(line, text="此 ID 的 mooInk 尺寸：").pack(side="left")
        sizing = ttk.Combobox(line, textvariable=selected_size,
                              values=("10.3","13.3"), width=10, state="readonly")
        sizing.pack(side="left",padx=5)
        ttk.Label(dialog, text="無法由裝置 ID 推算面板尺寸。請依實際機型選擇後儲存。",
                  foreground="#994400").pack(anchor="w",padx=10)
        def selected(event=None):
            index = picker.current()
            if index >= 0:
                selected_size.set(registry.lookup(readers[index]["id"]).get("size", ""))
        picker.bind("<<ComboboxSelected>>", selected)
        suggested = next((index for index, reader in enumerate(readers)
                          if reader.get("ready") and
                          registry.lookup(reader["id"]).get("size")
                          not in ("10.3", "13.3")), 0)
        picker.current(suggested)
        selected()
        def close_pairing():
            nonlocal manual_docx_busy
            if retry_manual is not None:
                manual_docx_busy = False
                update_button.configure(state="normal" if preview_session else "disabled")
            dialog.destroy()
        dialog.protocol("WM_DELETE_WINDOW", close_pairing)
        def assign():
            nonlocal force_pending
            index = picker.current()
            if index < 0 or not selected_size.get():
                messagebox.showwarning("裝置配對", "請選擇尺寸", parent=dialog)
                return
            r = readers[index]
            try:
                registry.assign(r["id"], selected_size.get(), r["name"])
            except Exception as exc:
                messagebox.showerror("配對失敗", str(exc), parent=dialog)
                return
            if retry_manual is not None:
                # First-time pairing should resume the SAME manual DOCX, not
                # queue an automatic DOCX update that may overwrite it.
                manual_requests.put(retry_manual)
            else:
                with settings_lock:
                    force_pending = True
            trigger.set()
            populate_tree()
            dialog.destroy()
        ttk.Button(dialog,text="儲存此裝置尺寸並繼續" if retry_manual is not None
                   else "儲存此裝置尺寸",command=assign).pack(pady=14)

    def open_device_photo_manager():
        from mooink_device_photo_ui import DevicePhotoManager
        DevicePhotoManager(root, registry, operation_lock,
                           CONFIG_DIR / "device_photo_backup")

    def open_photo_manager():
        """Integrated photo library, reusing the current preview and USB uploader.

        Paths are a user-managed library; originals never get moved/deleted.
        Batch conversion produces size-specific PNG files for offline use.
        """
        from mook_sleep_screen_manager import IMAGE_EXTS
        gallery_file = read_json(PHOTO_LIBRARY, {}).get("photos", [])
        photos = ([path for path in gallery_file
                   if isinstance(path,str) and Path(path).is_file()
                   and Path(path).suffix.casefold() in IMAGE_EXTS]
                  if isinstance(gallery_file,list) else [])
        window = tk.Toplevel(root)
        window.title("mooInk 照片管理")
        window.geometry("680x520")
        window.minsize(540,380)
        window.transient(root)
        window.configure(bg="#171b24" if mode_advanced else "#f6f7fa")
        body = ttk.Frame(window,padding=12)
        body.pack(fill="both",expand=True)
        ttk.Label(body,text="照片庫：加入／移除照片，選一張送至左側預覽；"
                  "批次製作兩種 mooInk 尺寸。",wraplength=620).pack(anchor="w",pady=(0,8))
        list_frame = ttk.Frame(body)
        list_frame.pack(fill="both",expand=True)
        items = tk.Listbox(list_frame,selectmode="extended",
                           exportselection=False, height=14,
                           bg="#242a35" if mode_advanced else "white",
                           fg="#eef2f6" if mode_advanced else "#1d2939",
                           selectbackground="#516d92")
        scrollbar=ttk.Scrollbar(list_frame,orient="vertical",command=items.yview)
        items.configure(yscrollcommand=scrollbar.set)
        items.pack(side="left",fill="both",expand=True)
        scrollbar.pack(side="right",fill="y")
        library_status = tk.StringVar(value="%d 張照片" % len(photos))
        def refresh():
            items.delete(0,"end")
            for file in photos:
                items.insert("end",Path(file).name + "  —  " + str(Path(file).parent))
            write_json(PHOTO_LIBRARY, {"photos":photos})
            library_status.set("照片庫：%d 張（只保存路徑，不修改原檔）" % len(photos))
        def add():
            choices = filedialog.askopenfilenames(
                parent=window, title="匯入照片（可多選）",
                filetypes=[("照片","*.jpg *.jpeg *.png *.webp *.bmp *.tif *.tiff *.gif *.avif")])
            for choice in choices:
                path = str(Path(choice).resolve())
                if path not in photos:
                    photos.append(path)
            if choices:
                refresh()
        def remove():
            for index in reversed(items.curselection()):
                photos.pop(index)
            refresh()
        def choose():
            choices=items.curselection()
            if len(choices)!=1:
                messagebox.showinfo("照片預覽","請先選取一張照片",parent=window)
                return
            source=photos[choices[0]]
            if not Path(source).is_file():
                messagebox.showwarning("照片不存在","找不到原始照片",parent=window)
                return
            select_manual_docx(source)
            window.destroy()
            root.lift()
        control=ttk.Frame(body)
        control.pack(fill="x",pady=(10,4))
        ttk.Button(control,text="加入照片（多選）",command=add).pack(side="left",padx=(0,5))
        ttk.Button(control,text="從清單移除",command=remove).pack(side="left",padx=(0,5))
        ttk.Button(control,text="預覽／設為更新照片",command=choose).pack(side="left",padx=(0,5))
        def batch_export():
            existing=[file for file in photos if Path(file).is_file()]
            if not existing:
                messagebox.showwarning("沒有照片","請先加入照片",parent=window)
                return
            directory=filedialog.askdirectory(parent=window,title="選擇雙尺寸輸出根目錄")
            if not directory:
                return
            export_button.configure(state="disabled")
            library_status.set("正在製作 %d 張照片的 10.3 與 13.3 吋圖檔…" % len(existing))
            def run():
                try:
                    output=prepare_photo_batch(existing,directory)
                    events.put(("photo_batch_done",
                                (window, library_status, export_button,
                                 "%d 張照片已轉檔完成；10.3：%d、13.3：%d，儲存於 %s" %
                                 (len(existing),len(output["10.3"]),len(output["13.3"]),directory),
                                 False)))
                except Exception as exc:
                    events.put(("photo_batch_done",
                                (window, library_status, export_button,
                                 "照片批次轉檔失敗：" + str(exc),True)))
            threading.Thread(target=run,daemon=True).start()
        export_button=ttk.Button(body,text="批次製作 10.3 + 13.3 吋（自動旋轉／去白邊）",
                                 command=batch_export)
        export_button.pack(fill="x",pady=(5,5))
        ttk.Label(body,textvariable=library_status,wraplength=630).pack(
            fill="x",anchor="w")
        refresh()
        items.bind("<Double-Button-1>",lambda _:choose())


    def set_mode(advanced):
        """Advanced: right settings pane + dark colors; simple: three controls
        and full-page preview on a light canvas. Never save mode to disk."""
        nonlocal mode_advanced
        mode_advanced = bool(advanced)
        background = "#171b24" if advanced else "#f6f7fa"
        surface = "#242a35" if advanced else "#ffffff"
        foreground = "#eef2f6" if advanced else "#1d2939"
        edge = "#444d5d" if advanced else "#ccd4df"
        style.configure(".", background=background, foreground=foreground,
                        fieldbackground=surface)
        for widget in ("TFrame","TLabel","TCheckbutton","TLabelframe"):
            style.configure(widget, background=background, foreground=foreground)
        style.configure("TLabelframe.Label", background=background,
                        foreground=foreground)
        style.configure("TButton", background=surface, foreground=foreground,
                        padding=(9, 6), bordercolor=edge)
        style.map("TButton",
                  background=[("active","#3b4558" if advanced else "#e4e9f1")],
                  foreground=[("active",foreground),("disabled","#a1a8b2")])
        for widget in ("TEntry", "TCombobox", "TSpinbox"):
            style.configure(widget, fieldbackground=surface, foreground=foreground,
                            background=surface)
            style.map(widget, fieldbackground=[("readonly",surface)],
                      foreground=[("readonly",foreground)])
        style.configure("TScale", background=background, troughcolor=edge)
        style.configure("Treeview", background=surface, fieldbackground=surface,
                        foreground=foreground)
        style.configure("Treeview.Heading", background=surface, foreground=foreground)
        root.configure(background=background)
        preview_canvas.configure(background="#212833" if advanced else "#f0f2f6")
        if advanced:
            frame.pack(side="right",fill="both",expand=True)
            mode_button.configure(text="模式:進階")
            width = min(root.winfo_screenwidth()-80, 1300)
            height = min(root.winfo_screenheight()-110, 850)
        else:
            frame.pack_forget()
            mode_button.configure(text="模式:精簡")
            width = min(root.winfo_screenwidth()-80, 530)
            height = min(root.winfo_screenheight()-110, 790)
        root.geometry("%dx%d" % (max(480,width), max(620,height)))
        show_preview_photo()

    def close_or_reside():
        if settings.stay_resident:
            root.withdraw()
        else:
            events.put(("exit", None))

    root.protocol("WM_DELETE_WINDOW", close_or_reside)

    def tray_action(action):
        return lambda *_: events.put((action,None))
    tray=pystray.Icon("mooink_display",icon,"mooInk Display Watcher",pystray.Menu(
        pystray.MenuItem("設定／USB 測試",tray_action("settings"),default=True),
        pystray.MenuItem("立即重新轉圖／傳送",tray_action("check")),
        pystray.MenuItem("開啟監控目錄",tray_action("folder")),
        pystray.MenuItem("開啟執行記錄",tray_action("log")),
        pystray.MenuItem("結束",tray_action("exit"))))
    thread=threading.Thread(target=worker,daemon=True)
    def drain():
        nonlocal force_pending, manual_busy, preview_loading
        nonlocal preview_generation, preview_session, preview_total, preview_page, preview_photo
        nonlocal preview_message, preview_draw_job, manual_docx_busy
        while True:
            try:
                action,value=events.get_nowait()
            except queue.Empty:
                break
            if action=="status":
                status_text.set(value)
            elif action=="preview_ready":
                generation, session = value
                if generation != preview_generation:
                    continue
                preview_loading = False
                preview_session = session
                preview_total = session.page_count
                preview_page = 1
                page_label.set("1/%d 頁" % preview_total)
                page_scale.configure(from_=1, to=preview_total)
                page_scale.set(1)
                page_scale.state(["!disabled"] if preview_total > 1 else ["disabled"])
                update_button.configure(
                    state="disabled" if manual_docx_busy else "normal")
                preview_status.set("共 %d 頁；拖曳卷軸即可預覽完整頁面" % preview_total)
                schedule_page_preview()
            elif action=="page_image":
                generation, selected_page, image = value
                if generation != preview_generation or selected_page != preview_page:
                    continue
                preview_photo = ImageTk.PhotoImage(image, master=root)
                preview_message = ""
                show_preview_photo()
            elif action=="preview_error":
                generation, message = value
                if generation != preview_generation:
                    continue
                preview_loading = False
                preview_session = None
                preview_photo = None
                preview_message = "無法預覽此檔案：" + message
                preview_status.set(preview_message)
                update_button.configure(state="disabled")
                draw_preview_placeholder()
            elif action=="photo_batch_done":
                gallery_window, status_var, export_button, message, failed = value
                if gallery_window.winfo_exists():
                    status_var.set(message)
                    export_button.configure(state="normal")
                publish(message)
                if failed:
                    messagebox.showwarning("照片管理",message,parent=root)
            elif action=="connection":
                connection_text.set(value)
            elif action=="inventory":
                device_info.clear()
                device_info.update({reader["id"]: reader for reader in value})
                connection_text.set("已連接 Mooink：" + str(sum(1 for r in value if r["ready"])) + " 台")
                populate_tree()
            elif action=="device_status":
                rid, msg = value
                device_status[rid] = msg
                populate_tree()
            elif action=="device_success":
                rid, result = value
                populate_tree()
                device_tree.selection_set(rid)
                device_tree.see(rid)
                show_preview(rid)
            elif action=="success":
                # Legacy one-device renderer compatibility (USB disabled).
                pass
            elif action=="manual_docx_done":
                manual_docx_busy = False
                update_button.configure(state="normal" if preview_session else "disabled")
                message, failed = value
                status_text.set(message)
                preview_status.set(message)
                if failed:
                    messagebox.showwarning("手動 DOCX 更新", message, parent=root)
            elif action=="manual_reader_help":
                request, reason, readers, detail = value
                status_text.set(detail)
                preview_status.set(detail)
                if reason == "unpaired":
                    # If the model knows the device but not its size, guide the
                    # first-time pairing and automatically retry the pending DOCX.
                    root.deiconify()
                    root.lift()
                    open_device_pairing(readers, retry_manual=request)
                else:
                    manual_docx_busy = False
                    update_button.configure(state="normal" if preview_session else "disabled")
                    messagebox.showwarning("Mooink USB 裝置狀態", detail, parent=root)
            elif action=="manual_result":
                operation, result, error = value
                manual_busy = False
                for button in action_buttons:
                    button.configure(state="normal")
                if error:
                    usb_status.set(error)
                    logger.info("USB/Pico: %s",error)
                    messagebox.showwarning("USB/Pico 操作",error,parent=root)
                elif operation == "wake":
                    usb_status.set(result)
                    logger.info("Pico 手動喚醒：%s", result)
                elif operation == "pair":
                    open_device_pairing(result)
                else:
                    usb_status.set("偵測到 %d 台 Mooink" % len(result))
                    dialog = tk.Toplevel(root)
                    dialog.title("USB Mooink sleep 圖片清單")
                    dialog.geometry("650x420")
                    listing = tk.Text(dialog,wrap="none")
                    listing.pack(fill="both",expand=True,padx=8,pady=8)
                    lines = []
                    for reader, files in result:
                        lines.append("%s  [ID %s]  %s" %
                            (reader["name"], reader["id"][:12],
                             "已就緒" if reader["ready"] else "WPD 未就緒"))
                        lines.extend("  %s   %s bytes" % (row["name"], row.get("size") or "?")
                                     for row in files)
                    listing.insert("1.0","\n".join(lines) or "（未找到 Mooink）")
                    listing.configure(state="disabled")
            elif action=="settings":
                root.deiconify()
                set_mode(True)
                root.lift()
            elif action=="check":
                with settings_lock:
                    force_pending=True
                trigger.set()
            elif action=="folder":
                os.startfile(settings.watch_dir)
            elif action=="log":
                os.startfile(str(LOG))
            elif action=="exit":
                stop.set()
                trigger.set()
                tray.stop()
                root.destroy()
                return
        root.after(150,drain)
    threading.Thread(target=tray.run,daemon=True).start()
    thread.start()
    threading.Thread(target=watch_device_events, args=(stop, notify_device, logger.info),
                     daemon=True).start()
    set_mode(False)
    root.deiconify()
    if saved_manual and Path(saved_manual).is_file():
        root.after(160, lambda: select_manual_docx(saved_manual))
    root.after(150,drain)
    root.mainloop()
    stop.set()
    trigger.set()
    preview_storage.cleanup()

if __name__=="__main__":
    import argparse
    parser = argparse.ArgumentParser(description="mooInk Word 顯示監控")
    parser.add_argument("--init-templates", action="store_true",
                        help="只補齊專案根目錄缺少的四個工作檔，不啟動監控")
    args = parser.parse_args()
    if args.init_templates:
        created = initialize_workfiles()
        print("已建立：" + "、".join(created) if created else "四個工作檔已存在，未覆寫")
    else:
        main()
