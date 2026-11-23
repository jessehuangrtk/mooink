"""Per-reader profiles and automatic Word -> MTP updates.

The persistent fingerprint is SHA-256 of the Windows WPD/PnP device ID.
Unknown devices NEVER inherit the global size or receive an upload.
"""
from __future__ import annotations
from dataclasses import replace
import json
import os
from pathlib import Path
import re
import threading
import uuid


def _atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2),
                             encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


class DeviceRegistry:
    """Per-device size and last verified delivery, kept across restarts."""
    def __init__(self, path):
        self.path = Path(path)
        self._lock = threading.RLock()
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            self.devices = raw.get("devices", {}) if isinstance(raw, dict) else {}
        except (OSError, ValueError, TypeError):
            self.devices = {}
        if not isinstance(self.devices, dict):
            self.devices = {}

    @staticmethod
    def valid_id(device_id):
        if not isinstance(device_id, str) or not re.fullmatch("[0-9a-f]{64}", device_id):
            raise ValueError("無效的 WPD 裝置 ID")
        return device_id

    def lookup(self, device_id):
        with self._lock:
            row = self.devices.get(device_id, {})
            return dict(row) if isinstance(row, dict) else {}

    def entries(self):
        with self._lock:
            return {key: dict(value) for key, value in self.devices.items()
                    if isinstance(value, dict)}

    def assign(self, device_id, size, name):
        self.valid_id(device_id)
        if size not in ("10.3", "13.3"):
            raise ValueError("尺寸僅支援 10.3 或 13.3")
        with self._lock:
            row = self.lookup(device_id)
            if row.get("size") != size:
                # Do not display a success for the wrong size.
                row = {}
            row.update({"size": size, "name": str(name)[:150]})
            new_data = dict(self.devices)
            new_data[device_id] = row
            _atomic_json(self.path, {"version": 1, "devices": new_data})
            self.devices = new_data
            if row.get("last_updated") is None:
                # A previous-size preview must not look like a verified image
                # for this newly assigned size.
                try:
                    self.preview_file(device_id).unlink(missing_ok=True)
                except OSError:
                    pass

    def success(self, device_id, result, size, name):
        self.valid_id(device_id)
        with self._lock:
            row = self.lookup(device_id)
            if row.get("size") != size:
                return
            image = self.path.parent / "verified_previews" / (device_id + ".png")
            image.parent.mkdir(parents=True, exist_ok=True)
            temporary = image.with_name(image.name + "." + uuid.uuid4().hex + ".tmp")
            try:
                temporary.write_bytes(result["png_bytes"])
                os.replace(temporary, image)
            finally:
                temporary.unlink(missing_ok=True)
            row.update({
                "name": name, "last_updated": result["updated_at"],
                "word_mtime": result["word_mtime"],
                "word_name": result["word_name"],
                "png_name": result["png_name"],
            })
            new_data = dict(self.devices)
            new_data[device_id] = row
            _atomic_json(self.path, {"version": 1, "devices": new_data})
            self.devices = new_data

    def preview_file(self, device_id):
        self.valid_id(device_id)
        return self.path.parent / "verified_previews" / (device_id + ".png")


class MultiDeviceController:
    """Dispatch verified uploads to exactly the ID/size saved by the user."""
    def __init__(self, registry, processor_factory, discover, event_match):
        self.registry = registry
        self.processor_factory = processor_factory
        self.discover = discover
        self.event_match = event_match
        self.processors = {}
        self.online = set()

    def check(self, settings, *, scheduled=True, force=False, device_events=(),
              log=lambda message: None, inventory=lambda rows: None,
              status=lambda device_id, message: None,
              success=lambda device_id, data: None):
        readers = self.discover()
        inventory(readers)
        now_online = {row["id"] for row in readers if row.get("ready")}
        for missing in self.online - now_online:
            status(missing, "已拔除，等待重新連線")
            self.processors.pop(missing, None)
        results = []
        for reader in readers:
            device_id = reader["id"]
            profile = self.registry.lookup(device_id)
            size = profile.get("size")
            if not reader.get("ready"):
                status(device_id, "WPD 尚未就緒")
                continue
            if size not in ("10.3", "13.3"):
                status(device_id, "尚未配對尺寸，禁止傳送")
                continue
            if device_id not in self.processors:
                self.processors[device_id] = self.processor_factory(device_id)
            processor = self.processors[device_id]
            events = [event for event in device_events
                      if self.event_match(event, reader.get("pnp", ""))]
            # Never issue unaddressed Bluetooth wake when multiple readers
            # can exist. Each USB MTP transfer is still independently verified.
            per_device_settings = replace(settings, size=size, pico_port="")
            try:
                msg = processor.check(
                    per_device_settings, force=force, scheduled=scheduled,
                    device_events=events, log=lambda m, rid=device_id: log(
                        reader["name"] + " [" + rid[:12] + "]: " + m),
                    on_connection=lambda m: None,
                    on_success=lambda result, rid=device_id, r=reader, sz=size:
                        self._verified(rid, result, sz, r["name"], success, log))
            except Exception as exc:
                msg = "更新失敗，下次重試：" + str(exc)
            if msg is not None:
                status(device_id, msg)
                results.append(reader["name"] + " [" + device_id[:12] + "]: " + msg)
        self.online = now_online
        return results

    def _verified(self, device_id, result, size, name, success, log):
        try:
            self.registry.success(device_id, result, size, name)
        except Exception as exc:
            log("成功上傳，但儲存本機驗證紀錄失敗：" + str(exc))
        success(device_id, result)


