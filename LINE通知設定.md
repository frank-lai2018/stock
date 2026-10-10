# 每晚的 LINE 策略通知

完整執行原本的 `nightly.py`／`run_nightly.bat`，在必要資料更新、選股快照刷新與決策保存完成後，會推播「日線型態突破＋趨勢持有60日」到 `LINE_USER_ID` 指定的個人 LINE。讀取與決策保存同一次掃描的資料，最多入選三檔。這項功能使用 LINE Messaging API。

## 帳號與設定

1. 在 LINE Official Account Manager 建立官方帳號，啟用 Messaging API。
2. 在 LINE Developers 的該頻道取得 Channel access token 與接收人的 User ID。
3. 用接收人的個人 LINE 加入該官方帳號好友，並保持未封鎖。
4. 在 Windows 環境變數設定 `LINE_TOKEN`、`LINE_USER_ID`。內容不要加入 Git、截圖或日誌。

程式先讀目前行程的環境變數；Windows 上若沒有，會補讀該使用者／系統已儲存的環境變數。所以啟動中的終端機也能讀到後來新增的設定。若是使用者環境變數，Windows 工作排程須以設定這些變數的使用者執行。未設定兩個變數時，資料更新照常執行、通知略過；只設定一個或格式錯誤時會報錯。

這次串接沿用原本的 nightly 入口，**沒有新增 Windows 定時工作**。電腦與資料庫須在 nightly 執行時開啟。可先手動執行原本的 `run_nightly.bat`；如果另設工作排程，建議在官方資料公布後的晚上執行，並於當晚完成。

## 通知採用的策略

| 項目 | 設定 |
| --- | --- |
| 決策模式／進場條件 | `trend_hold`／`breakout`，與策略研究的「趨勢持有60日・型態突破」共用引擎 |
| 母體 | 全部族群；20日平均成交額至少2千萬元；近3日型態突破 |
| 可執行條件 | 既有引擎判定型態突破為 priority，包括分數至少75、突破量比至少1.5、離頸線0～5%、RS至少70等條件 |
| 排序與大盤 | RS排序；加權指數站上60日均線才開新倉 |
| 下單參考 | 次交易日開盤進場；成交後重算股數與停損；未串接券商 |
| 出場 | 實際成交價下8%停損；收盤低於50日線後次交易日開盤出場；最長第60個交易日收盤出場，不設固定停利 |
| 部位試算 | 淨值100萬、可用現金100萬；單筆風險0.75%、單檔資金25%、每日最多新倉3檔、同產業最多2檔、總持股最多10檔、總停損風險4% |
| 零股 | `lot_size=1`，與研究的允許零股設定一致 |
| 既有持股 | 交易帳已持有的股票計入產業、持股檔數與風險限制，可能使新倉為零 |

上述為通知的固定試算設定，**不會跟隨瀏覽器頁面當下的族群、複合條件、資金或排序選項**。原本決策歷史的預設整張設定仍照常保存。新增通知不會改動核心策略或研究指紋。

日報包含行情日期、大盤狀態、入選股票的名稱／族群／型態／RS／分數／量比／收盤價／試算股數、未入選原因，以及交易帳的出場檢查。沒有新買候選也會收到一則日報。

交易帳沒有買進策略標籤，因此持股檢查是將這套出場規則套用到所有未平倉買批的參考。使用 FIFO 剩餘股數、每批實際成交價與買進日期；透過還原因子避免除權息造成假的停損。市場交易日含買進日起算，第59日提醒次交易日收盤到期，60日以上提醒核對未平倉紀錄。行情不完整會顯示無法完整檢查。這是盤後檢查，並無盤中停損監控。

## 檢查、預覽與測試

以下在 `stockselect/backend` 執行，Python 需有後端與夜間更新所需的套件；本機已驗證可用 `D:\python\3.13.13\python.exe`。

```powershell
# 讀取設定並向 LINE 查詢本月額度，不推播、不連資料庫
python notify_daily_decision.py --check-config

# 唯讀預覽最新行情；不發 LINE、不寫入決策紀錄
python notify_daily_decision.py --dry-run

# 傳一則連線測試；同日重跑不再發送
python notify_daily_decision.py --test

# 必要資料及決策已完成時，可手動補送當日日報
python notify_daily_decision.py
```

在專案根目錄可使用：

```powershell
python nightly.py --plan        # 僅顯示工作計畫
python nightly.py --skip-line   # 照常更新與保存決策，本次略過 LINE
```

自動推播只接在完整 nightly；`--only`、`--skip-refresh`、明確略過必要資料工作時不推播。歷史／未來日期也不推播，避免補跑時大量發送舊訊號。跨午夜後不補送前一天日報；可用 `--dry-run` 查閱。週末與證交所官方休市日略過正常日報；臨時停市若尚未在官方休市表內，會顯示當日行情未取得。

## 發送與錯誤處理

- 每個接收人、日期與策略的正常日報最多發送一次，連線測試和失敗告警有各自的防重識別。必要工作失敗只發告警；修復並重跑後可補送一則正常日報。
- 網路／LINE伺服器暫時失敗時，最多重試三次，保持相同訊息與 retry key；超過23小時停止重試，避免 LINE 的24小時重試期限過後重複發送。
- 發送前查詢月額度；額度用完就停止推播並讓 nightly 回報失敗，不會自行升級付費方案。
- 防重紀錄保存在 `stockselect/backend/.local/line_notifications.sqlite3`，休市快取也在 `.local`。這些檔案已忽略，不進版控。紀錄含訊息內容與接收人雜湊，不含 Token／User ID 原文；清除紀錄會失去防重能力。
- 「LINE 已接受推播請求」表示 API 已接受，仍須在手機確認有收到。如果沒有，先確認接收人、好友關係與是否封鎖官方帳號。LINE推播失敗可先用 `--check-config` 查驗，再查看 nightly 日誌。

官方文件：[Messaging API 設定](https://developers.line.biz/en/docs/messaging-api/getting-started/)、[Push API](https://developers.line.biz/en/reference/messaging-api/#send-push-message)、[相同請求重試](https://developers.line.biz/en/docs/messaging-api/retrying-api-request/)、[訊息計費方式](https://developers.line.biz/en/docs/messaging-api/pricing/)。
