#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""mooInk Pro Full Command Demo v1.1.1 (Windows / Python 3.9+).

ONE-FILE tool. Works with multiple first-generation mooInk Pro readers.
No device serial, registration client ID, or IP address is hardcoded.

Stages: dynamic USB discovery, optional Windows WPD/MTP metadata inspection,
LAN mDNS discovery/manual IP, awake/sleep network tests, authenticated Sony
Digital Paper API, document display tests with human observation, and guided
USB-MTP sleep-image test. Direct Wi-Fi/API wake and immediate standby remain
unproven. The user has manually confirmed Bluetooth keyboard wake, but not
Bluetooth mouse wake, and a selectable 15-second automatic standby timeout.
These Bluetooth observations are informational only, not automated tests.

Install: python -m pip install dpt-rp1-py comtypes
Run:     python mooink_full_cmd_demo.py --ip 192.168.1.137
Optional: python mooink_full_cmd_demo.py --ip 192.168.1.137

Sensitive data: per-reader client keys are encrypted with the CURRENT Windows
user's DPAPI and stored only in %LOCALAPPDATA%/mooink_interactive_lab/auth.
Never publish files from that directory. Reports omit serials/keys/PINs/PDF names.
The upstream Sony client skips certificate validation for its private LAN API.
Use only with your own devices on a trusted network.
"""
from __future__ import annotations

import argparse
import base64
import ctypes
from ctypes import wintypes
from datetime import datetime, timezone
import hashlib
import importlib
import ipaddress
import json
import os
from pathlib import Path
import platform
import re
import socket
import struct
import subprocess
import sys
import threading
import time
import traceback
from urllib.error import HTTPError, URLError
from urllib.request import build_opener, ProxyHandler, HTTPRedirectHandler, Request

TARGET_VID = "1F85"
TARGET_PID = "4E16"
COMMON_GUID = "{F0422A9C-5DC8-4440-B5BD-5DF28835658A}"
MTP_GUID = "{4D545058-1A2E-4106-A357-771E0819FC56}"
CLIENT_GUID = "{204D9F0C-2292-4080-9F42-40664E70F859}"
GENERIC_READ = 0x80000000

EXTENSION_NAMES = {
    "0x95C1": "GetPartialObject64 (讀取部分檔案)",
    "0x95C2": "SendPartialObject (寫入部分檔案)",
    "0x95C3": "TruncateObject (調整檔案長度)",
    "0x95C4": "BeginEditObject (開始檔案編輯)",
    "0x95C5": "EndEditObject (結束檔案編輯)",
}

# ---- Stable, verified WPD inspector core adapted from user's successful v004 ----
def clean_device_id(device_id: str, include_serial=False):
    """Avoid publishing the device serial in diagnostic reports by default."""
    if include_serial:
        return device_id
    # Device IDs may be USB\\VID_xxxx&PID_xxxx\\serial or
    # \\?\\USB#VID_xxxx&PID_xxxx#serial#{interface-GUID}; hide the serial.
    return re.sub(r"(?i)(VID_[0-9A-F]{4}&PID_[0-9A-F]{4})([\\#])([^\\#{}]+)",
                  r"\1\2<serial-redacted>", device_id)

def make_property_key(key_type, guid, pid, comtypes):
    key = key_type()
    key.fmtid = comtypes.GUID(guid)
    key.pid = pid
    return ctypes.pointer(key)

def string_guid(guid):
    value = str(guid)
    if value and not value.startswith("{"):
        value = "{" + value + "}"
    return value.upper()

def pnp_scan():
    """Best-effort PnP read; no setup changes, no admin rights required."""
    script = r'''
$ErrorActionPreference='Stop'
Get-PnpDevice -PresentOnly |
 Where-Object { $_.InstanceId -match 'VID_1F85&PID_4E16' } |
 ForEach-Object {
   $id=$_.InstanceId
   $p=Get-PnpDeviceProperty -InstanceId $id -KeyName DEVPKEY_Device_Service -ErrorAction SilentlyContinue
   [PSCustomObject]@{ Name=$_.FriendlyName; Class=$_.Class;
     Status=$_.Status; Service=$p.Data; InstanceId=$id }
 } | ConvertTo-Json -Compress -Depth 3
'''
    try:
        res = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=25, check=False,
        )
        if res.returncode != 0:
            return {"error": res.stderr.decode("utf-8", "replace").strip()}
        # Windows PowerShell may use the active console codepage, not UTF-8.
        raw = res.stdout.decode("utf-8-sig", "replace").strip()
        if not raw:
            return {"devices": []}
        obj = json.loads(raw)
        return {"devices": obj if isinstance(obj, list) else [obj]}
    except Exception as exc:
        return {"error": str(exc)}

def load_wpd():
    try:
        import comtypes
        import comtypes.client
    except ImportError as exc:
        raise RuntimeError("Missing dependency: py -m pip install comtypes") from exc
    # Type libraries ship with Windows, and the package generates wrappers.
    comtypes.client.GetModule("PortableDeviceApi.dll")
    comtypes.client.GetModule("PortableDeviceTypes.dll")
    api = importlib.import_module("comtypes.gen.PortableDeviceApiLib")
    types = importlib.import_module("comtypes.gen.PortableDeviceTypesLib")
    # Both type libraries may define a class named _tagpropertykey.
    # They are layout-compatible but NOT compatible Python ctypes classes.
    # PortableDeviceValues normally expects the TypesLib class.
    key_type = types._tagpropertykey
    return comtypes, comtypes.client, api, types, key_type

def call_with_property_key(obj, method, guid, pid, wpd, *values):
    """Call one WPD getter/setter with the exact PROPERTYKEY pointer type.

    Windows may generate the same C struct in both PortableDeviceApiLib and
    PortableDeviceTypesLib; these pointer classes are distinct to ctypes.
    A type validation failure occurs *before* any COM call. Only that specific
    failure is eligible for the alternate class; other failures are propagated.
    """
    comtypes, client, api, types, preferred_type = wpd
    candidates = [preferred_type]
    for cls in (types._tagpropertykey, api._tagpropertykey):
        if cls not in candidates:
            candidates.append(cls)
    last_error = None
    for cls in candidates:
        key = make_property_key(cls, guid, pid, comtypes)
        try:
            return getattr(obj, method)(key, *values)
        except (TypeError, ctypes.ArgumentError) as exc:
            if "LP__tagpropertykey" not in str(exc):
                raise
            last_error = exc
    if last_error is not None:
        raise last_error
    raise RuntimeError("No PROPERTYKEY class was available")

def enumerate_wpd(client, api):
    manager = client.CreateObject(api.PortableDeviceManager)
    length = ctypes.c_ulong(0)
    # Matching a Windows WPD sample approach to GetDevices().
    # comtypes WPD signatures require a *real ctypes pointer* here,
    # not ctypes.byref(count) (a CArgObject). Matches working Sony WPD code.
    manager.GetDevices(ctypes.POINTER(ctypes.c_wchar_p)(), ctypes.pointer(length))
    if length.value <= 0:
        return []
    ids = (ctypes.c_wchar_p * length.value)()
    manager.GetDevices(ids, ctypes.pointer(length))
    return [str(ids[i]) for i in range(length.value)]

def collection_count(col):
    """Support both COM wrappers with automatic [out] and explicit DWORD*.

    IPortableDeviceKeyCollection::GetCount(DWORD* pcElems) is documented to
    require a pointer. Some comtypes-generated methods fail to identify it as
    an [out] parameter and demand that Python supply the pointer explicitly.
    """
    try:
        count = col.GetCount()
        if isinstance(count, tuple):
            count = count[0]
        if count is not None:
            return int(count)
    except TypeError as exc:
        if "pcElems" not in str(exc) and "required argument" not in str(exc):
            raise
    out = ctypes.c_ulong(0)  # DWORD on Windows
    col.GetCount(ctypes.pointer(out))
    return out.value

def collection_key_at(col, index, wpd):
    """Retrieve one PROPERTYKEY while respecting comtypes typelib identity."""
    _, _, api, types, _ = wpd
    failures = []
    for cls in (types._tagpropertykey, api._tagpropertykey):
        key = cls()
        try:
            value = col.GetAt(index, ctypes.pointer(key))
            # The pointer output is authoritative; COM call result is HRESULT.
            return key
        except (TypeError, ctypes.ArgumentError) as exc:
            failures.append(str(exc))
    # comtypes versions that automatically marshal [out] may expect only index.
    try:
        key = col.GetAt(index)
        if isinstance(key, tuple):
            key = key[0]
        if hasattr(key, "contents"):
            key = key.contents
        if hasattr(key, "fmtid") and hasattr(key, "pid"):
            return key
    except (TypeError, ctypes.ArgumentError) as exc:
        failures.append(str(exc))
    raise RuntimeError("Unable to read collection key %d: %s" %
                       (index, "; ".join(failures)))

def get_key_collection(col, wpd):
    """Read device-advertised WPD command categories and IDs."""
    count = collection_count(col)
    results = []
    for i in range(min(count, 2048)):
        key = collection_key_at(col, i, wpd)
        results.append({"category": string_guid(key.fmtid), "id": int(key.pid)})
    return results

def propvariant_ui4(val):
    """Safely decode VT_UI4 from COM's PROPVARIANT without guessing unions."""
    if isinstance(val, tuple):
        val = val[0]
    if hasattr(val, "contents") and not hasattr(val, "vt"):
        val = val.contents
    # COM PROPVARIANT layout starts with VARTYPE at byte 0 and value at byte 8.
    # This is stable for 32-/64-bit Windows; only read when we have VT_UI4.
    raw = ctypes.string_at(ctypes.addressof(val), ctypes.sizeof(val))
    if len(raw) < 12:
        raise ValueError("PROPVARIANT returned too few bytes")
    vt = struct.unpack_from("<H", raw, 0)[0]
    if vt != 19:  # VT_UI4
        raise ValueError("Expected VT_UI4 (19), got VT=%s" % vt)
    return struct.unpack_from("<I", raw, 8)[0]

