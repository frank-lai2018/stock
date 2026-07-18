# 選股 TAG 判定標準

本文件定義選股系統所有 TAG（預設策略）與價量訊號的**量化判定條件**，對應程式碼：

- 特徵計算：[`sql/mv_stock_snapshot.sql`](sql/mv_stock_snapshot.sql)（每檔一列的特徵快照，每晚刷新）
- 條件白名單：[`backend/app/filters.py`](backend/app/filters.py)
- 預設策略：[`backend/app/routers/screen.py`](backend/app/routers/screen.py)
- VPA 逐根訊號：[`backend/app/vpa.py`](backend/app/vpa.py)（個股頁「價量診斷」）

> 所有價格計算一律用 **還原價（adj_close）**，交易日以 `row_number()` 位移（rn=1 為最新交易日）。
> 紅=偏多、綠=偏空（台股慣例）。

---

## 0. 共用定義

| 名詞 | 定義 |
|---|---|
| **母體 `in_universe`** | `交易天數 ≥ 60` 且 `近20日均成交額 ≥ 500 萬元`（濾掉冷門/新股） |
| **均線 maN** | 近 N 個交易日 adj_close 平均（ma20/50/60/120/150/200） |
| **200MA 翻揚 `ma200_up`** | 現在的 200MA > 一個月前的 200MA（rn 22–221 的均值） |
| **52週高/低** | 近 252 交易日 adj_close 的最高/最低 |
| **RS 原始分 `rs_raw`** | `2×(現價/3月前) + (現價/6月前) + (現價/12月前)`（近季加權報酬） |
| **RS 評等 `rs_rating`** | `rs_raw` 的**全市場百分位 × 100**（0–99，越大越強勢；Minervini 門檻 70） |
| **法人20日 `inst_net_20d`** | 近20日三大法人（外資+外資自營+投信+自營自行+自營避險）淨買超合計（股） |
| **千張大戶% `big1000_pct`** | 集保股權分散 level=15（≥1000張）最新持股比例 |
| **大戶變化 `big1000_chg`** | `big1000_pct` 減去約 5 期前（≈一個月）的值 |
| **融資20日增減 `margin_chg_20d`** | 最新融資餘額 − 20日前融資餘額 |

---

## 1. 動能股 `momentum`

強動能 + 站上季線 + 法人買超 + 有量。排序：`ret_12_1`。

| 條件 | 判定 |
|---|---|
| 12-1 動能 | `ret_12_1 ≥ 0.15`（1月前 vs 12月前報酬 ≥15%） |
| 站上季線 | `adj_close > ma60` |
| 法人買超 | `inst_net_20d ≥ 0` |
| 有量 | `amt20 ≥ 2000萬元` |
| 母體 | `in_universe = true` |

---

## 2. 價值成長 GARP `garp`

高 ROE + 低本益比 + 營收成長 + 低負債。排序：`roe`。

| 條件 | 判定 |
|---|---|
| ROE | `roe ≥ 15` |
| 本益比 | `0 < per ≤ 15` |
| 營收 YoY | `rev_yoy ≥ 10` |
| 負債比 | `debt_ratio ≤ 60` |
| 母體 | `in_universe = true` |

---

## 3. 高息存股 `dividend`

高殖利率 + ROE 穩健 + 低負債。排序：`dividend_yield`。

| 條件 | 判定 |
|---|---|
| 殖利率 | `dividend_yield ≥ 4` |
| ROE | `roe ≥ 8` |
| 負債比 | `debt_ratio ≤ 50` |
| 母體 | `in_universe = true` |

---

## 4. 籌碼強勢 `chip`

法人買超 + 站上季線 + 融資未過熱。排序：`inst_net_20d`。

| 條件 | 判定 |
|---|---|
| 法人買超 | `inst_net_20d ≥ 0` |
| 站上季線 | `adj_close > ma60` |
| 融資未過熱 | `margin_chg_20d ≤ 0` |
| 母體 | `in_universe = true` |

---

## 5. 趨勢範本 `minervini`（顯示為「VCP口袋名單」）

出處：Mark Minervini《超級績效》8 條趨勢範本（VCP 前提）。排序：`rs_rating`，預設顯示 100 名。
`trend_template` 為布林欄位，8 條**全部成立**才 true：

