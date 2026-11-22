# mooInk Pro 休眠畫面管理工具（Windows / Python）

本專案目前以 **第一代 mooInk Pro 10.3／13.3 吋的休眠圖片管理** 為主。提供 Windows 圖形介面、批次轉換、USB MTP 休眠圖片清單／新增／先備份再刪除；並保留 Wi-Fi 裝置能力研究及舊的鏡像傳輸原型。

> **重要限制**：目前沒有經實機證實可用的「遠端直接喚醒／立即休眠／立即刷新電子紙」命令。USB 可以改變 sleep 儲存內容，不代表畫面會即時更新。本專案不修改裝置韌體、不破解 DRM，也不保證 Windows 以外的平台具有 USB MTP 功能。

## Pico 2 W 藍牙鍵盤喚醒

已新增獨立 Pico 2 W USB→Bluetooth Classic HID 鍵盤韌體與 Windows 控制工具 [pico2w_usb2bluetooth.py](pico2w_usb2bluetooth.py)。支援 CDC 直接鍵盤輸入、原樣回顯及自動藍牙配對／重連，不使用控制指令。詳見 [編譯、燒錄、配對與使用說明](pico2w_usb2bluetooth/README.md)。實機刷新仍待驗證；不透過 Wi-Fi IP 發送藍牙按鍵。

## 程式入口

| 檔案 | 作用 |
| --- | --- |
| [mook_sleep_screen_manager_ui.py](mook_sleep_screen_manager_ui.py) | **建議入口**：Tkinter GUI；操作真正由後端執行 |
| [mook_sleep_screen_manager.py](mook_sleep_screen_manager.py) | 後端：圖片/PDF/Office 轉換、MTP sleep 檔案操作、Wi-Fi 探測與 CLI |
| [mooink_full_cmd_demo.py](mooink_full_cmd_demo.py) | USB WPD、授權 API 與醒／睡狀態實驗；不等於可用的電源控制 API |
| [tools/mooink_mtp_inspector_v004.py](tools/mooink_mtp_inspector_v004.py) | 保留已驗證的唯讀 MTP/WPD 研究版本 |
| [todo.md](todo.md) | 200 項依主題、優先級整理的待驗證與改進事項 |

## Windows 安裝（GUI Python 3.9+；USB WPD Python 3.10+）

在專案目錄開啟 PowerShell 或 CMD，**用同一個 `python` 執行安裝及啟動**：

~~~powershell
git pull
python -m pip install -r requirements.txt
python mook_sleep_screen_manager_ui.py
~~~

