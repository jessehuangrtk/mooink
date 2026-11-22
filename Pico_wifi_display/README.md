# Pico_wifi_display

獨立 Pico 2W 專案，所有新檔案都在本目錄。現有 Watch Tool、Pico USB→Bluetooth 韌體及原研究目錄均保留、不修改。

## 第一階段（本次實作）

- 原生 USB Host，自訂 TinyUSB PTP/MTP class，限制已實測的 VID:PID 1F85:4E16。
- 唯讀 GetDeviceInfo、OpenSession、GetStorageIDs、GetObjectHandles、GetObjectInfo、CloseSession；找唯一 sleep 並列出檔案。超出 16 KiB metadata 或 512 handles 停止，不截斷。
- 無刪除、上傳、未知 vendor opcode；沒有 SMB/BT 聯動或可用的整合 watcher。這是實驗 probe，須實機驗證。
- Python 獨立圖片準備工具，原子替換 Windows SMB 分享資料夾的 mooink_display.png。
- config.example.json 是後續整合的設定規格，**第一階段韌體尚未讀取它**。不要填密碼到受 Git 追蹤的檔案。

## WSL 編譯

~~~bash
sudo apt update
sudo apt install -y git cmake ninja-build gcc-arm-none-eabi libnewlib-arm-none-eabi libstdc++-arm-none-eabi-newlib libusb-1.0-0-dev
cd Pico_wifi_display
bash tests/run.sh
bash build_wsl.sh
~~~

輸出 build/pico_wifi_display_probe.uf2。預設 SDK 2.2.0 下載到本目錄 .deps；也可用環境變數 PICO_SDK_PATH 指向既有 SDK。全部建置產物在本目錄。

## USB 與除錯

另一片 Pico 2W 的 micro USB 用於 Host，不再是 Windows CDC。需要正確 5V VBUS 供電、具過流保護且避免回灌的 Host 配線；不可將兩個 Host 相接或將 5V 接 GPIO。第一階段使用 UART0 TX GP0（Pin1）→ USB-TTL RX、GND 共地，3.3V TTL，115200 8N1。除錯不需要接 TTL TX。Tera Term 連 TTL 的 COM。

預期 UART：USB mounted、MTP interface、GetDeviceInfo OK、storage count、ROOT、sleep objects、SLEEP、READ-ONLY PROBE COMPLETE。遇錯停止，USB 拔插重試。不同裝置 VID/PID 先核對，不直接放寬限制。

## PC 圖片工具

~~~powershell
python -m pip install -r Pico_wifi_display/pc/requirements.txt
python Pico_wifi_display/pc/prepare_image.py source.png "C:\mooink_share" --size 10.3
~~~

Windows 分享該目錄（SMB2/3，專用帳號、最小權限，不啟用 SMB1/guest 或關閉簽章）。Pico 未實作 SMB 讀取前，不會自動下載。

## 最終目標與後續驗收

Pico Wi-Fi/SMB2/3 主動讀取 mooink_display.png → 分塊 SHA-256 → USB MTP 備份、限定同名替換、回讀驗證 → Pico Bluetooth Classic HID Space → mooInk 自動休眠。

1. 實機 USB/MTP 唯讀驗收；補充 GetObject 串流回讀。
2. libsmb2 + FreeRTOS 的 Pico 2W SMB2/3 client，驗證 Windows signing/認證與記憶體，來源每 60 秒檢查。
3. SMB 來源穩定快照；專用備份目錄或可驗證的本機備份；不能安全備份就拒絕刪除。限定 sleep/mooink_display.png。
4. USB 寫入成功及完整回讀 SHA-256 後，才允許藍牙 Space；失敗不得喚醒或盲目重送。相同內容不重寫。
5. 獨立 Python 設定/診斷及端到端實機測試。既有 Watch Tool 不修改。

520 KiB SRAM，不將整張 PNG 假設放入記憶體；分塊處理。Wi-Fi 與 Bluetooth 共用 CYW43439，三介面共存尚未驗證。

