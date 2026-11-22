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

## P1.5 — SMB Debug Mode
- [x] debug_mode 開關，預設 OFF；私有 JSON 設定及 CDC menu RAM 預覽。
- [x] 16 KiB RAM 循環紀錄，uptime、序號、遺失計數、Wi-Fi/SMB/圖片 hash/heap。
- [x] SMB 已連線時將最近紀錄回寫固定 pico_log.txt，失敗保留 RAM 並下次重試；來源 PNG 仍唯讀。
- [x] 主機測試：開關、容量上限、控制字元、設定類型與選單。
- [ ] 交叉編譯 / Windows 寫入權限 / 斷線恢復實機驗收。
- [ ] 整合 MTP/BT 真實事件；CDC 設定持久化及運行中 Apply。

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


## 100 項 Code Review 改善清單（2026-11-22）

依現有原始碼靜態審查與尚未完成的整合功能建立。P0 = 阻擋安全寫入／整合或重大正確性問題；P1 = 功能、可靠性、驗收；P2 = 維護及交付品質。R001–R100 固定編號。只有具體實作與對應測試完成後才勾選；實機驗收另外記錄。原上方階段清單保留，交叉參照，不以此清單代替實機通過。

### A. 建置、依賴與發佈

- [ ] R001 [P0] 建立新目錄內的 check_all.sh；依序執行 host tests、三個現有 UF2 的交叉編譯與最終整合 target；每步失敗即退出，不能以檔案存在代替通過。
- [ ] R002 [P0] 在實際 SDK 2.2.0 下編譯 native Host、CDC menu、SMB probe；記錄首次錯誤與完整 build log，不假設上游 API 一定兼容。
- [ ] R003 [P0] 核對 libsmb2 的 PICO_PLATFORM、HAVE_CONFIG_H、lwIP socket/poll/errno 與 FreeRTOS linking；避免將 FreeRTOS INTERFACE sources 重複編進多個靜態 library。
- [ ] R004 [P0] 確認 RP2350 ARM NTZ FreeRTOS port、SDK、CYW43 driver 的設定一致；核對單核心 scheduler 與 SDK async context 行為。
- [ ] R005 [P1] 將 SDK 2.2.0 commit、TinyUSB submodule、libsmb2、FreeRTOS 與未來 PIO USB 的精確版本統一記錄於本目錄 dependency lock；腳本核對實際 SHA。
- [ ] R006 [P1] 建置腳本接受明確 SDK 路徑、檢查版本與 submodule；外部 SDK 只讀，不自動修改其他專案的依賴。
- [ ] R007 [P1] 建置前檢查 cmake 最低版本（SMB 要求 3.24）、ninja、ARM GCC、python3、git；給出 WSL 安裝指令。
- [ ] R008 [P1] build 專案分離 debug/release 與 probe/integrated 產物；避免 CMake cache 殘留錯誤 PICO_BOARD、設定或 SDK 路徑。
- [ ] R009 [P1] 輸出 map、flash/RAM 統計與 build ID；設定 RAM/flash 預算及超限失敗條件，保留足夠 stack/heap 餘量。
- [ ] R010 [P1] 建立不含任何真實密碼的測試 build；含私有設定的 UF2/ELF/map 全部隔離並禁止發佈；驗證 gitignore。

### B. USB Host、硬體及傳輸層

- [ ] R011 [P0] 定稿 USB 拓撲：預設原生 Host + UART 管理，或原生 CDC + PIO Host；兩者互斥 build，明確腳位、供電與驗收，不把同一 native USB 當雙角色同時使用。
- [ ] R012 [P0] 記錄 VBUS 5 V、電流限制、防回灌、GND、供電順序與 USB-TTL 3.3 V 的實機接線；測量 mooInk 充電時的電流，不由 GPIO 供電。
- [ ] R013 [P0] 若採 PIO Host，核對 RP2350 PIO USB 版本、D+/D-、時鐘、PIO SM/DMA/IRQ 與 CYW43 資源衝突；用實機測試同時傳輸。
- [ ] R014 [P0] 檢查 TinyUSB Host 端點 claim/release 與 xfer callback 合約；所有 submit 失敗都回到明確狀態，避免重複排程與回呼重入。
- [ ] R015 [P0] 將 main.c 自訂 class driver 與 MTP 應用狀態機拆開；為 transport 提供獨立 API，讓整合版與 probe 共用，而非複製互相分歧的程式。
- [ ] R016 [P0] 處理 USB disconnect generation：舊 callback、舊裝置位址、舊 session 或遲到 completion 不能影響新連線或觸發寫入。
- [ ] R017 [P1] 檢查 interface/endpoint descriptor 的長度、方向、數量、max packet size 與 alternate setting，拒絕不完整或重複 descriptor。
- [ ] R018 [P1] 完善 USB 枚舉限制與診斷：只有預期 VID/PID、指定 MTP interface，未知型號採唯讀探測，列出不含私人序號的識別資訊。
- [ ] R019 [P1] 針對 bulk 64-byte packet、512-byte transfer buffer、短包、整包倍數與 ZLP 實測，不因等待下一個封包卡死。
- [ ] R020 [P1] 新增總交易逾時與無進度逾時；連續 ZLP、STALL、NAK、拔插錯誤須有界限，先取消/關閉 session 再復原，不盲目重送寫指令。

