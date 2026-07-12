# 台股選股系統 — 起手骨架（Python + PostgreSQL/pgvector + RAG）

> 對應 [skill_gap_checklist.md](../skill_gap_checklist.md) 第 1～2 項、[stock_screener.md](../stock_screener.md) 第 0～3 期。
> 目的：**最小但正確**地把「抓資料 → 入庫 → RAG 報告」跑通。先求正確（還原價、point-in-time、發布時間），不求完整。

## 這個骨架有什麼
| 檔案 | 做什麼 | 對應 checklist |
|---|---|---|
| `schema.sql` | 建 PostgreSQL 表（含 pgvector） | #2 |
| `fetch_finmind.py` | FinMind 抓 2330 還原日K + 月營收 + 三大法人 → 入庫 | #1 Python |
| `rag_news.py` | 新聞文本 → embedding → pgvector 相似度檢索 → LLM 生成「為什麼選它」報告 | #2 RAG |
| `technical_signals.py` | 從 price_daily 算技術面訊號（均線排列/黃金叉/三線聚攏/帶量突破/量價/K棒型態） | 技術面選股 |

## 前置需求
1. **Python 3.10+**
2. **PostgreSQL 14+ 並安裝 pgvector 擴充**（`CREATE EXTENSION vector;` 要能成功）
3. **FinMind token**：到 https://finmindtrade.com 註冊取得（免費方案即可起步）
4. **OpenAI API key**：embeddings 用 `text-embedding-3-small`（之後可換 Claude/Voyage/本地模型）

## 安裝與設定
```bash
cd stock_screener_starter
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env        # 然後填入你的 token / 連線字串 / API key
```

## 執行順序
```bash
# 1) 建表（擇一）
psql "$DATABASE_URL" -f schema.sql
#    或者：python fetch_finmind.py 會在開頭自動套用 schema.sql

# 2) 抓資料入庫（預設 2330 台積電，近 2 年）
python fetch_finmind.py --stock 2330 --start 2024-01-01

# 3) 算技術面訊號（純 pandas；K棒型態需 TA-Lib，沒裝會自動略過）
python technical_signals.py                       # 掃所有股票，列出最新一天觸發的訊號
python technical_signals.py --stock 2330 --history  # 看單檔最近 20 天訊號

# 4) 跑 RAG（無新聞時會自動塞幾筆示範新聞）
python rag_news.py --stock 2330 --q "台積電最近的基本面與法人動向如何？"
```

> 註：`technical_signals.py` 目前只會掃你已抓進 `price_daily` 的股票（先只有 2330）。多抓幾檔（`fetch_finmind.py --stock XXXX`）後，它就會跨股掃描、列出當天觸發訊號的清單——這就是「技術面抓時機」那層的雛形。

## ⚠️ 誠實提醒（給學習用，非生產級）
- **FinMind 的方法名/欄位可能隨版本變動** —— 跑不動時對照官方文件 https://finmind.github.io/ 調整。
- **月營收的 `announce_date` 是近似值**（以次月 10 日估）；認真回測要用實際公告日，否則前視偏差。
- **新聞 `published_at` 一定要存**：回測檢索時只能用「當下已發布」的新聞，否則偷看未來。
- 這是**研究/原型**用的 Python 版；正式服務層你會用 Spring AI（見 [stock_screener.md](../stock_screener.md) 技術選型）。


---

# 📥 資料下載 SOP（CSV 落地版）

把全市場「股價」與「基本面」抓成 CSV，存到本機 `H:\data`。這條線**不依賴 PostgreSQL**，純抓檔，之後要入庫或用 DuckDB/pandas 讀都行。

