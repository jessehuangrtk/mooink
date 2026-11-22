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

## P0.5 — CDC 設定介面
- [x] 新增無逐字回顯、Enter 開啟選單、數字選項、CR/LF、Backspace 的 menu 模組。
- [x] Wi-Fi、SMB、Bluetooth 目標、尺寸、監控間隔 RAM 設定及密碼遮蔽。
- [x] 獨立 native USB CDC menu preview UF2 target；主機測試程式。
- [ ] 驗證 menu UF2 編譯及 Tera Term COM 實機操作。
- [ ] 持久化設定（保護 flash / BT TLV、斷電復原、不輸出密碼）；Save / Apply / Cancel。
- [ ] 實作 Wi-Fi 掃描／連線測試、SMB 測試、BT 配對／忘記／重連與真實狀態。
- [ ] 原生 CDC + PIO USB Host 雙 USB：需額外 Host 接頭、VBus 安全供電，核對 PIO/Wi-Fi 資源及 SDK 相容性。整合 menu 後驗收。

## P1 — Wi-Fi / SMB
- [x] 獨立 Wi-Fi/SMB probe：連線重試、私有設定生成（未編譯或實機驗收）。
- [x] libsmb2 / RP2350 ARM FreeRTOS 依賴固定、獨立 opt-in build（尚待交叉編譯確認）。
- [ ] 驗證 SMB2/3、NTLMv2、signing required；不降低 Windows 安全設定。
- [x] SMB stat/fstat、分塊讀取、SHA-256、來源版本前後檢查、PNG header 檢查（probe，待實測）。
- [x] 60 秒可設定輪詢、內容 hash 相同回報 UNCHANGED（只觀測，非寫入完成狀態）。
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