def propvariant_at(col, index, wpd):
    """Retrieve an advertised integer opcode from a PROPVARIANT collection."""
    _, _, api, types, _ = wpd
    variants = []
    for tlib in (types, api):
        for name in ("tag_inner_PROPVARIANT", "tagPROPVARIANT"):
            cls = getattr(tlib, name, None)
            if cls is not None and cls not in variants:
                variants.append(cls)
    failures = []
    for cls in variants:
        variant = cls()
        try:
            col.GetAt(index, ctypes.pointer(variant))
            return variant
        except (TypeError, ctypes.ArgumentError) as exc:
            failures.append(str(exc))
    try:
        variant = col.GetAt(index)
        if isinstance(variant, tuple):
            variant = variant[0]
        return variant
    except (TypeError, ctypes.ArgumentError) as exc:
        failures.append(str(exc))
    raise RuntimeError("Unable to get PROPVARIANT: " + "; ".join(failures))

def read_vendor_collection(result, wpd):
    """Read metadata only; never execute a vendor-provided opcode."""
    try:
        collection = call_with_property_key(
            result, "GetIPortableDevicePropVariantCollectionValue",
            MTP_GUID, 1005, wpd)
    except Exception as exc:
        return {"status": "error", "error": "Cannot read vendor codes: " + str(exc)}
    values = []
    try:
        count = collection_count(collection)
        for i in range(min(count, 1024)):
            code = propvariant_ui4(propvariant_at(collection, i, wpd))
            values.append("0x%04X" % code if code <= 0xffff else "0x%X" % code)
    except Exception as exc:
        return {"status": "error", "error": "Vendor collection decode failed: " + str(exc),
                "partial_codes": values}
    return {"status": "ok", "codes": values}

def inspect_device(device_id, *, wpd, show_id=False, vendor_query=True,
                   rw_vendor_query=False, force_vendor_query=False):
    comtypes, client, api, types, key_type = wpd
    report = {"device_id": clean_device_id(device_id, show_id),
              "wpd_commands": [], "wpd_commands_status": "unavailable",
              "mtp_vendor_operations": {"status": "not requested"}}
    device = client.CreateObject(api.PortableDevice)
    client_info = client.CreateObject(types.PortableDeviceValues)
    access = (GENERIC_READ | 0x40000000) if rw_vendor_query else GENERIC_READ
    report["access_requested"] = "readwrite" if rw_vendor_query else "read"
    # Requesting READ|WRITE permission is opt-in only. The code NEVER writes.
    call_with_property_key(client_info, "SetUnsignedIntegerValue",
                           CLIENT_GUID, 9, wpd, access)
    device.Open(device_id, client_info)
    try:
        try:
            caps = device.Capabilities()
            commands = caps.GetSupportedCommands()
            report["wpd_commands"] = get_key_collection(commands, wpd)
            report["wpd_commands_status"] = "ok"
        except Exception as exc:
            report["wpd_capabilities_error"] = (
                type(exc).__name__ + ": " + str(exc))
            report["wpd_capabilities_traceback"] = traceback.format_exc(limit=8)
            report["wpd_commands_status"] = "error"

        known = report["wpd_commands_status"] == "ok"
        advertised = any(
            c["category"] == string_guid(MTP_GUID) and c["id"] == 11
            for c in report["wpd_commands"])
        report["vendor_command_advertised"] = advertised if known else None
        if not vendor_query:
            report["mtp_vendor_operations"] = {"status": "skipped", "reason": "--no-vendor-query"}
        elif known and not advertised and not force_vendor_query:
            report["mtp_vendor_operations"] = {
                "status": "skipped",
                "reason": "WPD command list does not advertise MTP extension code 11. "
                          "Use --force-vendor-query to try the Microsoft metadata query anyway."}
        elif not known and not force_vendor_query:
            report["mtp_vendor_operations"] = {
                "status": "skipped",
                "reason": "WPD command list could not be read; vendor query disabled pending diagnosis"}
        else:
            # Microsoft's GET_SUPPORTED_VENDOR_OPCODES; no vendor-specific
            # command is executed and no data is modified.
            params = client.CreateObject(types.PortableDeviceValues)
            call_with_property_key(params, "SetGuidValue",
                                   COMMON_GUID, 1001, wpd, comtypes.GUID(MTP_GUID))
            call_with_property_key(params, "SetUnsignedIntegerValue",
                                   COMMON_GUID, 1002, wpd, 11)
            try:
                result = device.SendCommand(0, params)
                report["mtp_vendor_operations"] = read_vendor_collection(result, wpd)
            except Exception as exc:
                hresult = getattr(exc, "hresult", None)
                # Exception args often expose signed decimal 0x80070005.
                if hresult is None and getattr(exc, "args", None) and isinstance(exc.args[0], int):
                    hresult = exc.args[0]
                denied = (hresult is not None and (hresult & 0xffffffff) == 0x80070005)
                report["mtp_vendor_operations"] = {
                    "status": "access_denied" if denied else "error",
                    "error": type(exc).__name__ + ": " + str(exc),
                    "note": ("E_ACCESSDENIED; this does NOT mean zero vendor codes. "
                             "The requested access or Windows driver may block this metadata command."
                             if denied else "The driver or wrapper rejected the metadata query")}
    finally:
        try:
            device.Close()
        except Exception:
            pass
    return report