**不需安裝 `mtp` 套件！** PyPI `mtp==1.1.0`（常見相依套件 `urwid`）並不是此專案使用的 Windows WPD API；先前指引要求 `pip install mtp` 是錯誤的。WPD 原始碼已內建於 `third_party/heribert17_win_access.py`，上游為 [Heribert17/mtp](https://github.com/Heribert17/mtp)，MIT 授權；此專案只需安裝 `comtypes`（已列在 `requirements.txt`）。原來已安裝的 `mtp 1.1.0` 不會被本程式使用，不必為了 mooInk 另行安裝或重複安裝它。

執行記錄會列出 **GUI Python 的完整路徑**；若 USB WPD 無法載入，錯誤會顯示真正的 `comtypes`／Windows DLL／裝置列舉例外，而非一律提示「沒有 mtp」。

不需要另外下載 UI 框架：Tkinter 一般隨官方 Windows Python 安裝。Pillow 用於圖片處理／預覽、PyMuPDF 用於 PDF、pywin32 用於 Windows Shell/Office COM、comtypes 用於直接 WPD。Excel／Word 轉圖片還需要**已安裝的 Microsoft Office 或 LibreOffice**。

如果你需要完整離線安裝，參閱 [docs/OFFLINE_DEPENDENCIES.md](docs/OFFLINE_DEPENDENCIES.md)，並於有網路時先建立對應 Windows/Python 版本的 wheel 備份。

## GUI 操作

1. 在「圖片製作」指定方向（auto／portrait／landscape）及縮放（smart／contain／cover），**不必選尺寸**。
2. 加入圖片、PDF、Excel 或 Word；選擇輸出**根目錄**，點選 **同時製作 10.3 + 13.3 吋圖片**。程式透過同一個後端轉圖演算法產生兩套圖片並分別儲存：
   - `mooink_prepared/10.3/` — 10.3 吋版，1404×1872（直式）
   - `mooink_prepared/13.3/` — 13.3 吋版，1650×2200（直式）
   - 每個資料夾都有獨立的 `manifest.json`；輸出清單標示適用尺寸，點選檔案可預覽。根目錄可以在 UI 自訂。
3. 透過 USB 連線 mooInk，在「裝置 / sleep 管理」設定 **目前 mooInk 尺寸 10.3／13.3 吋**、按 **重新搜尋** 並選擇正確裝置。**此尺寸會自動儲存到本機設定檔，下次開啟 GUI 自動恢復。**
4. 按 **讀取圖片清單** 查看裝置 sleep 目錄；按 **選擇 JPG / PNG 上傳** 時會預設進入所選尺寸的 `mooink_prepared/<尺寸>/`，並拒絕誤選另一尺寸的已製作圖片。**不會自動覆寫同名檔**。
5. 需要刪除時先設定本機備份資料夾、選擇圖片、按 **備份並刪除選取**。每張依序執行 **備份 → 驗證 → 刪除**；某張備份失敗就跳過，不會刪除該張，也不會中斷其他圖片。整批結束後顯示可複製的逐檔結果（已確認刪除／待確認／跳過／失敗），失敗訊息會記錄在清單中。
   - 在「裝置 / sleep 管理」中可以看到 **整批檔案數進度條、目前檔案進度條、階段文字與已等待秒數**。整批百分比依完成的檔案數計算，不代表 MTP 傳輸位元組百分比。
   - 單檔備份可設定 **15 / 30 / 50 / 90 / 120 秒**，預設 50 秒。這是程式在備份命令與輪詢時的等待上限；Windows Shell CopyHere 本身若阻塞，Python 不一定能強制中斷。縮短等待可能讓較慢的傳輸被判定為逾時，因此不會對逾時檔案發出刪除要求。
   - 若 Windows MTP 只能一次產出完整備份檔、無法提供中途大小，單檔進度條會持續滾動，並顯示階段秒數；有觀察到本機備份大小時才會提供位元組比例估計。
6. Windows MTP 的 CopyHere／刪除可能非同步。**待確認不等於已經刪除**；請重新讀取清單、視需要使用檔案總管確認。備份失敗時報告會列出裝置宣告大小與本機備份大小，協助判讀。

**安全限制：** 同時接上兩台相同的 USB mooInk 時，為避免寫錯裝置，後端會拒絕寫入及刪除。建議一次只連接要修改的那台。初版 GUI 已寫成可執行程式，但尚未完整完成 Windows 雙機端到端回歸測試。

**尺寸設定儲存位置：** Windows 預設為 `%LOCALAPPDATA%\\mooink\\sleep_manager_ui_settings.json`，目前只儲存 `device_size`，不會記錄裝置序號、配對憑證或其他秘密資訊。此設定只控制裝置管理的尺寸及上傳預設資料夾，不影響圖片製作同時產生兩種尺寸。

## 修正：空白的備份子目錄與未知來源大小

如果在備份資料夾看到很多 `mooink_YYYYMMDD_HHMMSS_NNN` 空白子目錄，原因是舊版程式在確認 MTP 檔案來源大小之前，便建立了避免同名的備份子目錄；隨後來源大小讀取失敗，資料夾沒有任何檔案。

- 新版先檢查 MTP 原始大小（Shell `Size` 或 `System.Size`），確認資料足夠再建立目錄。**無法取得來源大小時，不會建立備份目錄，也不會刪除原始圖片**；同一批次中的剩餘檔案會停止操作。
- 可在 GUI 的備份資料夾列點選 **清理空白備份子目錄**。只刪除該備份資料夾下一層、符合 `mooink_YYYYMMDD_HHMMSS_NNN` 格式且完全空白的子目錄，**不會遞迴刪除**，不會碰任何有檔案的目錄。請先確認沒有 Windows MTP 傳輸正在進行。
- 若來源大小仍未知，可使用 **單張備份測試（不刪除）** 嘗試複製並確認本機圖片可以完整解碼。因為無法比對來源長度或位元組內容，這不是足以安全自動刪除原檔的驗證。

## USB 批次刪除：只確認一次（直接 WPD）

GUI 的「備份並刪除選取（一次確認）」**只在開始前顯示一次程式確認**。USB 模式改走直接 WPD 介面，不使用會造成 Windows 檔案總管逐張詢問的 `FolderItem.InvokeVerb("delete")`。

**WPD 模式需求：Windows 10／11、Python 3.10+、comtypes。** 只需執行上方的 `python -m pip install -r requirements.txt`，WPD 程式碼已附在專案內，無需從 GitHub 額外安裝 `mtp`。主程式的其他 Python 3.9 功能維持不變。進行實機刪除前請先測試一張並保留原圖。

**批次流程：** 選取 JPG/PNG → GUI 整批確認一次 → WPD 直接下載第一張 → 比對 WPD 來源與本機大小、解碼備份圖片 → WPD 刪除該張 → 再從裝置重新確認該檔消失 → 才處理下一張。沒有源大小、下載失敗、讀取到兩台匹配的裝置、`sleep` 目錄不唯一，或刪除結果無法確認，都會停止而非冒險刪除其他圖片。

**限制：** 直接 WPD 已經做模擬測試，但**尚未在實際 mooInk 上測試其裝置名稱、MTP 路徑與刪除支援**。若缺少額外函式庫或不符合唯一裝置條件，GUI 會明確報告並停止；**不會悄悄退回 Explorer 逐張確認模式**。CLI 原本的 Shell 模式仍保留，方便相容性研究。

## USB 上傳：即時進度與完整性確認

GUI 的 **「選擇 JPG / PNG 上傳」** 使用 `mook_sleep_screen_manager.py` 的統一後端；USB 傳輸則改由 `mook_mtp_wpd.py` 的 **WPD 直接上傳**（不再依賴 Windows Shell 非同步 `CopyHere`）。

- 在「裝置 / sleep 管理」可看到 **整批進度條、單張活動進度、目前檔名／階段與已等待秒數**。WPD `upload_file` 不提供逐位元組 callback，因此不能以虛構百分比表示真正的傳輸量；單張進度條在寫入期間會持續動畫。
- 逐檔流程：避免同名覆寫 → WPD 寫入 `sleep` → 重新讀取 `sleep` 目錄確認檔案存在 → 從裝置讀回本機暫存 → **SHA-256 比對一致**才標示 `verified`。
- 全部結束顯示每張的 **已驗證／同名跳過／失敗／未執行**，並重新列出裝置的 `sleep` 圖片。
- 如果 WPD 連線或識別失敗，不會退回 Windows Shell `CopyHere`。若裝置上傳失敗並留有部分檔案，不會擅自刪除遠端檔案；請重新讀取清單檢查。
- 直接 WPD 功能使用**隨專案附帶的 MIT 授權程式**，相依 `comtypes` 由 `requirements.txt` 安裝；不需要 `pip install mtp`。目前只驗證模擬裝置，尚待真實 mooInk 測試。

**驗收限制：** GUI、WPD 傳輸及檔案回讀已有模擬測試，但尚未實機確認你的 mooInk 可接受直接 WPD 上傳。若失敗，將錯誤訊息貼回，不能把「送出命令」誤當成「畫面更新」。即使檔案上傳成功，電子紙也不一定立即重繪休眠畫面。

## USB MTP 備份診斷與安全保護

**建議先做單張備份測試，不要直接對全部圖片執行刪除：**

1. 在「裝置 / sleep 管理」讀取圖片清單，**只選取一張**。
2. 指定本機備份資料夾，按 **單張備份測試（不刪除）**。
3. GUI 會顯示正在複製、已觀察到的本機檔案大小與等待時間。只有來源宣告大小與備份大小一致才回報備份大小驗證成功；此操作 **絕不執行刪除**。
4. 如失敗，檢查報告中的本機備份位置和大小。如果 Windows Shell `CopyHere` 沒有真正產生檔案，會明確標示 MTP 複製未啟動。這不是靠增加等待秒數就一定能修復的問題。
5. **同名備份問題已修正**：Shell `CopyHere` 不能指定輸出新檔名。若本機已有原始檔名，程式改為建立獨立子資料夾，避免同名覆寫與 Python 等待錯誤檔名。
6. 批次備份／刪除遇到單張一般錯誤會記錄後繼續，但如果完全沒有備份檔被建立，或已送出刪除要求卻無法確認消失，就會保護性停止後續檔案，避免空等或重複下達刪除命令。

**注意：** 目前只確認本機備份大小相符，**不是原檔與備份位元組相同的密碼學驗證**。Windows Shell MTP 無法提供可靠的 CopyHere 完成通知。未來若需要更強的完整性保證，可使用 Windows WPD `IPortableDeviceResources::GetStream` 逐位元組讀取原始 MTP 檔案。若裝置不支援該讀取方式，應保留原檔，不可跳過驗證。

## Wi-Fi 的使用範圍

- 輸入 mooInk LAN IP 後可以探測 TCP 8080／8443 的可達性，**不能藉此讀寫 sleep 目錄**。
- Wi-Fi 僅提供原生 API 探測、配對及資料夾查詢；sleep 圖片管理目前使用 USB MTP。
- 先前實機測試確認第一代裝置在休眠時仍可回應部分 Wi-Fi API；但遠端 PDF 顯示命令沒有使休眠電子紙直接喚醒。

詳情見 [research/remote_wake_strategy.md](research/remote_wake_strategy.md) 及 [research/mtp_usb_findings.md](research/mtp_usb_findings.md)。

## 不使用 GUI：命令列

~~~powershell
# 轉換 PDF／Office／圖片；不接觸 USB 裝置
py mook_sleep_screen_manager.py --size 13.3 --action convert --input "C:\tmp\example.pdf"

# 列出 USB 裝置 sleep 圖片（需要 Windows，請確認第 1 台是正確裝置）
py mook_sleep_screen_manager.py --transport usb --usb-index 1 --action list

# 檢查裝置 Wi-Fi 8080／8443（不作配對或電源操作）
py mook_sleep_screen_manager.py --action probe --ip 192.168.1.137

# 診斷指令研究工具（包含人為觀察及明確同意才會執行的操作）
py mooink_full_cmd_demo.py --ip 192.168.1.137
~~~

## 輸入輸出

- 來源：JPG/JPEG、PNG、WebP、BMP、TIFF、GIF、AVIF（依 Pillow 解碼能力）、PDF、Excel、Word、ODT/ODS。
- 輸出：依指定解析度縮放、16 級灰階、低於 990000 位元組的 JPG。
- PDF 最多 100 頁；GIF/TIFF 預設使用第一影格。
- 轉換後輸出目錄預設 mooink_prepared；本機刪除備份預設 mooink_backup。這些檔案已在 .gitignore 排除。

## 關於其他原型

[mirror_server.py](mirror_server.py) 與 [viewer.html](viewer.html) 是 Wi-Fi 傳輸／瀏覽器顯示研究原型，不是已在原廠 mooInk 上執行的螢幕鏡像產品。目前程式庫**沒有完整的 desktop_sender.py 或 Android Studio sender 專案**，請勿依舊版說明推定能直接安裝。原廠裝置若沒有可用的畫面接收執行環境，瀏覽器 viewer 也不能直接在 mooInk 上顯示。

## 授權與安全

專案自身原始碼以 MIT 授權；third_party/dpt-rp1-py 保留上游作者授權。不要將私人配對金鑰、裝置完整序號或個人檔案提交至 Git；研究資料與驗收報告務必先去識別化。


## mooInk Word 顯示監控（Windows 系統匣）

執行：python -m pip install -r requirements.txt，然後 python mooink_display_watcher.py。
第一次啟動出現設定視窗，儲存後常駐系統匣。設定檔及成功轉圖狀態位於 %LOCALAPPDATA%\mooink。
預設檢查間隔 60 秒、尺寸 10.3、Pico CDC COM6（清空 COM 欄位可停用）；不需 IP。

| 尺寸 | 監控 Word | 產出 PNG | 自訂 Word 紙張寬×高 |
|---|---|---|---|
| 10.3 | mooink_display_10.docx | mooink_display_10.png (1404×1872) | 15.70×20.93 cm |
| 13.3 | mooink_display_13.docx | mooink_display_13.png (1650×2200) | 20.27×27.03 cm |

### 範本與使用者工作檔分離

- `template/`：版本控制保留兩份 DOCX 與兩份對應的 PNG 範本。
- 專案根目錄：同名四個檔案是使用者工作檔，已由根目錄限定的 `.gitignore` 規則排除，不會提交或隨後續 git pull 更新。
- 第一次啟動監控或切換至新監控資料夾時，只從範本複製尚不存在的工作檔；既有 DOCX／PNG 一律不會被初始化覆寫。正常轉圖只更新所選尺寸的工作 PNG。不能直接監控 template/。
- 只想先建立根目錄工作檔，可執行 `python mooink_display_watcher.py --init-templates`，不會啟動監控或傳送圖片。
- **舊版升級注意：** 舊版的根目錄兩份 DOCX 曾受 Git 追蹤。請在首次拉取此版本之前關閉 Word 與 watcher，把根目錄四個工作檔複製到專案外備份。若 git pull 因已修改的 DOCX 停止，確認備份完整再還原這兩個受追蹤的檔案後重試。更新成功後，將備份的工作檔複製回根目錄；已經存在的工作檔不會被初始化覆寫。不要使用 git clean -x 清理工作檔。

兩個 Word 都設直式、0.5 cm 邊界、單頁。程式依目前設定的裝置尺寸監控對應 DOCX，保留兩個尺寸獨立檔名，不會傳錯另一尺寸。需要 Word 或 LibreOffice。超過一頁會報錯，不會自動截掉第二頁。

**USB 自動更新：** Wi-Fi PDF 開啟未成為休眠封面，Watch Tool 改回原生 USB WPD MTP，直接更新 sleep 內的專用 PNG。



Wi-Fi 配對及文件診斷保留於獨立研究工具；Watch Tool 不使用 Wi-Fi。

### Watch Tool USB sleep 自動更新

1. 先結束舊版 Watch Tool，git pull 後執行 `python mooink_display_watcher.py`。
2. 只連接一台 mooInk 的 USB 資料線；選擇正確尺寸、COM（選用），按「測試 USB／查詢 sleep」。
3. 勾選「啟用 USB 自動更新 sleep 圖片」並儲存。第一次上傳後，在 mooInk 選取 `mooink_display_10.png` 或 `mooink_display_13.png` 作為自訂休眠圖，而非閱讀中書本封面；設定 15 秒自動休眠。
4. Word 更新時轉換 PNG，WPD 直接比對 sleep 同名圖片。若不同，先回讀舊圖並備份至 `%LOCALAPPDATA%/mooink/watcher_backup`，確認刪除後上傳新圖，再回讀 SHA-256。只有驗證成功才發 Pico Space。其他圖片不刪除；若上傳失敗保留備份且不發 Space。
5. 相同內容不重複寫入；每次檢查實際連接的閱讀器。可從系統匣「立即重新轉圖／傳送」強制檢查並測試喚醒。若韌體因刪除重建物件而忘記選取的休眠圖，需實機確認是否重新選取。
6. 舊 Wi-Fi PDF 自動更新設定不再使用；以前產生的 PDF 不會被程式刪除。Wi-Fi 配對憑證保留。

### Pico 喚醒（CDC 直傳）

Tera Term 原字元直傳已由使用者確認可用。Watch Tool 以相同方式在 COM6 寫入單一 Space (`0x20`)，沒有舊控制指令、模式或換行。等待正常原字元回顯（0.5 秒），回顯一致只代表 Pico CDC 接收，不能當成藍牙送達或螢幕喚醒確認。未收到回顯或遇到斷線提示時，記錄傳送未確認；不盲目重送，可按「測試喚醒 Space」或「立即重新轉圖／傳送」再測試。使用 Watch Tool 前請關閉 Tera Term 以釋放 COM6。已燒錄直傳版 Pico 不需要重燒。
