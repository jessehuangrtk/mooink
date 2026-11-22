import unittest
from pico2w_usb2bluetooth import PicoKeyboard, key_code, shortcut, text_codes

class FakeSerial:
    def __init__(self, port, *args, **kwargs):
        self.is_open = True
        self.buffer = bytearray()
        self.commands = []

    @property
    def in_waiting(self):
        return len(self.buffer)

    def reset_input_buffer(self):
        self.buffer.clear()

    def read(self, n):
        res = bytes(self.buffer[:n])
        del self.buffer[:n]
        return res

    def close(self):
        self.is_open = False

    def write(self, data):
        cmd = data.decode("ascii").strip()
        self.commands.append(cmd)
        if data == b"\x1d\n":
            response = "OK MODE COMMAND"
        elif cmd == "HELLO":
            response = "OK HELLO MOOINK_PICO2W_BT/1"
        elif cmd == "STATUS":
            response = "OK STATUS ready=1 paired=1 connected=1 pairing=0 connecting=0 peer=AA:BB:CC:DD:EE:FF raw_echo=1"
        elif cmd == "MODE KEYBOARD ACK":
            response = "OK MODE KEYBOARD ACK"
        elif data == b" ":
            response = "OK RAW SENT ASCII=0x20 HID=0x2C"
        elif data == b"\x1d":
            response = "OK MODE COMMAND"
        elif cmd.startswith("KEY "):
            response = "EVENT diagnostic\r\nOK KEY SENT"
        elif cmd.startswith("KEYH "):
            response = "EVENT KEY DOWN\r\nEVENT KEY UP\r\nOK KEYH SENT"
        else:
            response = "ERR UNKNOWN_COMMAND"
        self.buffer.extend((response + "\r\n").encode())
        return len(data)


class KeyboardTests(unittest.TestCase):
    def test_letters_digits_and_navigation(self):
        self.assertEqual(key_code("a"), (0, 4))
        self.assertEqual(key_code("Z"), (2, 29))
        self.assertEqual(key_code("1"), (0, 30))
        self.assertEqual(key_code("0"), (0, 39))
        self.assertEqual(key_code("f12"), (0, 69))
        self.assertEqual(key_code("space"), (0, 44))
        self.assertEqual(key_code("pageup"), (0, 75))

    def test_symbols_and_shortcuts(self):
        self.assertEqual(shortcut("ctrl+shift+a"), (3, 4))
        self.assertEqual(shortcut("+"), (2, 46))
        self.assertEqual(key_code("?"), (2, 56))
        self.assertEqual(key_code("|"), (2, 49))
        self.assertEqual(text_codes("A\n"), [(2, 4), (0, 40)])
        with self.assertRaises(ValueError):
            text_codes("A中文")

    def test_handshake_and_explicit_tap(self):
        received = []
        with PicoKeyboard("COM6", serial_factory=FakeSerial, event=received.append) as board:
            self.assertEqual(board.ser.commands, ["", "HELLO"])
            self.assertEqual(board.status()["connected"], "1")
            self.assertEqual(board.wake("f12"), "OK KEY SENT")
            self.assertEqual(board.ser.commands[-1], "KEY 0 69")
            self.assertIn("EVENT diagnostic", received)
        self.assertFalse(board.ser.is_open)

    def test_old_firmware_escape_is_safe(self):
        class Old(FakeSerial):
            def write(self, data):
                if data == b"\x1d\n":
                    self.buffer.extend(b"ERR LINE_TOO_LONG\r\n")
                    return len(data)
                return super().write(data)
        with PicoKeyboard("COM6", serial_factory=Old) as board:
            self.assertEqual(board.wake("space"), "OK KEY SENT")

    def test_raw_space_uses_same_ascii_as_terminal(self):
        with PicoKeyboard("COM6", serial_factory=FakeSerial) as board:
            self.assertEqual(board.raw_key(" "), "OK RAW SENT ASCII=0x20 HID=0x2C")
            self.assertEqual(board.ser.commands[-3:], ["MODE KEYBOARD ACK","",""])
            self.assertNotIn("KEY 0 44",board.ser.commands)

    def test_long_space_diagnostic(self):
        received = []
        with PicoKeyboard("COM6", serial_factory=FakeSerial, event=received.append) as board:
            self.assertEqual(board.wake("space", hold_ms=500), "OK KEYH SENT")
            self.assertEqual(board.ser.commands[-1], "KEYH 0 44 500")
            self.assertIn("EVENT KEY DOWN", received)
            self.assertIn("EVENT KEY UP", received)
            with self.assertRaises(ValueError):
                board.wake("space", hold_ms=2001)

    def test_invalid_text_does_not_send_partial(self):
        with PicoKeyboard("COM6", serial_factory=FakeSerial) as board:
            with self.assertRaises(ValueError):
                board.type_text("abc中文")
            self.assertEqual(board.ser.commands, ["", "HELLO"])

    def test_error_not_success(self):
        with PicoKeyboard("COM6", serial_factory=FakeSerial) as board:
            with self.assertRaisesRegex(RuntimeError, "ERR UNKNOWN_COMMAND"):
                board.command("UNKNOWN")

    def test_reject_wrong_firmware(self):
        class Wrong(FakeSerial):
            def write(self, data):
                self.buffer.extend(b"OK MODE COMMAND\r\n" if data == b"\x1d\n" else b"OK HELLO other\r\n")
        with self.assertRaisesRegex(RuntimeError, "不是相容"):
            PicoKeyboard("COM6", serial_factory=Wrong)


if __name__ == "__main__":
    unittest.main()