# --------------------- Interactive reader-specific research ------------------

class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


def heading(text):
    print("\n" + "=" * 72)
    print(text)
    print("=" * 72)


def pause(message="按 Enter 繼續（或輸入 q 停止本台測試）: "):
    try:
        answer = input("\n" + message).strip().lower()
    except (EOFError, KeyboardInterrupt):
        raise StopReader()
    if answer in ("q", "quit", "exit"):
        raise StopReader()
    return answer


class StopReader(Exception):
    pass


def yes_no(message, default=False):
    marker = "[Y/n]" if default else "[y/N]"
    while True:
        reply = pause("%s %s: " % (message, marker))
        if not reply:
            return default
        if reply in ("y", "yes", "是", "好"):
            return True
        if reply in ("n", "no", "否"):
            return False
        print("請輸入 y 或 n。")


def choose(items, title, allow_skip=False, description=None):
    if not items:
        return None
    print("\n" + title)
    for i, item in enumerate(items, 1):
        desc = description(item) if description else str(item)
        print("  %d. %s" % (i, desc))
    if allow_skip:
        print("  0. 跳過")
    while True:
        s = pause("請選擇編號: ")
        try:
            idx = int(s)
        except ValueError:
            print("請輸入編號。")
            continue
        if allow_skip and idx == 0:
            return None
        if 1 <= idx <= len(items):
            return items[idx - 1]
        print("編號無效。")


def observation(question):
    print("\n實機觀察：" + question)
    response = choose(
        [("yes", "是／成功"), ("no", "否／沒有變化"),
         ("uncertain", "不確定／沒有觀察到")],
        "請依螢幕實際狀態回答", description=lambda x: x[1]
    )
    return response[0]


def timestamp():
    return datetime.now(timezone.utc).isoformat()


def fingerprint(info):
    # This is a dynamic identifier, NOT a hardcoded USB/client ID. The secret
    # is never written into a report or a source file.
    identity = "%s\x00%s" % (info.get("model_name", ""), info.get("serial_number", ""))
    if not info.get("serial_number"):
        return None
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]


def safe_key_error(exc):
    # Protect against exceptions mentioning device serials, tokens, or PEM.
    msg = "%s: %s" % (type(exc).__name__, exc)
    msg = re.sub(r"-----BEGIN .*?-----.*?-----END .*?-----", "<private-key>", msg,
                 flags=re.DOTALL)
    msg = re.sub(r"(?i)(serial_number|client_id|credentials|nonce)[^,;\n]*", r"\1=<redacted>", msg)
    return clean_device_id(msg)[:300]


class Recorder:
    def __init__(self, output_dir):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.report = {"tool": "mooInk Interactive Lab v1.0", "started_utc": timestamp(),
                       "note": "No direct wake/standby API has been proven.", "readers": []}
        stem = datetime.now().strftime("mooink_lab_%Y%m%d_%H%M%S_%f")
        self.file = self.output_dir / (stem + ".json")
        self.text_file = self.output_dir / (stem + ".txt")
        self.save()

    def save(self):
        self.file.write_text(json.dumps(self.report, ensure_ascii=False, indent=2) + "\n",
                             encoding="utf-8")
        lines = ["mooInk Pro Full Command Demo v1.1", "=" * 65,
                 "Started UTC: " + self.report["started_utc"],
                 "Note: HTTP 2xx never proves that e-paper woke up."]
        for index, reader in enumerate(self.report["readers"], 1):
            lines += ["", "[%d] %s" % (index, reader.get("label", "mooInk")),
                      "USB: " + str(reader.get("usb", {})),
                      "LAN: " + str(reader.get("lan_ip", "not tested"))]
            for e in reader.get("events", []):
                fields = []
                for field in ("http_status", "command_count", "vendor_codes", "items_count", "source", "ports", "statuses"):
                    if field in e and field not in {f[0] for f in fields}:
                        fields.append((field, str(e[field])))
                line = "  %s : %s" % (e.get("test"), e.get("status"))
                if fields:
                    line += " (" + ", ".join(k + "=" + v for k, v in fields) + ")"
                lines.append(line)
        self.text_file.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def start_reader(self, reader_label, usb):
        item = {"label": reader_label, "usb": {
            "found": bool(usb), "pnp_class": usb.get("Class") if usb else None,
            "service": usb.get("Service") if usb else None,
            "device_id": clean_device_id(usb.get("InstanceId", "")) if usb else None
        }, "events": [], "started_utc": timestamp()}
        self.report["readers"].append(item)
        self.save()
        return item

    def event(self, reader, name, status, **details):
        entry = {"time_utc": timestamp(), "test": name, "status": status}
        entry.update(details)
        reader["events"].append(entry)
        self.save()
        print("  [紀錄] %s: %s" % (name, status))


def usb_tail(instance_id):
    return (instance_id.rsplit("\\", 1)[-1][-5:] or "?????").upper()


def selected_wpd_id(pnp):
    """Match WPD to selected Windows PnP ID, never to a hardcoded serial."""
    if not pnp:
        return None
    serial = pnp.get("InstanceId", "").rsplit("\\", 1)[-1].upper()
    if not serial:
        return None
    return serial


def stage_usb(rec, reader, pnp, do_wpd):
    heading("[1] USB / MTP 裝置識別")
    if not pnp:
        print("沒有選擇 USB 裝置；後續仍可進行 Wi-Fi 測試。")
        rec.event(reader, "usb", "skipped")
        return
    print("選取：%s | USB 尾碼 ...%s" % (pnp.get("Name"), usb_tail(pnp["InstanceId"])))
    print("驅動：%s  狀態：%s" % (pnp.get("Service"), pnp.get("Status")))
    rec.event(reader, "usb", "found", class_name=pnp.get("Class"),
              service=pnp.get("Service"), pnp_status=pnp.get("Status"))
    if not do_wpd or not yes_no("檢查此台 WPD 支援命令（唯讀）？", default=True):
        return
    try:
        comtypes, client, api, types, keytype = wpd = load_wpd()
        ids = enumerate_wpd(client, api)
        serial = selected_wpd_id(pnp)
        targets = [x for x in ids if "VID_1F85" in x.upper() and
                   "PID_4E16" in x.upper() and serial in x.upper()]
        if not targets:
            print("無法唯一對應 USB / WPD 裝置；為避免查錯機器，不執行 WPD。")
            rec.event(reader, "wpd", "not_matched", target_count=len(targets))
            return
        if len(targets) > 1:
            print("有多個 WPD 介面符合，跳過以避免誤認。")
            rec.event(reader, "wpd", "ambiguous", match_count=len(targets))
            return
        # Don't do any vendor opcodes. At most request extra handle privilege
        # for the Microsoft-defined metadata query, with explicit user consent.
        readwrite = yes_no("要查詢 MTP 擴充碼嗎？需要讀寫權限開啟 WPD，但只查詢資訊、不修改資料。")
        info = inspect_device(targets[0], wpd=wpd, show_id=False,
                              vendor_query=readwrite, rw_vendor_query=readwrite,
                              force_vendor_query=False)
        cmds = info.get("wpd_commands", [])
        print("WPD 指令數：%d；狀態：%s" % (len(cmds), info.get("wpd_commands_status")))
        vendor = info.get("mtp_vendor_operations", {})
        codes = vendor.get("codes", [])
        print("MTP 擴充碼狀態：%s" % vendor.get("status", "unknown"))
        for code in codes:
            print("  %s  %s" % (code, EXTENSION_NAMES.get(code, "未知／未解碼")))
        print("注意：這些 WPD 指令屬於驅動介面，不代表找到喚醒命令。")
        if cmds and yes_no("要顯示完整 WPD 指令 GUID/編號嗎？"):
            for cmd in cmds:
                print("   %s / %s" % (cmd.get("category"), cmd.get("id")))
        rec.event(reader, "wpd", info.get("wpd_commands_status", "unknown"),
                  command_count=len(cmds), commands=cmds,
                  vendor_status=vendor.get("status"), vendor_codes=codes)
    except ImportError:
        print("未安裝 comtypes：python -m pip install comtypes")
        rec.event(reader, "wpd", "dependency_missing")
    except Exception as exc:
        print("WPD 查詢失敗：" + safe_key_error(exc))
        rec.event(reader, "wpd", "error", error=safe_key_error(exc))


