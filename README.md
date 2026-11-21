# mooInk Pro 休眠畫面管理工具（Windows / Python）

本專案目前以 **第一代 mooInk Pro 10.3／13.3 吋的休眠圖片管理** 為主。提供 Windows 圖形介面、批次轉換、USB MTP 休眠圖片清單／新增／先備份再刪除；並保留 Wi-Fi 裝置能力研究及舊的鏡像傳輸原型。

> **重要限制**：目前沒有經實機證實可用的「遠端直接喚醒／立即休眠／立即刷新電子紙」命令。USB 可以改變 sleep 儲存內容，不代表畫面會即時更新。本專案不修改裝置韌體、不破解 DRM，也不保證 Windows 以外的平台具有 USB MTP 功能。

## 程式入口

| 檔案 | 作用 |
| --- | --- |
| [mook_sleep_screen_manager_ui.py](mook_sleep_screen_manager_ui.py) | **建議入口**：Tkinter GUI；操作真正由後端執行 |
| [mook_sleep_screen_manager.py](mook_sleep_screen_manager.py) | 後端：圖片/PDF/Office 轉換、MTP sleep 檔案操作、Wi-Fi 探測與 CLI |
| [mooink_full_cmd_demo.py](mooink_full_cmd_demo.py) | USB WPD、授權 API 與醒／睡狀態實驗；不等於可用的電源控制 API |
| [tools/mooink_mtp_inspector_v004.py](tools/mooink_mtp_inspector_v004.py) | 保留已驗證的唯讀 MTP/WPD 研究版本 |
| [todo.md](todo.md) | 200 項依主題、優先級整理的待驗證與改進事項 |

## Windows 安裝（Python 3.9+）

在專案目錄開啟 PowerShell 或 CMD：

~~~powershell
py -m pip install -r requirements.txt
py mook_sleep_screen_manager_ui.py
~~~

不需要另外下載 UI 框架：Tkinter 一般隨官方 Windows Python 安裝。依賴中 Pillow 用於圖片處理／預覽、PyMuPDF 用於 PDF、pywin32 用於 Windows MTP 與 Office COM。Excel／Word 轉圖片還需要**已安裝的 Microsoft Office 或 LibreOffice**；安裝 Python 套件本身並不包含 Office。

如果你需要完整離線安裝，參閱 [docs/OFFLINE_DEPENDENCIES.md](docs/OFFLINE_DEPENDENCIES.md)，並於有網路時先建立對應 Windows/Python 版本的 wheel 備份。

## GUI 操作

1. 在「圖片製作」選擇 **10.3** 或 **13.3** 吋，指定方向（auto／portrait／landscape）及縮放（smart／contain／cover）。
2. 加入圖片、PDF、Excel 或 Word；選擇輸出資料夾，點選 **開始批次轉換**。完成後點選已轉換檔案即可預覽。
3. 透過 USB 連線 mooInk，在「裝置 / sleep 管理」選 USB MTP、按 **重新搜尋** 並選擇正確裝置。
4. 按 **讀取圖片清單** 查看裝置 sleep 目錄；按 **選擇 JPG / PNG 上傳** 新增圖片。**不會自動覆寫同名檔**。
5. 需要刪除時先設定本機備份資料夾、選擇圖片、按 **備份並刪除選取**。程式會先確認，再由後端進行備份驗證及刪除要求。
6. Windows MTP 的 CopyHere／刪除可能非同步。出現 verification-pending 或 delete requested 等狀態，不能視為裝置已完成；請重新讀取清單、視需要使用檔案總管確認。

**安全限制：** 同時接上兩台相同的 USB mooInk 時，為避免寫錯裝置，後端會拒絕寫入及刪除。建議一次只連接要修改的那台。初版 GUI 已寫成可執行程式，但尚未完整完成 Windows 雙機端到端回歸測試。

## Wi-Fi 的使用範圍

- 輸入 mooInk LAN IP 後可以探測 TCP 8080／8443 的可達性，**不能藉此讀寫 sleep 目錄**。
- Wi-Fi 檔案管理只支援你已經在作業系統**掛載、實際可讀寫**的遠端資料夾，且所選路徑最後一層必須叫做 sleep，例如某個已驗證的 SMB 共享資料夾。它**不是** mooInk 原廠 API 的既有功能保證。
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
