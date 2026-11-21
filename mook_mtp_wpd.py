# -*- coding: utf-8 -*-
"""Optional quiet WPD MTP backup/delete transport for mooInk readers.

Windows Shell FolderItem.InvokeVerb('delete') triggers Explorer dialogs on
some systems. Direct WPD IPortableDeviceContent.Delete through the optional
MIT-licensed 'mtp' (Heribert17/mtp) library avoids Shell context-menu verbs.

Safety:
* Only a uniquely identified device containing 'mooink' or 'readmoo'.
* Exactly one sleep directory and exactly one matching image.
* Back up over WPD, verify a positive WPD source size, local size, and decode.
* Delete ONLY after the caller has confirmed the whole batch, never folders.
* Never fall back silently to Explorer's per-file prompts.
* Re-enumerate the directory to confirm deletion before moving to next file.

This module never imports Windows COM libraries at import time.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import time
from datetime import datetime


class QuietMtpUnavailable(RuntimeError):
    """The direct WPD transport is not ready or cannot identify one reader."""


def _report(progress, stage, **fields):
    if progress is None:
        return
    try:
        progress(stage, fields)
    except Exception:
        pass


def _device_name(device):
    return str(getattr(device, "devicename", "") or "").strip()


def _choose_device(devices):
    matches = [
        dev for dev in devices
        if "mooink" in _device_name(dev).casefold()
        or "readmoo" in _device_name(dev).casefold()
    ]
    if len(matches) != 1:
        raise QuietMtpUnavailable(
            "WPD 找不到唯一的 mooInk/Readmoo 裝置（符合 %d 台，全部 %d 台）。"
            "為安全起見不進行刪除，也不改用 Windows 逐張提示。"
            % (len(matches), len(devices))
        )
    return matches[0]


def _sleep_directory(device):
    """Only descend known storage folder shapes; never guess arbitrary paths."""
    candidates = []
    for root in device.get_content():
        queue = [(root, 0)]
        while queue:
            node, depth = queue.pop(0)
            if str(getattr(node, "name", "")).casefold() == "sleep":
                candidates.append(node)
                continue
            if depth >= 3:
                continue
            for child in node.get_children():
                kind = int(getattr(child, "content_type", -1))
                if kind not in (0, 1):  # WPD storage and directory types
                    continue
                name = str(getattr(child, "name", "")).casefold()
                if depth == 0 or any(x in name for x in (
                    "internal", "storage", "shared", "儲存", "memory"
                )):
                    queue.append((child, depth + 1))
    if len(candidates) != 1:
        raise QuietMtpUnavailable(
            "WPD 找到 %d 個 sleep 資料夾；必須唯一，才能安全操作。" % len(candidates)
        )
    return candidates[0]


def _find_image(sleep, filename):
    if Path(filename).name != filename or Path(filename).suffix.lower() not in (
        ".jpg", ".jpeg", ".png"
    ):
        raise ValueError("只能操作 sleep 根目錄的 JPG/PNG 檔案")
    matches = [
        obj for obj in sleep.get_children()
        if str(getattr(obj, "name", "")).casefold() == filename.casefold()
        and int(getattr(obj, "content_type", -1)) == 2
    ]
    if len(matches) != 1:
        raise QuietMtpUnavailable(
            "WPD 中找到 %d 個同名檔案 %s，已停止以免誤刪。" % (len(matches), filename)
        )
    return matches[0]


def _unique_backup_path(base, filename):
    base.mkdir(parents=True, exist_ok=True)
    target = base / filename
    if not target.exists():
        return target
    prefix = datetime.now().strftime("%Y%m%d_%H%M%S")
    for index in range(1, 10000):
        folder = base / ("mooink_%s_%03d" % (prefix, index))
        try:
            folder.mkdir()
            return folder / filename
        except FileExistsError:
            continue
    raise RuntimeError("找不到可用的本機備份子目錄")


def _verify_local_image(path):
    from PIL import Image
    with Image.open(path) as src:
        src.verify()
    with Image.open(path) as src:
        src.load()


def backup_and_delete_one(filename, backup_dir, *,
                          progress=None, delete_after=True,
                          device_source=None):
    """Use WPD stream (not Shell.CopyHere) and a prompt-free file delete.

    device_source allows FakeWPD devices for regression testing. In production
    the imported library returns real COM device objects. Device close is
    performed in finally on the same COM-initialized worker thread.
    """
    _report(progress, "locate", message="WPD 搜尋 mooInk 裝置")
    if device_source is None:
        if os.name != "nt":
            raise QuietMtpUnavailable("WPD USB 模式僅支援 Windows")
        try:
            from mtp.win_access import get_portable_devices
        except (ImportError, RuntimeError) as exc:
            raise QuietMtpUnavailable(
                "缺少 WPD 直接存取套件，無法做到免逐張詢問。"
                "請在 Python 3.10+ 執行："
                "py -m pip install git+https://github.com/Heribert17/mtp.git"
            ) from exc
        devices = list(get_portable_devices())
    else:
        devices = list(device_source())

    try:
        device = _choose_device(devices)
        sleep = _sleep_directory(device)
        item = _find_image(sleep, filename)
        expected = int(getattr(item, "size", -1) or -1)
        if expected <= 0:
            raise QuietMtpUnavailable(
                "WPD 沒有提供 %s 的有效來源大小；不備份後刪除。" % filename
            )
        base = Path(backup_dir).resolve()
        path = _unique_backup_path(base, filename)
        _report(progress, "backup_target", message="WPD 備份位置：" + str(path))
        _report(progress, "copying", copied=0, total=expected,
                message="WPD 直接下載圖片")
        try:
            item.download_file(str(path))
            actual = path.stat().st_size
            _report(progress, "verifying", copied=actual, total=expected,
                    message="驗證 WPD 下載大小及圖片解碼")
            if actual != expected:
                raise RuntimeError(
                    "WPD 備份大小不符（來源 %d、本機 %d）；原檔保留" %
                    (expected, actual)
                )
            _verify_local_image(path)
            with path.open("rb") as stream:
                digest = hashlib.file_digest(stream, "sha256").hexdigest() if hasattr(
                    hashlib, "file_digest"
                ) else hashlib.sha256(stream.read()).hexdigest()
        except Exception:
            # Preserve partial files for troubleshooting. Do not delete source.
            raise
        _report(progress, "verified", copied=actual, total=expected,
                message="備份大小與圖片解碼已確認；SHA256=" + digest[:16])
        if not delete_after:
            return "backup-only-wpd-verified; backup: " + str(path)

        # Re-enumerate the same directory and re-check name/size before delete.
        # This operation never opens an Explorer context menu.
        latest = _find_image(sleep, filename)
        if int(getattr(latest, "size", -1) or -1) != expected:
            raise RuntimeError("WPD 原始檔大小在備份後改變，拒絕刪除")
        _report(progress, "deleting", message="WPD 刪除已備份圖片（不彈檔案總管確認）")
        latest.remove()
        # Re-query from device; confirmation is a disappearance, not a
        # method return that may only mean command submission.
        gone = all(
            str(getattr(obj, "name", "")).casefold() != filename.casefold()
            for obj in sleep.get_children()
        )
        if gone:
            _report(progress, "deleted", message="已確認 WPD 原檔消失")
            return "deleted+verified; backup: " + str(path)
        _report(progress, "pending", message="WPD 已送出刪除但未確認消失")
        return "Windows WPD delete requested; verify manually; backup: " + str(path)
    finally:
        for dev in devices:
            close = getattr(dev, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:
                    pass