def discover_mdns(duration):
    """Discover actual services; no assumptions about the reader's USB ID."""
    try:
        from zeroconf import ServiceBrowser, Zeroconf
    except ImportError:
        print("沒有 zeroconf，請安裝 dpt-rp1-py 或直接手動輸入 IP。")
        return []
    results, lock = {}, threading.Lock()

    class Listener:
        def add_service(self, zc, service_type, name):
            try:
                info = zc.get_service_info(service_type, name, timeout=1200)
                if not info:
                    return
                addresses = info.parsed_addresses()
                for addr in addresses:
                    try:
                        if ipaddress.ip_address(addr).version != 4:
                            continue
                    except ValueError:
                        continue
                    with lock:
                        results[addr] = {"ip": addr, "port": info.port,
                                         "service": service_type}
            except Exception:
                pass

        def remove_service(self, zc, service_type, name):
            pass

        def update_service(self, zc, service_type, name):
            pass

    print("mDNS 搜尋中（%d 秒），不會掃描其他 LAN 裝置 ..." % duration)
    zc = Zeroconf()
    try:
        listener = Listener()
        browsers = [ServiceBrowser(zc, t, listener) for t in (
            "_digitalpaper._tcp.local.", "_dp_readmoo._tcp.local.",
            "_dp_fujitsu._tcp.local.")]
        time.sleep(duration)
        _ = browsers
    finally:
        zc.close()
    return list(sorted(results.values(), key=lambda x: x["ip"]))


def require_private_ipv4(raw):
    parsed = ipaddress.ip_address(raw.strip())
    if parsed.version != 4 or parsed.is_loopback or not (parsed.is_private or parsed.is_link_local):
        raise ValueError("僅接受區域網路的 IPv4 位址")
    return str(parsed)


def choose_ip(args):
    if args.ip:
        ip = require_private_ipv4(args.ip)
        print("使用命令列指定 IP：" + ip)
        return ip
    candidates = [] if args.no_mdns else discover_mdns(args.discovery_seconds)
    if candidates:
        selected = choose(candidates, "探索到的 Sony / Readmoo 服務（請確認所選機器）",
                          allow_skip=True,
                          description=lambda x: "%s  Port %d  %s" %
                          (x["ip"], x["port"], x["service"]))
        if selected:
            return selected["ip"]
    while True:
        raw = pause("輸入要測試的 mooInk Wi-Fi IP（未知請先到閱讀器 Wi-Fi 設定找）：")
        try:
            return require_private_ipv4(raw)
        except ValueError as exc:
            print("IP 無效：" + str(exc))


def tcp_check(ip, port, timeout):
    try:
        with socket.create_connection((ip, port), timeout=timeout):
            return "open"
    except (OSError, TimeoutError) as exc:
        return type(exc).__name__


def http_get_metadata(ip, endpoint, timeout):
    opener = build_opener(ProxyHandler({}), NoRedirect())
    url = "http://%s:8080%s" % (ip, endpoint)
    try:
        with opener.open(Request(url, headers={"User-Agent": "mooink-lab/1"}),
                         timeout=timeout) as resp:
            code = resp.status
            blob = resp.read(16384)
            data = json.loads(blob.decode("utf-8"))
            return code, data
    except HTTPError as exc:
        return exc.code, None
    except (OSError, URLError, ValueError, UnicodeError) as exc:
        return 0, {"_error": type(exc).__name__}


def probe_network(rec, reader, ip, state, timeout):
    heading("[網路] %s：連線、公開 API 驗證" % ("喚醒" if state == "awake" else "休眠"))
    ports = {str(p): tcp_check(ip, p, timeout) for p in (8080, 8443)}
    for port, status in ports.items():
        print("TCP %s:%s  %s" % (ip, port, status))
    statuses, data = {}, {}
    if ports["8080"] == "open":
        for endpoint in ("/register/information", "/api_version"):
            status, value = http_get_metadata(ip, endpoint, timeout)
            statuses[endpoint] = status
            data[endpoint] = value
            print("GET %-28s HTTP %s" % (endpoint, status or "failed"))
    rec.event(reader, "network_" + state, "ok" if ports["8080"] == "open" and
              ports["8443"] == "open" else "partial", ports=ports,
              http_status=statuses)
    return data.get("/register/information") if isinstance(data.get("/register/information"), dict) else None


# --------------------------- Windows DPAPI secret store ---------------------

class _Blob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD),
                ("pbData", ctypes.POINTER(ctypes.c_ubyte))]


def dpapi_transform(data, protect=True):
    if os.name != "nt":
        raise RuntimeError("DPAPI 僅支援 Windows")
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    inbuffer = ctypes.create_string_buffer(data)
    inp = _Blob(len(data), ctypes.cast(inbuffer, ctypes.POINTER(ctypes.c_ubyte)))
    out = _Blob()
    fn = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
    fn.restype = wintypes.BOOL
    fn.argtypes = [ctypes.POINTER(_Blob), ctypes.c_void_p, ctypes.c_void_p,
                   ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD,
                   ctypes.POINTER(_Blob)]
    ok = fn(ctypes.byref(inp), None, None, None, None, 1, ctypes.byref(out))
    if not ok:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        kernel32.LocalFree.argtypes = [ctypes.c_void_p]
        kernel32.LocalFree.restype = ctypes.c_void_p
        kernel32.LocalFree(ctypes.cast(out.pbData, ctypes.c_void_p))


def secret_path(device_fingerprint):
    root = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local"))
    d = root / "mooink_interactive_lab" / "auth"
    d.mkdir(parents=True, exist_ok=True)
    return d / (device_fingerprint + ".dpapi")


def save_secret(device_fingerprint, client_id, key):
    payload = json.dumps({"client_id": client_id, "key": key.decode("utf-8") if
                          isinstance(key, bytes) else key}, ensure_ascii=False)
    secret_path(device_fingerprint).write_bytes(dpapi_transform(payload.encode("utf-8")))


def load_secret(device_fingerprint):
    p = secret_path(device_fingerprint)
    if not p.exists():
        return None
    data = json.loads(dpapi_transform(p.read_bytes(), protect=False).decode("utf-8"))
    return data["client_id"], data["key"].encode("utf-8")


def make_client(ip, timeout):
    import requests  # installed by dpt-rp1-py
    from dptrp1.dptrp1 import DigitalPaper
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    d = DigitalPaper(addr=ip)
    d.session.trust_env = False
    original_send = d.session.send

    def send_with_timeout(request, **kwargs):
        kwargs.setdefault("timeout", timeout)
        return original_send(request, **kwargs)

    d.session.send = send_with_timeout
    return d


