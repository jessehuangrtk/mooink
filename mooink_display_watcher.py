#!/usr/bin/env python3
"""Windows tray watcher: size-specific DOCX -> PNG -> verified Wi-Fi sleep share -> optional Pico F12.
The native Sony API does not have a verified PNG sleep-folder upload route.
Configure an actual UNC sleep share on the target reader before enabling transfer.
"""
from __future__ import annotations
import hashlib
import ipaddress
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path, PureWindowsPath
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
    mooink_ip: str = "192.168.1.140"
    pico_port: str = "COM6"  # blank disables Pico
    wifi_share: str = ""  # must be a real remotely exported sleep directory

    def validate(self):
        if self.size not in RESOLUTIONS:
            raise ValueError("尺寸必須為 10.3 或 13.3")
        if not 5 <= self.interval_sec <= 86400:
            raise ValueError("檢查秒數必須為 5～86400")
        ip = ipaddress.IPv4Address(self.mooink_ip)
        if not ip.is_private or ip.is_loopback or ip.is_unspecified or ip.is_multicast:
            raise ValueError("請指定 mooInk 私有 LAN IPv4 位址")
        if not Path(self.watch_dir).is_dir():
            raise ValueError("監控目錄不存在")
        watch_dir = Path(self.watch_dir).resolve()
        if watch_dir == TEMPLATE_DIR.resolve() or TEMPLATE_DIR.resolve() in watch_dir.parents:
            raise ValueError("template 是原始範本資料夾，不能作為監控目錄")
        if self.pico_port and not re.fullmatch(r"COM[1-9][0-9]*", self.pico_port, re.I):
            raise ValueError("Pico COM 埠格式為 COM6 等，空白表示停用")
        if self.wifi_share:
            share = PureWindowsPath(self.wifi_share)
            if not share.drive.startswith("\\\\") or share.drive.split("\\")[2] != str(ip) or share.name.lower() != "sleep" or ".." in share.parts:
                raise ValueError("Wi-Fi 共享路徑必須是同一 IP 的 UNC sleep 目錄，例如 \\\\192.168.1.140\\實際共享名稱\\sleep")
        return self

    def paths(self):
        stem = "mooink_display_" + ("10" if self.size == "10.3" else "13")
        root = Path(self.watch_dir)
        return root / (stem + ".docx"), root / (stem + ".png")

    def delivery_key(self):
        return json.dumps([str(Path(self.watch_dir).resolve()), self.size,
                           self.mooink_ip, self.wifi_share], ensure_ascii=False)

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

