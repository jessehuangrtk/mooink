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
    # Name can be "Digital Paper" instead of mooInk. A specific first-gen
    # reader was observed with USB VID_1F85/PID_4E16; device._p_id is the
    # upstream mtp.win_access PortableDevice WPD/PnP device identifier.
    # Requiring exactly one match still prevents cross-device writes.
    def known_reader(dev):
        name = _device_name(dev).casefold()
        pnp = str(getattr(dev, "_p_id", "") or "").casefold()
        return (
            "mooink" in name or "readmoo" in name
            or ("vid_1f85" in pnp and "pid_4e16" in pnp)
        )
    matches = [dev for dev in devices if known_reader(dev)]
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



def upload_one(source, *, progress=None, device_source=None):
    """Upload JPG/PNG to the unique mooInk sleep folder and read it back.

    Returns only a VERIFIED result after checking local and remote SHA256.
    The upstream WPD upload_file() method is synchronous but does not expose
    progress; callers show an indeterminate activity bar while it runs.

    A failed verification leaves the remote file untouched for diagnosis;
    never delete a partially created file automatically.
    """
    path = Path(source).expanduser().resolve()
    if not path.is_file() or path.suffix.lower() not in (".jpg", ".jpeg", ".png"):
        raise ValueError("WPD 僅允許上傳存在的 JPG/JPEG/PNG 圖片")
    if path.stat().st_size <= 0:
        raise ValueError("不能上傳空白檔案")
    expected_bytes = path.stat().st_size
    _report(progress, "locate", message="WPD 搜尋 mooInk sleep 資料夾")

    if device_source is None:
        if os.name != "nt":
            raise QuietMtpUnavailable("USB WPD 上傳僅支援 Windows")
        try:
            from mtp.win_access import get_portable_devices
        except (ImportError, RuntimeError) as exc:
            raise QuietMtpUnavailable(
                "WPD 上傳需要安裝 mtp 套件；請在 Windows Python 3.12+ 執行 "
                "py -m pip install git+https://github.com/Heribert17/mtp.git"
            ) from exc
        devices = list(get_portable_devices())
    else:
        devices = list(device_source())

    try:
        device = _choose_device(devices)
        sleep = _sleep_directory(device)
        candidates = [
            obj for obj in sleep.get_children()
            if str(getattr(obj, "name", "")).casefold() == path.name.casefold()
        ]
        if candidates:
            raise FileExistsError(
                "mooInk sleep 已有同名圖片 %s；為避免覆寫，未進行上傳" % path.name
            )
        _report(progress, "uploading", copied=0, total=expected_bytes,
                message="正在透過 WPD 傳輸 %s（Windows 無位元組進度回報）" % path.name)
        try:
            sleep.upload_file(path.name, str(path))
        except Exception as exc:
            raise RuntimeError(
                "WPD 上傳發生錯誤；裝置可能留下未完成檔案，請重新讀取 sleep 清單再處理。"
                "原始錯誤：%s: %s" % (type(exc).__name__, exc)
            ) from exc

        _report(progress, "checking", message="上傳指令已返回，正在重新讀取 mooInk 目錄")
        uploaded = None
        for _ in range(8):
            matches = [
                obj for obj in sleep.get_children()
                if str(getattr(obj, "name", "")).casefold() == path.name.casefold()
                and int(getattr(obj, "content_type", -1)) == 2
            ]
            if len(matches) > 1:
                raise RuntimeError("WPD 目錄出現多個同名物件，無法確認上傳")
            if matches:
                uploaded = matches[0]
                break
            time.sleep(0.5)
        if uploaded is None:
            raise RuntimeError(
                "WPD 已回傳，但 sleep 內找不到 %s；不宣稱上傳成功" % path.name
            )

        remote_size = int(getattr(uploaded, "size", -1) or -1)
        if remote_size > 0 and remote_size != expected_bytes:
            raise RuntimeError(
                "WPD 上傳大小不符：來源 %d bytes，裝置 %d bytes；不宣稱成功" %
                (expected_bytes, remote_size)
            )

        _report(progress, "verifying", message="正在從 mooInk 讀回檔案並比對 SHA-256",
                copied=expected_bytes, total=expected_bytes)
        import tempfile
        with tempfile.TemporaryDirectory(prefix="mooink_upload_check_") as tmp:
            back = Path(tmp) / path.name
            uploaded.download_file(str(back))
            if back.stat().st_size != expected_bytes:
                raise RuntimeError(
                    "WPD 回讀大小不符：來源 %d bytes，回讀 %d bytes；不宣稱成功" %
                    (expected_bytes, back.stat().st_size)
                )

            def digest(src):
                h = hashlib.sha256()
                with src.open("rb") as stream:
                    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                        h.update(chunk)
                return h.hexdigest()

            if digest(back) != digest(path):
                raise RuntimeError(
                    "WPD 回讀 SHA-256 不一致，無法確認裝置檔案完整；請檢查 sleep"
                )

        _report(progress, "verified", copied=expected_bytes, total=expected_bytes,
                message="裝置回讀 SHA-256 一致，已確認上傳")
        return "uploaded+sha256-verified; sleep: " + path.name
    finally:
        for device in devices:
            close = getattr(device, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:
                    pass



def list_images(*, device_source=None):
    """List JPG/PNG files directly from WPD after a verified upload."""
    if device_source is None:
        if os.name != "nt":
            raise QuietMtpUnavailable("WPD USB 清單僅支援 Windows")
        try:
            from mtp.win_access import get_portable_devices
        except (ImportError, RuntimeError) as exc:
            raise QuietMtpUnavailable(
                "WPD 清單需要安裝 mtp 套件，請安裝並重新啟動 GUI"
            ) from exc
        devices = list(get_portable_devices())
    else:
        devices = list(device_source())
    try:
        sleep = _sleep_directory(_choose_device(devices))
        output = []
        for item in sleep.get_children():
            name = str(getattr(item, "name", ""))
            if Path(name).name != name:
                continue
            if Path(name).suffix.lower() not in (".jpg", ".jpeg", ".png"):
                continue
            if int(getattr(item, "content_type", -1)) != 2:
                continue
            size = int(getattr(item, "size", -1) or -1)
            output.append({"name": name, "size": size if size > 0 else None})
        return sorted(output, key=lambda row: row["name"].casefold())
    finally:
        for device in devices:
            close = getattr(device, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:
                    pass