### 唯讀 GetObject / SHA-256

在唯一 sleep 目錄中若存在唯一 mooink_display.png（1 byte～16 MiB），probe 使用 GetObject、512-byte USB buffer 及 RP2350 pico_sha256 增量 hash。UART 印出 SHA256 和 bytes；不儲存／修改檔案。比較 Windows `Get-FileHash -Algorithm SHA256` 的來源值。MTP 容器解析器支援 header 及資料跨 transfer、同一 transfer 內相接容器；交易 ID、長度及 response 必須一致。SDK 2.2.0 header API 已核對，尚未確認編譯／實機。

## CDC 設定選單（獨立 preview）

新 target `build/pico_wifi_display_menu.uf2` 是單獨 CDC 設定介面預覽，不執行 USB Host／SMB／Bluetooth。以 Tera Term 連接新 Pico 的原生 USB COM，115200 8N1、Local echo OFF、Transmit CR。任何字元均不逐字回顯，閒置時只按 Enter 顯示主選單；數字 + Enter 選取。Wi-Fi SSID/密碼、SMB server/share/user/密碼、Bluetooth MAC、尺寸與週期可輸入 RAM 草稿；狀態僅顯示密碼是否已設置，重啟會遺失。Bluetooth 真實配對、執行連線及 flash Save/Apply 尚未實作。0 回上一層或離開，再按 Enter 顯示。此 UF2 不會送出鍵盤字元，也不修改 mooInk。

最終希望保留 Pico 原生 USB CDC，另以 PIO USB Host 連接 mooInk（官方 dual mode 架構）。須增加獨立的 USB Host 接頭、適當 USB D+/D- 及保護供電；目前 P0 的 native Host probe 仍獨立，兩個 UF2 不能同時執行。等 PIO Host、CYW43439 PIO/DMA 資源及時序實測通過才整合。若選用原生 USB Host，則設定選單必須改走外接 USB-TTL 或其他介面。不要把 Windows 和 mooInk 同時連到原生 USB 埠。

## P1：獨立 Wi-Fi / SMB 唯讀 probe（待編譯、實機）

另有 `build_smb_wsl.sh`，與 USB Host probe 和 CDC menu 各自獨立。先在本目錄複製 `config.example.json` 為 `config.local.json`，填 Wi-Fi SSID/密碼、Windows server IP、share、SMB 專用帳號/密碼、domain（Windows 本機帳號可填電腦名稱）、尺寸、interval。來源固定 `mooink_display.png`。必須 SMB signing，若 Windows 要求加密設定 require_encryption=true；不允許 guest 或關閉簽章。

~~~bash
cd Pico_wifi_display
cp -n config.example.json config.local.json
# 編輯 config.local.json 後
bash tests/run.sh
bash build_smb_wsl.sh
~~~

UF2：`build/smb/pico_wifi_display_smb_probe.uf2`。私有設定編譯入 UF2（明文可擷取），不要分享 UF2、ELF 或 config.local.*；全部在本目錄 gitignore 範圍內。此探測版不使用 menu 的 RAM 設定，也不更新 mooInk。透過原生 CDC 或 UART 看 log：Wi-Fi connected、SMB CHANGED/UNCHANGED、bytes、SHA256、FreeRTOS heap/stack watermark。比對 Windows Get-FileHash。以 4096 bytes 分塊讀取，限制 16 MiB，兩次 stat 及開檔前後 fstat/stat 必須一致，檢查 PNG header 尺寸及 8-bit grayscale（不是完整 PNG 解碼驗證）。每次都 hash，避免單靠 mtime。任何失敗不會碰 USB。libsmb2 與 RP2350 FreeRTOS 使用固定 commit；交叉編譯、Windows signing/NTLMv2 相容性、RAM 與共存仍未驗證。請勿為了測試降低 Windows 安全政策。
