# 安全更新 adapter 合約

`display_update.c` 是獨立的更新協調器，其 callback 尚未接上真實 MTP、SMB、Bluetooth 或 flash。現有 UF2 全部維持唯讀（debug log 除外）。不能把本模組當作可燒錄的整合版。

## 安全流程

1. 讀取持久 journal；未完成、損壞、無法讀取即拒絕自動施工。
2. Discover 唯一的目標，驗證 USB generation、storage、sleep parent、物件身分及舊圖完整 hash。
3. Stage 來源為不可變快照，確認封存 ID、大小與 hash。預設呼叫 enable_write=false，只執行檢查與觀測；adapter 的 stage 可能仍需要專用暫存區，但不得修改 mooInk。
4. 內容相同即跳過；寫入模式需可寫 storage、必要 opcodes、保守剩餘容量（不依賴舊圖刪除後釋出的空間）。
5. 若有舊圖，備份到獨立封存位置；完整重讀 hash/size，重新確認目標及來源。
6. 持久化 PREPARED、MUTATING 記錄，確認耐久寫入；之後才可能執行刪除。每個 mutator 必須在實際操作前再次確認同一 USB session / handle / parent；不能只相信先前的 discover。
7. 刪除後確認同一目標位置無同名檔，才上傳封存版本。重新列舉及 GetObject 完整 hash 驗證，再寫 VERIFIED。
8. 先持久化 WAKE_ATTEMPTED，最後核對目標身分及內容，才交給 Bluetooth 送一次 Space down/up。不自動重送。COMPLETE 只表示本地堆疊接受，並非螢幕真的刷新。

所有失敗均保留備份與 journal；journal 未完成需人工以唯讀方式確認現況，禁止自動刪除重試。持久層必須提供原子／可復原的寫入、完整性校驗，及明確的未初始化狀態。CRC/雙槽實作尚待完成。adapter 將來需負責封存的真實驗證、時間限制、取消、垃圾回收、PIN/BT peer 綁定及復原工具。

測試 fake 注入每個 callback 失敗（含模擬已發生副作用的失敗）、USB generation 變動、備份 hash 錯誤、回讀錯誤、容量不足、重複輪詢。不代表硬體測試。
