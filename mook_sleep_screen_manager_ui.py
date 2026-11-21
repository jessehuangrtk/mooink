#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""mooInk sleep-screen Windows GUI (Python 3.9+).

This file is intentionally a thin UI layer. All conversion, MTP file
operations, backups/deletes, and Wi-Fi probing delegate to
mook_sleep_screen_manager.py. Tkinter is in the Python standard library.

Windows MTP operations run in a dedicated COM-initialized worker thread.
Do not treat an MTP asynchronous CopyHere/delete request as confirmed.
"""

from __future__ import annotations

import queue
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from mook_sleep_screen_manager import (
    MooinkLowLevel,
    SleepScreenManager,
)


class MooinkSleepUI:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("mooInk Pro · 休眠畫面管理")
        self.root.geometry("1120x760")
        self.root.minsize(850, 600)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        self.output_dir = tk.StringVar(value=str(Path.cwd() / "mooink_prepared"))
        self.backup_dir = tk.StringVar(value=str(Path.cwd() / "mooink_backup"))
        self.reader_size = tk.StringVar(value="13.3")
        self.orientation = tk.StringVar(value="auto")
        self.fit = tk.StringVar(value="smart")
        self.transport = tk.StringVar(value="usb")
        self.device_ip = tk.StringVar()
        self.wifi_share = tk.StringVar()
        self.status = tk.StringVar(value="就緒：先匯入圖片，或連接 USB mooInk。")
        self._inputs: list[str] = []
        self._converted: list[Path] = []
        self._device_names: list[str] = []
        self._preview_path: Path | None = None
        self._preview_photo = None
        self._busy = False
        self._messages: queue.Queue = queue.Queue()

        self._style()
        self._build()
        self.root.after(100, self._drain)
        self._log("UI 已啟動；所有裝置檔案操作由 mook_sleep_screen_manager.py 執行。")

    def _style(self):
        style = ttk.Style(self.root)
        if "clam" in style.theme_names():
            style.theme_use("clam")
        family = "Microsoft JhengHei UI" if self.root.tk.call("tk", "windowingsystem") == "win32" else "TkDefaultFont"
        style.configure(".", font=(family, 10))
        style.configure("Title.TLabel", font=(family, 17, "bold"))
        style.configure("Subtitle.TLabel", foreground="#4f5d6a")
        style.configure("Heading.TLabelframe.Label", font=(family, 11, "bold"))
        style.configure("Action.TButton", padding=(10, 7))
        style.configure("TNotebook.Tab", padding=(15, 8))

    def _build(self):
        shell = ttk.Frame(self.root, padding=(16, 12))
        shell.pack(fill="both", expand=True)
        ttk.Label(shell, text="mooInk Pro 休眠畫面管理", style="Title.TLabel").pack(anchor="w")
        ttk.Label(
            shell, style="Subtitle.TLabel",
            text="10.3 / 13.3 吋 · 圖片與 Office/PDF 轉換 · Windows USB MTP · Wi-Fi 僅支援已掛載的 sleep 共享資料夾",
        ).pack(anchor="w", pady=(2, 12))
        notebook = ttk.Notebook(shell)
        notebook.pack(fill="both", expand=True)
        tab_convert = ttk.Frame(notebook, padding=10)
        tab_device = ttk.Frame(notebook, padding=10)
        tab_logs = ttk.Frame(notebook, padding=10)
        notebook.add(tab_convert, text="圖片製作")
        notebook.add(tab_device, text="裝置 / sleep 管理")
        notebook.add(tab_logs, text="執行記錄")
        self.notebook = notebook

        self._build_convert(tab_convert)
        self._build_device(tab_device)
        self._build_logs(tab_logs)

        foot = ttk.Frame(shell)
        foot.pack(fill="x", pady=(10, 0))
        self.progress = ttk.Progressbar(foot, mode="indeterminate", length=130)
        self.progress.pack(side="right", padx=(10, 0))
        ttk.Label(foot, textvariable=self.status, anchor="w").pack(side="left", fill="x", expand=True)

    def _build_convert(self, tab):
        tab.columnconfigure(0, weight=3)
        tab.columnconfigure(1, weight=2)
        tab.rowconfigure(0, weight=1)

        left = ttk.Frame(tab)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 12))
        right = ttk.LabelFrame(tab, text="  圖片預覽  ", style="Heading.TLabelframe")
        right.grid(row=0, column=1, sticky="nsew")
        right.rowconfigure(0, weight=1)
        right.columnconfigure(0, weight=1)
        self.preview_label = ttk.Label(
            right, text="轉檔後點選檔案，即可在這裡預覽。", anchor="center", justify="center",
        )
        self.preview_label.grid(row=0, column=0, sticky="nsew", padx=8, pady=8)
        right.bind("<Configure>", lambda _e: self._render_preview())

        config = ttk.LabelFrame(left, text="  輸出設定  ", style="Heading.TLabelframe", padding=10)
        config.pack(fill="x", pady=(0, 10))
        line = ttk.Frame(config)
        line.pack(fill="x")
        ttk.Label(line, text="螢幕尺寸").pack(side="left")
        ttk.Combobox(
            line, state="readonly", width=8, textvariable=self.reader_size, values=("10.3", "13.3"),
        ).pack(side="left", padx=(6, 16))
        ttk.Label(line, text="方向").pack(side="left")
        ttk.Combobox(
            line, state="readonly", width=11, textvariable=self.orientation,
            values=("auto", "portrait", "landscape"),
        ).pack(side="left", padx=(6, 16))
        ttk.Label(line, text="縮放").pack(side="left")
        ttk.Combobox(
            line, state="readonly", width=10, textvariable=self.fit,
            values=("smart", "contain", "cover"),
        ).pack(side="left", padx=6)

        pathrow = ttk.Frame(config)
        pathrow.pack(fill="x", pady=(10, 0))
        ttk.Label(pathrow, text="輸出目錄").pack(side="left")
        ttk.Entry(pathrow, textvariable=self.output_dir).pack(side="left", fill="x", expand=True, padx=8)
        ttk.Button(pathrow, text="瀏覽", command=self._choose_output).pack(side="left")

        files_box = ttk.LabelFrame(left, text="  原始檔案（可多選）  ", style="Heading.TLabelframe", padding=10)
        files_box.pack(fill="both", expand=True, pady=(0, 10))
        top = ttk.Frame(files_box)
        top.pack(fill="x", pady=(0, 6))
        ttk.Button(top, text="加入圖片 / Word / Excel / PDF", command=self._add_inputs).pack(side="left")
        ttk.Button(top, text="移除選取", command=self._remove_inputs).pack(side="left", padx=6)
        ttk.Button(top, text="全部清除", command=self._clear_inputs).pack(side="left")
        list_frame = ttk.Frame(files_box)
        list_frame.pack(fill="both", expand=True)
        self.inputs_list = tk.Listbox(list_frame, selectmode="extended", height=7, exportselection=False)
        scroll = ttk.Scrollbar(list_frame, orient="vertical", command=self.inputs_list.yview)
        self.inputs_list.configure(yscrollcommand=scroll.set)
        self.inputs_list.pack(side="left", fill="both", expand=True)
        scroll.pack(side="left", fill="y")

        ttk.Button(
            left, text="開始批次轉換", command=self._convert, style="Action.TButton",
        ).pack(fill="x", pady=(0, 10))

        outputs = ttk.LabelFrame(left, text="  已轉換檔案（點選預覽）  ", style="Heading.TLabelframe", padding=10)
        outputs.pack(fill="both", expand=True)
        self.output_tree = ttk.Treeview(outputs, columns=("name", "size"), show="headings", height=6)
        self.output_tree.heading("name", text="檔名")
        self.output_tree.heading("size", text="大小")
        self.output_tree.column("name", width=370, stretch=True)
        self.output_tree.column("size", width=80, stretch=False, anchor="e")
        self.output_tree.pack(fill="both", expand=True)
        self.output_tree.bind("<<TreeviewSelect>>", self._select_output)
        ttk.Label(
            outputs, text="提醒：轉換不會自動上傳；請到「裝置 / sleep 管理」選擇圖片後上傳。",
            style="Subtitle.TLabel",
        ).pack(anchor="w", pady=(5, 0))

    def _build_device(self, tab):
        tab.columnconfigure(0, weight=1)
        tab.rowconfigure(2, weight=1)

        conn = ttk.LabelFrame(tab, text="  裝置連線  ", style="Heading.TLabelframe", padding=12)
        conn.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        first = ttk.Frame(conn)
        first.pack(fill="x")
        ttk.Radiobutton(first, text="USB MTP（Windows）", variable=self.transport, value="usb").pack(side="left")
        ttk.Radiobutton(first, text="已掛載 Wi-Fi sleep 共享目錄", variable=self.transport, value="wifi").pack(side="left", padx=24)
        second = ttk.Frame(conn)
        second.pack(fill="x", pady=(10, 0))
        ttk.Label(second, text="USB 裝置").pack(side="left")
        self.device_combo = ttk.Combobox(second, state="readonly", width=45)
        self.device_combo.pack(side="left", padx=8, fill="x", expand=True)
        ttk.Button(second, text="重新搜尋", command=self._refresh_devices).pack(side="left")
        iprow = ttk.Frame(conn)
        iprow.pack(fill="x", pady=(8, 0))
        ttk.Label(iprow, text="裝置 IP").pack(side="left")
        ttk.Entry(iprow, textvariable=self.device_ip, width=20).pack(side="left", padx=(8, 16))
        ttk.Button(iprow, text="8080 / 8443 網路探測", command=self._probe_wifi).pack(side="left")
        share = ttk.Frame(conn)
        share.pack(fill="x", pady=(8, 0))
        ttk.Label(share, text="Wi-Fi sleep 共享路徑").pack(side="left")
        ttk.Entry(share, textvariable=self.wifi_share).pack(side="left", fill="x", expand=True, padx=8)
        ttk.Button(share, text="瀏覽", command=self._choose_share).pack(side="left")

        note = ttk.Label(
            tab, style="Subtitle.TLabel", wraplength=980,
            text="安全提示：同時接上兩台 USB mooInk 時，為避免誤寫，後端會拒絕寫入／刪除。"
                 "只有連線成功不代表能控制電子紙喚醒；Wi-Fi IP 本身不能當作 sleep 共享資料夾。",
        )
        note.grid(row=1, column=0, sticky="w", pady=(0, 10))

        storage = ttk.LabelFrame(tab, text="  裝置 sleep 圖片  ", style="Heading.TLabelframe", padding=12)
        storage.grid(row=2, column=0, sticky="nsew")
        storage.rowconfigure(1, weight=1)
        storage.columnconfigure(0, weight=1)
        controls = ttk.Frame(storage)
        controls.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        ttk.Button(controls, text="讀取圖片清單", command=self._list_sleep).pack(side="left")
        ttk.Button(controls, text="選擇 JPG / PNG 上傳", command=self._upload).pack(side="left", padx=8)
        ttk.Button(controls, text="備份並刪除選取", command=self._delete).pack(side="left")
        tree_frame = ttk.Frame(storage)
        tree_frame.grid(row=1, column=0, sticky="nsew")
        self.sleep_tree = ttk.Treeview(tree_frame, columns=("name", "size"), show="headings", selectmode="extended")
        self.sleep_tree.heading("name", text="sleep 內的檔案")
        self.sleep_tree.heading("size", text="大小")
        self.sleep_tree.column("name", width=580, stretch=True)
        self.sleep_tree.column("size", width=140, anchor="e")
        self.sleep_tree.pack(side="left", fill="both", expand=True)
        vbar = ttk.Scrollbar(tree_frame, orient="vertical", command=self.sleep_tree.yview)
        self.sleep_tree.configure(yscrollcommand=vbar.set)
        vbar.pack(side="left", fill="y")
        back = ttk.Frame(storage)
        back.grid(row=2, column=0, sticky="ew", pady=(12, 0))
        ttk.Label(back, text="備份資料夾").pack(side="left")
        ttk.Entry(back, textvariable=self.backup_dir).pack(side="left", fill="x", expand=True, padx=8)
        ttk.Button(back, text="瀏覽", command=self._choose_backup).pack(side="left")

    def _build_logs(self, tab):
        ttk.Label(tab, text="操作結果：傳輸請求與驗證狀態分開呈現，不會把非同步請求誤報為已完成。").pack(anchor="w")
        frame = ttk.Frame(tab)
        frame.pack(fill="both", expand=True, pady=(8, 0))
        self.logs = tk.Text(frame, state="disabled", wrap="word", height=15)
        self.logs.pack(side="left", fill="both", expand=True)
        scroll = ttk.Scrollbar(frame, orient="vertical", command=self.logs.yview)
        self.logs.configure(yscrollcommand=scroll.set)
        scroll.pack(side="left", fill="y")

    def _log(self, text: str):
        from datetime import datetime
        self.logs.configure(state="normal")
        self.logs.insert("end", "[%s] %s\n" % (datetime.now().strftime("%H:%M:%S"), text))
        self.logs.see("end")
        self.logs.configure(state="disabled")

    def _choose_output(self):
        path = filedialog.askdirectory(title="選擇轉換圖片輸出目錄", initialdir=self.output_dir.get())
        if path:
            self.output_dir.set(path)

    def _choose_backup(self):
        path = filedialog.askdirectory(title="選擇備份目錄")
        if path:
            self.backup_dir.set(path)

    def _choose_share(self):
        path = filedialog.askdirectory(title="選擇已掛載的 sleep 資料夾")
        if path:
            self.wifi_share.set(path)

    def _add_inputs(self):
        paths = filedialog.askopenfilenames(
            title="選擇圖片、PDF、Excel 或 Word",
            filetypes=[("支援的檔案", "*.jpg *.jpeg *.png *.webp *.bmp *.tif *.tiff *.gif *.avif "
                       "*.pdf *.xlsx *.xls *.xlsm *.xlsb *.ods *.docx *.doc *.odt"),
                       ("所有檔案", "*.*")],
        )
        for raw in paths:
            if raw not in self._inputs:
                self._inputs.append(raw)
                self.inputs_list.insert("end", raw)

    def _remove_inputs(self):
        for i in reversed(self.inputs_list.curselection()):
            self.inputs_list.delete(i)
            del self._inputs[i]

    def _clear_inputs(self):
        self._inputs.clear()
        self.inputs_list.delete(0, "end")

    def _task(self, title, action, on_success):
        if self._busy:
            messagebox.showinfo("操作進行中", "請先完成目前的操作。")
            return
        self._busy = True
        self.status.set(title + "…")
        self.progress.start(12)
        self._log("開始：" + title)

        def run():
            try:
                result = action()
                self._messages.put((title, result, None, on_success))
            except Exception as exc:
                self._messages.put((title, None, "%s: %s" % (type(exc).__name__, exc), on_success))

        threading.Thread(target=run, daemon=True, name="mooink-gui-worker").start()

    def _drain(self):
        try:
            while True:
                title, result, error, callback = self._messages.get_nowait()
                self._busy = False
                self.progress.stop()
                if error:
                    self.status.set(title + "：失敗")
                    self._log("錯誤：" + error)
                    messagebox.showerror(title + "失敗", error)
                else:
                    try:
                        callback(result)
                        self.status.set(title + "：已結束（請查看結果）")
                        self._log("結束：" + title)
                    except Exception as exc:
                        self.status.set(title + "：顯示結果失敗")
                        self._log("介面顯示錯誤：" + str(exc))
                        messagebox.showerror("顯示錯誤", str(exc))
        except queue.Empty:
            pass
        self.root.after(100, self._drain)

    @staticmethod
    def _com_call(action):
        # Each GUI operation creates fresh Shell COM objects in this thread.
        # Never pass Shell COM proxies between the UI and worker threads.
        import pythoncom
        pythoncom.CoInitialize()
        try:
            return action()
        finally:
            pythoncom.CoUninitialize()

    def _manager_options(self):
        """Snapshot UI configuration on Tk's main thread before starting a worker."""
        return {
            "size": self.reader_size.get(),
            "orientation": self.orientation.get(),
            "fit": self.fit.get(),
            "output_dir": self.output_dir.get().strip(),
        }

    def _convert(self):
        if not self._inputs:
            messagebox.showinfo("缺少檔案", "請先加入至少一個圖片或文件。")
            return
        inputs = list(self._inputs)
        options = self._manager_options()
        if not options["output_dir"]:
            messagebox.showwarning("缺少目錄", "請指定輸出目錄。")
            return

        def work():
            manager = SleepScreenManager(**options)
            paths = manager.convert(inputs)
            return ([str(p) for p in paths], list(manager.logs))

        def done(result):
            names, logs = result
            for line in logs:
                self._log(line)
            self._converted = [Path(p) for p in names]
            for item in self.output_tree.get_children():
                self.output_tree.delete(item)
            for index, path in enumerate(self._converted):
                self.output_tree.insert("", "end", iid=str(index),
                                        values=(path.name, "%0.1f KB" % (path.stat().st_size / 1024)))
            self._log("轉換產生 %d 張圖片；尚未傳到裝置。" % len(names))
            if names:
                self.output_tree.selection_set("0")

        self._task("轉換圖片", work, done)

    def _select_output(self, _event=None):
        selected = self.output_tree.selection()
        if not selected:
            return
        self._preview_path = self._converted[int(selected[0])]
        self._render_preview()

    def _render_preview(self):
        if not self._preview_path:
            return
        try:
            from PIL import Image, ImageTk
            width = max(190, self.preview_label.winfo_width() - 20)
            height = max(190, self.preview_label.winfo_height() - 20)
            with Image.open(self._preview_path) as im:
                preview = im.copy()
            preview.thumbnail((width, height), Image.Resampling.LANCZOS)
            self._preview_photo = ImageTk.PhotoImage(preview)
            self.preview_label.configure(image=self._preview_photo, text="")
        except Exception as exc:
            self.preview_label.configure(image="", text="預覽失敗：" + str(exc))

    def _refresh_devices(self):
        def work():
            io = MooinkLowLevel(transport="usb")
            return self._com_call(io.usb_devices)

        def done(names):
            self._device_names = list(names)
            labels = ["%d. %s" % (i + 1, name) for i, name in enumerate(names)]
            self.device_combo["values"] = labels
            self.device_combo.set(labels[0] if labels else "")
            self._log("目前偵測到 %d 台 mooInk MTP 裝置。" % len(names))
            if len(names) > 1:
                self._log("安全限制：兩台同時連接時禁止 USB 寫入及刪除。")

        self._task("搜尋 USB 裝置", work, done)

    def _transport_snapshot(self):
        mode = self.transport.get()
        ip = self.device_ip.get().strip() or None
        share = self.wifi_share.get().strip() or None
        if mode == "usb":
            index = self.device_combo.current()
            if index < 0 or index >= len(self._device_names):
                raise ValueError("請先按『重新搜尋』，再選取 USB 裝置。")
            return {"transport": "usb", "ip": ip, "usb_index": index + 1}
        if not share:
            raise ValueError("Wi-Fi 檔案管理需要已掛載、且名稱為 sleep 的共享資料夾；只有 IP 不夠。")
        return {"transport": "wifi", "ip": ip, "wifi_share": share}

    def _device_job(self, title, action, done, *, writing=False):
        try:
            settings = self._transport_snapshot()
        except ValueError as exc:
            messagebox.showwarning("裝置尚未設定", str(exc))
            return
        if writing and settings["transport"] == "usb" and len(self._device_names) > 1:
            messagebox.showwarning("禁止跨裝置誤寫", "同時接上兩台 mooInk。請先拔除另一台，再執行寫入或刪除。")
            return

        def work():
            def operate():
                io = MooinkLowLevel(**settings)
                return action(io)
            return self._com_call(operate) if settings["transport"] == "usb" else operate()

        self._task(title, work, done)

    def _list_sleep(self):
        def done(rows):
            self._replace_sleep(rows)
            self._log("讀取到 %d 張 sleep 圖片。" % len(rows))
        self._device_job("讀取 sleep 圖片", lambda io: io.list_sleep(), done)

    def _replace_sleep(self, rows):
        for item in self.sleep_tree.get_children():
            self.sleep_tree.delete(item)
        for i, row in enumerate(rows):
            size = row.get("size")
            self.sleep_tree.insert("", "end", iid=str(i),
                                   values=(row["name"], "未知" if size is None else "%0.1f KB" % (size / 1024)))

    def _upload(self):
        paths = filedialog.askopenfilenames(
            title="選擇要新增至 mooInk sleep 的 JPG/PNG",
            initialdir=self.output_dir.get() if Path(self.output_dir.get()).is_dir() else None,
            filetypes=[("JPG / PNG", "*.jpg *.jpeg *.png")],
        )
        if not paths:
            return
        if not messagebox.askyesno(
            "確認新增", "要新增 %d 張圖片至 sleep 嗎？\n已有同名檔案時會拒絕覆寫。" % len(paths),
        ):
            return

        options = self._manager_options()

        def operation(io):
            manager = SleepScreenManager(io_layer=io, **options)
            return manager.manage(files=list(paths), operation="add")

        def done(result):
            for path, state in result:
                self._log("上傳 %s：%s" % (Path(path).name, state))
            self._log("若回報 verification-pending，表示尚未確認 USB 傳輸完成，請重新讀取清單。")

        self._device_job("新增 sleep 圖片", operation, done, writing=True)

    def _delete(self):
        selected = self.sleep_tree.selection()
        if not selected:
            messagebox.showinfo("未選取圖片", "請先選擇 sleep 清單中需要備份與刪除的檔案。")
            return
        names = [self.sleep_tree.item(i, "values")[0] for i in selected]
        back_dir = self.backup_dir.get().strip()
        if not back_dir:
            messagebox.showwarning("未指定備份目錄", "請指定本機備份目錄。")
            return
        if not messagebox.askyesno(
            "確認備份並刪除",
            "即將對 %d 張圖片逐一執行「先備份、驗證，再要求刪除」。\n\n%s\n\n確定繼續？" %
            (len(names), "\n".join(names[:10]) + ("\n…" if len(names) > 10 else "")),
        ):
            return

        options = self._manager_options()

        def operation(io):
            manager = SleepScreenManager(io_layer=io, **options)
            return manager.manage(files=names, operation="delete", backup_dir=back_dir, confirm=False)

        def done(result):
            for name, state in result:
                self._log("備份／刪除 %s：%s" % (name, state))
            self._log("Windows MTP 刪除為非同步要求；請重新讀取清單確認是否真的刪除。")

        self._device_job("備份並刪除", operation, done, writing=True)

    def _probe_wifi(self):
        ip = self.device_ip.get().strip()
        if not ip:
            messagebox.showwarning("缺少 IP", "請輸入 mooInk 的區域網路 IPv4 位址。")
            return

        def done(result):
            self._log("Wi-Fi TCP 8080 / 8443：" + str(result))
            messagebox.showinfo("網路探測結果", str(result) + "\n\n這不代表已支援遠端喚醒或 sleep 檔案存取。")

        self._task("探測 Wi-Fi API", lambda: MooinkLowLevel(transport="wifi", ip=ip).wifi_probe(), done)

    def _on_close(self):
        if self._busy:
            messagebox.showwarning("裝置操作中", "目前有轉換／傳輸作業，請等作業結束再關閉視窗。")
            return
        self.root.destroy()


def main():
    root = tk.Tk()
    MooinkSleepUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
