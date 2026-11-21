#!/usr/bin/env python3
"""Read-only Sony Digital Paper-compatible service probe for mooInk Pro.

Windows / Python 3.9+, no third-party dependencies.
Only TCP connections and two unauthenticated HTTP GET requests.
No pairing, file modifications, power commands or display commands.
"""
import argparse
import ipaddress
import json
import socket
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import build_opener, ProxyHandler, HTTPRedirectHandler, Request


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


def tcp_test(ip, port, timeout):
    try:
        with socket.create_connection((ip, port), timeout=timeout):
            return {"open": True}
    except (OSError, TimeoutError) as exc:
        return {"open": False, "error": type(exc).__name__}


def http_test(opener, url, timeout):
    req = Request(url, headers={"User-Agent": "mooink-research-readonly/1.0"})
    try:
        with opener.open(req, timeout=timeout) as response:
            code = response.status
            content_type = response.headers.get("Content-Type", "")
            payload = response.read(16384)
    except HTTPError as exc:
        return {"reachable": True, "http_status": exc.code}
    except (URLError, OSError, TimeoutError, ValueError) as exc:
        return {"reachable": False, "error": type(exc).__name__}
    info = {"reachable": True, "http_status": code, "content_type": content_type}
    try:
        data = json.loads(payload)
        if isinstance(data, dict):
            info["json_keys_only"] = sorted(data.keys())
        else:
            info["json_type"] = type(data).__name__
    except (ValueError, UnicodeDecodeError):
        info["json"] = False
    return info


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ip", required=True, help="mooInk private IPv4 address")
    parser.add_argument("--state", required=True, choices=["awake", "sleep"])
    parser.add_argument("--timeout", type=float, default=2.5)
    args = parser.parse_args()
    try:
        addr = ipaddress.ip_address(args.ip)
    except ValueError:
        parser.error("enter a valid IPv4 address")
    if addr.version != 4 or not (addr.is_private or addr.is_link_local) or addr.is_loopback:
        parser.error("only private LAN IPv4 addresses accepted")
    if not 0.5 <= args.timeout <= 10:
        parser.error("timeout must be between 0.5 and 10 seconds")

    ip = str(addr)
    output = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "device_ip": ip,
        "reported_state": args.state,
        "tcp": {},
        "http": {},
        "note": "Network discovery only; no wake or sleep commands are sent."
    }
    opener = build_opener(ProxyHandler({}), NoRedirect())
    for port in (8080, 8443):
        output["tcp"][str(port)] = tcp_test(ip, port, args.timeout)
    if output["tcp"]["8080"]["open"]:
        for p in ("/register/information", "/api_version"):
            output["http"][p] = http_test(opener, "http://%s:8080%s" % (ip, p), args.timeout)
    else:
        output["http"]["skipped"] = "port 8080 unreachable"
    result = json.dumps(output, ensure_ascii=False, indent=2)
    print(result)
    filename = Path("sony_probe_%s.json" % args.state)
    filename.write_text(result + "\n", encoding="utf-8")
    print("\nSaved:", filename.resolve())


if __name__ == "__main__":
    main()
