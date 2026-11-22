# Pico_wifi_display TODO

狀態： [x] 程式／文件已存在（不等於實機通過）；[ ] 尚待施工或驗收。所有新檔案只在本目錄，禁止修改舊 Watch Tool、Pico、根目錄 CI 或其他檔案。

## P0 — 獨立基礎
- [x] 建立獨立目錄、README、WSL build、設定範例及本目錄 gitignore。
- [x] 獨立 Python 圖片產生工具，統一檔名 mooink_display.png。
- [x] Pico 原生 USB Host MTP 唯讀探測程式及 codec 測試。
- [ ] 核對 SDK 2.2.0 編譯、USB Host 5 V 供電及 UART 實機輸出。
- [x] 建立主機單元測試，涵蓋跨 USB packet 的容器（尚待執行）。
- [x] 實作 GetObject 分塊讀取、長度驗證、增量 SHA-256（待編譯及實機確認）。
- [ ] 實機確認唯一 sleep、對應 VID/PID 與檔案 hash。

## P1 — Wi-Fi / SMB
- [ ] Pico Wi-Fi 連線及重連、私有設定生成（密碼不提交）。
- [ ] 移植 libsmb2，固定依賴版本，確認 Pico 2W / FreeRTOS 或 lwIP 相容。
- [ ] 驗證 SMB2/3、NTLMv2、signing required；不降低 Windows 安全設定。
- [ ] SMB stat、分塊讀取、SHA-256、來源檔案版本一致性。
- [ ] 設定每 60 秒檢查；同內容跳過，不因時間改變重寫。
- [ ] 整合 SMB 與 USB Host，測量 RAM、逾時及網路斷線。
- [ ] Windows 端獨立設定與診斷工具（不常駐推送）。

## P2 — 安全更新
- [ ] 唯一目標 USB／sleep／保留檔名、重名及不明物件拒絕。
- [ ] 舊圖完整備份及回讀 hash，保留可恢復版本。
- [ ] 來源圖穩定快照／容量管理；超限不刪圖。
- [ ] 實作 SendObjectInfo/SendObject、DeleteObject；預設唯讀。
- [ ] 寫入後回讀 SHA-256，失敗保留備份、不喚醒。
- [ ] 斷電／USB 重插／SMB 中斷恢復；禁止盲目重試刪除。
- [ ] 實機確認替換圖片後自訂休眠圖選取是否保留。

## P3 — Bluetooth 與整合
- [ ] 獨立複製已驗證的 Classic HID Space 路徑，不修改舊韌體。
- [ ] 專用配對、目標綁定、自動重連。
- [ ] 只在新圖回讀驗證完成後按一次 Space。
- [ ] 相同內容不喚醒；失敗不誤報、喚醒未確認不盲目重送。
- [ ] Wi-Fi/USB/BT 同時運作、長時間休眠與重連測試。
- [ ] 完整獨立 UF2、設定工具、使用說明及實機驗收。
