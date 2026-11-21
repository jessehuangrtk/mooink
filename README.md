# mooInk Mirror (experimental)

把 **Windows / Android 手機** 畫面擷取、灰階化，透過區域網路傳到接收端的開源原型。**不是已驗證可在原廠 mooInk 顯示器上鏡像的產品**。

## 重要限制（先看）

Readmoo 公開資料表示 mooInk 為封閉式系統，**不提供第三方 App 安裝**。官方 mooInk Pro Connect 的「螢幕鏡像」方向是 **mooInk → 手機**，不是反向。現有公開資訊不足以確認 mooInk Pro / Pro 2 / Pro 2C 有開放的遠端畫面接收介面。

本專案已提供可實際測試的 **Windows 螢幕擷取 / Android 螢幕擷取 → Python Wi-Fi 接收伺服器 → 瀏覽器顯示**，作為傳輸、灰階顯示及延遲驗證。要直接顯示到原廠 mooInk，仍須取得該機型 **可運行的接收器**（例如能開指定網址的網頁瀏覽器），或得到可驗證的官方裝置端顯示 API。光有 Wi-Fi 傳檔功能不足以實現連續自動刷新。

此程式**不破解**裝置、**不修改**韌體、**不涉及**官方 APK / 訂閱驗證。

## 系統架構

```text
Windows ---desktop_sender.py--\
                              >--- mirror_server.py (LAN) ---> /viewer 網頁
Android ---Android MediaProjection/                     （若 mooInk 無瀏覽器則不能直接顯示）
```

## 快速測試（Windows / macOS / Linux，Python 3.9+）

```powershell
py -m pip install -r requirements.txt
py mirror_server.py
```

伺服器會印出 `LAN 地址` 與一次性亂數 `token`，以及可打開的 Viewer 連結。**只在可信任的私有 Wi-Fi 使用**，服務預設監聽所有介面，沒有 TLS。

另開終端機（複製實際 token）：

```powershell
py desktop_sender.py --server http://127.0.0.1:8765 --token <TOKEN> --fps 1 --monitor 1
```

`--fps 1` 表示每秒取樣 1 張；`--width 1200` 控制影像最長邊；`--threshold 175` 啟用黑白二值化；`--demo` 不需螢幕擷取權限，用於連線測試。

**手機或其他電腦要觀看**：在同一個 Wi-Fi 開啟伺服器提供的完整 `http://<PC-IP>:8765/viewer?token=<TOKEN>`。注意：這必須是**接收端能使用網頁瀏覽器**時才可行。

## Android 手機擷取

`android_sender/` 是獨立 Android Studio 專案（Java，API 26+）。在 Android Studio 開啟該資料夾、Sync、Build APK，安裝在 **Android 手機**上（不是 mooInk），輸入電腦伺服器位置及 token，點擊 Start，授予系統螢幕錄製權限，再去 `/viewer` 檢視影像。

- 螢幕擷取使用 Android MediaProjection，手機會顯示系統錄製提示及常駐通知。
- 每約 1.5 秒傳送一張最高寬度約 1000px 的 JPEG 灰階影像。
- 需 Android 手機與電腦同 Wi-Fi，且 Windows 防火牆允許連入 TCP 8765。
- Android 畫面錄製受 Android 系統限制；DRM / 安全畫面可能全黑。
- **iPhone 螢幕擷取尚未實作**；不可將 Android APK 安裝於 iPhone。

## 網頁觀看端

- `/viewer?token=...`：隨新影像更新；可暫停、自訂更新間隔、黑白反轉。
- `/api/status?token=...`：目前序號、大小及更新時間。
- `/frame.jpg?token=...`：最新影像。
- `POST /api/frame?token=...`：接收上限 4 MiB 的 JPEG。
- `/health`：服務健康檢查。

HTTP 請求皆在本機處理，未啟用雲端。

## 如何判斷你的 mooInk 能否接上？

1. 提供**完整型號**（如 mooInk Pro 2、Pro 2C、Pro 13）、系統版本。
2. 確認是否有可在 mooInk **直接輸入網址**的瀏覽器；若有，在同 Wi-Fi 開 Viewer 網址測試。
3. 若沒有瀏覽器，也不能安裝 App，則需要先透過裝置官方支援或硬體實測確認可用的顯示控制途徑；本專案不能憑空使封閉韌體持續顯示外來影像。
4. mooInk 2026 年新推出的 Wi-Fi **傳檔**是文件匯入，而不是已證明可連續更新的「顯示協定」。

## 開發狀態

| 模組 | 狀態 |
|---|---|
| Windows 螢幕擷取 / 灰階 / JPEG | 初版原始碼 |
| HTTP 安全 token / Frame 管線 | 初版原始碼 |
| 瀏覽器灰階顯示 | 初版原始碼 |
| Android 手機 MediaProjection | 原始碼；尚未在實機編譯或執行 |
| iPhone | 未實作 |
| mooInk 原機顯示 / 自動刷新 | **未驗證、未實作** |

## 資料來源

- [Readmoo 2023：mooInk Pro Connect 畫面方向](https://news.readmoo.com/2023/10/31/mooink-pro-connect-app/)
- [mooInk 第三方 App 安裝政策](https://www.cite.com.my/information.php?id=1294)
- [Readmoo 2026：Wi-Fi 傳檔](https://news.readmoo.com/2026/08/20/260820-wifi/)

## 授權

MIT（僅本專案自行撰寫程式）。請確認連線僅在本人擁有與允許的網路及裝置上使用。