## 下載工具一覽
| 檔案 | 用途 | 來源 / 認證 |
|---|---|---|
| `fetch_stock_codes.py` | 全市場代碼+名稱+市場別+上市櫃日期 → Excel | TWSE ISIN，免認證 |
| `batch_download.py` | 批次抓**股價**（上市/上櫃自動分流、依上市日定起點）| TWSE/TPEx，免認證 |
| `download_twse_csv.py` / `download_tpex_csv.py` | 單檔股價下載器（上市 / 上櫃）| TWSE/TPEx，免認證 |
| `batch_fundamentals.py` | 批次抓 **FinMind 個股資料集**（10 種，見步驟 2）| FinMind，**需 token** |
| `fetch_fundamentals.py` | 單檔 FinMind 資料集下載器（`--datasets` 選）| FinMind，**需 token** |
| `fetch_index.py` | 大盤指數（TAIEX 加權報酬指數）| FinMind，**需 token** |
| `build_adjusted_price.py` | 用原始日K + 股利算**還原股價**（純本機運算）| 無（讀本機 CSV）|
| `update_prices.py` | **更新指定交易日**全市場股價，併入各股月檔（2 個請求）| TWSE/TPEx，免認證 |
| `update_month_prices.py` | **檢查/補齊指定月份**月檔，殘缺就整月重抓覆蓋 | TWSE/TPEx，免認證 |
| `load_to_db.py` | 讀 CSV **入庫 PostgreSQL**（正規化 + 算 available_date + upsert；支援 `--since` 增量）| 本機 CSV → DB |
| `daily_update.py` | **每日一鍵**（4 步）：update_prices → build_adjusted_price → load_to_db --since → update_chips | 綜合（收盤後跑）|
| `update_chips.py` | **by-date 全市場**法人/融資/PER 直接入庫 + 存原始快照（取代 FinMind 逐檔，一天 6 請求）| TWSE/TPEx，免認證 |
| `update_revenue.py` | **by-date 全市場**月營收 → `monthly_revenue`（官方 OpenAPI，正確營收月）| TWSE/TPEx OpenAPI，免認證 |
| `update_fundamentals.py` | **低頻**財報/EPS/股利/減資更新：串 `fetch_fundamentals → load_to_db`（季/年跑；preset：quarterly/dividend/revenue/capreduction/all）| FinMind，**需 token** |
| `update_holderdist.py` | **by-date 全市場**集保戶股權分散（千張/400張大戶）→ `shareholding_dist`（每週）| TDCC opendata，免 token |
| `nightly.py` | **排程大腦**：每晚跑這一支，依當天日期自動判斷該跑哪些更新（狀態檔防重）| 綜合（每晚固定跑）|

> 需求：`pip install pandas lxml openpyxl`（下載器本身純標準庫，代碼清單 / 批次驅動 / 基本面 / 還原才需要這些）；入庫另需 `pip install psycopg2-binary`。

> **資料更新管道**：① 每日的價量與籌碼（股價/法人/融資/PER）走**證交所 by-date**（`daily_update.py` + `update_chips.py`，快、免 token）；② 月營收走**官方 OpenAPI by-date**（`update_revenue.py`）；③ 集保股權分散走 **TDCC opendata by-date**（`update_holderdist.py`）；④ 低頻的財報/EPS/股利/減資走 **FinMind 逐檔**（`update_fundamentals.py`）。
> **FinMind 自動限流**：`fetch_fundamentals.py` 內建滑動視窗限流，預設**每小時最多 600 次**（免費層額度），達上限自動暫停等視窗釋放，不會撞 402——可一行跑到底、無人值守（`--max-per-hour` 可調，付費層調高、0=不限）。`batch_/update_fundamentals` 皆沿用。

## 輸出結構
```
H:\data\
├─ <股號>\
│  ├─ <股號>_YYYYMM.csv            股價（每檔每月一個 CSV，原始價）
│  └─ <股號>_adj.csv               還原股價（build_adjusted_price.py 產出）
├─ <股號>.txt                      該檔「沒抓到資料」的月份清單（參考用）
├─ Index\TAIEX.csv                 大盤加權報酬指數
├─ Chips\<日期>\                   每日全市場籌碼/估值原始快照（update_chips.py 存，備份/稽核）
│  ├─ TWSE_T86.csv / TWSE_MI_MARGN.csv / TWSE_BWIBBU.csv     上市法人/融資/PER（Big5）
│  └─ TPEX_inst.json / TPEX_margin.json / TPEX_per_openapi.json  上櫃法人/融資/PER
├─ Holders\                        集保戶股權分散原始快照（update_holderdist.py --raw-root 存）
│  └─ tdcc_holderdist_YYYYMMDD.csv 每週一份（TDCC opendata 全市場）
└─ Fundamentals\<股號>\            FinMind 個股資料（不只基本面，含籌碼/估值）
   ├─ <股號>_revenue.csv           月營收（含 月增率% / 年增率%）
   ├─ <股號>_financials.csv        綜合損益表（EPS / 毛利率% / 營業利益率%）
   ├─ <股號>_balance.csv           資產負債表     ├─ <股號>_cashflow.csv   現金流量表
   ├─ <股號>_dividend.csv          股利政策       ├─ <股號>_per.csv        每日 PE/PB/殖利率
   ├─ <股號>_institutional.csv     三大法人淨額   ├─ <股號>_margin.csv     融資融券
   ├─ <股號>_shareholding.csv      外資持股 + 發行股數
   └─ <股號>_capreduction.csv      減資參考價（還原用）
```

