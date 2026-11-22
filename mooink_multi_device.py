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
