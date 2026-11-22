#!/usr/bin/env python3
"""Windows watcher: DOCX -> grayscale PNG -> USB MTP sleep -> optional Pico F12.
Only the reserved image for the selected size can be replaced.
"""
from __future__ import annotations
import hashlib
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
from dataclasses import dataclass, asdict
import uuid

from mook_sleep_screen_manager import SleepScreenManager, RESOLUTIONS

CONFIG_DIR = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "mooink"
CONFIG = CONFIG_DIR / "mooink_display_watcher.json"
STATE = CONFIG_DIR / "mooink_display_watcher_state.json"
LOG = CONFIG_DIR / "mooink_display_watcher.log"
PROJECT_DIR = Path(__file__).resolve().parent
TEMPLATE_DIR = PROJECT_DIR / "template"
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

def usb_upload(settings, source, log):
    import pythoncom
    from mook_mtp_wpd import replace_watch_image
    pythoncom.CoInitialize()
    try:
        return replace_watch_image(
            source, CONFIG_DIR / "watcher_backup",
            progress=lambda stage, detail: log(detail.get("message", stage)))
    finally:
        pythoncom.CoUninitialize()


def usb_list():
    import pythoncom
    from mook_mtp_wpd import list_images
    pythoncom.CoInitialize()
    try:
        return list_images()
    finally:
        pythoncom.CoUninitialize()


def pico_wake(port, log):
    if not port:
        return "未設定 Pico，略過 F12"
    try:
        from pico2w_usb2bluetooth import PicoKeyboard, ports
        if not any(p.device.casefold() == port.casefold() for p in ports()):
            return "找不到 " + port + "，略過 F12"
        with PicoKeyboard(port, event=log) as kb:
            kb.wake(key="f12", wait=0)
        return "F12 已送出；電子紙是否喚醒尚待觀察"
    except Exception as exc:
        return "Pico 喚醒略過：" + str(exc)

class Processor:
    """Persist local renders, verify the attached reader before each update."""
    def __init__(self, state_path=STATE, render=render_png, upload=usb_upload,
                 wake=pico_wake):
        self.state_path = state_path
        self.rendered = read_json(state_path, {})
        self.render, self.upload, self.wake = render, upload, wake

    def check(self, settings, force=False, log=lambda msg: None):
        settings.validate()
        src, dst = settings.paths()
        if not src.is_file():
            return "等待 " + src.name
        digest = sha256(src)
        key = settings.render_key()
        record = self.rendered.get(key)
        current = (isinstance(record, dict) and record.get("source") == digest
                   and dst.is_file() and record.get("png") == sha256(dst))
        if force or not current:
            with tempfile.TemporaryDirectory(prefix="mooink_source_") as directory:
                snapshot = Path(directory) / src.name
                shutil.copyfile(src, snapshot)
                if sha256(snapshot) != digest or sha256(src) != digest:
                    return "Word 正在儲存，稍後重試"
                log("正在轉換 " + src.name)
                self.render(snapshot, dst, settings.size)
            if sha256(src) != digest:
                return "Word 已再更新，稍後重試最新版本"
            record = {"source": digest, "png": sha256(dst)}
            self.rendered[key] = record
            write_json(self.state_path, self.rendered)
            log("本機 PNG 已輸出：" + dst.name)
        if not settings.enable_usb:
            return ("無更新：" + src.name if current and not force else
                    dst.name + " 已輸出；USB 自動傳送尚未啟用")
        # Recheck the actual USB reader each cycle: a reconnect or another
        # reader at the same port must not inherit a previous delivery cache.
        with tempfile.TemporaryDirectory(prefix="mooink_transfer_") as directory:
            snapshot = Path(directory) / dst.name
            shutil.copyfile(dst, snapshot)
            if sha256(snapshot) != record["png"] or sha256(src) != digest:
                return "本機檔案已更新，暫不傳送；稍後重試"
            result = self.upload(settings, snapshot, log)
        if sha256(src) != digest or sha256(dst) != record["png"]:
            return "檔案已更新，暫不喚醒；稍後重試"
        if not result["changed"] and not force:
            return "無更新：sleep/" + dst.name + " 已回讀驗證"
        wake_result = self.wake(settings.pico_port, log)
        log(wake_result)
        return "USB sleep/" + dst.name + " 已回讀驗證；" + wake_result + "；請觀察休眠畫面"