### C. MTP 唯讀協定與資料驗證

- [ ] R021 [P0] mtp_codec.c 依 opcode 驗證精確參數數量及必要 handle/storage/session 值，而非僅限制參數不超過五個；唯讀 allowlist 保留。
- [ ] R022 [P0] 解析 GetDeviceInfo 的 operation/format/event 列表，確認設備真的宣告 GetObject 與後續寫入能力；未宣告時維持唯讀。
- [ ] R023 [P0] 實作 GetStorageInfo，排除唯讀/不可用 storage；解析容量、剩餘空間與錯誤 response，在寫入前確認目標容量。
- [ ] R024 [P0] 完整解析 ObjectInfo 的 storage ID、format、protection、compressed size、association type、parent 和 filename；確認每個欄位與查詢條件一致。
- [ ] R025 [P0] 修正 UTF-16LE 名稱目前非 ASCII 轉 '?' 的有損判斷；目標名稱需無損字元比較，非目標名稱可安全顯示，不容許截斷造成名稱碰撞。
- [ ] R026 [P0] 將 sleep 資料夾與目標檔案匹配（大小寫衝突、重複 handle、非檔案、錯誤 parent/storage）改為明確拒絕，而非跳過後仍顯示 probe complete。
- [ ] R027 [P1] mtp_stream.c 保存 MTP response 的最多五個參數，而不是直接丟棄；後續 SendObjectInfo 需取得 storage/parent/new handle 並檢核。
- [ ] R028 [P1] 區分 SessionAlreadyOpen、InvalidTransactionID、DeviceBusy、StoreFull 等回應；交易 ID rollover、CloseSession 與重新枚舉有明確規則。
- [ ] R029 [P1] 針對 metadata 16 KiB／512 handles／16 storages 上限，明確回報超限而不當成不存在；研究安全分頁或拒絕寫入策略。
- [ ] R030 [P1] GetObject 應核對預先取得大小、container 長度、actual bytes 與 SHA-256；取得來源 hash 與 MTP hash 時序列化 RP2350 唯一 SHA 硬體鎖。

### D. MTP 安全寫入、備份及復原

- [ ] R031 [P0] 建立 update transaction 狀態機（DISCOVER/SNAPSHOT/BACKUP/VERIFY/WRITE/READBACK/WAKE），預設 dry-run；只有明確 enable_write 且所有 guard 通過才進入寫入。
- [ ] R032 [P0] 明確限制唯一可修改路徑 sleep/mooink_display.png；實際 USB device、storage、parent、handle 與檔案完整身份在每次破壞性操作前再檢查。
- [ ] R033 [P0] 設計來源不可變快照（SMB 專用 staged 檔案或安全本地容量）；確認 PNG 全檔、SHA-256 與大小後才允許依快照寫入，原始來源變更不得中途混合。
- [ ] R034 [P0] 舊圖備份至獨立受限 SMB 備份位置，使用唯一名稱及 O_EXCL，分塊寫入、flush、重新讀回並比對 hash；備份失敗不得刪除原圖。
- [ ] R035 [P0] 實作標準 MTP SendObjectInfo/SendObject 的 data-out 容器及參數編碼，檢查短傳、交易 response、回傳 handle 與資料長度。
- [ ] R036 [P0] 只在同名舊圖已完整備份且重新確認身份/內容後才允許 DeleteObject；禁止 all-objects/filter 刪除及未知 vendor opcode。
- [ ] R037 [P0] 評估能否新圖先寫至安全暫存名後再 rename；若設備不支援，明確使用受保護刪除再寫入流程，不假稱原子更新。
- [ ] R038 [P0] 每次寫入後重新列出唯一目標並完整 GetObject 回讀 SHA-256；大小/內容/metadata 不一致標記失敗，不觸發藍牙。
- [ ] R039 [P0] 建立持久交易 journal，記錄來源/備份/目標 hash、階段，不含密碼；斷電後只讀辨識現況，避免以舊 handle 繼續刪除或無限重試。
- [ ] R040 [P1] 提供獨立人工復原工具與驗收：保留舊圖備份、確認 partially written file、允許明確恢復；實機確認檔案替換不會丟失自訂休眠圖選取。

