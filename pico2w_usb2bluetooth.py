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

def send_space(port, *, serial_factory=None):
    """Send exactly 0x20 and observe the normal terminal echo.
    No configuration, handshake, control command, newline or retry.
    A matching echo is CDC reception evidence, not a HID delivery ACK.
    """
    if serial_factory is None:
        import serial
        serial_factory = serial.Serial
    with serial_factory(port, 115200, timeout=.5, write_timeout=2) as ser:
        ser.reset_input_buffer()
        if ser.write(b" ") != 1:
            raise OSError("CDC Space 寫入長度不符")
        ser.flush()
        response = ser.read(256)
    if response == b" ":
        return True
    if b"Bluetooth not connected" in response:
        raise RuntimeError("Pico 藍牙尚未連線；Space 未送達")
    if b"Keyboard queue full" in response:
        raise RuntimeError("Pico 鍵盤佇列滿；Space 未送達")
    if not response:
        raise TimeoutError("已寫入 CDC，但未收到 Space 原字元回顯；請手動測試")
    # Other console messages may be asynchronous. Do not mistake a space in
    # their prose for the echo of our key, or print possible pairing data.
    raise RuntimeError("CDC 回覆並非單一 Space，無法確認接收；請用 Tera Term 檢查")


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
