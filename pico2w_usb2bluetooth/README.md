# Pico 2 W：USB → Bluetooth Classic HID 鍵盤

Windows Python 透過 USB CDC (COM) 對 Pico 2 W 下達命令，Pico 2 W 模擬藍牙鍵盤，將明確要求的按鍵送給已配對的第一代 mooInk。**不是 BLE 鍵盤、不是 Windows USB HID 鍵盤，也不接管全系統鍵盤。**

mooInk Wi-Fi IP：192.168.1.140（僅供現有 Wi-Fi demo 使用，藍牙不依賴 IP）。Pico 只需一般 USB 資料線連至 Windows，不需 TTL 或其他藍牙模組。Pico 上原有韌體會被覆蓋，若要保留之前的 GPIO 監控功能，先保存原有 UF2 或使用另一塊板。

## 1. 編譯韌體（Windows + WSL Ubuntu）

在 WSL 安裝工具（只需一次）：

~~~bash
sudo apt update
sudo apt install -y git cmake ninja-build gcc-arm-none-eabi libnewlib-arm-none-eabi libstdc++-arm-none-eabi-newlib libusb-1.0-0-dev
~~~

在 mooink Git 專案目錄：

~~~bash
git pull
bash pico2w_usb2bluetooth/build_wsl.sh
~~~

腳本預設取得 Pico SDK 2.2.0 及 submodules；亦可先設定 PICO_SDK_PATH 使用既有相容 SDK。輸出：pico2w_usb2bluetooth/build/pico2w_usb2bluetooth.uf2。GitHub Actions 亦會編譯並在成功時提供 UF2 artifact。

## 2. 燒錄

1. 拔掉 Pico 2 W 的 USB。
2. 按住 BOOTSEL，再插入 USB 資料線。
3. 在 Windows 將 UF2 複製到 RP2350 磁碟（預期會自動卸載並重啟）。
4. 正常啟動後可在裝置管理員的連接埠看到 USB Serial Device (COMx)。

## 3. Windows GUI 與配對

~~~powershell
git pull
python -m pip install pyserial
python pico2w_usb2bluetooth.py
~~~

選擇 Pico 的 COM 埠（只有一台 Raspberry Pi USB 裝置時可留白自動辨識），按「連接 USB」及「開放配對 120 秒」。在 mooInk 藍牙設定中搜尋 **mooInk Pico2W Keyboard** 並配對。如果 mooInk 顯示數字 PIN，於 GUI 輸入並按「送出 PIN」；如果 GUI 提示 CONFIRM_REQUIRED，請核對配對裝置及顯示的數字再按「確認配對」。

必須重新和 Pico 配對，舊實體鍵盤的配對不能直接移植。Pico 使用 SDK 的 TLV flash 保存單一目標地址及 BTstack 金鑰，平時不開放新裝置配對。重新插拔 USB 後，可按「藍牙連線」重新連線。若需更換目標，先斷線、在 GUI 忘記配對，並在舊 mooInk 刪除鍵盤配對紀錄。

## 4. 喚醒與一般鍵盤

- 將 mooInk 設定為 15 秒自動休眠。待螢幕休眠後按 GUI「喚醒」。預設 F12，亦可選 Space、Enter、Escape、方向鍵。這些只是一般 HID 按鍵，未經第一代 mooInk 實機確認哪個最合適；有可能造成頁面/選單變動。
- GUI 的「啟用按鍵轉送」需勾選，點擊下方專用輸入區後才轉送按鍵；離開輸入區自動停止接受新按鍵。支援英文、數字、常見標點、方向鍵、翻頁、F1-F12、Ctrl/Alt/Shift 組合；按一下會送出完整按下與放開，並非完整即時按住、多鍵 rollover 或中文輸入法。
- 韌體不會在開機、配對或自動重連時送出按鍵；只接受主機明確下達的 KEY 指令。
- KEY SENT 只代表按下、放開報告已交給 BTstack，**不代表電子紙已喚醒**。喚醒、15 秒後休眠及新圖刷新均須實機觀察。

## 5. 命令列及 Python API

先使用 GUI 配對。COM6 請換成裝置管理員實際顯示的值。

~~~powershell
python pico2w_usb2bluetooth.py ports
python pico2w_usb2bluetooth.py --port COM6 status
python pico2w_usb2bluetooth.py --port COM6 wake --key f12 --wait 20
python pico2w_usb2bluetooth.py --port COM6 key right
python pico2w_usb2bluetooth.py --port COM6 key "ctrl+a"
~~~

可從其他 Python 程式重用：