### E. Wi-Fi、SMB 及來源檔案

- [ ] R041 [P0] 確認 SMB2/3 NTLMv2、signing required、可選 encryption 真實協商成功；遇 Windows 政策不相容需清楚報錯，不降級 SMB1/guest 或放寬簽章。
- [ ] R042 [P0] smb_main.c 的 valid_file 目前只看 type/size；增加 reparse point、非普通檔、link count 及必要 identity 檢查，拒絕非預期來源。
- [ ] R043 [P0] 驗證 smb2_open/fstat/stat 在 Windows 回傳的 inode、ctime、nlink 一致性；用相同身份條件進行前後快照比較，不將缺失 identity 視為已驗證。
- [ ] R044 [P0] 目前只檢查 PNG header；增加串流 PNG chunk 長度、CRC、IHDR/IEND、IDAT 邊界、壓縮資料完整性與最大尺寸驗證後才允許寫入。
- [ ] R045 [P1] SMB chunk 長度取 min(4096, server max read/write, 剩餘資料)，處理短讀/短寫、EOF、unexpected extra data，封鎖超限。
- [ ] R046 [P1] 目前每輪 delay 位於 probe 後，實際週期為處理時間加設定值；改成 monotonic deadline 排程，不重疊作業並記錄逾時。
- [ ] R047 [P1] Wi-Fi 斷線、DNS、DHCP、SMB authentication 與 transport 故障分開處理；有限 exponential backoff 加 jitter，避免重試風暴。
- [ ] R048 [P1] 設定每次來源讀取/SMB session 的總時間上限，包含所有短讀，以免慢速設備一直延長單個 command timeout；超時可取消。
- [ ] R049 [P1] 明確區分 observed SHA、verified USB SHA、pending wake；SMB 內容相同不代表 mooInk 相同，上電、USB 重連、journal 未完成時重新回讀目標。
- [ ] R050 [P1] 驗證 SMB handle/context 在 open/read/close 中途失敗的釋放契約；記錄錯誤階段與安全數值 code，長時間重連不得漏資源。

### F. 設定介面、驗證及 flash

- [ ] R051 [P0] 整合 display_settings、config.local.json 與 firmware 實際使用的 schema；不同設定入口使用相同驗證，不讓 menu 顯示值與 runtime 值分歧。
- [ ] R052 [P0] 補充 config schema 的 enable_write、SMB domain/encryption、Bluetooth peer、USB VID/PID 或受控 target ID；未知/錯誤型別必須拒絕，不靜默忽略。
- [ ] R053 [P1] generate_config.py 的 require_encryption 目前僅比較 is True；改為嚴格 bool 驗證，舊設定缺 domain 採明確相容缺省值。
- [ ] R054 [P1] Wi-Fi 密碼驗證統一 8–63 bytes WPA2 passphrase，或明確支援 open network/64 hex PSK 的獨立模式；不可空值被不同入口不一致接受。
- [ ] R055 [P1] display_menu.c 拒絕 server/share 中的 URL/路徑分隔字元；MAC 必須合法 unicast 且非 broadcast/零地址，UTF-8/ASCII 欄位限制一致。
- [ ] R056 [P1] 選單增加 Save/Apply/Cancel/Restore defaults；draft 與 active settings 分離，Apply 只在工作安全 idle 邊界切換，取消不改 runtime。
- [ ] R057 [P0] 設計 flash 分區，與程式 XIP、BTstack TLV、其他儲存區不重疊；linker/build 時驗證容量，禁止覆蓋既有藍牙配對資料。
- [ ] R058 [P0] 設定持久化使用雙槽或可復原 journal、magic/version/length/CRC/commit marker；模擬寫入中斷、壞 CRC、未知新版，回退最後有效設定。
- [ ] R059 [P1] flash 擦寫時符合 SDK flash_safe_execute/多 core/RTOS/XIP 限制，安全停下相關 IRQ；避免每輪存檔，限制磨耗並保護秘密。
- [ ] R060 [P1] 提供不顯示密碼的真實狀態與 Wi-Fi 掃描/連線測試、SMB 測試、配對管理；按 Enter 開啟選單，流量持續也不逐字回顯或洩露草稿。

