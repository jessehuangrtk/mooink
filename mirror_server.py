#!/usr/bin/env python3
"""Small token-protected LAN frame receiver. Not a mooInk device-side receiver."""
import argparse
import hmac
import json
import secrets
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

MAX_FRAME = 4 * 1024 * 1024
VIEWER = Path(__file__).with_name("viewer.html")


class FrameStore:
    def __init__(self):
        self._lock = threading.Lock()
        self._data = None
        self._seq = 0
        self._when = 0.0

    def save(self, data):
        with self._lock:
            self._data = data
            self._seq += 1
            self._when = time.time()
            return self._seq

    def snapshot(self):
        with self._lock:
            return self._data, self._seq, self._when

    def status(self):
        data, seq, when = self.snapshot()
        return {"seq": seq, "bytes": len(data) if data else 0,
                "updated_unix": when, "has_frame": data is not None}


class MirrorServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, token):
        super().__init__(address, MirrorHandler)
        self.token = token
        self.frames = FrameStore()


class MirrorHandler(BaseHTTPRequestHandler):
    server_version = "MooInkMirror/0.1"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        # Do not log authentication tokens embedded in URLs.
        print("%s - %s" % (self.address_string(), fmt.split("?")[0]))

    def _path_and_authorize(self):
        parsed = urlsplit(self.path)
        values = parse_qs(parsed.query)
        provided = values.get("token", [""])[0]
        return parsed.path, hmac.compare_digest(provided, self.server.token)

    def _reply(self, status, body=b"", mime="text/plain; charset=utf-8"):
        self.send_response(status)
        self.send_header("Content-Type", mime)
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body:
            self.wfile.write(body)

    def _json(self, status, obj):
        self._reply(status, json.dumps(obj, separators=(",", ":")).encode("utf-8"),
                    "application/json; charset=utf-8")

    def do_GET(self):
        path, authorized = self._path_and_authorize()
        if path == "/health":
            self._json(200, {"ok": True, "service": "mooink-mirror"})
            return
        if not authorized:
            self._json(401, {"error": "missing or invalid token"})
            return
        if path in ("/", "/viewer"):
            try:
                body = VIEWER.read_bytes()
            except OSError:
                self._json(500, {"error": "viewer.html is missing"})
                return
            self._reply(200, body, "text/html; charset=utf-8")
        elif path == "/api/status":
            self._json(200, self.server.frames.status())
        elif path == "/frame.jpg":
            data, _, _ = self.server.frames.snapshot()
            if data is None:
                self._json(404, {"error": "no frame yet"})
            else:
                self._reply(200, data, "image/jpeg")
        else:
            self._json(404, {"error": "not found"})

    def do_POST(self):
        path, authorized = self._path_and_authorize()
        if not authorized:
            self._json(401, {"error": "missing or invalid token"})
            return
        if path != "/api/frame":
            self._json(404, {"error": "not found"})
            return
        if self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower() != "image/jpeg":
            self._json(415, {"error": "expect image/jpeg"})
            return
        try:
            size = int(self.headers.get("Content-Length", ""))
        except ValueError:
            size = 0
        if not 4 <= size <= MAX_FRAME:
            self._json(413, {"error": "frame must be 4 bytes to 4 MiB"})
            return
        self.connection.settimeout(10)
        try:
            data = self.rfile.read(size)
        except (OSError, TimeoutError):
            self.close_connection = True
            return
        if len(data) != size or not data.startswith(bytes.fromhex("ffd8")) or not data.endswith(bytes.fromhex("ffd9")):
            self._json(400, {"error": "invalid or truncated JPEG"})
            return
        seq = self.server.frames.save(data)
        self._json(200, {"ok": True, "seq": seq})


def local_ipv4():
    # Select a local interface without sending data to the external address.
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        try:
            sock.connect(("192.0.2.1", 9))
            return sock.getsockname()[0]
        except OSError:
            return "127.0.0.1"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="0.0.0.0",
                        help="Bind address (use 127.0.0.1 for local-only testing)")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--token", default=None,
                        help="Shared secret; random token if omitted")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("port must be 1..65535")
    if args.token is not None and len(args.token) < 12:
        parser.error("--token must have at least 12 characters")
    token = args.token or secrets.token_urlsafe(24)
    with MirrorServer((args.host, args.port), token) as server:
        host = local_ipv4() if args.host == "0.0.0.0" else args.host
        base = "http://%s:%d" % (host, args.port)
        print("mooInk Mirror LAN receiver (experimental)")
        print("Token: %s" % token)
        print("Viewer: %s/viewer?token=%s" % (base, token))
        print("Sender: %s" % base)
        print("This is not a native mooInk receiver. Press Ctrl+C to stop.", flush=True)
        try:
            server.serve_forever(poll_interval=0.2)
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
