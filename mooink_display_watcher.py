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
from mooink_multi_device import DeviceRegistry, MultiDeviceController

CONFIG_DIR = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "mooink"
CONFIG = CONFIG_DIR / "mooink_display_watcher.json"
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

    def validate(self):
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
        return root / (stem + ".docx"), root / (stem + ".png")

    def render_key(self):
        return json.dumps(["render-v4", str(Path(self.watch_dir).resolve()), self.size],
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

def render_png(source, output, size):
    """Render one page with existing Office/PDF backend, preserving margins.
    Refuse multi-page inputs rather than silently dropping content.
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
            mgr = SleepScreenManager(size=size, orientation="portrait", fit="contain", output_dir=work)
            pages = mgr._pdf_pages(Path(source), work, render_scale=3.5)
            try:
                first = next(pages, None)
                if first is None:
                    raise RuntimeError("Word 沒有可轉換頁面")
                if next(pages, None) is not None:
                    raise RuntimeError("Word 超過一頁；請調整到單頁，避免漏掉內容")
            finally:
                pages.close()
            out = ImageOps.pad(first[1].convert("L"), RESOLUTIONS[size],
                               method=Image.Resampling.LANCZOS, color=255)
            out = out.point([round(i/17)*17 for i in range(256)])
            temp = Path(output).with_name("." + Path(output).name + "." + uuid.uuid4().hex + ".tmp")
            try:
                out.save(temp, format="PNG", optimize=True)
                os.replace(temp, output)
            finally:
                temp.unlink(missing_ok=True)
    finally:
        if com is not None:
            com.CoUninitialize()

def usb_upload(settings, source, log, reader_id=None):
    import pythoncom
    from mook_mtp_wpd import replace_watch_image
    pythoncom.CoInitialize()
    try:
        return replace_watch_image(
            source, CONFIG_DIR / "watcher_backup",
            progress=lambda stage, detail: log(detail.get("message", stage)),
            reader_id=reader_id)
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
    """Match a WPD interface notification to the known reader, not Pico."""
    path = str(path or "").casefold()
    identity = str(identity or "").casefold()
    if not path:
        return False
    if "vid_1f85" in path and "pid_4e16" in path:
        return True
    # WPD interface class suffixes can differ between enumeration and events.
    def prefix(value):
        return value.split("#{", 1)[0]
    return bool(identity and prefix(path) == prefix(identity))


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
                 wake=pico_wake, probe=usb_probe):
        self.state_path = state_path
        self.rendered = read_json(state_path, {})
        self.render, self.upload, self.wake, self.probe = render, upload, wake, probe
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
        usb_error = None
        was_ready = self.usb_ready
        if settings.enable_usb:
            if any(reader_event_matches(path, self.reader_identity) for path in device_events):
                self.reconnect_pending = True
            try:
                identity = self.probe()
                if self.reader_identity is not None and identity != self.reader_identity:
                    self.reconnect_pending = True
                self.reader_identity = identity
                self.usb_ready = True
            except Exception as exc:
                self.usb_ready = False
                usb_error = str(exc)
            if self.usb_ready and not was_ready:
                self.reconnect_pending = True
                log("mooInk USB 已連線，強制重新轉圖及更新")
            on_connection("mooInk USB：已連線" if self.usb_ready else "mooInk USB：等待連線")
        else:
            on_connection("mooInk USB：自動更新未啟用")
        # Keep the reconnect request until a verified transfer completes.
        force = force or (self.usb_ready and self.reconnect_pending)
        if not scheduled and not force:
            if was_ready and not self.usb_ready:
                return "mooInk USB 已離線，等待重新連線"
            return None
        src, dst = settings.paths()
        if not src.is_file():
            return "等待 " + src.name
        source_stat = src.stat()
        digest = sha256(src)
        record = self.rendered.get(key)
        current = (isinstance(record, dict) and record.get("source") == digest
                   and record.get("word_mtime_ns") == source_stat.st_mtime_ns
                   and dst.is_file() and record.get("png") == sha256(dst))
        rendered_now = force or not current
        if rendered_now:
            with tempfile.TemporaryDirectory(prefix="mooink_source_") as directory:
                snapshot = Path(directory) / src.name
                shutil.copyfile(src, snapshot)
                if (sha256(snapshot) != digest or sha256(src) != digest
                        or src.stat().st_mtime_ns != source_stat.st_mtime_ns):
                    return "Word 正在儲存，稍後重試"
                log("正在轉換 " + src.name)
                self.render(snapshot, dst, settings.size)
            if sha256(src) != digest or src.stat().st_mtime_ns != source_stat.st_mtime_ns:
                return "Word 已再更新，稍後重試最新版本"
            record = {"source": digest, "png": sha256(dst),
                      "word_mtime": source_stat.st_mtime,
                      "word_mtime_ns": source_stat.st_mtime_ns}
            self.rendered[key] = record
            write_json(self.state_path, self.rendered)
            log("本機 PNG 已輸出：" + dst.name)
        if not settings.enable_usb:
            return ("無更新：" + src.name if current and not force else
                    dst.name + " 已輸出；USB 自動傳送尚未啟用")
        if not self.usb_ready:
            return "等待 mooInk USB 連線；本機 PNG 已就緒：" + (usb_error or "")
        # The immutable transfer snapshot is also the source of the UI preview.
        with tempfile.TemporaryDirectory(prefix="mooink_transfer_") as directory:
            snapshot = Path(directory) / dst.name
            shutil.copyfile(dst, snapshot)
            if sha256(snapshot) != record["png"] or sha256(src) != digest:
                return "本機檔案已更新，暫不傳送；稍後重試"
            try:
                result = self.upload(settings, snapshot, log)
            except Exception:
                self.usb_ready = False
                self.reconnect_pending = True
                on_connection("mooInk USB：傳送失敗，等待重試")
                raise
            if result["changed"] or rendered_now:
                on_success({"updated_at": time.time(), "word_mtime": record["word_mtime"],
                            "word_name": src.name, "png_name": dst.name,
                            "png_bytes": snapshot.read_bytes()})
        self.reconnect_pending = False
        if sha256(src) != digest or sha256(dst) != record["png"]:
            return "檔案已更新，暫不喚醒；稍後重試"
        if not result["changed"] and not rendered_now:
            return "無更新：sleep/" + dst.name + " 已回讀驗證"
        try:
            wake_result = self.wake(settings.pico_port, log) if settings.pico_port else "未設定 Pico，略過 Space"
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
    root.title("mooInk Display Watcher 設定")
    root.resizable(False, False)
    events = queue.Queue()
    stop, trigger = threading.Event(), threading.Event()
    hotplug_events = queue.Queue()
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
                            force, publish, scheduled=scheduled)
                        results = multiple.check(
                            current, force=force, scheduled=scheduled, device_events=paths,
                            log=publish,
                            inventory=lambda rows: events.put(("inventory", rows)),
                            status=lambda rid, msg: events.put(("device_status", (rid, msg))),
                            success=lambda rid, data: events.put(("device_success", (rid, data))))
                        result = results[-1] if results else (local_result or "裝置掃描完成（未配對或無裝置）")
                    else:
                        result = processor.check(current, force, publish,
                            scheduled=scheduled)
            except Exception as exc:
                result = "失敗，下次重試：" + str(exc)
            if result is not None and (result != previous or force):
                publish(result)
                previous = result
            remaining = max(0.0, next_check - time.monotonic())
            trigger.wait(min(USB_POLL_SEC, remaining) if current.enable_usb else remaining)

    fields = {}
    frame = ttk.Frame(root, padding=15)
    frame.pack(fill="both", expand=True)
    for index,(name,label) in enumerate([
        ("watch_dir","監控資料夾"), ("size","mooInk 尺寸"),
        ("interval_sec","檢查秒數"),
        ("pico_port","Pico CDC COM（空白停用）")]):
        ttk.Label(frame,text=label).grid(row=index,column=0,sticky="w",pady=5)
        fields[name] = tk.StringVar(value=str(getattr(settings,name)))
        if name=="size":
            widget=ttk.Combobox(frame,textvariable=fields[name],values=("10.3","13.3"),state="readonly",width=44)
        else:
            widget=ttk.Entry(frame,textvariable=fields[name],width=47)
        widget.grid(row=index,column=1,sticky="ew",padx=8)
    ttk.Button(frame,text="瀏覽",command=lambda: fields["watch_dir"].set(filedialog.askdirectory() or fields["watch_dir"].get())).grid(row=0,column=2)
    enable_usb = tk.BooleanVar(value=settings.enable_usb)
    ttk.Checkbutton(frame,text="啟用 USB 自動更新 sleep 圖片",
                    variable=enable_usb).grid(row=4,column=0,columnspan=3,sticky="w",pady=5)
    ttk.Label(frame,text="依尺寸產生 mooink_display_10 / 13 的 PNG。\nUSB MTP 備份舊同名 PNG → 上傳 → SHA-256 回讀驗證 → Pico Space（選用）。\n可同時連接多台 mooInk，先以裝置 ID 配對尺寸。\n多裝置 USB 更新不會自動傳送 Pico Space。",wraplength=530).grid(row=6,column=0,columnspan=3,sticky="w",pady=8)
    ttk.Label(frame,textvariable=connection_text).grid(row=5,column=0,columnspan=3,sticky="w")
    ttk.Label(frame,textvariable=status_text,wraplength=550).grid(row=7,column=0,columnspan=3,sticky="w",pady=5)
    def save():
        nonlocal settings, force_pending
        try:
            values={k:v.get().strip() for k,v in fields.items()}
            values["interval_sec"]=int(values["interval_sec"])
            values["enable_usb"] = bool(enable_usb.get())
            new=Settings(**values).validate()
            if new.enable_usb and (not settings.enable_usb or new.size != settings.size):
                if not messagebox.askyesno("啟用 USB 更新",
                    "可同時接多台 Mooink，每台需先以裝置 ID 指定尺寸。\n"
                    "將自動備份及替換 sleep 內專用同名 PNG；其他圖片不會刪除。確定啟用？",parent=root):
                    return
            write_json(CONFIG,asdict(new))
            with settings_lock:
                force_pending = force_pending or (new.enable_usb and new != settings)
                settings=new
            trigger.set()
            root.withdraw()
        except (ValueError,OSError) as exc:
            messagebox.showerror("設定錯誤", str(exc), parent=root)
    usb_box = ttk.LabelFrame(frame, text="USB MTP / Pico 測試", padding=8)
    usb_box.grid(row=8,column=0,columnspan=3,sticky="ew",pady=(4,8))
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
    save_button = ttk.Button(frame,text="儲存並開始監控",command=save)
    save_button.grid(row=9,column=1,sticky="e",pady=10)
    action_buttons.append(save_button)

    preview_box = ttk.LabelFrame(frame, text="最後一次 USB SHA-256 驗證成功的圖片", padding=8)
    preview_box.grid(row=10,column=0,columnspan=3,sticky="ew",pady=5)
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

    def open_device_pairing(readers):
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
        picker.current(0)
        selected()
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
            with settings_lock:
                force_pending = True
            trigger.set()
            populate_tree()
            dialog.destroy()
        ttk.Button(dialog,text="儲存此裝置尺寸",command=assign).pack(pady=14)

    root.protocol("WM_DELETE_WINDOW",root.withdraw)
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
        nonlocal force_pending, manual_busy
        while True:
            try:
                action,value=events.get_nowait()
            except queue.Empty:
                break
            if action=="status":
                status_text.set(value)
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
    if not CONFIG.exists() or not settings.enable_usb:
        root.deiconify()
    root.after(150,drain)
    root.mainloop()

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
