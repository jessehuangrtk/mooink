"""Interactive Wi-Fi pairing for the Windows watcher, shared with demo DPAPI cache.

The API authenticates Sony-compatible document operations. Pairing does not
establish that the firmware exposes the sleep image folder over Wi-Fi.
"""
from __future__ import annotations
import json
import os
from pathlib import Path
from types import FunctionType
import uuid

from mooink_full_cmd_demo import (
    require_private_ipv4, http_get_metadata, fingerprint, make_client,
    load_secret, secret_path, dpapi_transform,
)

class WifiPairingError(RuntimeError):
    pass

class PairingCancelled(WifiPairingError):
    pass

def identity(ip, timeout=8):
    ip = require_private_ipv4(ip)
    status, info = http_get_metadata(ip, "/register/information", timeout)
    if status != 200 or not isinstance(info, dict):
        raise WifiPairingError("無法讀取裝置識別資訊（HTTP %s）；請確認 IP、Wi-Fi 及喚醒狀態。" % (status or "連線失敗"))
    device = fingerprint(info)
    if not device:
        raise WifiPairingError("裝置未提供序號，無法安全區分兩台 mooInk。")
    return device

def new_client(ip, timeout=8):
    try:
        client = make_client(ip, timeout)
    except ImportError:
        raise WifiPairingError("缺少 dpt-rp1-py；請執行 python -m pip install -r requirements.txt") from None
    # Do not send credentials through redirects, or expose URLs containing
    # client IDs in exception text.
    original = client.session.send
    def send(request, **kwargs):
        kwargs["allow_redirects"] = False
        kwargs.setdefault("timeout", timeout)
        response = original(request, **kwargs)
        if not 200 <= response.status_code < 300:
            raise WifiPairingError("mooInk API HTTP %d；請檢查配對或裝置狀態。" % response.status_code)
        return response
    client.session.send = send
    return client

def verify(client, credentials):
    try:
        response = client.authenticate(*credentials)
        if not response.ok or not client._get_endpoint("/ping").ok:
            raise WifiPairingError("配對憑證未通過驗證。")
    except WifiPairingError:
        raise
    except Exception as exc:
        # Never expose nonce, credential, PIN, response payload or private key.
        raise WifiPairingError("認證失敗（%s）；請在本台 mooInk 重新配對。" % type(exc).__name__) from None
    return client

def save_credentials(device, credentials):
    """Use the demo's DPAPI format, atomically; never overwrite other readers."""
    client_id, key = credentials
    if isinstance(key, bytes):
        key = key.decode("utf-8")
    payload = json.dumps({"client_id": client_id, "key": key}).encode("utf-8")
    target = secret_path(device)
    temporary = target.with_name(target.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        temporary.write_bytes(dpapi_transform(payload))
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)

def register_with_pin(client, request_pin):
    """Use upstream registration algorithm with local I/O callbacks.

    Isolate its function globals so no process-wide builtins/input/print patch
    can steal input or silence other application threads. No console needed.
    """
    method = client.register
    func = method.__func__
    def read_pin(_prompt=""):
        pin = request_pin()
        if pin is None:
            raise PairingCancelled("已取消 PIN 輸入，未保存新憑證。")
        if not isinstance(pin, str) or not pin.isascii() or not pin.isdigit() or not 1 <= len(pin) <= 16:
            raise WifiPairingError("PIN 必須是 mooInk 螢幕顯示的數字。")
        return pin
    namespace = dict(func.__globals__)
    namespace.update(input=read_pin, print=lambda *a, **kw: None)
    register = FunctionType(func.__code__, namespace, func.__name__,
                            func.__defaults__, func.__closure__)
    register.__kwdefaults__ = func.__kwdefaults__
    try:
        result = register(client)
        if not result or len(result) != 3 or not result[1] or not result[2]:
            raise WifiPairingError("配對未完成，請檢查 PIN 或閱讀器狀態。")
        _, key, client_id = result
        return client_id, key.encode("utf-8") if isinstance(key, str) else key
    except WifiPairingError:
        raise
    except Exception as exc:
        raise WifiPairingError("配對失敗（%s），請保持 mooInk 喚醒並重試。" % type(exc).__name__) from None

def pair(ip, request_pin, timeout=8):
    """Explicit user action only; verify reader identity and credentials."""
    device = identity(ip, timeout)
    client = new_client(ip, timeout)
    try:
        credentials = register_with_pin(client, request_pin)
        if identity(ip, timeout) != device:
            raise WifiPairingError("配對期間 IP 對應裝置已變更；未保存新憑證。")
        verify(client, credentials)
        save_credentials(device, credentials)
        return device
    finally:
        client.session.close()

def import_credentials(ip, directory, timeout=8):
    """Import an explicitly selected CLI credential pair after verification."""
    device = identity(ip, timeout)
    directory = Path(directory)
    try:
        credentials = ((directory / "deviceid.dat").read_text(encoding="utf-8").strip(),
                       (directory / "privatekey.dat").read_bytes())
    except (OSError, UnicodeError):
        raise WifiPairingError("所選資料夾必須包含 deviceid.dat 與 privatekey.dat。") from None
    if not all(credentials):
        raise WifiPairingError("憑證檔案是空的。")
    client = new_client(ip, timeout)
    try:
        verify(client, credentials)
        if identity(ip, timeout) != device:
            raise WifiPairingError("驗證期間裝置已變更，未保存憑證。")
        save_credentials(device, credentials)
        return device
    finally:
        client.session.close()

def connect_saved(ip, timeout=8):
    device = identity(ip, timeout)
    try:
        credentials = load_secret(device)
    except Exception:
        raise WifiPairingError("本機 DPAPI 憑證無法解密；請以原 Windows 使用者登入或重新配對。") from None
    if not credentials:
        raise WifiPairingError("本台 mooInk 尚無配對憑證；請按 Wi-Fi 配對，或匯入之前的憑證。")
    client = new_client(ip, timeout)
    try:
        verify(client, credentials)
        if identity(ip, timeout) != device:
            raise WifiPairingError("目前 IP 對應裝置已變更。")
    except BaseException:
        client.session.close()
        raise
    return client, device

def inspect_folders(ip, timeout=8):
    """Only authenticated GET requests; no create/upload/delete operations."""
    client, device = connect_saved(ip, timeout)
    try:
        response = client._get_endpoint("/documents2?entry_type=all")
        data = response.json()
        entries = data.get("entry_list")
        if not isinstance(entries, list):
            raise WifiPairingError("裝置未回傳有效的資料夾清單。")
        folders = sorted({str(row["entry_path"]) for row in entries
                          if isinstance(row, dict) and row.get("entry_type") == "folder"
                          and isinstance(row.get("entry_path"), str)})
        complete = data.get("count") == len(entries)
        sleep = [path for path in folders if path.rstrip("/").split("/")[-1].casefold() == "sleep"]
        return {"device": device, "folders": folders, "complete": complete, "sleep": sleep}
    finally:
        client.session.close()

def test_connection(ip, timeout=8):
    client, device = connect_saved(ip, timeout)
    client.session.close()
    return device
