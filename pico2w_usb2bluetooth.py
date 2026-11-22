#!/usr/bin/env python3
"""Windows USB CDC -> Pico 2 W -> paired Bluetooth Classic keyboard.
No global keyboard hooks, no background keystrokes. Python 3.9+ / pyserial.
"""
from __future__ import annotations

import argparse
import queue
import threading
import time

TARGET_IP = "192.168.1.140"  # Wi-Fi reference only; never used for HID transport.
MAGIC = "OK HELLO MOOINK_PICO2W_BT/1"

KEYS = {chr(97+i): 4+i for i in range(26)}
KEYS.update({str(i): 29+i for i in range(1, 10)})
KEYS["0"] = 39
KEYS.update({
    "return": 40, "enter": 40, "escape": 41, "esc": 41,
    "backspace": 42, "tab": 43, "space": 44, "minus": 45,
    "equal": 46, "bracketleft": 47, "bracketright": 48,
    "backslash": 49, "semicolon": 51, "apostrophe": 52,
    "grave": 53, "comma": 54, "period": 55, "slash": 56,
    "caps_lock": 57, "print": 70, "scroll_lock": 71, "pause": 72,
    "insert": 73, "home": 74, "prior": 75, "pageup": 75,
    "delete": 76, "end": 77, "next": 78, "pagedown": 78,
    "right": 79, "left": 80, "down": 81, "up": 82,
})
KEYS.update({"f%d" % (i+1): 58+i for i in range(12)})
SHIFT_SYMBOL = dict(zip('!@#$%^&*()_+{}|:"~<>?', '1234567890-=[]\\;\'\x60,./'))
SYMBOL = dict(zip("-=[]\\;'\x60,./", [
    "minus","equal","bracketleft","bracketright","backslash","semicolon",
    "apostrophe","grave","comma","period","slash",
]))
TK_SYMBOL = {
    "exclam":"!", "at":"@", "numbersign":"#", "dollar":"$", "percent":"%",
    "asciicircum":"^", "ampersand":"&", "asterisk":"*", "parenleft":"(",
    "parenright":")", "underscore":"_", "plus":"+", "braceleft":"{",
    "braceright":"}", "bar":"|", "colon":":", "quotedbl":'"',
    "asciitilde":"~", "less":"<", "greater":">", "question":"?",
}
MODS = {"ctrl": 1, "control": 1, "shift": 2, "alt": 4, "win": 8,
        "gui": 8, "rctrl": 16, "rshift": 32, "ralt": 64, "rwin": 128}


def key_code(key):
    """A named key or one US-layout character -> (modifier, HID usage)."""
    if key in SHIFT_SYMBOL:
        base = SHIFT_SYMBOL[key]
        return 2, KEYS[SYMBOL.get(base, base)]
    if key in SYMBOL:
        return 0, KEYS[SYMBOL[key]]
    if key == " ":
        return 0, KEYS["space"]
    if len(key) == 1 and "A" <= key <= "Z":
        return 2, KEYS[key.lower()]
    name = key.lower()
    if name not in KEYS:
        raise ValueError("不支援的按鍵：" + repr(key))
    return 0, KEYS[name]


def shortcut(value):
    # '+' by itself is a literal plus character.
    if value == "+":
        return key_code(value)
    parts = value.split("+")
    mod = 0
    for word in parts[:-1]:
        if word.lower() not in MODS:
            raise ValueError("不支援的修飾鍵：" + word)
        mod |= MODS[word.lower()]
    extra, usage = key_code(parts[-1])
    return mod | extra, usage


def text_codes(value):
    result = []
    for char in value:
        if char == "\n":
            result.append(key_code("enter"))
        elif char == "\t":
            result.append(key_code("tab"))
        else:
            result.append(key_code(char))
    return result


def ports():
    from serial.tools import list_ports
    return list(list_ports.comports())