def auth_session(rec, reader, ip, info, timeout):
    heading("[2] API 配對 / RSA 認證（每台單獨、無硬編 ID）")
    try:
        d = make_client(ip, timeout)
    except ImportError as exc:
        print("缺少套件：python -m pip install dpt-rp1-py comtypes")
        rec.event(reader, "auth", "dependency_missing", error=safe_key_error(exc))
        return None
    device_fp = fingerprint(info or {})
    if not device_fp:
        print("無法從公開 API 取得 serial_number，故無法安全地配對本台金鑰。")
        rec.event(reader, "auth", "no_identity")
        return None
    print("本台動態識別碼：%s（不可逆摘要，不是寫死的序號）" % device_fp[:12])
    reader["device_fingerprint"] = device_fp[:12]
    rec.save()
    attempts = []
    try:
        cached = load_secret(device_fp)
        if cached:
            attempts.append(("encrypted_cache", cached))
    except Exception as exc:
        print("無法解密本機配對憑證：" + safe_key_error(exc))
    try:
        from dptrp1.dptrp1 import get_default_auth_files
        id_path, key_path = get_default_auth_files()
        if Path(id_path).is_file() and Path(key_path).is_file():
            attempts.append(("existing_dptrp1", (Path(id_path).read_text(encoding="utf-8").strip(),
                                                 Path(key_path).read_bytes())))
    except Exception as exc:
        print("讀取現有憑證失敗：" + safe_key_error(exc))
    for origin, (client_id, key) in attempts:
        try:
            r = d.authenticate(client_id, key)
            r.raise_for_status()
            if d._get_endpoint("/ping").ok:
                print("既有憑證驗證成功（來源：%s）" % origin)
                if origin == "existing_dptrp1":
                    save_secret(device_fp, client_id, key)
                rec.event(reader, "auth", "ok", source=origin)
                return d
        except Exception as exc:
            print("既有憑證未通過，略過：" + safe_key_error(exc))
            d = make_client(ip, timeout)
    print("本機尚無可用的本台憑證。")
    if not yes_no("要執行官方相容配對？會修改配對狀態、顯示 PIN，並建立此台的新憑證。"):
        rec.event(reader, "auth", "skipped")
        return None
    print("讓 mooInk 保持喚醒並準備輸入螢幕 PIN。勿公開 PIN 或私鑰。")
    pause("準備好後按 Enter 開始配對：")
    try:
        returned = d.register()   # Mutates pairing state; explicit opt-in above.
        if not returned or len(returned) != 3:
            raise RuntimeError("register 沒有回傳三個配對資料")
        _ca, key, client_id = returned
        if not key or not client_id:
            raise RuntimeError("register 沒有產生有效憑證")
        r = d.authenticate(client_id, key)
        r.raise_for_status()
        if not d._get_endpoint("/ping").ok:
            raise RuntimeError("配對後 /ping 未通過")
        save_secret(device_fp, client_id, key)
        print("成功！此台憑證已用 Windows DPAPI 加密保存。")
        rec.event(reader, "auth", "ok", source="new_pairing")
        return d
    except Exception as exc:
        print("配對 / 認證失敗：" + safe_key_error(exc))
        rec.event(reader, "auth", "error", error=safe_key_error(exc))
        return None


def get_readonly_details(rec, reader, d, phase):
    print("\nHTTPS 8443 已授權測試（%s）：" % phase)
    statuses = {}
    for label, endpoint in (("battery", "/system/status/battery"),
                            ("firmware", "/system/status/firmware_version"),
                            ("sleep_timeout", "/system/configs/timeout_to_standby")):
        try:
            resp = d._get_endpoint(endpoint)
            statuses[label] = resp.status_code
            value = None
            if resp.ok and label in ("sleep_timeout", "battery"):
                obj = resp.json()
                if isinstance(obj, dict):
                    if label == "sleep_timeout":
                        value = obj.get("value")
                    elif label == "battery":
                        value = {k: v for k, v in obj.items() if k in
                                 {"level", "value", "charge", "charging", "battery_level"}}
            print("  %-16s HTTP %d%s" % (label, resp.status_code,
                                         " value=" + str(value) if value is not None else ""))
        except Exception as exc:
            statuses[label] = "error"
            print("  %-16s %s" % (label, safe_key_error(exc)))
    try:
        resp = d._get_endpoint("/documents2?entry_type=all")
        statuses["documents"] = resp.status_code
        docs = resp.json().get("entry_list", []) if resp.ok else []
        print("  %-16s HTTP %s; 項目數：%d" % ("documents", resp.status_code, len(docs)))
    except Exception as exc:
        docs = []
        statuses["documents"] = "error"
        print("  documents        " + safe_key_error(exc))
    rec.event(reader, "authenticated_" + phase, "done", statuses=statuses,
              items_count=len(docs))
    return docs


def send_display(rec, reader, d, doc, page, phase):
    # Deliberate display operation (does not modify PDF bytes).
    # Check real HTTP status, unlike upstream DigitalPaper.display_document().
    target = doc.get("entry_id")
    if not target:
        print("文件沒有 entry_id，無法送出顯示命令。")
        rec.event(reader, "display_" + phase, "no_document_id")
        return None
    try:
        resp = d._put_endpoint("/viewer/controls/open2", data={
            "document_id": target, "page": int(page)})
        print("PUT /viewer/controls/open2 -> HTTP %d" % resp.status_code)
        rec.event(reader, "display_http_" + phase,
                  "accepted" if 200 <= resp.status_code < 300 else "rejected",
                  http_status=resp.status_code)
        return resp.status_code
    except Exception as exc:
        print("PUT 發生錯誤：" + safe_key_error(exc))
        rec.event(reader, "display_http_" + phase, "error", error=safe_key_error(exc))
        return None


def stage_display_tests(rec, reader, d, docs, ip):
    heading("[3] 畫面控制：喚醒時與休眠時分別送出 display-document")
    pdfs = [entry for entry in docs if entry.get("entry_type") == "document" and
            str(entry.get("entry_path", "")).lower().endswith(".pdf")]
    if not pdfs:
        print("未找到 PDF 文件，略過畫面命令測試。")
        rec.event(reader, "display", "no_pdf")
        return
    if not yes_no("要開始畫面對照測試嗎？會要求打開已存在的 PDF（不修改內容）。"):
        rec.event(reader, "display", "skipped")
        return
    doc = choose(pdfs[:80], "選擇測試 PDF（顯示名稱只在本機，報告不記錄）",
                 description=lambda x: x.get("entry_path", "(unknown)"))
    if not doc:
        return
    while True:
        s = pause("指定頁碼 [預設 1]: ") or "1"
        if s.isdigit() and 1 <= int(s) <= 100000:
            page = int(s)
            break
        print("頁碼需為正整數。")
    print("\n請先讓 mooInk 喚醒，並記住目前顯示畫面。")
    pause("螢幕已喚醒後按 Enter：")
    send_display(rec, reader, d, doc, page, "awake")
    visual_awake = observation("現在是否切換到指定 PDF 頁面？")
    rec.event(reader, "display_screen_awake", visual_awake)
    # Use a DIFFERENT PDF in sleep so that we can tell whether a queued
    # display request was applied only after the user manually wakes the panel.
    others = [entry for entry in pdfs if entry.get("entry_id") != doc.get("entry_id")]
    if others:
        print("\n請選擇另一份 PDF，作為休眠時的目標，以便區分兩次顯示命令。")
        sleep_doc = choose(others[:80], "休眠時要開啟的 PDF",
                           description=lambda x: x.get("entry_path", "(unknown)"))
        sleep_page = 1
    else:
        print("\n只有一份 PDF；可指定不同的頁碼（若該 PDF 有足夠頁數）。")
        sleep_doc = doc
        while True:
            text = pause("休眠時指定頁碼（必須與醒著測試不同；例如 2）: ")
            if text.isdigit() and 1 <= int(text) <= 100000 and int(text) != page:
                sleep_page = int(text)
                break
            print("請輸入不同於醒著測試的有效頁碼；若 PDF 只有一頁，輸入 q 略過。")
    print("\n請手動讓 mooInk 進入休眠，等待約 30 秒。")
    pause("確認螢幕已休眠後按 Enter：")
    probe_network(rec, reader, ip, "sleep", 3.0)
    get_readonly_details(rec, reader, d, "sleep")
    send_display(rec, reader, d, sleep_doc, sleep_page, "sleep")
    visual_sleep = observation("這次命令是否『直接』將電子紙從休眠喚醒並切換到第二份文件／頁碼？")
    rec.event(reader, "display_screen_sleep", visual_sleep)
    if visual_sleep != "yes":
        print("\n進階確認：休眠時收到的第二個文件命令，是否在手動喚醒後才生效？")
        if yes_no("要手動喚醒並檢查嗎？"):
            pause("請短按閱讀器電源鍵喚醒，再按 Enter：")
            eventual = observation("手動喚醒後，是否切換到休眠時指定的第二份文件／頁碼？")
            rec.event(reader, "post_manual_wake_page", eventual)
    print("\n判讀：API 收到 HTTP 2xx ≠ 螢幕已喚醒。實機觀察為準。")