| # | 條件 | 判定 |
|---|---|---|
| 1 | 站上 150/200MA | `adj_close > ma150` 且 `adj_close > ma200` |
| 2 | 150MA > 200MA | `ma150 > ma200` |
| 3 | 200MA 翻揚 | `ma200_up = true` |
| 4 | 50MA 在 150/200MA 之上 | `ma50 > ma150` 且 `ma50 > ma200` |
| 5 | 站上 50MA | `adj_close > ma50` |
| 6 | 高於 52 週低 ≥30% | `adj_close ≥ low_52w × 1.30` |
| 7 | 距 52 週高 <25% | `adj_close ≥ high_52w × 0.75` |
| 8 | RS 評等 ≥70 | `rs_rating ≥ 70` |

---

## 6. VCP 收縮買點 `vcp`

出處：《超級績效》VCP（Volatility Contraction Pattern）。趨勢範本**之上**再加收縮/量縮/貼樞紐。排序：`rs_rating`。
`vcp` 為布林欄位，以下**全部成立**才 true：

| 條件 | 判定 | 意義 |
|---|---|---|
| 前提 | `trend_template = true` | 先過 8 條趨勢範本 |
| 收斂到位 | `tight_recent ≤ 0.10` | 近15日振幅 ≤10% |
| 波動收縮 | `tight_recent < tight_prior × 0.85` | 近期比前期(16–40日)明顯更緊 |
| 量縮 | `vol_dry < 0.80` | 近10日均量 < 前期(11–50日)八成 |
| 貼近樞紐 | `near_pivot ≥ 0.90` | 收盤在 60 日平台高點 10% 內 |

- `tight_recent = (近15日最高 − 近15日最低) / 現價`
- `tight_prior = (16–40日最高 − 16–40日最低) / 現價`
- `vol_dry = 近10日均量 / (11–50日均量)`
- `near_pivot = 現價 / 近60日最高`

---

## 7. 主力承接 `mf_accumulate`

出處：Anna Coulling《不說謊的價量》VPA。近20日承接訊號淨多 + 大戶或法人進場。排序：`vpa_accum_20d`。
`mf_accumulate` 為布林欄位：

| 條件 | 判定 |
|---|---|
| 承接淨多 | `vpa_accum_20d > vpa_distrib_20d` |
| 承接足量 | `vpa_accum_20d ≥ 2` |
| 有人進場 | `big1000_chg > 0`（大戶%上升）**或** `inst_net_20d > 0`（法人淨買超） |
| 母體 | `in_universe = true` |

---

## 8. 主力出貨 `mf_distribute`（警示）

出處：VPA。近20日出貨訊號淨多 + 大戶或法人退場 + 高檔。排序：`vpa_distrib_20d`。
`mf_distribute` 為布林欄位：

| 條件 | 判定 |
|---|---|
| 出貨淨多 | `vpa_distrib_20d > vpa_accum_20d` |
| 出貨足量 | `vpa_distrib_20d ≥ 2` |
| 有人退場 | `big1000_chg < 0`（大戶%下降）**或** `inst_net_20d < 0`（法人淨賣超） |
| 高檔 | `near_pivot ≥ 0.85`（距 60 日高 15% 內，才算出貨；避免低檔殺盤誤判） |
| 母體 | `in_universe = true` |

---

## 附錄：VPA 逐根價量訊號定義

用於個股頁「價量診斷」（[`vpa.py`](backend/app/vpa.py)）與 tag 7/8 的近20日計數（SQL 版門檻相同）。
以「**前 20 根**」的均量、均振幅為基準：

- `vr`（量能倍數）= 當日量 / 前20日均量
- `sr`（振幅倍數）= 當日振幅 / 前20日均振幅
- `pos`（收盤位置）= (收盤 − 最低) / (最高 − 最低)，0=最低、1=最高
- `up`/`down` = 收盤 > / < 昨收

| 訊號 | 階段 | 方向 | 判定 |
|---|---|---|---|
| **停損量** | 承接 | 偏多 | `down` 且 `vr≥2.0` 且 `sr≥1.5` 且 `pos≥0.45`（爆量寬振幅收上半＝賣壓被接走） |
| **承接量** | 承接 | 偏多 | `down` 且 `vr≥1.5` 且 `pos≥0.6`（高量下跌卻收高） |
| **測試無賣壓** | 測試 | 偏多 | `down` 且 `vr≤0.6` 且 `sr≤0.6`（下跌但量縮振幅小＝浮額洗清） |
| **無買氣** | 測試 | 偏空 | `up` 且 `vr≤0.6` 且 `sr≤0.6`（上漲卻量縮＝虛漲） |
| **量價背離** | 出貨 | 中性 | `vr≥1.5` 且 `sr≤0.6`（大量卻推不動＝高檔換手） |
| **買盤高潮** | 出貨 | 偏空 | 漲勢中 `up` 且 `vr≥2.0` 且 `sr≥1.5` 且 `pos≤0.5`（爆量寬振幅收下半＝追高被倒貨） |

