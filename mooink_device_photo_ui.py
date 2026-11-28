"""Windows GUI to manage photos physically stored in a selected mooInk.

Unlike the local photo library, this browser lists the reader's WPD sleep
directory and offers verified upload, verified backup, and backup-before-delete.
All WPD calls run under a serialized lock in COM-initialized worker threads.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import queue
import tempfile
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from mooink_photo import prepare_photo_preview, render_photo_png
from mook_sleep_screen_manager import safe_file_name


def prepare_named_photo(source, work_dir, size):
    """Create a safe, stable *new* image name for WPD's no-overwrite upload."""
    source = Path(source).expanduser().resolve()
    preview = prepare_photo_preview(source, work_dir)
    digest = preview.source_sha256[:12]
    name = safe_file_name(source.stem)[:65] + "_" + digest + ".png"
    destination = Path(work_dir) / name
    render_photo_png(preview, size, destination)
    if not preview.source_unchanged():
        raise RuntimeError("原始照片在轉圖期間已修改，停止上傳")
    return destination


class DevicePhotoManager:
    """A separate dialog for managing files on the actual USB device."""
    def __init__(self, parent, registry, operation_lock, backup_root):
        self.registry = registry
        self.operation_lock = operation_lock
        self.backup_root = Path(backup_root)
        self.window = tk.Toplevel(parent)
        self.window.title("mooInk 裝置內照片管理（USB / sleep）")
        self.window.geometry("930x660")
        self.window.minsize(700, 520)
        self.window.transient(parent)
        self.messages = queue.Queue()
        self.busy = False
        self.readers = []
        self.controls = []
        self.reader_id = None
        self.preview_photo = None
        self._build()
        self.window.after(100, self._drain)
        self.refresh_readers()

    def _build(self):
        outer = ttk.Frame(self.window, padding=12)
        outer.pack(fill="both", expand=True)
        ttk.Label(outer, text="裝置內照片 / sleep 資料夾",
                  font=("Microsoft JhengHei UI", 13, "bold")).pack(anchor="w")
        ttk.Label(outer, text="每台 Mooink 獨立選取。刪除一定先下載備份並驗證；上傳不覆寫同名圖片。"
                  ).pack(anchor="w",pady=(2,9))
        device_row = ttk.Frame(outer)
        device_row.pack(fill="x",pady=(0,9))
        ttk.Label(device_row,text="USB 裝置：").pack(side="left")
        self.reader_combo = ttk.Combobox(device_row, state="readonly", width=72)
        self.reader_combo.pack(side="left",fill="x",expand=True,padx=5)
        self.reader_combo.bind("<<ComboboxSelected>>", self._select_reader)
        self._button(device_row,"重新搜尋裝置",self.refresh_readers).pack(side="left",padx=4)
        self._button(device_row,"重新整理照片",self.refresh_photos).pack(side="left")

        main = ttk.Frame(outer)
        main.pack(fill="both",expand=True)
        table_frame = ttk.Frame(main)
        table_frame.pack(side="left",fill="both",expand=True)
        self.tree = ttk.Treeview(table_frame, columns=("bytes",),show="tree headings",
                                 selectmode="extended")
        self.tree.heading("#0",text="裝置內照片")
        self.tree.heading("bytes",text="檔案大小")
        self.tree.column("#0",width=400,stretch=True)
        self.tree.column("bytes",width=110,anchor="e",stretch=False)
        scroll = ttk.Scrollbar(table_frame,orient="vertical",command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side="left",fill="both",expand=True)
        scroll.pack(side="right",fill="y")
        self.tree.bind("<Double-Button-1>",lambda event:self.preview_selected())
        preview_frame=ttk.LabelFrame(main,text="裝置照片預覽",padding=8)
        preview_frame.pack(side="right",fill="both",padx=(10,0))
        self.image_label=ttk.Label(preview_frame,text="選取一張後點「預覽」",
                                   anchor="center",width=32)
        self.image_label.pack(fill="both",expand=True)

        actions=ttk.Frame(outer)
        actions.pack(fill="x",pady=(9,5))
        self._button(actions,"預覽選取",self.preview_selected).pack(side="left",padx=2)
        self._button(actions,"上傳新照片",self.upload_selected_photo).pack(side="left",padx=2)
        self._button(actions,"備份選取",self.backup_selected).pack(side="left",padx=2)
        self._button(actions,"備份並刪除選取",self.delete_selected).pack(side="left",padx=2)
        self._button(actions,"開啟備份資料夾",self.open_backups).pack(side="right",padx=2)
        self.status=tk.StringVar(value="搜尋已連線的 Mooink…")
        ttk.Label(outer,textvariable=self.status,wraplength=850).pack(anchor="w")

    def _button(self,container,label,command):
        button=ttk.Button(container,text=label,command=command)
        self.controls.append(button)
        return button

    def _wpd(self,func,*args,**kwargs):
        import pythoncom
        pythoncom.CoInitialize()
        try:
            return func(*args,**kwargs)
        finally:
            pythoncom.CoUninitialize()

    def _run(self,description,task,callback):
        if self.busy:
            return
        self.busy=True
        self.status.set(description)
        for button in self.controls:
            button.configure(state="disabled")
        def execute():
            try:
                with self.operation_lock:
                    result=task()
                self.messages.put((callback,result,None))
            except Exception as exc:
                self.messages.put((callback,None,str(exc)))
        threading.Thread(target=execute,daemon=True).start()

    def _drain(self):
        if not self.window.winfo_exists():
            return
        while True:
            try:
                callback,result,error=self.messages.get_nowait()
            except queue.Empty:
                break
            self.busy=False
            for button in self.controls:
                button.configure(state="normal")
            if error:
                self.status.set("操作失敗：" + error)
                messagebox.showwarning("mooInk 裝置照片管理",error,parent=self.window)
            else:
                try:
                    callback(result)
                except Exception as exc:
                    self.status.set("顯示結果失敗：" + str(exc))
        self.window.after(100,self._drain)

    def refresh_readers(self):
        from mook_mtp_wpd import list_readers
        def loaded(readers):
            previous=self.reader_id
            self.readers=readers
            labels=[
                "%s  [ID %s]  %s" % (
                    r["name"],r["id"][:12],
                    "可存取" if r["ready"] else "等待 WPD / sleep")
                for r in readers]
            self.reader_combo.configure(values=labels)
            self.reader_id=None
            if not readers:
                self.tree.delete(*self.tree.get_children())
                self.reader_combo.set("")
                self.status.set("WPD 找不到 Mooink；請確認 USB 資料線和 Windows 檔案總管")
                return
            index=next((i for i,r in enumerate(readers) if r["id"]==previous),None)
            if index is None:
                index=next((i for i,r in enumerate(readers) if r["ready"]),0)
            self.reader_combo.current(index)
            self._select_reader()
        self._run("正在搜尋 USB 閱讀器…",
                  lambda:self._wpd(list_readers),loaded)

    def _select_reader(self,event=None):
        index=self.reader_combo.current()
        if index<0 or index>=len(self.readers):
            return
        reader=self.readers[index]
        self.reader_id=reader["id"]
        self.tree.delete(*self.tree.get_children())
        self.preview_photo=None
        self.image_label.configure(image="",text="選取一張後點「預覽」")
        if not reader["ready"]:
            self.status.set("裝置尚無法讀取 sleep：" + reader.get("error",""))
            return
        self.refresh_photos()

    def refresh_photos(self):
        from mook_mtp_wpd import list_images
        rid=self.reader_id
        if not rid:
            self.status.set("請先選擇裝置")
            return
        def show(result):
            if self.reader_id!=rid:
                return
            self.tree.delete(*self.tree.get_children())
            for item in result:
                self.tree.insert("","end",iid=item["name"],
                                 text=item["name"],
                                 values=("%s bytes" % item["size"]
                                         if item["size"] else "未知",))
            self.status.set("讀取完成：%d 張；裝置 ID %s" % (len(result),rid[:12]))
        self._run("正在讀取 Mooink 裝置內照片…",
                  lambda:self._wpd(list_images,reader_id=rid),show)

    def _selection(self):
        if not self.reader_id:
            messagebox.showwarning("沒有選擇裝置","請先選擇 USB Mooink",parent=self.window)
            return None
        names=tuple(self.tree.selection())
        if not names:
            messagebox.showwarning("沒有選擇照片","請先選取一張或多張裝置內照片",parent=self.window)
            return None
        return self.reader_id,names

    def _backup_path(self,rid):
        return self.backup_root / rid

    def preview_selected(self):
        selected=self._selection()
        if selected is None:
            return
        rid,names=selected
        if len(names)!=1:
            messagebox.showinfo("照片預覽","請只選取一張照片",parent=self.window)
            return
        from mook_mtp_wpd import backup_and_delete_one
        def download():
            response=self._wpd(backup_and_delete_one,names[0],
                                self._backup_path(rid),
                                reader_id=rid,delete_after=False)
            filename=response.partition("backup: ")[2]
            if not filename:
                raise RuntimeError("WPD 未回報驗證成功的本機備份")
            return filename
        def show(filename):
            from PIL import Image, ImageTk
            with Image.open(filename) as img:
                photo=img.copy()
            photo.thumbnail((310,410),Image.Resampling.LANCZOS)
            self.preview_photo=ImageTk.PhotoImage(photo,master=self.window)
            self.image_label.configure(image=self.preview_photo,text="")
            self.status.set("已從 Mooink 回讀、驗證並預覽：" + names[0])
        self._run("正在從裝置下載預覽（同時保存備份）…",download,show)

    def upload_selected_photo(self):
        rid=self.reader_id
        if not rid:
            messagebox.showwarning("沒有選擇裝置","請先選擇 USB Mooink",parent=self.window)
            return
        size=self.registry.lookup(rid).get("size")
        if size not in ("10.3","13.3"):
            messagebox.showwarning("尚未設定尺寸",
                                   "請先在進階設定配對此 Mooink 的 10.3／13.3 吋尺寸。",
                                   parent=self.window)
            return
        source=filedialog.askopenfilename(
            parent=self.window,title="選擇要上傳到 Mooink 的照片",
            filetypes=[("照片","*.jpg *.jpeg *.png *.webp *.bmp *.tif *.tiff *.gif *.avif")])
        if not source:
            return
        from mook_mtp_wpd import upload_one
        def transfer():
            with tempfile.TemporaryDirectory(prefix="mooink_device_upload_") as tmp:
                png=prepare_named_photo(source,Path(tmp),size)
                return self._wpd(upload_one,png,reader_id=rid)
        def done(result):
            self.status.set("上傳並 SHA-256 回讀驗證成功：" + result)
            self.refresh_photos()
        self._run("正在轉換、旋轉照片並上傳指定 Mooink…",transfer,done)

    def _backup_or_delete(self,delete):
        selected=self._selection()
        if selected is None:
            return
        rid,names=selected
        if delete:
            if not messagebox.askyesno(
                "確認備份並刪除",
                "確定要從裝置 [%s] 刪除以下 %d 張照片？\n\n%s\n\n"
                "會先逐張下載、核對大小與內容 SHA-256，確認備份成功才刪除。"
                % (rid[:12],len(names),"\n".join(names[:12])),
                parent=self.window):
                return
        from mook_mtp_wpd import backup_and_delete_one
        def operation():
            processed=[]
            for name in names:
                result=self._wpd(backup_and_delete_one,name,
                     self._backup_path(rid),reader_id=rid,delete_after=delete)
                processed.append((name,result))
                if delete and not result.startswith("deleted+verified;"):
                    # WPD may acknowledge delete without verifying disappearance.
                    raise RuntimeError("已停止後續操作：%s：%s" % (name,result))
            return processed
        def done(records):
            self.status.set("%d 張已%s（備份位置：%s）" %
                (len(records),"備份並刪除" if delete else "備份",
                 str(self._backup_path(rid))))
            self.refresh_photos()
        self._run("正在%s %d 張裝置照片…" %
             ("備份並刪除" if delete else "備份",len(names)),operation,done)

    def backup_selected(self):
        self._backup_or_delete(False)

    def delete_selected(self):
        self._backup_or_delete(True)

    def open_backups(self):
        if not self.reader_id:
            return
        folder=self._backup_path(self.reader_id)
        folder.mkdir(parents=True,exist_ok=True)
        if os.name=="nt":
            os.startfile(str(folder))
        else:
            self.status.set(str(folder))
