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
| `batch_fundamentals.py` | 批次抓 **FinMind 個股資料集**（9 種，見步驟 2）| FinMind，**需 token** |
| `fetch_fundamentals.py` | 單檔 FinMind 資料集下載器（`--datasets` 選）| FinMind，**需 token** |
| `fetch_index.py` | 大盤指數（TAIEX 加權報酬指數）| FinMind，**需 token** |
| `build_adjusted_price.py` | 用原始日K + 股利算**還原股價**（純本機運算）| 無（讀本機 CSV）|

> 需求：`pip install pandas lxml openpyxl`（下載器本身純標準庫，代碼清單 / 批次驅動 / 基本面 / 還原才需要這些）。

## 輸出結構
```
H:\data\
├─ <股號>\
│  ├─ <股號>_YYYYMM.csv            股價（每檔每月一個 CSV，原始價）
│  └─ <股號>_adj.csv               還原股價（build_adjusted_price.py 產出）
├─ <股號>.txt                      該檔「沒抓到資料」的月份清單（參考用）
├─ Index\TAIEX.csv                 大盤加權報酬指數
└─ Fundamentals\<股號>\            FinMind 個股資料（不只基本面，含籌碼/估值）
   ├─ <股號>_revenue.csv           月營收（含 月增率% / 年增率%）
   ├─ <股號>_financials.csv        綜合損益表（EPS / 毛利率% / 營業利益率%）
   ├─ <股號>_balance.csv           資產負債表     ├─ <股號>_cashflow.csv   現金流量表
   ├─ <股號>_dividend.csv          股利政策       ├─ <股號>_per.csv        每日 PE/PB/殖利率
   ├─ <股號>_institutional.csv     三大法人淨額   ├─ <股號>_margin.csv     融資融券
   └─ <股號>_shareholding.csv      外資持股 + 發行股數
```

## 標準流程

### 步驟 0：抓全市場代碼清單
```bash
python fetch_stock_codes.py --out 台股股票代碼NEW.xlsx
```
產出欄位：股票代碼、中文名稱、市場別、上市櫃日期（只含普通股，已濾掉 ETF/權證）。

### 步驟 1：抓股價（免 token）
```bash
python batch_download.py --xlsx 台股股票代碼NEW.xlsx --limit 5     # 先小測
python batch_download.py --xlsx 台股股票代碼NEW.xlsx               # 全跑
```
- 每檔起抓月份 = `max(上市櫃日期月份, floor)`；floor：上市=`--start`(預設 2010-01)、上櫃=`--start-otc`(預設 1994-01)。
- 全市場很慢（~1900 檔），可中途停、重跑自動跳過已下載月份。

### 步驟 2：抓 FinMind 個股資料（需 token）
9 個資料集（`--datasets` 選，預設全部）：`revenue, financials, balance, cashflow, dividend, institutional, margin, per, shareholding`。
```bash
# 確認 token 被讀到：跑這行若沒出現「⚠️ 未提供 token」就 OK
python batch_fundamentals.py --xlsx 台股股票代碼NEW.xlsx --limit 3 --delay 6

# 建議先挑重點（省額度）
python batch_fundamentals.py --xlsx 台股股票代碼NEW.xlsx --datasets institutional,per,balance,dividend --delay 7

# 全部 9 個資料集（每檔 9 次 API，1970 檔≈17700 次，要分多時段跑）
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
用步驟 1 的原始日K + 步驟 2 的 `dividend.csv`，算出還原價（除權息連續，技術面/回測必用）。
```bash
python build_adjusted_price.py 2330                              # 單檔
python build_adjusted_price.py                                    # 掃 H:\data 下全部個股
```
- 產出 `<股號>_adj.csv`，含 `adj_open/high/low/close` 與 `cumfactor`；最新一天=原始價，往前調整。
- 需要該股已有原始日K + `dividend.csv`；沒有股利的股票則 `adj=原始`（因子=1）。

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
- **單位不同**：TWSE 股價成交股數=股、金額=元；TPEx=張、千元；FinMind 月營收=元。合併分析要換算。
- **回測防前視**：月營收次月 10 日才公告、財報 5/15・8/14・11/14・3/31 才公告；FinMind 的 `date` 是期別日非公告日，回測請用公告日過濾。
- **技術面/回測一律用 `_adj.csv`（還原價）**，別用原始 `_YYYYMM.csv`，否則除權息日會被當成暴跌。