## 標準流程

### 步驟 0：抓全市場代碼清單
```bash
python fetch_stock_codes.py --out 台股股票代碼NEW.xlsx
```
產出欄位：股票代碼、中文名稱、市場別、上市櫃日期、產業別（只含普通股，已濾掉 ETF/權證）。

### 步驟 1：抓股價（免 token）
```bash
python batch_download.py --xlsx 台股股票代碼NEW.xlsx --limit 5     # 先小測
python batch_download.py --xlsx 台股股票代碼NEW.xlsx               # 全跑
```
- 每檔起抓月份 = `max(上市櫃日期月份, floor)`；floor：上市=`--start`(預設 2010-01)、上櫃=`--start-otc`(預設 1994-01)。
- 全市場很慢（~1900 檔），可中途停、重跑自動跳過已下載月份。

### 步驟 2：抓 FinMind 個股資料（需 token）
10 個資料集（`--datasets` 選，預設全部）：`revenue, financials, balance, cashflow, dividend, institutional, margin, per, shareholding, capreduction`。
```bash
# 確認 token 被讀到：跑這行若沒出現「⚠️ 未提供 token」就 OK
python batch_fundamentals.py --xlsx 台股股票代碼NEW.xlsx --limit 3 --delay 6

# 建議先挑重點（省額度）
python batch_fundamentals.py --xlsx 台股股票代碼NEW.xlsx --datasets institutional,per,balance,dividend --delay 7

# 全部 10 個資料集（每檔 10 次 API，1970 檔≈19700 次，要分多時段跑）
python batch_fundamentals.py --xlsx 台股股票代碼NEW.xlsx --delay 7

# 之後更新
python batch_fundamentals.py --xlsx 台股股票代碼NEW.xlsx --delay 7 --refresh
```
- token：設環境變數 `FINMIND_TOKEN`（用 `setx` 設要開新終端機才生效），或 `--token 你的TOKEN`。
- 續抓是**資料集層級**：哪個 CSV 缺補哪個；撞到額度(402)自動退避，改天重跑會自動接續。
- **還原股價（`_adj`）不在這裡**：FinMind 的還原價是付費資料集，改用步驟 3 自己算。

### 步驟 2.5：抓大盤指數（需 token）
```bash
python fetch_index.py --start 2010-01-01        # → H:\data\Index\TAIEX.csv
```

### 步驟 3：算還原股價（純本機，免 token）
用步驟 1 的原始日K + 步驟 2 的 `dividend.csv` **與 `capreduction.csv`**，算出還原價（除權息＋減資都連續，技術面/回測必用）。
```bash
python build_adjusted_price.py 2330                              # 單檔
python build_adjusted_price.py                                    # 掃 H:\data 下全部個股
```
- 產出 `<股號>_adj.csv`，含 `adj_open/high/low/close` 與 `cumfactor`；最新一天=原始價，往前調整。
- **除權息**（dividend）+ **減資**（capreduction）都會還原；沒有事件的股票則 `adj=原始`（因子=1）。

### 步驟 4：每日更新（收盤後跑，最省）
歷史抓完後，每天只要更新當天即可——用「當日全市場」端點，**2 個請求**更新整個市場：
```bash
python update_prices.py                       # 更新「今天」→ H:\data
python update_prices.py --date 2026-07-03     # 指定某交易日
```
- 只更新**已下載過**（有資料夾）的個股；`--all` 才會連新股/ETF 一起建檔。
- 上市走 TWSE MI_INDEX、上櫃走 TPEx（自動換算成 張/千元 對齊月檔）；當天那列**併入** `<股號>_YYYYMM.csv`，依日期去重排序，可重跑。
- 更新完記得重跑 `build_adjusted_price.py` 讓還原價含最新日。非交易日會自動略過。
- 若某月是在**月中**下載的（月檔只到當時、之後交易日缺漏），用 `python update_month_prices.py 2026-07` 逐檔比對「月檔最大日 vs 該月最後交易日」，殘缺就整月重抓覆蓋（先 `--dry-run` 看缺哪些）。