~~~python
from pico2w_usb2bluetooth import PicoKeyboard
with PicoKeyboard("COM6") as keyboard:
    keyboard.wake(key="f12", wait=20)
~~~

未來整合：先用 sleep manager 上傳並驗證 JPG，再呼叫 wake，等待 20 秒（預留 15 秒休眠餘裕）。目前不自動修改 sleep 檔案或設定，也不自動執行喚醒；請先驗證鍵盤能在短期及長時間休眠後喚醒閱讀器。

## 6. USB 指令協定

ASCII 每行一命令、LF/CRLF，115200（USB CDC 標示速度）。回覆 OK / ERR；非同步狀態行 EVENT。HELLO 驗證韌體版本，STATUS 回報 ready/paired/connected/pairing/connecting/peer。PAIR、CANCEL、PIN 數字、CONFIRM、CONNECT、DISCONNECT、FORGET、KEY 修飾鍵位元遮罩 HID_usage_decimal。KEY 完成後 OK KEY SENT；逾時或中途斷線會回錯，PC 不自動重送。禁止同時從 GUI 和 CLI 開啟同一 COM。

## 7. 驗證狀態

韌體以 Pico SDK 官方 Bluetooth Classic HID 架構實作，SDK/BTstack 授權適用。GitHub 自動編譯與 Python 模擬測試不能取代 Pico 2 W 與 mooInk 真實配對、喚醒及長時間休眠驗收。

參考：
- https://github.com/raspberrypi/pico-examples/tree/master/bluetooth/btstack_examples/hid_keyboard_demo
- https://www.raspberrypi.com/documentation/pico-sdk/networking.html

## 8. Space 喚醒診斷

實體鍵盤的 Space 已能喚醒 mooInk；Pico 的 Classic HID 連線正常但短按未喚醒。先確認 mooInk 清醒時在搜尋欄能接收 Pico 的普通按鍵，再重新編譯／燒錄新版 UF2，比較 60 ms、500 ms、1000 ms 的 Space。新版新增 `KEYH mod usage hold_ms`（20～2000 ms），並回報 DOWN/UP；原 KEY 保持 60 ms，配對及 Watch Tool 預設行為不變。

~~~powershell
python pico2w_usb2bluetooth.py --port COM6 wake --key space --hold-ms 500
python pico2w_usb2bluetooth.py --port COM6 wake --key space --hold-ms 1000
~~~

長按可能在清醒時造成重複輸入或翻頁，僅用於明確的診斷操作。KEYH SENT 是本地 BTstack 交付回報，不是主機確認。

### KEY 回傳 UNKNOWN_COMMAND

新版改用獨立 ASCII 十進位解析器，不再依賴 sscanf。重新編譯並燒錄 UF2 後，STATUS 應出現 `key_parser=2`；HELLO 保持相容。先在清醒的 mooInk 搜尋欄測試 `key a`，再測試休眠 Space。無效 KEY 參數會回傳 INVALID_USAGE_OR_HOLD，而非 UNKNOWN_COMMAND。

## Tera Term 即時鍵盤模式（僅 USB CDC）

先關閉 Watch Tool/Python GUI，避免 COM 埠占用。Tera Term 選 Serial COM6，115200、8N1、Flow control None，Terminal Transmit CR（CRLF 亦可），Local echo On 可看到所輸入的指令。開啟後輸入 STATUS、必要時 CONNECT、待 connected=1 再輸入 MODE KEYBOARD（每條指令按 Enter）。

進入 KEYBOARD 後，ASCII 英數、標點、Space、Enter、Tab、Backspace 逐字直接送 HID，不需 Enter；按下 Ctrl+]（0x1D）返回 COMMAND。F1～F12/方向鍵 escape sequence 暫不支援，請回 COMMAND 用 KEY/KEYH；非 ASCII/中文不轉送。每鍵回顯 ASCII/HID/modifier、DOWN/UP、OK RAW SENT 或明確錯誤。佇列最多 32 個待送按鍵，滿載拒絕新輸入；斷線清除佇列，不重送。按 Ctrl+] 會丟棄待送佇列，已開始的按鍵仍完成放開。Python 開啟 COM 時先送 Ctrl+] 切回 COMMAND，新舊韌體相容。請勿同時開啟 Tera Term 和 Watch Tool。

範例（實際回報不代表目標裝置已顯示）：

~~~text
MODE KEYBOARD
OK MODE KEYBOARD
KEY SPACE ASCII=0x20 HID=0x2C MOD=0x00
EVENT KEY DOWN mod=0 usage=44 hold_ms=60
EVENT KEY UP
OK RAW SENT ASCII=0x20 HID=0x2C
~~~