### G. Bluetooth Classic HID 與喚醒

- [ ] R061 [P0] 在新目錄獨立實作／移植已驗證 Classic HID descriptor、Space usage 0x2C 與 key down/up 60 ms，不引用或修改舊 main.c。
- [ ] R062 [P0] 在新程式內限制明確配對窗口、指定 peer 地址、拒絕其他裝置；PIN/SSP 交互經設定介面確認，禁止自動接受陌生主機。
- [ ] R063 [P0] 使用 BTstack TLV 保存綁定及連線金鑰，檢核持久化成功；重啟後只重連已保存 peer。
- [ ] R064 [P1] 實作 Forget/Pair/Cancel/Connect/Disconnect 的選單操作，安全清除舊 link key，不格式化整片 flash。
- [ ] R065 [P1] Bluetooth reconnect/backoff 與 Wi-Fi 共存；驗證睡眠、sniff mode、藍牙重連時不讓 SMB/USB task 飢餓。
- [ ] R066 [P0] 只有新圖 MTP 回讀成功且 transaction ID/hash 未被取代，才產生單一喚醒 token；不以 SMB observed hash 或 CDC 回顯當交付成功。
- [ ] R067 [P0] 喚醒 token 與 key state 包含 down、hold、up、timeout/disconnect；即使中斷仍確保不持續按鍵，不向錯誤裝置送 key。
- [ ] R068 [P1] BTstack CAN_SEND_NOW、send result、連線變更與上報分離；區分 accepted by local stack、未確認、實際刷新，禁止假稱已喚醒。
- [ ] R069 [P1] 喚醒不確定時保留狀態並供人工重試；避免重開機或 SMB poll 重複 Space 造成翻頁/非預期輸入。
- [ ] R070 [P1] 實機驗證 mooInk 15 秒休眠、USB 連線中 Space、長時間待機後更新，並核對舊 Pico 不會被新 Pico 的配對設定覆蓋。

### H. Debug Log、錯誤診斷與資源控制

- [ ] R071 [P1] smb_main.c 改用 64-bit uptime（time_us_64()/1000），避免 to_ms_since_boot 32-bit 在約 49.7 天回捲，分清 reboot/session。
- [ ] R072 [P1] debug_log.c 16 KiB 滿載目前逐行 memmove；改為環形索引、保留整行及遺失數，避免長時間記錄時 O(n) 搬移成本。
- [ ] R073 [P0] 整合後多 task/IRQ 的 log 需單一 owner 或 bounded message queue；IRQ 不做 printf/SMB/長時間鎖，不導入 race 或死鎖。
- [ ] R074 [P0] flush_log 驗證 pico_log.txt 不是來源 PNG 的別名或其他連結，且是專用普通檔；檢查 pre/post-open identity，避免不安全 truncation。
- [ ] R075 [P1] Log 採可驗證快照或雙檔輪替設計；目前 pwrite/truncate 中途失敗可能留下混合內容，須以完整性標記或安全 atomic replace 改善。
- [ ] R076 [P1] 確認 Windows 專用 log 權限、預先建立檔、分享模式、O_EXCL 與 fstat/nlink 實際行為；debug OFF 不發出任何 log SMB 寫入操作。
- [ ] R077 [P1] 增加 boot reason、build ID、USB/MTP opcode/response/bytes、BT connect/key state、update phase 與安全錯誤代碼，不列印帳密、PIN、完整私密路徑。
- [ ] R078 [P1] 縮短重複 log／限速，分開 debug 與 ERROR；SMB 不通時在 UART 可見原因，復原後刷新 buffer，禁止日誌失敗阻止安全判斷。
- [ ] R079 [P1] 啟用 FreeRTOS stack overflow/malloc failed hooks，記錄最小 free heap、高水位與 lwIP/BT/USB buffer 用量；設置耗盡前拒絕更新的條件。
- [ ] R080 [P1] 增加能區分健康進度與阻塞的 watchdog；重啟前保留故障階段（避免記密碼），watchdog recovery 不得自動重做破壞性操作。