### 步驟 5：入庫（CSV → PostgreSQL）
把前面所有 CSV 讀進 PostgreSQL 供 SQL 選股/回測。**前四步純 CSV、不碰 DB，這步才用到資料庫。**

前置：先建 database 並套用**核心表** schema（`schema_rag.sql` 是新聞向量，需先裝 pgvector，等做 RAG 再跑）：
```bash
psql -U postgres -c "CREATE DATABASE twstock ENCODING 'UTF8';"
psql -U postgres -d twstock -f schema.sql
```
連線字串擇一：設環境變數 `DATABASE_URL`，或每次帶 `--dsn`。

**全量載入**（第一次；可重跑，靠 `ON CONFLICT DO UPDATE` upsert）：
```bash
python load_to_db.py --dry-run                       # 先不連 DB，只驗證讀檔/轉換（印各表列數+樣本）
python load_to_db.py --dsn "postgresql://postgres:pwd@localhost:5432/twstock"
python load_to_db.py --dsn "..." --codes 2330,5483   # 只灌指定幾檔（測試）
python load_to_db.py --dsn "..." --tables price_daily,monthly_revenue   # 只灌指定表
```
- 股價讀 `<股號>_adj.csv`（**還原檔**，不是原始月檔）→ 務必先跑完步驟 3；同一列同時存原始 OHLC、`volume`、`amount` 與還原 `adj_*` + `adj_factor`。
- 上櫃**成交量張→股、成交金額千元→元**（皆 ×1000）正規化；月營收/財報自動算 `available_date`（防前視）。

**增量載入**（每日同步，`--since`）：只 upsert 日期 >= 指定日的資料列，DB 寫入量大減。
```bash
python load_to_db.py --dsn "..." --since 2026-07-11
```
- 時序表依各自日期欄過濾（`price_daily`/`inst_trades`… 用 `trade_date`、`monthly_revenue` 用 `revenue_month`、財報用 `period_date`、`dividend` 用 `announce_date`）；維度表 `stock` 不受限、永遠全載（外鍵母表）。
- 仍會**讀完**所有 CSV，只是少 upsert：`--since` 省的是 DB 寫入不是讀檔；要更快可再加 `--codes` / `--tables` 縮範圍。

**每日一條龍**（收盤後）：上面步驟已包成一支 **`daily_update.py`**，跑一行即可：
```bash
python daily_update.py --dsn "postgresql://frank:pwd@localhost:5432/twstock"  # 股價+籌碼全更新
python daily_update.py --date 2026-07-11 --dsn "..."   # 指定交易日
python daily_update.py --date 2026-07-11 --dry-run     # 步驟 1、2 照做；入庫只驗證不寫 DB
python daily_update.py --skip-load                     # 只更新 CSV（不寫 DB；籌碼仍存快照）
python daily_update.py --skip-chips --dsn "..."        # 只更新股價，略過法人/融資/PER
python daily_update.py --only 2330 5483 --dsn "..."    # 只跑幾檔股價（測試/補單檔）
```
它依序做（順序不可顛倒）：
1. `update_prices.run` 抓當天全市場股價 → 併入各股月檔，**回傳當天有更新的股號**；
2. 只對「有更新的股」重算 `_adj.csv`（含最新日，效率比全掃高）；
3. `load_to_db --since <日>` 只把當天股價 upsert 進 DB（預設只灌 `price_daily`，`--tables` 可調）；
4. `update_chips --date <日>` by-date 抓全市場**法人/融資/PER** 直接入庫 + 存原始快照。
> ⚠️ 為什麼 1→2→3 不可顛倒：跳過第 2 步，`load_to_db` 讀不到當天的還原價，`price_daily` 就缺這一天。非交易日第 1 步回空 → 自動跳過後續。
> 連線：設環境變數 `DATABASE_URL` 或帶 `--dsn`（`--dry-run` / `--skip-load` 不需要；此時 step 4 只存快照不寫 DB）。

### 步驟 6：籌碼 / 估值 / 營收 / 財報的定期更新
`daily_update.py` 只顧股價（+ step 4 的籌碼）。其餘基本面依頻率各有工具：

