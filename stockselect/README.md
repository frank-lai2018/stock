# stockselect — 台股選股系統（Vue 3 + FastAPI）

> 建立日期：2026-07-13
> 資料來源：既有 PostgreSQL 資料庫 **`twstock`**（由上層 `stock_screener_starter` 的資料管線每日更新）。
> 本系統**只讀** DB，不重抓資料；資料更新交給上層的 `nightly.py`。

---

## 1. 目標

把「散在各表的台股資料」變成一套**可互動選股**的網頁工具：

- **選股器**：用動能 / 基本面 / 估值 / 籌碼條件篩選，結果依分數排名。
- **預設策略**：動能股 / 價值成長 / 高息存股 / 籌碼強勢，一鍵套用。
- **個股頁**：還原 K 線圖 + 基本面 + 籌碼（法人/融資/大戶）+ 新聞。
- **自選股**：加入追蹤、批次看訊號。
- （後續）評分排名、回測、RAG「為什麼選它」報告。

---

## 2. 技術棧

| 層 | 選型 | 說明 |
|----|------|------|
| 前端 | **Vue 3 + Vite**（Composition API）| 開發快、生態成熟 |
| 前端 UI | **Element Plus** | 表格/表單/篩選元件完整，適合資料密集介面 |
| 圖表 | **KLineChart**（K 線）+ **ECharts**（一般圖）| 台股 K 線 + 均線/指標；籌碼柱狀圖用 ECharts |
| 狀態 | **Pinia** | 自選股、篩選條件 |
| 路由 | **Vue Router** | 頁面切換 |
| HTTP | **axios** | 打後端 API |
| 後端 | **FastAPI + uvicorn**（Python）| 輕量、自動 API 文件（/docs）、型別驗證 |
| DB 存取 | **psycopg2**（沿用上層）+ 連線池 | 唯讀查詢為主；參數化避免注入 |
| 驗證/序列化 | **Pydantic** | request/response schema |
| DB | **PostgreSQL `twstock`**（現成）| 15+ 張表；本系統讀取 |

> 為什麼 FastAPI：與你既有 Python 生態一致、自動生成 `/docs` 互動文件、Pydantic 型別安全、非同步友善。

---

## 3. 系統架構

```
┌────────────────┐   HTTP / JSON    ┌──────────────────┐   SQL(唯讀)   ┌──────────────────┐
│  Vue 3 (Vite)  │ ───────────────▶ │  FastAPI 後端     │ ───────────▶ │ PostgreSQL twstock│
│  選股器/個股頁 │ ◀─────────────── │  /api/*          │ ◀─────────── │ (nightly.py 更新) │
└────────────────┘                  └──────────────────┘              └──────────────────┘
        ▲                                                                        ▲
        │ Vite dev proxy /api → :8000                                            │
        └────────────────────────────────────────────────────────────────────────┘
                                                     資料更新獨立：上層 nightly.py 每晚寫 DB
```

- **前後端分離**：前端 :5173（Vite dev）、後端 :8000（uvicorn），dev 期用 Vite proxy 把 `/api` 轉到後端。
- **資料層解耦**：App 不負責抓資料；DB 由上層管線更新，App 永遠讀到最新。

---

## 4. 專案結構

```
stockselect/
├─ README.md                    ← 本檔
├─ backend/
│  ├─ requirements.txt          fastapi, uvicorn, psycopg2-binary, pydantic, python-dotenv
│  ├─ .env.example              DATABASE_URL=...
│  └─ app/
│     ├─ main.py                建立 FastAPI、掛 routers、CORS
│     ├─ config.py              讀 .env（DATABASE_URL）
│     ├─ db.py                  psycopg2 連線池 + 查詢輔助
│     ├─ schemas.py             Pydantic：ScreenRequest / StockRow / ...
│     ├─ filters.py             篩選條件 → 參數化 SQL（白名單，防注入）
│     └─ routers/
│        ├─ screen.py           POST /api/screen、GET /api/strategies
│        ├─ stock.py            GET /api/stock/{id}、/prices、/chips、/news
│        └─ watchlist.py        GET/POST/DELETE /api/watchlist
├─ frontend/
│  ├─ package.json              vue, vue-router, pinia, axios, element-plus, klinecharts, echarts
│  ├─ vite.config.js            dev proxy /api → http://localhost:8000
│  ├─ index.html
│  └─ src/
│     ├─ main.js
│     ├─ App.vue
│     ├─ api/index.js           axios 實例 + 各 API 函式
│     ├─ router/index.js
│     ├─ stores/watchlist.js    Pinia
│     ├─ views/
│     │  ├─ ScreenerView.vue    選股器（左：條件面板；右：結果表）
│     │  ├─ StockDetailView.vue 個股（K 線 + 基本面 + 籌碼 + 新聞）
│     │  └─ WatchlistView.vue   自選股
│     └─ components/
│        ├─ FilterPanel.vue     動能/基本面/估值/籌碼 條件
│        ├─ ResultTable.vue     可排序結果表 + 加入自選
│        ├─ PriceChart.vue      KLineChart 還原 K 線 + 均線
│        └─ ChipPanel.vue       法人/融資/大戶 圖表
└─ sql/
   └─ mv_stock_snapshot.sql     選股特徵物化視圖（見 §6）
```

