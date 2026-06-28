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


上市股 → download_twse_csv.py

# 單檔、單月（2015年3月）
python download_twse_csv.py 2330 --start 2015-03 --end 2015-03

# 單檔、區間（2015整年）
python download_twse_csv.py 2330 --start 2015-01 --end 2015-12

# 多檔、同一段月份
python download_twse_csv.py 2330 2317 1101 --start 2015-03 --end 2015-06
上櫃股 → download_tpex_csv.py

# 單檔、單月
python download_tpex_csv.py 5483 --start 2015-03 --end 2015-03

# 單檔、區間
python download_tpex_csv.py 5483 --start 2015-01 --end 2015-12