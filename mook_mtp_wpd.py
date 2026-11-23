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
import sys
from pathlib import Path
import time
from datetime import datetime


class QuietMtpUnavailable(RuntimeError):
    """The direct WPD transport is not ready or cannot identify one reader."""


def _get_device_list():
    """Use the vendored WPD implementation, not the unrelated PyPI 'mtp'.

    The PyPI mtp 1.1.0 package depends on urwid and has no win_access.
    The upstream Heribert17/mtp GitHub setup.py also currently points at
    src/ despite placing win_access.py under mtp/, so Git installation
    cannot be relied upon to provide mtp.win_access.
    """
    if os.name != "nt":
        raise QuietMtpUnavailable("USB WPD 模式僅支援 Windows")
    if sys.version_info < (3, 10):
        raise QuietMtpUnavailable(
            "WPD 後端需要 Python 3.10 以上。GUI 正使用：" + sys.executable
        )
    try:
        from third_party.heribert17_win_access import get_portable_devices
    except Exception as exc:
        raise QuietMtpUnavailable(
            "內建 WPD 模組初始化失敗；不需要再安裝 mtp 套件。"
            "請在啟動 GUI 的同一個 Python 執行："
            "python -m pip install -r requirements.txt。"
            "目前 Python=%s；真正的錯誤=%s: %s" %
            (sys.executable, type(exc).__name__, exc)
        ) from exc
    try:
        return list(get_portable_devices())
    except Exception as exc:
        raise QuietMtpUnavailable(
            "WPD 裝置列舉失敗；目前 Python=%s，實際錯誤=%s: %s。" %
            (sys.executable, type(exc).__name__, exc)
        ) from exc


def _report(progress, stage, **fields):
    if progress is None:
        return
    try:
        progress(stage, fields)
    except Exception:
        pass


def _device_name(device):
    return str(getattr(device, "devicename", "") or "").strip()


def _known_reader(dev):
    # Sony-based first-gen devices sometimes report only "Digital Paper".
    name = _device_name(dev).casefold()
    pnp = str(getattr(dev, "_p_id", "") or "").casefold()
    return ("mooink" in name or "readmoo" in name
            or ("vid_1f85" in pnp and "pid_4e16" in pnp))


def _reader_id(dev):
    """Opaque, reproducible per-device ID from Windows WPD/PnP identity.

    Never fall back to a model name: two identical readers must not share an ID.
    Windows may assign a new PnP ID after a USB port change on devices without
    an embedded serial number, in which case a new pairing is required.
    """
    pnp = str(getattr(dev, "_p_id", "") or "").strip().casefold()
    if not pnp:
        raise QuietMtpUnavailable("裝置缺少 Windows WPD 唯一 ID，拒絕自動配對")
    return hashlib.sha256(pnp.encode("utf-8")).hexdigest()