def auto_port():
    candidates = [p.device for p in ports() if p.vid == 0x2e8a]
    if len(candidates) != 1:
        raise RuntimeError("找不到唯一 Pico USB COM 埠；請使用 --port COM6 等明確指定")
    return candidates[0]


class PicoKeyboard:
    """Synchronous serial session; use from a single worker/thread."""
    def __init__(self, port=None, *, event=None, serial_factory=None):
        self.event = event or (lambda message: None)
        self._buffer = bytearray()
        if serial_factory is None:
            import serial
            serial_factory = serial.Serial
        self.port = port or auto_port()
        self.ser = serial_factory(self.port, 115200, timeout=0.10, write_timeout=2)
        try:
            self.ser.reset_input_buffer()
            # Exit direct terminal mode before sending any printable command.
            # Old firmware treats Ctrl+] as a bad line, which is also safe.
            self.ser.write(b"\x1d\n")
            deadline = time.monotonic() + 3
            while True:
                response = self._line(deadline)
                if response is None:
                    raise TimeoutError("Pico 無法切回 COMMAND 模式")
                if response in ("OK MODE COMMAND", "ERR LINE_TOO_LONG"):
                    break
                if response:
                    self.event(response)
            result = self.command("HELLO", timeout=3)
            if result != MAGIC:
                raise RuntimeError("COM 埠不是相容的 mooInk Pico 2W 韌體：" + result)
        except BaseException:
            self.close()
            raise

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def close(self):
        self.ser.close()

    def _line(self, deadline):
        while time.monotonic() < deadline:
            i = self._buffer.find(b"\n")
            if i >= 0:
                raw = bytes(self._buffer[:i])
                del self._buffer[:i+1]
                return raw.decode("utf-8", errors="replace").strip()
            data = self.ser.read(min(4096, max(1, self.ser.in_waiting)))
            self._buffer.extend(data)
            if len(self._buffer) > 8192:
                self._buffer.clear()
                self.event("ERR serial line overflow")
        return None

    def poll(self, seconds=.15):
        deadline = time.monotonic() + seconds
        while True:
            line = self._line(deadline)
            if line is None:
                return
            if line:
                self.event(line)

    def command(self, command, timeout=8):
        if not command or "\r" in command or "\n" in command or len(command) >= 100:
            raise ValueError("無效命令")
        self.ser.write((command + "\n").encode("ascii"))
        deadline = time.monotonic() + timeout
        verb = command.split(" ", 1)[0]
        while True:
            line = self._line(deadline)
            if line is None:
                self.close()  # unknown outstanding request: no automatic retry
                raise TimeoutError("Pico 回覆逾時；已關閉 COM，請檢查後重新連線")
            if line == "OK " + verb or line.startswith("OK " + verb + " "):
                return line
            if line.startswith("ERR "):
                raise RuntimeError(line)
            if line:
                self.event(line)

    def status(self):
        return dict(x.split("=", 1) for x in self.command("STATUS").split()[2:])

    def ensure_connected(self, timeout=24):
        status = self.status()
        if status.get("connected") == "1":
            return
        if status.get("paired") != "1":
            raise RuntimeError("Pico 尚未配對：先開啟 PAIR，從 mooInk 藍牙設定選擇鍵盤")
        self.command("CONNECT")
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.poll(.25)
            status = self.status()
            if status.get("connected") == "1":
                return
            if status.get("connecting") != "1":
                break
        raise RuntimeError("藍牙 HID 尚未連線；mooInk 可能需先手動喚醒或重新連線")

    def send(self, modifier, usage):
        if not 0 <= modifier <= 255 or not 4 <= usage <= 231:
            raise ValueError("HID usage/modifier 超出範圍")
        return self.command("KEY %d %d" % (modifier, usage), timeout=8)

    def key(self, value):
        return self.send(*shortcut(value))

    def wake(self, key="f12", wait=0, *, hold_ms=None):
        # Ordinary HID tap, not proof of a physical wake. Longer diagnostic
        # taps require the updated firmware and never silently fall back.
        if hold_ms is not None and (not isinstance(hold_ms, int) or not 20 <= hold_ms <= 2000):
            raise ValueError("hold_ms 必須為 20～2000 ms")
        mod, usage = shortcut(key)
        self.ensure_connected()
        if hold_ms is None:
            response = self.send(mod, usage)
        else:
            try:
                response = self.command("KEYH %d %d %d" % (mod, usage, hold_ms), timeout=10)
            except RuntimeError as exc:
                if str(exc) == "ERR UNKNOWN_COMMAND":
                    raise RuntimeError("Pico 仍為舊韌體；請重新燒錄支援 KEYH 的 UF2") from exc
                raise
        if wait:
            time.sleep(wait)
        return response

    def raw_key(self, value):
        """Send one ASCII character through the same path as Tera Term.
        Machine ACK mode suppresses echo and reports the completed HID tap.
        """
        if len(value) != 1 or not (32 <= ord(value) <= 126 or value in "\r\n\t\b"):
            raise ValueError("raw_key 僅支援單一 ASCII 字元")
        self.ensure_connected()
        status = self.status()
        if status.get("raw_echo") != "1":
            raise RuntimeError("請燒錄支援直接 ASCII 鍵盤模式的新版 UF2")
        if self.command("MODE KEYBOARD ACK") != "OK MODE KEYBOARD ACK":
            self.close()
            raise RuntimeError("Pico 未進入 ACK 模式")
        try:
            self.ser.write(value.encode("ascii"))
            expected = "OK RAW SENT ASCII=0x%02X " % ord(value)
            deadline = time.monotonic() + 8
            while True:
                line = self._line(deadline)
                if line is None:
                    raise TimeoutError("Pico ASCII 按鍵逾時")
                if line.startswith(expected):
                    break
                if line.startswith("ERR "):
                    raise RuntimeError(line)
                if line:
                    self.event(line)
            result = line
            # Exit raw mode without sending printable command bytes into HID.
            self.ser.write(b"\x1d")
            deadline = time.monotonic() + 3
            while True:
                line = self._line(deadline)
                if line is None:
                    raise TimeoutError("Pico 無法切回 COMMAND")
                if line == "OK MODE COMMAND":
                    break
                if line:
                    self.event(line)
            # Keep this API in COMMAND for subsequent commands.
            return result
        except BaseException:
            self.close()
            raise

    def type_text(self, value):
        codes = text_codes(value)  # validate ALL before sending any
        for mod, usage in codes:
            self.send(mod, usage)