class ManualReaderUnavailable(RuntimeError):
    """No safe manual upload target; caller may offer first-time pairing."""

    def __init__(self, reason, readers, detail):
        super().__init__(detail)
        self.reason = reason
        self.readers = list(readers)


def manual_reader_diagnostic(readers, registry):
    """Explain which safety gate blocked upload; never guess panel size."""
    readers = list(readers)
    if not readers:
        return ManualReaderUnavailable(
            "not_found", readers,
            "WPD 沒有找到相容的 Mooink。請確認 Windows 檔案總管能看到閱讀器、"
            "USB 使用資料線，並檢查「管理裝置／尺寸配對」清單。")
    ready = [reader for reader in readers if reader.get("ready")]
    if not ready:
        details = "; ".join(
            (reader.get("name") or "Mooink") + "：" +
            (reader.get("error") or "sleep 資料夾尚無法存取")
            for reader in readers)
        return ManualReaderUnavailable(
            "not_ready", readers,
            "找到 %d 台 Mooink，但 WPD/sleep 尚未就緒。%s。請重新插拔、解鎖裝置並確認 sleep 目錄可讀。"
            % (len(readers), details))
    unpaired = [reader for reader in ready
                if registry.lookup(reader["id"]).get("size") not in ("10.3", "13.3")]
    if unpaired:
        return ManualReaderUnavailable(
            "unpaired", readers,
            "找到 %d 台已連線 Mooink，其中 %d 台尚未建立裝置 ID／尺寸配對。"
            "請先選擇 10.3 或 13.3 吋，儲存後即可傳送。"
            % (len(ready), len(unpaired)))
    return None


def update_selected_docx(source, *, registry, discover, render, upload,
                         page_number=1, on_success=lambda reader_id, data: None,
                         on_status=lambda reader_id, message: None,
                         log=lambda message: None):
    """One-shot manual DOCX upload. Never changes automatic input or cache.

    Render only into a temporary folder using reserved MTP PNG basenames.
    Each paired reader receives a size-matched rendering and independent
    verified success metadata. No automatic watcher settings are modified.
    """
    import hashlib
    import shutil
    import tempfile
    import time

    path = Path(source).expanduser().resolve()
    if path.suffix.casefold() != ".docx" or not path.is_file():
        raise ValueError("請先選擇存在的 DOCX 檔案")
    if not isinstance(page_number, int) or page_number < 1:
        raise ValueError("手動傳送頁碼必須從 1 開始")
    original = path.stat()
    checksum = hashlib.sha256(path.read_bytes()).digest()

    def unchanged():
        return (path.is_file()
                and path.stat().st_mtime_ns == original.st_mtime_ns
                and hashlib.sha256(path.read_bytes()).digest() == checksum)

    readers = discover()
    paired = []
    for reader in readers:
        if not reader.get("ready"):
            continue
        size = registry.lookup(reader["id"]).get("size")
        if size in ("10.3", "13.3"):
            paired.append((reader, size))
        else:
            on_status(reader["id"], "尚未配對尺寸，手動傳送略過")
    if not paired:
        raise manual_reader_diagnostic(readers, registry)

    results = {"successful": [], "failed": []}
    with tempfile.TemporaryDirectory(prefix="mooink_manual_") as temp:
        work = Path(temp)
        snapshot = work / path.name
        shutil.copyfile(path, snapshot)
        if not unchanged() or hashlib.sha256(snapshot.read_bytes()).digest() != checksum:
            raise RuntimeError("Word 正在儲存，請儲存完成後重試")

        rendered = {}
        for size in sorted({size for _, size in paired}):
            suffix = "10" if size == "10.3" else "13"
            png = work / ("mooink_display_" + suffix + ".png")
            render(snapshot, png, size)
            if not png.is_file() or not png.stat().st_size:
                raise RuntimeError("Word 轉圖失敗：" + str(png))
            rendered[size] = png
            log("手動轉圖完成：" + png.name)
        if not unchanged():
            raise RuntimeError("Word 在轉圖期間被修改，未傳送；請重試")

        for reader, size in paired:
            rid = reader["id"]
            name = reader.get("name") or "Mooink"
            try:
                if not unchanged():
                    raise RuntimeError("Word 在傳送期間被修改，請重試")
                png = rendered[size]
                upload(rid, png, size, log)
                if not unchanged():
                    raise RuntimeError("Word 在傳送期間被修改，請重試")
                delivery = {
                    "updated_at": time.time(), "word_mtime": original.st_mtime,
                    "word_name": path.name + ("（第%d頁）" % page_number
                        if page_number > 1 else ""), "png_name": png.name,
                    "page_number": page_number,
                    "png_bytes": png.read_bytes()
                }
                registry.success(rid, delivery, size, name)
                on_success(rid, delivery)
                on_status(rid, "手動更新成功（SHA-256 回讀驗證）")
                results["successful"].append(rid)
            except Exception as exc:
                message = "手動更新失敗：" + str(exc)
                on_status(rid, message)
                log(name + " [" + rid[:12] + "]: " + message)
                results["failed"].append(rid)
    return results