---

## 5. 後端 API 設計

| Method | Path | 說明 | 主要來源表 |
|--------|------|------|-----------|
| GET | `/api/strategies` | 預設策略清單（動能/價值/高息/籌碼）| — |
| POST | `/api/screen` | 依條件篩選 + 排名，回傳股票清單 | `mv_stock_snapshot` |
| GET | `/api/stock/{id}` | 個股最新特徵快照 | `mv_stock_snapshot`, `stock` |
| GET | `/api/stock/{id}/prices?from&to&adj=1` | K 線 OHLC（還原）| `price_daily` |
| GET | `/api/stock/{id}/chips?days=60` | 法人/融資/大戶時序 | `inst_trades`,`margin_trading`,`shareholding_dist` |
| GET | `/api/stock/{id}/fundamentals` | 營收/EPS/財務比率 | `monthly_revenue`,`fundamentals_quarterly` |
| GET | `/api/stock/{id}/news?days=30` | 個股新聞 | `news` |
| GET/POST/DELETE | `/api/watchlist` | 自選股（存後端小表或前端 localStorage）| — |

**`POST /api/screen` 範例**
```jsonc
// request
{
  "filters": {
    "ret_3m_min": 0.1, "above_ma60": true,
    "roe_min": 15, "rev_yoy_min": 10,
    "per_max": 20, "inst_net_20d_min": 0,
    "amt20_min": 20000000            // 流動性門檻（元）
  },
  "sort": "momentum_score", "limit": 50
}
// response
{ "count": 23, "as_of": "2026-07-09",
  "items": [ { "stock_id":"2451","name":"創見","industry":"半導體業",
               "ret_3m":0.42,"roe":31.0,"per":8.2,"inst_net_20d":1234000,
               "momentum_score":0.88 }, ... ] }
```

**安全**：`filters` 走**白名單**（`filters.py` 只允許已知欄位/運算子），一律**參數化查詢**，杜絕 SQL 注入。DB 帳號建議另開**唯讀角色**給 App 用。

---

## 6. 資料層：`mv_stock_snapshot`（地基）

選股系統的核心是一張「**每檔一列、最新交易日**」的特徵寬表（物化視圖），把 4 面向指標預算好，選股即 `SELECT … WHERE`。欄位（約 30+）：

- **動能**：`ret_1m/3m/6m/12m`、`ret_12_1`、`ma20/60`、`above_ma60`、`ma_bull`、`dist_52w_high`、`rs_6m`
- **基本面**：`roe`、`eps`、`gross_margin`、`net_margin`、`debt_ratio`、`rev_yoy`、`rev_mom`
- **估值**：`per`、`pbr`、`dividend_yield`
- **籌碼**：`inst_net_20d`、`foreign_ratio`、`margin_chg_20d`、`big1000_pct`（千張大戶，集保有資料後）
- **品質/濾網**：`amt20`（近20日均額）、`trading_days`、`close`、`as_of_date`

> ⚠️ **point-in-time 正確**：報酬一律用 `adj_close`；基本面用 `available_date` 防前視；排除上市不足/流動性過低者。詳見上層 [../動能分析設計.md](../動能分析設計.md)。
>
> 這張視圖是上層「動能分析」的擴充版（動能 + 基本面 + 籌碼 + 估值）。SQL 放在 `sql/mv_stock_snapshot.sql`，每晚由 `nightly.py` 更新完 DB 後 `REFRESH MATERIALIZED VIEW CONCURRENTLY`。

---

## 7. 啟動步驟

