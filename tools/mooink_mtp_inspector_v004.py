#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""mooInk MTP Inspector v0.4 -- Windows read-only WPD/MTP discovery.

Requires Python 3.9+ and comtypes. Does NOT change USB drivers, execute
vendor-specific MTP opcodes, modify files/settings, or issue power commands.

Default: request GENERIC_READ; query WPD capabilities.  An optional explicit
--rw-vendor-query requests both READ and WRITE access solely to run the
Microsoft-defined supported-vendor-opcodes metadata query (NOT any vendor opcode).
This may still return E_ACCESSDENIED if the driver restricts it.
"""

from __future__ import annotations

import argparse
import ctypes
import importlib
import json
from pathlib import Path
import platform
import re
import subprocess
import traceback
import struct
import sys
from datetime import datetime, timezone

TARGET_VID = "1F85"
TARGET_PID = "4E16"
COMMON_GUID = "{F0422A9C-5DC8-4440-B5BD-5DF28835658A}"
MTP_GUID = "{4D545058-1A2E-4106-A357-771E0819FC56}"
CLIENT_GUID = "{204D9F0C-2292-4080-9F42-40664E70F859}"
GENERIC_READ = 0x80000000


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


def format_report(report):
    lines = ["mooInk Pro USB / MTP read-only / metadata inspector v0.4", "=" * 56,
             "Timestamp: " + report["timestamp_utc"],
             "Target VID/PID: " + TARGET_VID + "/" + TARGET_PID]
    ps = report.get("pnp", {})
    for d in ps.get("devices", []):
        lines.append("PnP: %s | %s | %s | %s" % (
            d.get("Name"), d.get("Class"), d.get("Status"), d.get("Service")))
        lines.append("  ID: " + str(d.get("InstanceId")))
    if ps.get("error"):
        lines.append("PnP scan failed: " + ps["error"])
    if report.get("wpd_error_traceback"):
        lines += ["", "WPD exception traceback:", report["wpd_error_traceback"]]
    for device in report.get("wpd", []):
        lines += ["", "WPD Device: " + device["device_id"]]
        if device.get("error"):
            lines.append("  ERROR: " + device["error"])
            if device.get("traceback"):
                lines.extend(("  Traceback:", device["traceback"]))
            continue
        lines.append("  Access requested: " + device.get("access_requested", "unknown"))
        status = device.get("wpd_commands_status", "unavailable")
        commands = device.get("wpd_commands", [])
        count = str(len(commands)) if status == "ok" else "UNKNOWN"
        lines.append("  WPD supported commands: %s (%s)" % (count, status))
        for cmd in commands:
            lines.append("    %s / %d" % (cmd["category"], cmd["id"]))
        if device.get("wpd_capabilities_error"):
            lines.append("  Capabilities error: " + device["wpd_capabilities_error"])
            lines.append("  Traceback:\n" + device.get("wpd_capabilities_traceback", ""))
        lines.append("  Vendor command advertised: " + str(device.get("vendor_command_advertised")))
        vendor = device.get("mtp_vendor_operations", {})
        codes = vendor.get("codes")
        lines.append("  MTP vendor opcode status: " + vendor.get("status", "unknown"))
        lines.append("  MTP vendor operation codes: " +
                     (str(len(codes)) if codes is not None else "UNKNOWN"))
        for code in codes or []:
            lines.append("    " + code)
        if vendor.get("error"):
            lines.append("  Vendor query error: " + vendor["error"])
        if vendor.get("reason"):
            lines.append("  Vendor query skipped: " + vendor["reason"])
        if vendor.get("note"):
            lines.append("  Note: " + vendor["note"])
    for warning in report.get("warnings", []):
        lines.append("NOTE: " + warning)
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--basic", action="store_true", help="PnP only: no comtypes or WPD access")
    parser.add_argument("--no-vendor-query", action="store_true",
                        help="Only WPD capabilities; skip the MTP metadata query")
    parser.add_argument("--force-vendor-query", action="store_true",
                        help="Request Microsoft vendor-code metadata even if WPD command 11 not advertised")
    parser.add_argument("--rw-vendor-query", action="store_true",
                        help="Opt in to READ|WRITE handle permissions, but execute NO modifying command; "
                             "only inspect supported WPD/MTP metadata")
    parser.add_argument("--show-serial", action="store_true",
                        help="Include full USB instance IDs (private: contain serial number)")
    parser.add_argument("--out", default="mooink_mtp_report", help="File base (no suffix)")
    args = parser.parse_args()
    if args.rw_vendor_query and args.no_vendor_query:
        parser.error("--rw-vendor-query conflicts with --no-vendor-query")
    if platform.system() != "Windows":
        parser.error("Please run on Windows 10/11 with mooInk attached via USB")
    print("mooInk USB / MTP Inspector v0.4 -- read only")
    print("Scanning PnP for VID_%s / PID_%s ..." % (TARGET_VID, TARGET_PID), flush=True)
    report = {"timestamp_utc": datetime.now(timezone.utc).isoformat(),
              "target": {"vid": TARGET_VID, "pid": TARGET_PID},
              "pnp": pnp_scan(), "wpd": [], "warnings": []}
    for device in report["pnp"].get("devices", []):
        device["InstanceId"] = clean_device_id(device.get("InstanceId", ""), args.show_serial)
    if not args.basic:
        print("Opening WPD read-only...", flush=True)
        stage = "load WPD COM type libraries"
        try:
            wpd = load_wpd()
            stage = "enumerate WPD devices"
            ids = enumerate_wpd(wpd[1], wpd[2])
            targets = [x for x in ids if ("VID_" + TARGET_VID) in x.upper()
                       and ("PID_" + TARGET_PID) in x.upper()]
            if not targets:
                report["warnings"].append("No target WPD device found; check connection and Windows Explorer")
            for device_id in targets:
                print("Reading WPD capabilities for %s ..." % clean_device_id(device_id), flush=True)
                try:
                    device_report = inspect_device(device_id, wpd=wpd,
                                                   show_id=args.show_serial,
                                                   vendor_query=not args.no_vendor_query,
                                                   rw_vendor_query=args.rw_vendor_query,
                                                   force_vendor_query=args.force_vendor_query)
                except Exception as exc:
                    device_report = {"device_id": clean_device_id(device_id, args.show_serial),
                                     "error": type(exc).__name__ + ": " + str(exc),
                                     "traceback": traceback.format_exc(limit=7)}
                report["wpd"].append(device_report)
        except Exception as exc:
            report["warnings"].append("WPD failed at %s: %s: %s" % (
                stage, type(exc).__name__, exc))
            report["wpd_error_traceback"] = traceback.format_exc(limit=8)
    stem = Path(args.out)
    txt_path = stem.with_suffix(".txt")
    json_path = stem.with_suffix(".json")
    text = format_report(report)
    print("\n" + text)
    txt_path.write_text(text, encoding="utf-8")
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("Saved: %s, %s" % (txt_path.resolve(), json_path.resolve()))
    if not report["pnp"].get("devices"):
        print("No matching PnP device found. Connect mooInk by USB first.")
    if report["warnings"] or any(
        "error" in x or x.get("wpd_commands_status") == "error"
        or x.get("mtp_vendor_operations", {}).get("status") in {"error", "access_denied"}
        for x in report["wpd"]
    ):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())