def main():
    import tkinter as tk
    from tkinter import ttk, filedialog, messagebox
    import pystray
    from PIL import Image, ImageDraw
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
    settings_lock = threading.Lock()
    operation_lock = threading.Lock()  # serialize USB and manual F12
    force_pending = False
    status_text = tk.StringVar(value="等待首次檢查")
    processor = Processor()
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
        initialized = set()
        while not stop.is_set():
            trigger.clear()
            with settings_lock:
                current, force = settings, force_pending
                force_pending = False
            try:
                directory = Path(current.watch_dir).resolve()
                if directory not in initialized:
                    created = initialize_workfiles(directory)
                    if created:
                        publish("已建立本機工作檔：" + "、".join(created))
                    initialized.add(directory)
                with operation_lock:
                    result = processor.check(current, force, publish)
            except Exception as exc:
                result = "失敗，下次重試：" + str(exc)
            if result != previous or force:
                publish(result)
                previous = result
            trigger.wait(current.interval_sec)

    fields = {}
    frame = ttk.Frame(root, padding=15)
    frame.pack(fill="both", expand=True)
    for index,(name,label) in enumerate([
        ("watch_dir","監控資料夾"), ("size","mooInk 尺寸"),
        ("interval_sec","檢查秒數"),
        ("pico_port","Pico COM（空白停用）")]):
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
    ttk.Label(frame,text="依尺寸產生 mooink_display_10 / 13 的 PNG。\nUSB MTP 備份舊同名 PNG → 上傳 → SHA-256 回讀驗證 → Pico F12（選用）。\n只接一台 mooInk，並將同名 PNG 設為休眠圖。",wraplength=530).grid(row=6,column=0,columnspan=3,sticky="w",pady=8)
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
                    "請只接一台 mooInk USB，並確認 Pico 配對同一台。\n"
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
            if not messagebox.askyesno("測試 F12","將送出 F12 至 Pico 已配對的閱讀器，請確認目標裝置。",parent=root):
                return
        manual_busy = True
        for button in action_buttons:
            button.configure(state="disabled")
        usb_status.set("正在檢查…")
        def run():
            try:
                with operation_lock:
                    result = usb_list() if action == "list" else pico_wake(port, logger.info)
                events.put(("manual_result",(action,result,None)))
            except Exception as exc:
                events.put(("manual_result",(action,None,str(exc))))
        threading.Thread(target=run,daemon=True).start()
    for label, action in (("測試 USB／查詢 sleep","list"),("測試 F12","wake")):
        button = ttk.Button(buttons,text=label,command=lambda a=action: manual_job(a))
        button.pack(side="left",padx=3)
        action_buttons.append(button)
    save_button = ttk.Button(frame,text="儲存並開始監控",command=save)
    save_button.grid(row=9,column=1,sticky="e",pady=10)
    action_buttons.append(save_button)

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
                else:
                    usb_status.set("USB 連線成功；sleep 內 %d 張圖片" % len(result))
                    dialog = tk.Toplevel(root)
                    dialog.title("USB mooInk sleep 圖片清單")
                    dialog.geometry("600x420")
                    listing = tk.Text(dialog,wrap="none")
                    listing.pack(fill="both",expand=True,padx=8,pady=8)
                    listing.insert("1.0","\n".join(
                        "%s   %s bytes" % (row["name"],row.get("size") or "?")
                        for row in result) or "（沒有圖片）")
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