前置需求：Python 3.10+、Node.js 18+、可連線的 PostgreSQL `twstock`。
（路徑以專案根目錄 `stock_screener_starter\` 為基準；指令用 Windows CMD。）

### 步驟 0：建選股快照視圖（只需一次；資料表有大改時再重建）
```cmd
cd /d c:\Users\apple\Desktop\work_suggest\stock_screener_starter
psql -U frank -d twstock -f stockselect\sql\mv_stock_snapshot.sql
```
> 檔案含 `SET client_encoding='UTF8'`，Windows psql 直接跑即可。成功會印 `SELECT xxxx` + `CREATE INDEX`。
> 每晚資料更新後刷新：`psql -U frank -d twstock -c "REFRESH MATERIALIZED VIEW CONCURRENTLY mv_stock_snapshot;"`

### 步驟 1：後端（FastAPI）— 首次設定
```cmd
cd stockselect\backend
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
:: 打開 .env 填入 DATABASE_URL（建議用唯讀角色）
```

### 步驟 2：前端（Vue 3）— 首次設定
```cmd
cd stockselect\frontend
npm install
```

### 每次啟動（開兩個終端機，同時執行）

**終端機 A — 後端**
```cmd
cd /d c:\Users\apple\Desktop\work_suggest\stock_screener_starter\stockselect\backend
.venv\Scripts\activate
uvicorn app.main:app --reload --port 8000
```
→ API 文件 http://localhost:8000/docs ｜健康檢查 http://localhost:8000/api/health

**終端機 B — 前端**
```cmd
cd /d c:\Users\apple\Desktop\work_suggest\stock_screener_starter\stockselect\frontend
npm run dev
```
→ 開啟 **http://localhost:5173**（選股畫面）

> 前端 `vite.config.js` 已設 dev proxy：`/api` → `http://localhost:8000`，所以前端呼叫 `/api/...` 會自動轉到後端；**兩個都要開著**。

### 環境變數（backend/.env）
```
DATABASE_URL=postgresql://frank:密碼@localhost:5432/twstock   # 建議改用唯讀角色
CORS_ORIGINS=http://localhost:5173
```

### 常見問題
| 症狀 | 原因 / 解法 |
|------|------------|
| 前端「無法連到後端 /api」 | 後端沒開，或不在 :8000 → 確認終端機 A 的 uvicorn 有跑 |
| `/api/screen` 回 500、找不到 `mv_stock_snapshot` | 沒跑步驟 0 建視圖 |
| psql 中文亂碼 / BIG5 錯誤 | 用本專案 `.sql`（已含 `SET client_encoding='UTF8'`）；或先 `set PGCLIENTENCODING=UTF8` |
| 選股結果沒更新到最新交易日 | 資料更新後未刷新視圖 → 跑 `REFRESH MATERIALIZED VIEW CONCURRENTLY mv_stock_snapshot;` |

---

## 8. 開發路線圖

| 階段 | 內容 | 產出 |
|------|------|------|
| **Phase 0** ✅ | `mv_stock_snapshot` 物化視圖（動能+基本面+估值+籌碼）| 選股資料地基（已建）|
| **Phase 1** ✅ | 後端：FastAPI + 連線池 + `/api/screen`、`/api/strategies`、`/api/stock/{id}`(+prices/chips/fundamentals) | `/docs` 可測（已完成）|
| **Phase 2** ✅ | 前端：ScreenerView（策略按鈕 + 條件面板 + 結果表）+ 個股頁基本資訊 | 能在網頁選股（已完成）|
| **Phase 3** | 個股頁：KLineChart 還原 K 線 + 籌碼面板 | 個股全貌 |
| **Phase 4** | 預設策略、自選股、評分排名 | 完整選股工作流 |
| **Phase 5**（進階）| 回測、RAG「為什麼選它」報告（接 `news`）| 決策輔助 |

---

## 9. 與上層資料管線的關係

```
上層 stock_screener_starter/
   nightly.py（每晚）→ 更新 twstock（股價/法人/融資/PER/月營收/集保…）
        │
        └─(更新完) REFRESH MATERIALIZED VIEW mv_stock_snapshot
                        │
stockselect/ 後端讀 twstock + snapshot → 前端選股
```

- **本系統不抓資料、不寫業務資料**（除了自選股這種 App 狀態）。
- 資料正確性（還原價、防前視、單位）由上層管線保證，見 [../README.md](../README.md)。

---

## 10. 待確認 / 決策點

- **自選股存哪**：前端 localStorage（最簡）vs 後端新表（跨裝置）。
- **snapshot 更新頻率**：每晚 REFRESH（夠用）vs 盤中即時（本系統定位日/週選股，不需要）。
- **部署**：先本機開發；日後要不要包成 Docker / 對外服務再議。
- **權限**：建議建 PostgreSQL 唯讀角色給 App，降低風險。