def _choose_device(devices, reader_id=None):
    """Select exactly one known reader, optionally locked to its persistent ID."""
    matches = [dev for dev in devices if _known_reader(dev)]
    if reader_id is not None:
        matches = [dev for dev in matches if _reader_id(dev) == reader_id]
    if len(matches) != 1:
        raise QuietMtpUnavailable(
            "WPD 找不到唯一的指定 mooInk 裝置（符合 %d 台，全部 %d 台）。"
            "不進行刪除或寫入；請確認裝置 ID 與連線。"
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
        devices = _get_device_list()
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
        devices = _get_device_list()
    else:
        devices = list(device_source())

    try:
        device = _choose_device(devices)
        sleep = _sleep_directory(device)
        return _upload_to_sleep(sleep, path, progress)
    finally:
        for device in devices:
            close = getattr(device, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:
                    pass



def _upload_to_sleep(sleep, path, progress=None):
    expected_bytes = path.stat().st_size
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


WATCH_NAMES = frozenset(("mooink_display_10.png", "mooink_display_13.png"))


def replace_watch_image(source, backup_dir, *, progress=None, device_source=None, reader_id=None):
    """Replace only a reserved watcher image on one reader, in one WPD session.

    Existing image is downloaded and validated before deletion. An identical
    image is left untouched. No other image is removed. If a new upload fails,
    the verified backup is retained; never blindly delete a partial upload.
    """
    import tempfile
    path = Path(source).resolve()
    if path.name not in WATCH_NAMES or not path.is_file():
        raise ValueError("只能更新 Watch Tool 專用 PNG")
    _verify_local_image(path)
    expected_hash = hashlib.sha256(path.read_bytes()).digest()
    devices = _get_device_list() if device_source is None else list(device_source())
    backup = None
    try:
        sleep = _sleep_directory(_choose_device(devices, reader_id=reader_id))
        def matches():
            return [obj for obj in sleep.get_children()
                    if str(getattr(obj, "name", "")).casefold() == path.name.casefold()]
        found = matches()
        if len(found) > 1 or (found and int(getattr(found[0], "content_type", -1)) != 2):
            raise QuietMtpUnavailable("專用檔名有重複或不是檔案，停止更新")
        if found:
            item = found[0]
            size = int(getattr(item, "size", -1) or -1)
            with tempfile.TemporaryDirectory(prefix="mooink_old_") as tmp:
                old = Path(tmp) / path.name
                item.download_file(str(old))
                if not old.is_file() or old.stat().st_size <= 0 or (size > 0 and old.stat().st_size != size):
                    raise RuntimeError("舊 PNG 回讀大小不符，原檔保留")
                _verify_local_image(old)
                old_hash = hashlib.sha256(old.read_bytes()).digest()
                if old_hash == expected_hash:
                    _report(progress, "verified", message="已存在相同 PNG，回讀 SHA-256 一致")
                    return {"changed": False, "name": path.name, "backup": None}
                if size <= 0:
                    raise RuntimeError("舊 PNG 缺少有效 WPD 大小，拒絕刪除")
                backup = _unique_backup_path(Path(backup_dir).resolve(), path.name)
                with backup.open("xb") as stream:
                    stream.write(old.read_bytes())
                    stream.flush()
                    os.fsync(stream.fileno())
                if hashlib.sha256(backup.read_bytes()).digest() != old_hash:
                    raise RuntimeError("本機備份驗證失敗，原檔保留")
                # Confirm the latest object is still the backed-up image.
                latest = matches()
                if len(latest) != 1 or int(getattr(latest[0], "content_type", -1)) != 2 or int(getattr(latest[0], "size", -1) or -1) != size:
                    raise RuntimeError("舊 PNG 已變更，拒絕刪除")
                reread = Path(tmp) / "recheck.png"
                latest[0].download_file(str(reread))
                if hashlib.sha256(reread.read_bytes()).digest() != old_hash:
                    raise RuntimeError("舊 PNG 內容已變更，拒絕刪除")
                if hashlib.sha256(path.read_bytes()).digest() != expected_hash:
                    raise RuntimeError("本機 PNG 已變更，拒絕刪除")
                _report(progress, "backup", message="舊 PNG 已備份：" + str(backup))
                latest[0].remove()
                if matches():
                    raise RuntimeError("未確認舊 PNG 刪除，停止上傳")
        _upload_to_sleep(sleep, path, progress)
        if hashlib.sha256(path.read_bytes()).digest() != expected_hash:
            raise RuntimeError("本機 PNG 在傳送期間變更")
        return {"changed": True, "name": path.name, "backup": str(backup) if backup else None}
    except Exception:
        if backup:
            _report(progress, "backup", message="更新未完成，舊 PNG 備份保留：" + str(backup))
        raise
    finally:
        for device in devices:
            close = getattr(device, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:
                    pass


def list_images(*, device_source=None, reader_id=None):
    """List JPG/PNG files directly from WPD after a verified upload."""
    if device_source is None:
        devices = _get_device_list()
    else:
        devices = list(device_source())
    try:
        sleep = _sleep_directory(_choose_device(devices, reader_id=reader_id))
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


def probe_reader(*, device_source=None, reader_id=None):
    """Return the identity of a unique, MTP-ready reader, not a cached USB flag."""
    devices = _get_device_list() if device_source is None else list(device_source())
    try:
        device = _choose_device(devices, reader_id=reader_id)
        _sleep_directory(device)
        return str(getattr(device, "_p_id", "") or _device_name(device))
    finally:
        for device in devices:
            close = getattr(device, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:
                    pass


def list_readers(*, device_source=None):
    """Discover all supported readers without writing anything.

    A reader is eligible for MTP transfer only when its sleep folder is ready.
    The fingerprint is used for per-device size mapping; no device is selected
    on the basis of its name or enumeration order.
    """
    devices = _get_device_list() if device_source is None else list(device_source())
    try:
        output = []
        for dev in devices:
            if not _known_reader(dev):
                continue
            try:
                key = _reader_id(dev)
            except QuietMtpUnavailable:
                continue
            error = ""
            try:
                _sleep_directory(dev)
                ready = True
            except Exception as exc:
                # WPD COM errors are not guaranteed to subclass OSError.
                # A not-yet-mounted reader must not hide other devices.
                ready = False
                error = str(exc)[:250]
            output.append({"id": key, "name": _device_name(dev), "ready": ready,
                           "error": error,
                           "pnp": str(getattr(dev, "_p_id", "") or "")})
        if len({reader["id"] for reader in output}) != len(output):
            raise QuietMtpUnavailable("WPD 裝置 ID 重複，拒絕自動更新")
        return sorted(output, key=lambda row: (row["name"].casefold(), row["id"]))
    finally:
        for device in devices:
            close = getattr(device, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:
                    pass
