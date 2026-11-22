# Pico 2W mooInk USB Host（獨立實驗專案）

狀態：架構與可行性研究；尚無可燒錄的 MTP Host 韌體，也尚未完成 PC Python 應用。現有 `pico2w_usb2bluetooth/`、`pico2w_usb2bluetooth.py`、`mooink_display_watcher.py` 不修改；使用另一塊 Pico 2W。

## 目標

Windows Python 轉換 Word 為 10.3/13.3 吋 PNG → 經 Wi-Fi 傳至 Pico 2W → Pico 原生 USB Host 與 mooInk MTP 通訊 → 備份、替換 sleep 專用圖、回讀驗證 → Bluetooth Classic HID Space → mooInk 自動休眠並更新畫面。

PC 不再需要直接 USB 連接 mooInk。不使用 mooInk 自身 Wi-Fi API。PC/Pico 的 Wi-Fi 僅負責圖片及狀態傳輸。

## 已有證據與限制

- 實機既有 USB MTP 實驗記錄：[`research/mtp_usb_findings.md`](../research/mtp_usb_findings.md)。測試裝置 VID/PID 1F85:4E16；可在休眠時寫入 sleep。不同裝置須重新確認。
- 現有 Pico Bluetooth Classic HID Space 與 Windows Watch Tool 已由使用者確認可用。
- RP2350 原生 USB 支援 Host/Device，但只有一個控制器，同一埠不可同時連 Windows CDC 和 mooInk Host。選擇原生 Host + Wi-Fi。
- Pico 2W 只有 520 KiB SRAM、USB Full Speed 12 Mbit/s；不得假設完整 PNG 可放入 SRAM。採分塊傳輸、增量 SHA-256。Wi-Fi/Bluetooth 共用 CYW43439，需實測並避免在圖片傳輸期間喚醒。
- TinyUSB MTP 支援 Device，沒有現成的 MTP Host class driver，需要自行實作 Host class 與 PTP/MTP bulk container、交易管理、資料集解析。
- Pico Host 必須有 5 V VBUS 電源，電源與線材要能供應連接裝置，使用具過流保護且不回灌的 Host 配線；不得直接把兩個 USB Host 相接或將 5 V 接 GPIO。

## 開發與驗收順序

1. **唯讀 USB Host 枚舉**：取得 VID/PID、interface、bulk IN/OUT 與 interrupt IN（若有）。不用 Windows WPD，不切換 mooInk 驅動或下達未知 vendor command。
2. **唯讀 MTP**：GetDeviceInfo (0x1001)、OpenSession、GetStorageIDs/GetStorageInfo、GetObjectHandles/GetObjectInfo，辨識唯一 sleep 資料夾。GetObject 分塊下載並比較與 Windows 取得的原圖 SHA-256。
3. **獨立 Python 傳輸端**：限定目標裝置、經具驗證的本地網路連線分塊讀寫，限制長度與逾時；保存舊圖完整備份於 Windows，先驗證後才允許修改。Pico 不公開匿名檔案寫入介面。
4. **MTP 寫入**：只允許 mooink_display_10.png 或 mooink_display_13.png；同名/hash 相同不重複寫入。SendObjectInfo/SendObject 並回讀完整 SHA-256。重名拒絕、備份不完整不刪除；遇失敗保留備份、停止、不喚醒。先使用測試圖，確認替換是否維持 mooInk 休眠圖選取。
5. **藍牙 HID**：另存現有已驗證的 Space 行為到新韌體，不在舊應用修改。完成 MTP 驗證後只送一次 Space，不自動重送不確定的按鍵。觀察休眠畫面刷新。
6. **整合與穩定性**：使用獨立的 Windows GUI/設定/日誌，測試 USB 重插、網路中斷、低記憶體、休眠時 MTP、Bluetooth 斷線及不同尺寸。確認舊 Watch Tool 完全不受影響。

## 預計目錄

```
pico2w_mooink_usb_host/
    README.md
    firmware/       # 獨立 Pico SDK / TinyUSB Host + BTstack
    pc/             # 獨立 Python client / Watch GUI
    tests/          # MTP codec、串流與失敗保護
```

目前只建立本研究規格；實作將從唯讀 USB Host 開始。

## 官方參考

- https://www.raspberrypi.com/documentation/microcontrollers/pico-series.html
- https://github.com/raspberrypi/pico-examples （USB Host / USB Dual Mode）
- https://docs.tinyusb.org/en/latest/reference/class/ （Host/Device 支援矩陣）
- https://docs.tinyusb.org/en/latest/reference/class/mtp.html