def wifi_upload(settings, source, expected_hash):
    """Transfer only through an explicitly configured, accessible UNC sleep share.
    Stage, verify, then replace our own fixed filename; never remove other photos.
    """
    if not settings.wifi_share:
        raise RuntimeError("尚未設定已確認可存取的 Wi-Fi sleep 共享路徑；PNG 已保留在本機")
    settings.validate()
    target = Path(settings.wifi_share)
    if not target.is_dir():
        raise RuntimeError("無法存取指定的 Wi-Fi sleep 共享資料夾")
    final = target / Path(source).name
    temp = target / ("." + final.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        shutil.copyfile(source, temp)
        if sha256(temp) != expected_hash:
            raise RuntimeError("Wi-Fi 暫存檔回讀 SHA-256 不符")
        os.replace(temp, final)
        if sha256(final) != expected_hash:
            raise RuntimeError("Wi-Fi 目標檔回讀 SHA-256 不符")
    finally:
        temp.unlink(missing_ok=True)
    return str(final)

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
    """No simultaneous jobs; source snapshot + SHA256 + persistent delivery state."""
    def __init__(self, state_path=STATE, render=render_png, upload=wifi_upload, wake=pico_wake):
        self.state_path = state_path
        self.sent = read_json(state_path, {})
        self.render, self.upload, self.wake = render, upload, wake
        self.rendered = {}

    def check(self, settings, force=False, log=lambda msg: None):
        settings.validate()
        src, dst = settings.paths()
        if not src.is_file():
            return "等待 " + src.name
        digest = sha256(src)
        key = settings.delivery_key()
        sent = self.sent.get(key)
        if (not force and isinstance(sent, dict) and sent.get("source") == digest
                and dst.is_file() and sent.get("png") == sha256(dst)):
            return "無更新：" + src.name
        cache_key = str(dst.resolve())
        if (force or not dst.is_file()
                or self.rendered.get(cache_key) != (digest, sha256(dst))):
            with tempfile.TemporaryDirectory(prefix="mooink_source_") as directory:
                snapshot = Path(directory) / src.name
                shutil.copyfile(src, snapshot)
                if sha256(snapshot) != digest or sha256(src) != digest:
                    return "Word 正在儲存，稍後重試"
                log("正在轉換 " + src.name)
                self.render(snapshot, dst, settings.size)
            self.rendered[cache_key] = (digest, sha256(dst))
        if sha256(src) != digest:
            return "Word 已再更新，稍後重試最新版本"
        if not settings.wifi_share:
            return dst.name + " 已輸出；Wi-Fi sleep 共享尚未設定，不送 F12"
        log("正在傳送並回讀驗證 " + dst.name)
        remote = self.upload(settings, dst, sha256(dst))
        # Persist successful transfer before optional wake, preventing repeated
        # upload when Pico is unavailable. Transfer failure stays retryable.
        self.sent[key] = {"source": digest, "png": sha256(dst)}
        write_json(self.state_path, self.sent)
        return "已驗證傳送 " + remote + "；" + self.wake(settings.pico_port, log)

def main():
    import tkinter as tk
    from tkinter import ttk, filedialog, messagebox, simpledialog
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
        ("interval_sec","檢查秒數"), ("mooink_ip","mooInk Wi-Fi IP"),
        ("pico_port","Pico COM（空白停用）"), ("wifi_share","Wi-Fi sleep 共享路徑")]):
        ttk.Label(frame,text=label).grid(row=index,column=0,sticky="w",pady=5)
        fields[name] = tk.StringVar(value=str(getattr(settings,name)))
        if name=="size":
            widget=ttk.Combobox(frame,textvariable=fields[name],values=("10.3","13.3"),state="readonly",width=44)
        else:
            widget=ttk.Entry(frame,textvariable=fields[name],width=47)
        widget.grid(row=index,column=1,sticky="ew",padx=8)
    ttk.Button(frame,text="瀏覽",command=lambda: fields["watch_dir"].set(filedialog.askdirectory() or fields["watch_dir"].get())).grid(row=0,column=2)
    ttk.Label(frame,text="10.3：mooink_display_10.docx → mooink_display_10.png\n13.3：mooink_display_13.docx → mooink_display_13.png\n未設定共享路徑時只產生 PNG，不會假裝上傳成功或送 F12。",wraplength=530).grid(row=6,column=0,columnspan=3,sticky="w",pady=8)
    ttk.Label(frame,textvariable=status_text,wraplength=550).grid(row=7,column=0,columnspan=3,sticky="w",pady=5)
    def save():
        nonlocal settings, force_pending
        try:
            values={k:v.get().strip() for k,v in fields.items()}
            values["interval_sec"]=int(values["interval_sec"])
            new=Settings(**values).validate()
            write_json(CONFIG,asdict(new))
            with settings_lock:
                settings=new
            trigger.set()
            root.withdraw()
        except (ValueError,OSError) as exc:
            messagebox.showerror("設定錯誤", str(exc), parent=root)
    wifi_box = ttk.LabelFrame(frame, text="mooInk Wi-Fi API 配對（不需要共享路徑）", padding=8)
    wifi_box.grid(row=8,column=0,columnspan=3,sticky="ew",pady=(4,8))
    wifi_status = tk.StringVar(value="尚未測試目前 IP 的配對狀態")
    ttk.Label(wifi_box,textvariable=wifi_status,wraplength=540).pack(anchor="w")
    wifi_buttons = ttk.Frame(wifi_box)
    wifi_buttons.pack(fill="x",pady=(6,0))
    pairing_busy = False
    action_buttons = []

    def wifi_job(action):
        nonlocal pairing_busy
        if pairing_busy:
            return
        from mooink_wifi_pairing import (pair, import_credentials, test_connection,
                                         inspect_folders, WifiPairingError)
        from mooink_full_cmd_demo import require_private_ipv4
        try:
            ip = require_private_ipv4(fields["mooink_ip"].get())
        except ValueError as exc:
            messagebox.showerror("IP 錯誤",str(exc),parent=root)
            return
        directory = None
        if action == "pair":
            if not messagebox.askyesno(
                "Wi-Fi 配對",
                "請確認 %s 是你要配對的 mooInk，並讓閱讀器保持喚醒。\n"
                "此操作會啟動裝置配對流程並顯示 PIN，可能變更配對狀態。\n"
                "其他閱讀器的本機憑證不會被覆寫。確定繼續？" % ip,
                parent=root):
                return
        elif action == "import":
            directory = filedialog.askdirectory(
                title="選擇含 deviceid.dat 和 privatekey.dat 的憑證資料夾",
                parent=root)
            if not directory:
                return
        pairing_busy = True
        for button in action_buttons:
            button.configure(state="disabled")
        wifi_status.set("正在連線 %s…" % ip)

        def request_pin():
            answer = {"value": None}
            done = threading.Event()
            events.put(("wifi_pin", (ip, answer, done)))
            while not done.wait(.2):
                if stop.is_set():
                    return None
            return answer["value"]

        def run():
            try:
                if action == "pair":
                    device = pair(ip, request_pin)
                    result = ("已配對並驗證，憑證已加密儲存", device)
                elif action == "import":
                    device = import_credentials(ip, directory)
                    result = ("已驗證並匯入加密憑證", device)
                elif action == "folders":
                    result = inspect_folders(ip)
                else:
                    result = ("已通過 Wi-Fi API 認證", test_connection(ip))
                events.put(("wifi_result", (ip, action, result, None)))
            except WifiPairingError as exc:
                events.put(("wifi_result", (ip, action, None, str(exc))))
            except Exception as exc:
                events.put(("wifi_result", (ip, action, None,
                    "Wi-Fi 操作失敗（%s）；請檢查裝置及相依套件。" % type(exc).__name__)))
        threading.Thread(target=run, daemon=True).start()

    for label, action in (("Wi-Fi 配對","pair"),("匯入既有憑證","import"),
                          ("測試連線","test"),("查詢資料夾","folders")):
        button = ttk.Button(wifi_buttons,text=label,command=lambda a=action: wifi_job(a))
        button.pack(side="left",padx=3)
        action_buttons.append(button)
    save_button = ttk.Button(frame,text="儲存並開始監控",command=save)
    save_button.grid(row=9,column=1,sticky="e",pady=10)
    action_buttons.append(save_button)
    fields["mooink_ip"].trace_add("write",lambda *_: wifi_status.set(
        "IP 已修改，請測試本台裝置的配對狀態" if not pairing_busy else "配對操作進行中；結果將標示原 IP"))

    root.protocol("WM_DELETE_WINDOW",root.withdraw)
    def tray_action(action):
        return lambda *_: events.put((action,None))
    tray=pystray.Icon("mooink_display",icon,"mooInk Display Watcher",pystray.Menu(
        pystray.MenuItem("設定／Wi-Fi 配對",tray_action("settings"),default=True),
        pystray.MenuItem("立即重新轉圖與傳送",tray_action("check")),
        pystray.MenuItem("開啟監控目錄",tray_action("folder")),
        pystray.MenuItem("開啟執行記錄",tray_action("log")),
        pystray.MenuItem("結束",tray_action("exit"))))
    thread=threading.Thread(target=worker,daemon=True)
    def drain():
        nonlocal force_pending, pairing_busy
        while True:
            try:
                action,value=events.get_nowait()
            except queue.Empty:
                break
            if action=="status":
                status_text.set(value)
            elif action=="wifi_pin":
                ip, answer, done = value
                root.deiconify()
                root.lift()
                try:
                    answer["value"] = simpledialog.askstring(
                        "mooInk Wi-Fi PIN", "請輸入 %s 螢幕上顯示的配對 PIN：" % ip,
                        show="*",parent=root)
                finally:
                    done.set()
            elif action=="wifi_result":
                ip, operation, result, error = value
                pairing_busy = False
                for button in action_buttons:
                    button.configure(state="normal")
                if error:
                    wifi_status.set(ip + "：" + error)
                    logger.info("Wi-Fi API %s: %s", ip, error)
                    messagebox.showwarning("Wi-Fi 操作", error, parent=root)
                elif operation == "folders":
                    folders = result["folders"]
                    sleep = result["sleep"]
                    summary = ("找到 sleep：" + "、".join(sleep) if sleep else
                               "目前回傳清單未列出 sleep")
                    if not result["complete"]:
                        summary += "（API 清單可能不完整）"
                    wifi_status.set("%s：認證成功；%s" % (ip, summary))
                    dialog = tk.Toplevel(root)
                    dialog.title("mooInk Wi-Fi 資料夾 - " + ip)
                    dialog.geometry("620x460")
                    ttk.Label(dialog,text="API 回傳 %d 個資料夾；%s。\n即使列出 sleep，也尚未證明 PNG 寫入可用。" %
                              (len(folders),summary),wraplength=590,padding=8).pack(fill="x")
                    panel = ttk.Frame(dialog)
                    panel.pack(fill="both",expand=True,padx=8,pady=8)
                    listing = tk.Text(panel,wrap="none")
                    scroll = ttk.Scrollbar(panel,orient="vertical",command=listing.yview)
                    listing.configure(yscrollcommand=scroll.set)
                    scroll.pack(side="right",fill="y")
                    listing.pack(side="left",fill="both",expand=True)
                    listing.insert("1.0","\n".join(folders) or "（沒有資料夾）")
                    listing.configure(state="disabled")
                    logger.info("Wi-Fi API %s: 資料夾 %d 個, sleep 列出 %s, 清單完整 %s",
                                ip,len(folders),bool(sleep),result["complete"])
                else:
                    message, device = result
                    wifi_status.set("%s：%s（%s）" % (ip,message,device[:12]))
                    logger.info("Wi-Fi API %s: %s",ip,message)
                if fields["mooink_ip"].get().strip() != ip:
                    wifi_status.set("原 IP %s 操作已結束；目前 IP 已改，請重新測試。" % ip)
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
    if not CONFIG.exists():
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