### I. Windows Python、使用與安全維護

- [ ] R081 [P1] prepare_image.py 在合成前正確處理 alpha（先鋪白底），保留 EXIF orientation，固定 16 灰階與 1404×1872/1650×2200。
- [ ] R082 [P1] 限制來源圖尺寸、解碼檔案大小與 Pillow decompression bomb；拒絕多影格或明確選第一幀，不允許惡意/損壞輸入產生半成品。
- [ ] R083 [P1] 原子發布前先驗證暫存 PNG 可重新解碼、尺寸、最大 16 MiB、SHA；只在確認有效後 os.replace，原圖失敗保留。
- [ ] R084 [P1] 目前 prepare_image 在 replace 後重開 target 計 hash；改為暫存檔確認 hash 並防止競爭寫入，明確標示發布版本。
- [ ] R085 [P1] 實作獨立 Word 單頁轉 PNG 選項（可選 Office/LibreOffice），不足依賴時報錯；不匯入舊 Watch Tool 或更改舊設定。
- [ ] R086 [P1] 獨立 Windows 設定 GUI/CLI 產生私有 JSON、校驗 IP/share/port/權限；密碼隱藏，不在 log 或命令列參數暴露。
- [ ] R087 [P1] Windows SMB 診斷工具測試唯讀來源、原子發布與專用 log/backup 權限，列出 SMB dialect/signing 觀察值及 Windows 防火牆問題。
- [ ] R088 [P1] 獨立 Python log viewer 支援手動／輪詢讀取 pico_log.txt、辨識 reboot/序號/不完整快照；只讀，不需要常駐推送程式。
- [ ] R089 [P1] 文件化 private UF2 內帳密可被擷取的風險；使用低權限 SMB 帳號、限制 LAN、憑證更新步驟及安全退役流程。
- [ ] R090 [P2] 所有設定、工具、暫存、log、範例、依賴與授權聲明位於 Pico_wifi_display/；README 清楚區分 probe 與完成版，不聲稱未驗證功能已可用。

### J. 測試、整合與交付驗收

- [ ] R091 [P0] 執行目前所有 C/Python host tests；擴充 gcc/clang ASan/UBSan 與嚴格 warnings，記錄日期/環境/結果，不虛構 CI 成功。
- [ ] R092 [P0] MTP codec/stream 測試加入 random fragmentation、零長度、錯交易、超長、畸形 UTF-16、response params、ZLP／截斷與 fuzz inputs。
- [ ] R093 [P0] 模擬 USB transport 注入 STALL/timeout/detach/舊 callback，驗證 session 與 target identity 失效時不會觸發寫入。
- [ ] R094 [P0] 用 mock SMB server 或 abstraction 測試 signing/auth fail、短讀、metadata race、重連、log no permission；來源錯誤不得造成圖檔修改。
- [ ] R095 [P0] 建立 update transaction 失敗注入測試；每一步 backup/write/readback/flush/journal/power loss 均驗證不喚醒、不誤刪、不將失敗標成成功。
- [ ] R096 [P1] 持久設定測試覆蓋 CRC、版本、斷電、雙槽選擇、輸入超長、CRLF、無回顯、Apply/Cancel 與舊 BT 金鑰保留。
- [ ] R097 [P1] 建置整合 target，以 USB task、SMB task、BT event loop 與 log owner 的明確隊列/優先權協調；同時觀察 CPU、stack、heap 和 PIO/DMA 使用。
- [ ] R098 [P0] 使用獨立 Pico 與備份後的實機測試：先乾跑/唯讀枚舉與回讀 hash，再用專用測試圖寫入，最後才允許一次 Space；禁止影響舊 Watch Tool。
- [ ] R099 [P1] 長時間 soak test 至少 24/72 小時、SMB/Wi-Fi 斷線、USB 拔插、BT 斷線、休眠/重啟及異常斷電；確認無 RAM leak、log 爆長、意外翻頁。
- [ ] R100 [P1] 交付前以 Git diff 檢查所有變更均在指定目錄，確認舊應用 unchanged；README 附實際測試結果、未通過項目、版本及完整獨立操作步驟。
