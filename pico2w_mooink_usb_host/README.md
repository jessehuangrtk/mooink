# Pico 2W mooInk SMB Watch（獨立實驗專案）

狀態：架構與可行性研究，尚無可燒錄的整合韌體。使用另一塊 Pico 2W，現有 `pico2w_usb2bluetooth/`、`pico2w_usb2bluetooth.py`、`mooink_display_watcher.py` 不修改。

## 目標

- Pico Wi-Fi：作為 SMB2/3 Client，主動連線 Windows SMB 分享資料夾，每 60 秒（可設定）檢查 `mooink_display.png` 是否變更。
- Pico USB：原生 USB Host，透過 MTP 更新 mooInk 的 `sleep/mooink_display.png`，完成回讀驗證。
- Pico Bluetooth：沿用已驗證的 Classic HID Space 喚醒，等待 mooInk 自動休眠並更新顯示。
- Windows 只需提供 SMB 分享資料夾與圖片。獨立 Python 可用於設定、圖片產生及診斷，但不需要常駐推送圖片，不使用原 Watch Tool，不連接 mooInk USB。
- 不使用 mooInk 自身 Wi-Fi API。

## 狀態流程

Wi-Fi 連線 → SMB 認證 → 檢查檔案 → 確認穩定版本／SHA-256 → USB MTP 確認目標及 sleep → 備份舊圖 → 寫入 → 回讀 SHA-256 → Bluetooth Space → 等待下次檢查。

使用 SMB 檔案大小、最後修改時間作為快速篩選，增量 SHA-256 為最終內容判定。啟動及 USB 重連時回讀遠端實際檔案，不能單靠本機快取。連續兩次 metadata 穩定後再讀取；建議 Windows 寫入暫存檔後同目錄原子替換。下載期間再次確認 metadata，避免讀到半成品。

## 設定與安全

- Wi-Fi SSID/密碼、Windows IP/主機名、SMB share、相對檔案路徑、SMB 專用帳號/密碼、檢查間隔、mooInk 尺寸與 USB/BT 目標身分。
- Windows 使用 SMB2/3 與現有安全政策（含簽章要求），不可啟用 SMB1 或匿名 guest、不可為相容而關閉 SMB 簽章。認證與 signing/encryption 相容性須先驗證。
- SMB 僅讀來源圖；若自動備份舊圖到 Windows，使用另設的專用可寫備份目錄及最小權限。無法保留且驗證備份時拒絕刪除舊圖片。配對資訊及密碼不提交 Git、不印至 log。
- 10.3 吋 PNG 1404×1872、13.3 吋 1650×2200。新應用統一檔名 mooink_display.png，與現有 Watch Tool 的兩尺寸專用檔案互不干擾。
- 只允許更新唯一 sleep 目錄中的專用同名圖片，不修改其他檔案。先以測試圖驗證自訂休眠圖選取是否保留。
- 圖片以分塊傳輸、增量 hash 處理，不能假設完整圖可放入 RAM。SMB 暫存／備份空間、Flash 容量和操作中斷後的復原策略須在啟用寫入前驗證。
- SMB 或 USB 失敗時不喚醒；等網路恢復再檢查。USB 寫入後 SHA-256 一致才允許 Space。相同內容不重寫或重複喚醒；藍牙送出與真正畫面刷新分開記錄。

## 技術可行性

- 既有實機 MTP 記錄：[`research/mtp_usb_findings.md`](../research/mtp_usb_findings.md)。測試 VID/PID 1F85:4E16；休眠仍可寫入 sleep。不同裝置需要確認。
- 現有 Pico Bluetooth Classic HID Space 與 Windows Watch Tool 已由使用者確認可用。
- [libsmb2](https://github.com/sahlberg/libsmb2) 提供 RP2040 Pico W + Pico SDK + FreeRTOS 移植範例，可作為 RP2350 Pico 2W 的移植起點；尚未驗證 Windows SMB 政策、記憶體與三介面共存。
- RP2350 原生 USB 支援 Host/Device，但同一控制器不能同時當 Windows CDC 與 mooInk USB Host。使用原生 Host；除錯可用 UART 或後續獨立的網路唯讀診斷。
- Pico 2W 有 520 KiB SRAM、USB Full Speed 12 Mbit/s，Wi-Fi/BT 共用 CYW43439。
- TinyUSB MTP Device 支援不等於 MTP Host 支援。須自行實作 USB Host class 與 PTP/MTP bulk container、交易與資料集解析。
- USB Host 必須正確供應 5 V VBUS，有過流保護且不回灌；不可直接將兩個 USB Host 相接或將 5 V 接 GPIO。

## 開發與驗收順序

1. Pico 2W Wi-Fi + libsmb2/FreeRTOS 唯讀 SMB，讀取 mooink_display.png，驗證 stat、分塊讀取與 SHA-256。
2. 原生 USB Host 唯讀枚舉 mooInk，確認 VID/PID、interface、bulk IN/OUT、interrupt IN（若有）。
3. 唯讀 MTP GetDeviceInfo、OpenSession、GetStorageIDs/GetStorageInfo、GetObjectHandles/GetObjectInfo、GetObject；確認唯一 sleep 目錄與原圖 hash。禁止未知 vendor commands。
4. 同時執行 SMB + USB，驗證 RAM 使用、Wi-Fi 斷線及 USB 重插；實作備份與來源版本穩定保護。
5. 限定同名圖片 MTP 更新及回讀驗證；失敗不喚醒，不刪除其他檔案。
6. 整合 Bluetooth HID Space 與自動重連，只在完整驗證後喚醒。
7. 獨立 Python 設定／圖片產生工具與 GUI（非必須常駐），獨立設定與日誌；完整實機回歸後才作為可使用版本。

## 目錄

```
pico2w_mooink_usb_host/
    README.md
    firmware/       # Pico SDK + FreeRTOS + libsmb2 + TinyUSB Host + BTstack
    pc/             # 獨立 Python 設定與圖片產生工具
    tests/          # SMB、MTP codec、串流與失敗保護
```

目前只有規格；不代表 SMB + MTP + Bluetooth 已可使用。

## 參考

- https://github.com/sahlberg/libsmb2
- https://www.raspberrypi.com/documentation/pico-sdk/networking.html
- https://www.raspberrypi.com/documentation/microcontrollers/pico-series.html
- https://docs.tinyusb.org/en/stable/index.html