**① 法人 / 融資 / PER（每日）— `update_chips.py`**
by-date 抓證交所/櫃買當日全市場，直接入庫 `inst_trades` / `margin_trading` / `valuation_daily`，並存原始快照到 `H:\data\Chips\<日期>\`。**已內含在 `daily_update.py` step 4**；要單獨跑或回補：
```bash
python update_chips.py --date 2026-07-09 --dsn "..."                 # 單日
python update_chips.py --start 2026-07-06 --end 2026-07-09 --dsn "..." # 回補區間（非交易日自動略過）
python update_chips.py --date 2026-07-09 --dry-run                    # 不寫 DB，只存快照+印筆數
```
- 單位已對齊 DB：法人=股、融資=張、PER=倍，**免換算**。只灌存在於 `stock` 表的代號（ETF 等自動過濾）。
- 上櫃 PER 走 OpenAPI（只回最新日）；回補舊日期時因日期不符會自動略過上櫃 PER（上市 PER 照抓）。

**② 月營收（每月）— `update_revenue.py`**
官方 OpenAPI 抓全市場（上市一般/金融 + 上櫃），直接 upsert `monthly_revenue`。
```bash
python update_revenue.py --dsn "..."            # 每月 11~15 號跑；間隔幾天再跑補晚申報者
python update_revenue.py --dsn "..." --dry-run  # 只印月份與筆數
```
- 用「資料年月」＝**真實營收月**（非 FinMind 的公告月）；當月營收單位仟元 → 自動 ×1000 對齊元。
- 各公司申報時間不一，單次通常涵蓋 ~1750 檔，晚申報者隔幾天重跑即補齊（upsert 可重複）。

**③ 財報 / EPS / 股利 / 減資（每季 / 每年 / 偶發）— `update_fundamentals.py`**
低頻資料沿用 FinMind 逐檔（成本可接受）；串「fetch → load」一鍵，**內建 600/hr 限流可一行跑到底**：
```bash
# 季報季（2/5/8/11 月中）補最新季財報/EPS
python update_fundamentals.py --preset quarterly --start 2025-06-30 --dsn "..."
# 股利旺季（5~8 月）
python update_fundamentals.py --preset dividend --start 2026-01-01 --dsn "..."
# 減資（偶發；還原價會用到）
python update_fundamentals.py --preset capreduction --start 2010-01-01 --dsn "..."
python update_fundamentals.py --preset quarterly --codes 2330,2317 --start 2025-06-30 --dsn "..."  # 先測幾檔
python update_fundamentals.py --preset capreduction --codes-file remaining.txt --start 2010-01-01 --dsn "..."  # 用清單續跑
```
- `--start` 給近一年即可（只重抓/入庫近期）；需 `FINMIND_TOKEN`。preset：`quarterly`（財報+EPS）/ `dividend` / `revenue` / `capreduction`（減資）/ `all`。
- **限流**：全市場 ~1970 檔逐檔（1 檔 1 請求含全歷史），在 600/hr 下自動衝→睡→衝，約 3~4 小時無人值守跑完；不用再手動分時段。`--max-per-hour` 可調。
- **續跑**：`--codes-file 檔案`（每行一代碼）可只跑指定清單，用於中斷後接續。

**④ 集保戶股權分散（每週）— `update_holderdist.py`**
TDCC opendata 抓全市場「集保戶股權分散表」（免 token）→ `shareholding_dist`（持股分級 1~15；**千張大戶=level 15、400張大戶=level 12~15**）。TDCC 只回最新一週 → 每週跑一次累積。
```bash
python update_holderdist.py --dsn "..." --raw-root "H:\data\Holders"   # 抓本週入庫 + 存快照
python update_holderdist.py --dry-run                                  # 只印日期與筆數
```
查大戶持股比例：
```sql
SELECT stock_id, pct FROM shareholding_dist WHERE data_date='2026-07-03' AND level=15 ORDER BY pct DESC;      -- 千張
SELECT stock_id, sum(pct) FROM shareholding_dist WHERE data_date='2026-07-03' AND level>=12 GROUP BY stock_id; -- 400張
```
> 歷史回補：TDCC opendata 只有最新一週；要補歷史需 FinMind 逐檔（同樣受 600/hr 限流）。

**定期更新排程總表**
| 頻率 | 指令 | 更新內容 |
|---|---|---|
| 每交易日收盤 | `daily_update.py --dsn ...` | 股價 + 法人 + 融資 + PER |
| 每月 11~15 號 | `update_revenue.py --dsn ...` | 月營收（隔幾天再跑補晚申報） |
| 每週（TDCC 更新後）| `update_holderdist.py --dsn ... --raw-root H:\data\Holders` | 集保股權分散（大戶持股）|
| 2/5/8/11 月中 | `update_fundamentals.py --preset quarterly --start <近一年> --dsn ...` | 財報 / EPS |
| 股利旺季 5~8 月 | `update_fundamentals.py --preset dividend --start <年初> --dsn ...` | 股利 |
| 偶發 / 一次性 | `update_fundamentals.py --preset capreduction --start 2010-01-01 --dsn ...` | 減資 |

### 步驟 7：排程大腦（每晚一鍵，推薦）— `nightly.py`
不想自己記上表「哪天跑哪支」？**每晚固定跑 `nightly.py` 就好**，它依「今天日期」自動判斷該執行哪些工作：
```bash
python nightly.py --plan                 # 先看今天會跑哪些、為什麼（不執行）
python nightly.py --dsn "..."            # 實際執行（或設好 DATABASE_URL 後直接 python nightly.py）
python nightly.py --only quarterly --dsn "..."   # 強制單跑某工作（忽略排程/狀態檔）
python nightly.py --date 2026-08-15 --plan       # 模擬某天的排程決策
```
它管理的工作與規則：

| 工作 | 何時跑 | 防重 |
|---|---|---|
| daily（股價+法人+融資+PER）| 每晚（非交易日自動跳過）| — |
| revenue（月營收）| 每月 11~20 號 | 便宜，窗口內每晚跑以補晚申報 |
| quarterly（財報/EPS）| 4月(年報)、5/16、8/15、11/15 起 | 狀態檔記「本季已跑」，跨夜不重跑 |
| dividend（股利）| 5~8 月每週一次 | 狀態檔記「本週已跑」 |

- **狀態檔 `nightly_state.json`**（自動建立）：記錄 quarterly/dividend 這類 FinMind 逐檔的重工作「本季/本週已完成」，避免跨夜重跑；daily/revenue 便宜則照窗口每晚跑。
- 有工作失敗會以非 0 離開，方便排程器告警。

**設成每晚自動執行（Windows 工作排程器）**：先設環境變數（開新視窗才生效），再建排程：
```bat
setx DATABASE_URL "postgresql://frank:pwd@localhost:5432/twstock"
setx FINMIND_TOKEN "你的token"
schtasks /create /tn "twstock nightly" /tr "python <專案路徑>\nightly.py" /sc daily /st 20:00
```
設好環境變數後 `nightly.py` 免帶 `--dsn` 也會讀 `DATABASE_URL`；之後就完全不用管。

## 指定單檔 / 指定月份（臨時補抓）
月份用 `--start` / `--end`（`YYYY-MM`），**單月就兩個設一樣**：
```bash
# 上市
python download_twse_csv.py 2330 --start 2015-03 --end 2015-03      # 單檔單月
python download_twse_csv.py 2330 2317 1101 --start 2015-01 --end 2015-12   # 多檔區間
# 上櫃
python download_tpex_csv.py 5483 --start 2015-03 --end 2015-03