def stage_sleep_image(rec, reader, usb):
    heading("[4] MTP 休眠圖與重新顯示（人工安全驗證）")
    if not usb:
        print("未選擇 USB 裝置，無法進行本階段。")
        rec.event(reader, "sleep_image", "skipped_no_usb")
        return
    print("此階段需在 Windows 檔案總管操作，程式不會自動刪除或覆寫檔案。")
    print("務必先備份原有的 sleep 圖片，且確認是你選擇的那台 USB mooInk。")
    if not yes_no("要驗證『休眠時更換圖片 → 手動喚醒 → 再休眠』嗎？"):
        rec.event(reader, "sleep_image", "skipped")
        return
    print("\n[4A] 請讓閱讀器休眠，打開檔案總管的 mooInk Pro -> sleep。")
    print("     先備份正在使用的 JPG；再覆蓋相同檔名的 JPG。")
    print("     若不知道哪張圖正在使用，請跳過，不要試刪檔或覆蓋系統資料。")
    if not yes_no("已經在休眠期間成功替換 JPG，且有備份原檔嗎？"):
        rec.event(reader, "sleep_image", "not_replaced")
        return
    auto_refresh = observation("保持休眠、沒有碰電源鍵時，電子紙是否自動更新？")
    rec.event(reader, "sleep_image_auto_refresh", auto_refresh)
    pause("請手動短按電源鍵喚醒，再按 Enter：")
    pause("請再次短按電源鍵讓閱讀器休眠，再按 Enter：")
    refreshed = observation("新的休眠圖是否出現在電子紙上？")
    rec.event(reader, "sleep_image_after_wake_sleep", refreshed)
    print("這是人工重現，不代表已找到 Wi-Fi/API 直接喚醒或立即休眠命令。")


def run_reader(rec, args, usb):
    label = pause("為這台設定識別標籤（例如 10.3 吋／13.3 吋；僅用於報告）[預設 mooInk]: ") or "mooInk"
    # Limit label to keep resulting report name and console manageable.
    label = label[:60]
    reader = rec.start_reader(label, usb)
    stage_usb(rec, reader, usb, not args.no_wpd)
    heading("[2] 選擇 LAN 裝置（與 USB 對應須人工確認）")
    print("USB ID 並不等於網路 IP；腳本不會把兩台同型號機器猜成同一台。")
    ip = choose_ip(args)
    reader["lan_ip"] = ip
    rec.save()
    if not yes_no("確認 %s 就是目前要測試的『%s』？" % (ip, label), default=True):
        rec.event(reader, "network_identity", "not_confirmed")
        return
    print("\n先讓本台 mooInk 保持『喚醒』狀態。")
    pause("螢幕喚醒後按 Enter：")
    info = probe_network(rec, reader, ip, "awake", args.timeout)
    if not info or not info.get("serial_number"):
        print("無法確認網路裝置身分，停止本台的授權／顯示測試。")
        rec.event(reader, "network_identity", "unverified")
        return
    reader["network_device"] = {"fingerprint": fingerprint(info)[:12],
                                "model_name": str(info.get("model_name", "unknown"))[:80]}
    rec.save()
    d = auth_session(rec, reader, ip, info, args.timeout)
    if d is None:        return
    docs = get_readonly_details(rec, reader, d, "awake")
    stage_display_tests(rec, reader, d, docs, ip)
    stage_sleep_image(rec, reader, usb)
    print("\n本台測試完成。")


# ===== Full Command Demo (auto diagnostic + interactive observations) =====
# To add newly discovered commands, append an entry to READ_ONLY_COMMANDS
# or implement a separate gated test if an operation can alter device state.
DEMO_VERSION = "1.1.1"

# User-reported physical observations, not results of this demo invocation.
# The exact tested model, firmware, key and sleep duration were not specified.
BLUETOOTH_WAKE_NOTES = (
    "使用者實機回報：已配對的藍牙鍵盤可以喚醒 mooInk。",
    "使用者實機回報：藍牙滑鼠無法喚醒 mooInk。",
    "系統可手動設定 15 秒自動休眠。",
    "未來可嘗試 Raspberry Pi 5 模擬 Bluetooth HID 鍵盤，"
    "更新 sleep 圖片後送出按鍵喚醒，再等待 15 秒自動休眠。",
    "仍待確認：長時間休眠後能否喚醒、USB 連接時能否自動休眠、"
    "再次休眠是否顯示新圖，以及不同型號／韌體的相容性。",
    "本 Demo 僅記錄文字，不實作藍牙配對、按鍵發送、"
    "修改休眠設定或自動刷新；Wi-Fi/API 直接喚醒仍未證實。",
)

READ_ONLY_COMMANDS = (
    ("ping", "/ping", "驗證目前的授權連線"),
    ("battery", "/system/status/battery", "讀取電池狀態"),
    ("firmware", "/system/status/firmware_version", "讀取韌體版本"),
    ("standby_timeout", "/system/configs/timeout_to_standby", "讀取自動休眠設定（不修改）"),
    ("configuration", "/system/configs/", "讀取系統設定（報告只紀錄欄位名稱）"),
    ("documents", "/documents2?entry_type=all", "讀取文件清單（報告只紀錄數量）"),
)

