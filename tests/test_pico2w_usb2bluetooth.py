import unittest
from pico2w_usb2bluetooth import send_text

class FakeSerial:
    def __init__(self,*args,**kwargs):
        self.writes=[]
        self.flushed=False
        self.closed=False
    def __enter__(self):return self
    def __exit__(self,*args):self.closed=True
    def write(self,data):
        self.writes.append(data)
        return len(data)
    def flush(self):self.flushed=True

class KeyboardTests(unittest.TestCase):
    def test_space_only_no_command_or_newline(self):
        ser=FakeSerial()
        self.assertEqual(send_text("COM6"," ",serial_factory=lambda *a,**kw:ser),1)
        self.assertEqual(ser.writes,[b" "])
        self.assertTrue(ser.flushed)
        self.assertTrue(ser.closed)
    def test_literal_words_not_commands(self):
        ser=FakeSerial()
        send_text("COM6","STATUS\r",serial_factory=lambda *a,**kw:ser)
        self.assertEqual(ser.writes,[b"STATUS\r"])
    def test_reject_non_ascii_before_open(self):
        def bad(*args,**kwargs):self.fail("Must validate before opening COM")
        with self.assertRaises(ValueError):send_text("COM6","a中文",serial_factory=bad)
    def test_short_write_fails(self):
        class Short(FakeSerial):
            def write(self,data):return 0
        with self.assertRaises(OSError):send_text("COM6"," ",serial_factory=Short)