# 基本面（注意 FinMind 用 YYYY-MM-DD）
python fetch_fundamentals.py 2330 --start 2015-01-01
```
> 重抓某月 → 先刪掉該月 CSV 再跑（已存在會被跳過）。

## ⚠️ 下載相關提醒
- **TWSE 個股日成交端點只到 2010/01**；更早回傳空，屬資料源限制，非程式問題。
- **單位不同**：TWSE 股價成交股數=股、金額=元；TPEx=張、千元；FinMind 月營收=元、官方 OpenAPI 月營收=仟元（`update_revenue.py` 已 ×1000）。合併分析要換算。
- **月營收月份**：FinMind 的 `date` 欄是「公告月」（比真實營收月多一個月），`load_to_db.load_revenue` 已改用 `revenue_year`/`revenue_month` 欄組真實營收月；`update_revenue.py` 用官方「資料年月」亦為真實營收月。`available_date` = 次月 10 日。
- **回測防前視**：月營收次月 10 日才公告、財報 5/15・8/14・11/14・3/31 才公告；財報 FinMind 的 `date` 是期別日非公告日，回測請用 `available_date` 過濾。
- **技術面/回測一律用 `_adj.csv`（還原價）**，別用原始 `_YYYYMM.csv`，否則除權息日會被當成暴跌。