class DemoRecorder(Recorder):
    """Append-only report for every API call, without leaking credentials/paths.

    JSON is written alongside the verbose human-readable TXT for future parsing.
    """
    def __init__(self, output_dir):
        super().__init__(output_dir)
        self.report["tool"] = "mooInk Full Command Demo " + DEMO_VERSION
        self.report["purpose"] = "Describe every safe read and manually observed display test."
        self.report["bluetooth_wake_notes"] = {
            "source": "user-reported physical observations; not tested by this run",
            "notes": list(BLUETOOTH_WAKE_NOTES),
        }
        stem = datetime.now().strftime("mooink_full_cmd_demo_%Y%m%d_%H%M%S_%f")
        self.file = self.output_dir / (stem + ".json")
        self.text_file = self.output_dir / (stem + ".txt")
        self.save()

    def save(self):
        # The original Recorder __init__ invokes save() before our new names
        # are assigned. Both states are valid.
        self.file.write_text(json.dumps(self.report, ensure_ascii=False, indent=2) + "\n",
                             encoding="utf-8")
        lines = ["mooInk Pro Full Command Demo " + DEMO_VERSION,
                 "=" * 82,
                 "Started: " + self.report["started_utc"],
                 "Goal: USB/MTP, Sony Wi-Fi API, screen state and sleep-image research",
                 "Caution: HTTP 2xx != display wake. Direct Wi-Fi/API wake and immediate standby remain unproven.",
                 "", "藍牙喚醒研究紀錄（使用者實測回報，非本次 Demo 測試）：",
                 *BLUETOOTH_WAKE_NOTES,
                 "", "LEGEND: ok=completed, rejected=HTTP non-2xx, skipped=not attempted,",
                 "        observed=yes/no/uncertain from human, not inferred by software."]
        for idx, reader in enumerate(self.report.get("readers", []), 1):
            lines += ["", "=" * 82, "READER %d: %s" % (idx, reader.get("label")),
                      "LAN address: " + str(reader.get("lan_ip", "none"))]
            usb = reader.get("usb", {})
            lines.append("USB selected: %s / %s" % (usb.get("pnp_class", "not selected"),
                                                   usb.get("service", "none")))
            for j, event in enumerate(reader.get("events", []), 1):
                lines.append("")
                lines.append("%03d. %s  |  result=%s  |  %s" %
                             (j, event.get("test"), event.get("status"), event.get("time_utc", "")))
                keys = [k for k in event if k not in ("test", "status", "time_utc")]
                for k in keys:
                    value = event[k]
                    if isinstance(value, (list,dict)):
                        value = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
                    # Do not output raw serial, PDF paths, PEM, cookies, or GUID of
                    # credentials. The event producers only provide safe fields.
                    lines.append("     %-20s : %s" % (k, str(value)[:12000]))
        self.text_file.write_text("\n".join(lines) + "\n", encoding="utf-8")


def safe_report_response(name, response):
    """Only whitelist non-sensitive metadata. Never emit raw API bodies."""
    if not response.ok:
        return {"status_code": response.status_code}
    try:
        value = response.json()
    except Exception:
        return {"status_code": response.status_code, "format": "not-json"}
    meta = {"status_code": response.status_code}
    if name == "ping":
        meta["result"] = "reachable"
    elif name == "standby_timeout":
        if isinstance(value, dict):
            meta["timeout"] = str(value.get("value", "unknown"))[:80]
    elif name == "battery" and isinstance(value, dict):
        meta["battery"] = {k: str(v)[:80] for k, v in value.items()
                           if k in {"level","value","charging","charge","battery_level","status"}}
    elif name == "firmware" and isinstance(value, dict):
        meta["firmware"] = str(value.get("value", "unknown"))[:120]
    elif name == "configuration" and isinstance(value, dict):
        meta["available_keys"] = sorted(str(k) for k in value.keys())[:100]
    elif name == "documents" and isinstance(value, dict):
        rows = value.get("entry_list", [])
        meta["entry_count"] = len(rows) if isinstance(rows, list) else "unknown"
    return meta


def command_demo(rec, reader, client, phase):
    heading("授權 API 唯讀指令展示：" + phase)
    docs = []
    for name, path, purpose in READ_ONLY_COMMANDS:
        print("  GET %s : %s" % (path, purpose))
        try:
            response = client._get_endpoint(path)
            safe = safe_report_response(name, response)
            success = 200 <= response.status_code < 300
            print("       HTTP %d; %s" % (response.status_code,
                                       "OK" if success else "REJECTED"))
            rec.event(reader, "api_" + phase + "_" + name,
                      "ok" if success else "rejected", method="GET", path=path,
                      purpose=purpose, response=safe,
                      interpretation="API replied; no wake conclusion" if success else "API rejected GET")
            if name == "documents" and success:
                js = response.json()
                docs = js.get("entry_list", []) if isinstance(js, dict) else []
        except Exception as exc:
            error = safe_key_error(exc)
            print("       ERROR: " + error)
            rec.event(reader, "api_" + phase + "_" + name, "error", method="GET",
                      path=path, purpose=purpose, error=error)
    return docs


def auto_usb_demo(rec, reader, skip_wpd=False):
    heading("USB / PnP / MTP 自動診斷：列出全部已連接設備")
    pnp = pnp_scan()
    if pnp.get("error"):
        rec.event(reader, "usb_pnp", "error", error=pnp["error"][:200])
        return pnp.get("devices", [])
    devices = [x for x in pnp.get("devices", []) if x.get("InstanceId")]
    rec.event(reader, "usb_pnp", "ok", detected_count=len(devices),
              devices=[{"name": str(x.get("Name",""))[:80],
                        "class": x.get("Class"), "service": x.get("Service"),
                        "status": x.get("Status"),
                        "usb_id": clean_device_id(x.get("InstanceId", ""))} for x in devices],
              interpretation="All matching USB devices discovered; no serial hard-coded")
    print("共發現 %d 台 VID:1F85 PID:4E16" % len(devices))
    for i, item in enumerate(devices,1):
        print("  #%d: %s (%s, %s), USB ...%s" %
              (i, item.get("Name"), item.get("Class"), item.get("Status"),
               usb_tail(item.get("InstanceId", ""))))
    if skip_wpd or not devices:
        rec.event(reader, "wpd", "skipped", reason="--no-wpd or no USB devices")
        return devices
    try:
        comtypes, client, api, types, keytype = wpd = load_wpd()
        all_ids = enumerate_wpd(client, api)
    except Exception as exc:
        rec.event(reader, "wpd_init", "error", error=safe_key_error(exc))
        return devices
    for i, dev in enumerate(devices, 1):
        suffix = selected_wpd_id(dev)
        matches = [v for v in all_ids if suffix and
                   suffix in v.upper() and "VID_1F85" in v.upper() and "PID_4E16" in v.upper()]
        if len(matches) != 1:
            rec.event(reader, "wpd_device_%d" % i, "skipped",
                      reason="not uniquely matchable", candidate_count=len(matches))
            continue
        # The user's v004 run showed vendor opcode metadata requires RW handle;
        # no MTP *write* opcode is executed here. This is just metadata.
        try:
            result = inspect_device(matches[0], wpd=wpd,
                                    vendor_query=True, rw_vendor_query=True)
            cmds = result.get("wpd_commands", [])
            vendor = result.get("mtp_vendor_operations", {})
            print("  USB #%d -> WPD commands=%d; vendor=%s" %
                  (i, len(cmds), vendor.get("status")))
            rec.event(reader, "wpd_device_%d" % i, result.get("wpd_commands_status","unknown"),
                      method="WPD COM", purpose="GetSupportedCommands + GetSupportedVendorOpcodes",
                      command_count=len(cmds), commands=cmds,
                      vendor_codes=vendor.get("codes", []),
                      vendor_status=vendor.get("status"),
                      vendor_meanings={code:EXTENSION_NAMES.get(code,"unknown") for code in vendor.get("codes", [])},
                      interpretation="WPD support list and file edit codes do NOT imply wake")
        except Exception as exc:
            rec.event(reader, "wpd_device_%d" % i, "error", error=safe_key_error(exc))
    return devices


