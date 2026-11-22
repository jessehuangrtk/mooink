#!/usr/bin/env python3
"""Plain USB CDC ASCII sender. No firmware commands or control protocol."""
from __future__ import annotations
import argparse

def ports():
    from serial.tools import list_ports
    return list(list_ports.comports())

def send_text(port, value, *, serial_factory=None):
    # Validate everything before writing; no accidental partial Unicode input.
    if not value or any(not (32 <= ord(c) <= 126 or c in "\r\n\t\b") for c in value):
        raise ValueError("僅支援 ASCII 英數、標點及 Enter/Tab/Backspace")
    data = value.encode("ascii")
    if serial_factory is None:
        import serial
        serial_factory = serial.Serial
    with serial_factory(port, 115200, timeout=.1, write_timeout=2) as ser:
        if ser.write(data) != len(data):
            raise OSError("CDC 寫入長度不符")
        ser.flush()
    # Success here means host CDC write only; no HID acknowledgement.
    return len(data)

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", default="COM6")
    parser.add_argument("--text", help="直接傳送 ASCII；不附加換行")
    args = parser.parse_args()
    if args.text is None:
        parser.print_help()
        return
    print("CDC bytes written:", send_text(args.port, args.text))

if __name__ == "__main__":
    main()