> 近20日計數：`vpa_accum_20d` = 承接類（停損量/承接量/測試無賣壓）出現次數；
> `vpa_distrib_20d` = 出貨類（無買氣/量價背離/買盤高潮）出現次數。

---

## 9. 持股診斷評分 `diagnose`

持股診斷頁（`/portfolio`）對每檔持股的綜合體檢。程式：[`backend/app/diagnose.py`](backend/app/diagnose.py)。

**資料模型**：交易明細帳 `trade_log`（見 [`sql/trade_log.sql`](sql/trade_log.sql)，後端首次呼叫自動建立）為真實來源，
每列一筆買/賣（含日期、張數、價、手續費、證交稅）。未平倉持股與已實現損益由 [`backend/app/ledger.py`](backend/app/ledger.py)
的 **FIFO 引擎**推導：賣單依買進先進先出配對，買進手續費攤入成本、賣出手續費/稅於平倉時實現。
診斷對象＝各檔的未平倉部位（張數＝買−賣、成本＝剩餘買批加權均價）。

### 三面向分數（各有中性基準，加減後夾在範圍內）

**技術面（0–40，基準 20）**
| 項目 | 加減 |
|---|---|
| 趨勢範本 `trend_template` | +12 |
| 站上季線 `above_ma60` | +6 / 未站上 −6 |
| RS 評等 | `(rs−50)/50×10`，夾 −10…+10 |
| VCP 收縮 | +4 |
| 均線多頭 `ma_bull` | +2 |

**籌碼面（0–35，基準 17）**
| 項目 | 加減 |
|---|---|
| 主力承接 `mf_accumulate` | +10 |
| 主力出貨 `mf_distribute` | −12 |
| 法人20日 `inst_net_20d` | >0 +5 / <0 −5 |
| 大戶變化 `big1000_chg` | >0 +5 / <0 −5 |
| VPA 淨承接 `vpa_accum_20d − vpa_distrib_20d` | 夾 −5…+5 |

**基本面（0–25，基準 12）**
| 項目 | 加減 |
|---|---|
| ROE | ≥15 +6 / ≥8 +3 / <0 −4 |
| 營收 YoY | ≥10 +5 / >0 +2 / <0 −3 |
| 本益比 | 0<per≤20 +3 / >40 −2 |
| 殖利率 | ≥4 +2 |

**總分** = 技術 + 籌碼 + 基本面（0–100）。

### 評級 level（顏色依台股慣例：strong=紅偏多、reduce=綠偏空）
| 評級 | 顯示 | 判定（依序） |
|---|---|---|
| `reduce` | 🔴 減碼（綠） | `mf_distribute`，或總分<40，或（跌破季線 且（法人賣超 或 大戶減持）） |
| `strong` | 🟢 續抱（紅） | `trend_template`，或總分≥70，或（站上季線 且 主力承接 且 RS≥70） |
| `watch` | 🟡 觀察（橘） | 其餘 |

> 未實現損益：`未平倉張數×1000×(現價−均成本)`；已實現：FIFO 平倉逐筆 `(賣價−買價)×張數×1000 − 手續費 − 證交稅`；
> 支撐/壓力取自 `/levels` 偵測；關鍵訊號標籤由上述條件產生。績效：勝率、持有天數、報酬率由已實現明細計算。

---

## 維護須知

- 改判定條件時：**同步更新本文件、`mv_stock_snapshot.sql`、`filters.py`、`screen.py`（與 `vpa.py` 若涉及訊號）**，並重建 MV：
  `psql -U frank -d twstock -f stockselect/sql/mv_stock_snapshot.sql`
- 僅資料更新（非改結構）：`nightly.py` 每晚自動 `REFRESH MATERIALIZED VIEW CONCURRENTLY`，tag 判定隨最新資料更新，無需手動重建。