def demo_display(rec, reader, client, docs, no_physical):
    """Runs known viewer PUT with checked HTTP codes and real human observations."""
    pdfs = [d for d in docs if isinstance(d, dict) and d.get("entry_type") == "document" and
            str(d.get("entry_path", "")).lower().endswith(".pdf") and d.get("entry_id")]
    rec.event(reader, "display_target_discovery", "ok" if pdfs else "no_pdf", count=len(pdfs),
              note="Raw file names omitted from the report")
    if not pdfs or no_physical:
        rec.event(reader, "display_demo", "skipped", reason="--no-physical or no PDF")
        return
    print("\n將用現有 PDF 測試（不刪除或編輯檔案）：")
    print("  測試 A = 第一份 PDF 第 1 頁（裝置醒著時）")
    print("  測試 B = 另一份 PDF 第 1 頁，或原 PDF 第 2 頁（休眠時）")
    print("  報告只記錄 document#1/#2，不包含私人檔名。")
    if not yes_no("允許本程式送出『打開 PDF』命令？裝置當前文件畫面可能改變。", default=False):
        rec.event(reader, "display_demo", "skipped", reason="permission not granted")
        return
    first = pdfs[0]
    second = pdfs[1] if len(pdfs)>1 else first
    second_page = 1 if second is not first else 2
    pause("請確保 mooInk 醒著、螢幕可見。確認後按 Enter：")
    _display_with_details(rec, reader, client, first, 1, "awake", "document#1")
    a = observation("醒著時，螢幕是否切到第一份 PDF 第 1 頁？")
    rec.event(reader, "awake_display_screen_observed", a, source="human")
    print("\n請短按實體電源鍵讓 mooInk 進入休眠，並保持 Wi-Fi 運作。")
    pause("螢幕已顯示休眠圖後，按 Enter 繼續：")
    probe_network(rec, reader, reader["lan_ip"], "sleep", 3.0)
    command_demo(rec, reader, client, "sleep")
    _display_with_details(rec, reader, client, second, second_page, "sleep", "document#2")
    b = observation("休眠中，命令是否立即喚醒電子紙並顯示指定第二份 PDF／頁碼？")
    rec.event(reader, "sleep_display_screen_observed", b, source="human",
              interpretation="Only human observation can prove physical e-paper wake")
    if b != "yes":
        pause("請短按實體電源鍵手動喚醒，然後按 Enter：")
        c = observation("手動喚醒後，是否出現休眠期間指定的第二份 PDF／頁碼？")
        rec.event(reader, "delayed_display_after_manual_wake", c, source="human")


def _display_with_details(rec, reader, d, doc, page, phase, label):
    purpose = "Request document display; NOT a confirmed hardware wake command"
    print("  PUT /viewer/controls/open2 (%s page %d)" % (label, page))
    try:
        resp = d._put_endpoint("/viewer/controls/open2", data={"document_id":doc["entry_id"],"page":page})
        state = "ok" if 200<=resp.status_code<300 else "rejected"
        print("  -> HTTP %d" % resp.status_code)
        rec.event(reader, "display_" + phase, state, method="PUT",
                  path="/viewer/controls/open2", purpose=purpose,
                  target=label, page=page, http_status=resp.status_code,
                  interpretation="HTTP response alone does not prove panel change")
    except Exception as exc:
        rec.event(reader, "display_" + phase, "error", method="PUT",
                  path="/viewer/controls/open2", error=safe_key_error(exc))


def demo_main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("ip_positional", nargs="?", help="設備 LAN IPv4，如 192.168.1.137")
    parser.add_argument("--ip", help="與位置參數等效；不綁 USB 序號")
    parser.add_argument("--no-wpd", action="store_true", help="略過 USB/WPD 診斷")
    parser.add_argument("--no-physical", action="store_true", help="只跑全自動唯讀檢測，不等待實際畫面測試")
    parser.add_argument("--timeout", type=float, default=8.0, help="HTTP 請求逾時，預設 8 秒")
    parser.add_argument("--output-dir", default="mooink_reports", help="報告儲存目錄")
    args = parser.parse_args(argv)
    if platform.system() != "Windows":
        parser.error("This program requires Windows (WPD + DPAPI)")
    if args.ip and args.ip_positional and args.ip != args.ip_positional:
        parser.error("位置參數 IP 與 --ip 不一致")
    raw_ip = args.ip or args.ip_positional
    if not raw_ip:
        parser.error("必須提供裝置 IP，例如 python mooink_full_cmd_demo.py --ip 192.168.1.137")
    try:
        ip = require_private_ipv4(raw_ip)
    except ValueError as exc:
        parser.error(str(exc))
    if not 1 <= args.timeout <= 30:
        parser.error("--timeout 必須介於 1~30")

    heading("mooInk Full Command Demo " + DEMO_VERSION)
    print("自動展示 USB、WPD、公開 API、授權 API；會改變畫面的操作需人員確認。")
    print("兩台 USB 皆列舉；本次 Wi-Fi 只測命令列指定的 IP，不綁任何固定序號。")
    print("不修改韌體 / USB driver / MTP 檔案 / 休眠時間。")
    print("\n[藍牙喚醒研究紀錄 — 僅文字說明]")
    for note in BLUETOOTH_WAKE_NOTES:
        print("  - " + note)
    rec = DemoRecorder(args.output_dir)
    reader = rec.start_reader("mooInk Wi-Fi " + ip, None)
    reader["lan_ip"] = ip
    rec.save()
    try:
        usb = auto_usb_demo(rec, reader, skip_wpd=args.no_wpd)
        rec.event(reader, "device_selection", "ok", requested_ip=ip,
                  usb_device_count=len(usb),
                  note="Cannot automatically correlate LAN IP to a given USB device")
        if not args.no_physical:
            pause("請先讓 IP=%s 的 mooInk 保持喚醒；確認後按 Enter：" % ip)
        info = probe_network(rec, reader, ip, "awake", args.timeout)
        # The public info includes serial but it is used ONLY to create a hash
        # for per-device encrypted credentials, never stored in the report.
        if not info or not info.get("serial_number"):
            rec.event(reader, "identity", "error",
                      reason="Public information not obtainable; refusing to guess identity")
            return 2
        reader["network_device"] = {
            "fingerprint": fingerprint(info)[:12],
            "model": str(info.get("model_name", "unknown"))[:80],
        }
        rec.save()
        print("\n網路裝置已動態識別：" + reader["network_device"]["fingerprint"])
        d = auth_session(rec, reader, ip, info, args.timeout)
        if d is None:
            rec.event(reader, "authenticated_tests", "skipped", reason="not paired/authenticated")
            return 3
        docs = command_demo(rec, reader, d, "awake")
        demo_display(rec, reader, d, docs, args.no_physical)
        if not args.no_physical:
            # No automated deletion or copy to device; the user has multiple
            # identical-looking WPD devices and mistaken deletion is dangerous.
            if yes_no("要進行 USB sleep 待機圖手動替換驗證嗎？（必須事先備份）", default=False):
                # Ask which physical MTP device rather than guessing its serial
                pnp = pnp_scan().get("devices", [])
                chosen = choose(pnp, "請指定要操作的 USB 裝置", allow_skip=True,
                                description=lambda x: "%s / USB ...%s" %
                                (x.get("Name","mooInk"), usb_tail(x.get("InstanceId", ""))))
                if chosen:
                    stage_sleep_image(rec, reader, chosen)
                else:
                    rec.event(reader, "sleep_image", "skipped_no_usb_selection")
            else:
                rec.event(reader, "sleep_image", "skipped_user_choice")
        else:
            rec.event(reader, "sleep_image", "skipped", reason="--no-physical")
        rec.event(reader, "wake_capability", "not_confirmed",
                  note="Direct Wi-Fi/API wake and immediate standby remain unverified. "
                       "User-reported Bluetooth keyboard wake is documented separately.")
        return 0
    except (StopReader, KeyboardInterrupt, EOFError):
        rec.event(reader, "user_stop", "partial", note="User ended manual portion")
        return 130
    except Exception as exc:
        rec.event(reader, "unhandled", "error", error=safe_key_error(exc))
        print("發生例外：" + safe_key_error(exc))
        return 1
    finally:
        rec.report["ended_utc"] = timestamp()
        rec.save()
        print("\n已儲存詳細 TXT：" + str(rec.text_file.resolve()))
        print("已儲存結構化 JSON：" + str(rec.file.resolve()))
        print("隱私：報告不含私鑰、配對 PIN、設備完整序號、文件檔名。")


if __name__ == "__main__":
    sys.exit(demo_main())