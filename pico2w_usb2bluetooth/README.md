# Pico 2 W：USB CDC → Bluetooth Classic HID 鍵盤

## 使用方式

1. 更新程式：`git pull`，在 WSL 執行 `bash pico2w_usb2bluetooth/build_wsl.sh`。
2. 按住 BOOTSEL 插入 Pico USB，將 `pico2w_usb2bluetooth/build/pico2w_usb2bluetooth.uf2` 複製至 RP2350 磁碟。或使用 GitHub Actions 成功編譯的 UF2 artifact。
3. Tera Term 連線 COM6（依實際 COM）、115200、8N1、Flow control None、Transmit CR、Local echo **OFF**。
4. 直接輸入文字。Pico 回顯原字元，並轉換為 US layout HID 按鍵。無任何文字控制指令，也不需按 Enter 才送出普通按鍵。Enter、Tab、Backspace 支援；CRLF 視為一次 Enter。中文、功能鍵與終端 escape sequence 不轉送。

## 藍牙配對

已配對的 Pico 自動重連原本的 mooInk。未配對時，上電開放 120 秒配對；在 mooInk 藍牙設定選擇 mooInk Pico2W Keyboard。如果閱讀器要求 PIN，Tera Term 直接輸入閱讀器顯示的數字再按 Enter，不需要 PIN 指令。數字比較時必須輸入相同六位數確認。配對碼不回顯。逾時後重新插拔 Pico 可再開啟配對窗。配對儲存方式維持原 TLV。

不再提供 FORGET、PAIR、CONNECT、KEY、STATUS 等指令。如果需要更換已保存的配對對象，須先清除 Pico 的舊 flash 配對資料；整片 flash 擦除會清除所有韌體及資料，請僅在必要時採用，並備妥 UF2。

## Watch Tool

Watch Tool 只透過 CDC 傳送一個空白字元 (0x20)，無換行、無控制命令。也可用下列命令在 Windows 明確傳送一次：

~~~powershell
python pico2w_usb2bluetooth.py --port COM6 --text " "
~~~

請勿同時開啟 Tera Term 和 Watch Tool。CDC 回顯只確認輸入；藍牙斷線、佇列滿時 Pico 會顯示錯誤，且不會把斷線期間的按鍵延後送出。Python 寫入成功不代表藍牙送達或電子紙喚醒。

Pico SDK 2.2.0 / Bluetooth Classic HID，非 BLE。一般按鍵維持 60 ms。實機喚醒仍需驗證。

Watch Tool 會觀察 Tera Term 一樣的單一 Space 回顯（0.5 秒），無額外控制命令。CDC 回顯一致不代表藍牙送達；若無回顯或收到錯誤提示，紀錄未確認，不自動重送。