def gui(port=None):
    import tkinter as tk
    from tkinter import ttk, messagebox
    root = tk.Tk()
    root.title("mooInk Pico 2W 藍牙鍵盤")
    root.geometry("760x570")
    root.minsize(640, 460)
    root.columnconfigure(0, weight=1)
    root.rowconfigure(5, weight=1)
    portvar = tk.StringVar(value=port or "")
    armed = tk.BooleanVar(value=False)
    state = tk.StringVar(value="USB 未連線")
    pinvar = tk.StringVar()
    wakekey = tk.StringVar(value="f12")
    jobs = queue.Queue(maxsize=16)
    events = queue.Queue()
    stop = threading.Event()

    def enqueue(op, *args):
        try:
            jobs.put_nowait((op, args))
        except queue.Full:
            events.put("指令佇列已滿；未傳送此次按鍵")

    def worker():
        keyboard = None
        try:
            while not stop.is_set():
                try:
                    op, args = jobs.get(timeout=.1)
                except queue.Empty:
                    if keyboard is not None:
                        try:
                            keyboard.poll(.1)
                        except Exception as exc:
                            events.put("USB 錯誤：" + str(exc))
                            keyboard.close()
                            keyboard = None
                            events.put(("state", "USB 已中斷"))
                    continue
                try:
                    if op == "open":
                        if keyboard is not None:
                            keyboard.close()
                            keyboard = None
                        keyboard = PicoKeyboard(args[0] or None, event=events.put)
                        events.put(("state", "USB 已連線：" + keyboard.port))
                        events.put(keyboard.command("STATUS"))
                    elif op == "close":
                        if keyboard is not None:
                            keyboard.close()
                            keyboard = None
                        events.put(("state", "USB 未連線"))
                    elif keyboard is None:
                        raise RuntimeError("請先連接 Pico USB COM 埠")
                    elif op == "cmd":
                        events.put(keyboard.command(args[0]))
                    elif op == "key":
                        events.put(keyboard.send(*args))
                    elif op == "wake":
                        events.put(keyboard.wake(args[0]))
                        events.put("HID 按鍵已送出；是否實際喚醒、15 秒後休眠須觀察螢幕。")
                except Exception as exc:
                    events.put("錯誤：" + str(exc))
                    if keyboard is not None and not keyboard.ser.is_open:
                        keyboard = None
                        events.put(("state", "USB 已關閉，請重新連接"))
        finally:
            if keyboard is not None:
                keyboard.close()

    threading.Thread(target=worker, daemon=True).start()
    top = ttk.Frame(root, padding=10)
    top.grid(row=0, column=0, sticky="ew")
    ttk.Label(top, text="USB COM 埠（空白自動辨識）").pack(side="left")
    combo = ttk.Combobox(top, textvariable=portvar, width=11,
                         values=[p.device for p in ports()])
    combo.pack(side="left", padx=8)
    ttk.Button(top, text="重新搜尋", command=lambda: combo.configure(
        values=[p.device for p in ports()])).pack(side="left")
    ttk.Button(top, text="連接 USB", command=lambda: enqueue("open", portvar.get().strip())).pack(side="left", padx=5)
    ttk.Button(top, text="關閉 USB", command=lambda: (armed.set(False), enqueue("close"))).pack(side="left")
    ttk.Label(root, textvariable=state, padding=(10, 0)).grid(row=1, column=0, sticky="w")

    pair = ttk.LabelFrame(root, text="藍牙配對與連線", padding=8)
    pair.grid(row=2, column=0, sticky="ew", padx=10, pady=7)
    for title, cmd in (("開放配對 120 秒", "PAIR"), ("停止配對", "CANCEL"),
                       ("藍牙連線", "CONNECT"), ("斷開藍牙", "DISCONNECT"),
                       ("狀態", "STATUS")):
        ttk.Button(pair, text=title, command=lambda c=cmd: enqueue("cmd", c)).pack(side="left", padx=2)
    row = ttk.Frame(root, padding=(10, 0))
    row.grid(row=3, column=0, sticky="ew")
    ttk.Label(row, text="若 mooInk 顯示配對碼").pack(side="left")
    ttk.Entry(row, textvariable=pinvar, width=9, show="*").pack(side="left", padx=4)
    def submit_pin():
        value = pinvar.get().strip()
        if value.isascii() and value.isdigit() and 1 <= len(value) <= 16:
            enqueue("cmd", "PIN " + value)
            pinvar.set("")
        else:
            messagebox.showwarning("PIN", "請輸入 mooInk 顯示的數字配對碼")
    ttk.Button(row, text="送出 PIN", command=submit_pin).pack(side="left")
    ttk.Button(row, text="確認配對", command=lambda: enqueue("cmd", "CONFIRM")
               if messagebox.askyesno("確認", "確認是你正在配對的 mooInk？") else None).pack(side="left", padx=4)
    ttk.Button(row, text="忘記配對", command=lambda: enqueue("cmd", "FORGET")
               if messagebox.askyesno("忘記", "清除 Pico 目前的藍牙配對記錄？") else None).pack(side="right")

    control = ttk.LabelFrame(root, text="按鍵轉送（僅下方區域有焦點時）", padding=8)
    control.grid(row=4, column=0, sticky="ew", padx=10, pady=8)
    first = ttk.Frame(control); first.pack(fill="x")
    ttk.Label(first, text="喚醒鍵").pack(side="left")
    ttk.Combobox(first, width=10, textvariable=wakekey,
                 values=("f12", "space", "enter", "escape", "right", "left"),
                 state="readonly").pack(side="left", padx=4)
    ttk.Button(first, text="喚醒", command=lambda: enqueue("wake", wakekey.get())).pack(side="left")
    ttk.Checkbutton(first, text="啟用按鍵轉送", variable=armed).pack(side="left", padx=8)
    capture = tk.Text(control, height=2, wrap="none", takefocus=True)
    capture.insert("1.0", "勾選啟用，點這裡後按鍵。只轉送單次按下／放開；離開此區立即停用。")
    capture.pack(fill="x", pady=6)
    def on_key(event):
        if not armed.get():
            return "break"
        symbol = TK_SYMBOL.get(event.keysym, event.keysym)
        try:
            mod, usage = key_code(symbol)
        except ValueError:
            return "break"
        if event.state & 0x1: mod |= 2
        if event.state & 0x4: mod |= 1
        if event.state & (0x8 | 0x20000): mod |= 4
        enqueue("key", mod, usage)
        return "break"
    capture.bind("<KeyPress>", on_key)
    capture.bind("<KeyRelease>", lambda e: "break")
    capture.bind("<FocusOut>", lambda e: armed.set(False))

    log = tk.Text(root, wrap="word", state="disabled", height=10)
    log.grid(row=5, column=0, sticky="nsew", padx=10, pady=(0, 10))
    def drain():
        try:
            while True:
                msg = events.get_nowait()
                if isinstance(msg, tuple):
                    state.set(msg[1])
                else:
                    log.configure(state="normal")
                    log.insert("end", str(msg) + "\n")
                    log.see("end")
                    log.configure(state="disabled")
        except queue.Empty:
            pass
        root.after(100, drain)
    def close():
        armed.set(False)
        stop.set()
        root.destroy()
    root.protocol("WM_DELETE_WINDOW", close)
    root.after(100, drain)
    root.mainloop()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", help="Pico USB COM port, e.g. COM6")
    sub = parser.add_subparsers(dest="action")
    for action in ("gui","ports","status","pair","cancel","connect","disconnect","forget","confirm","monitor"):
        sub.add_parser(action)
    p = sub.add_parser("pin"); p.add_argument("value", nargs="?")
    p = sub.add_parser("key"); p.add_argument("value")
    p = sub.add_parser("text"); p.add_argument("value")
    p = sub.add_parser("wake"); p.add_argument("--key", default="f12"); p.add_argument("--wait", type=float, default=0); p.add_argument("--hold-ms", type=int, help="診斷：按住 20～2000 ms（需新版 UF2）")
    args = parser.parse_args(argv)
    try:
        if args.action == "ports":
            for p in ports():
                print(p.device, p.description)
            return 0
        if args.action in (None, "gui"):
            gui(args.port)
            return 0
        with PicoKeyboard(args.port, event=print) as board:
            action = args.action
            if action == "status":
                print(board.status())
            elif action == "monitor":
                while True:
                    board.poll(1)
            elif action == "pin":
                from getpass import getpass
                value = args.value or getpass("mooInk PIN: ")
                if not value.isascii() or not value.isdigit() or len(value) > 16:
                    raise ValueError("PIN 必須是數字")
                print(board.command("PIN " + value))
            elif action == "wake":
                if not 0 <= args.wait <= 300:
                    raise ValueError("--wait 必須 0～300 秒")
                print(board.wake(args.key, args.wait, hold_ms=args.hold_ms))
                print("按鍵已送出，不代表實體螢幕已確認喚醒。")
            elif action == "key":
                board.ensure_connected()
                print(board.key(args.value))
            elif action == "text":
                board.ensure_connected()
                board.type_text(args.value)
                print("OK TEXT")
            elif action == "connect":
                board.ensure_connected()
                print("OK CONNECTED")
            else:
                print(board.command(action.upper()))
        return 0
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        print("錯誤：", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
