SET client_encoding = 'UTF8';   -- 本檔為 UTF-8；Windows psql 預設 BIG5 會讀壞中文，故先宣告

-- mv_stock_snapshot — 選股特徵快照（每檔一列、最新交易日）
-- 選股系統地基：把動能/基本面/估值/籌碼 ~30 指標預算好，選股即 SELECT … WHERE。
-- point-in-time：報酬一律用 adj_close（還原價）；均線/報酬用「交易日」位移（row_number），非日曆。
--
-- 建立/重建（可重複執行）：
--   psql -U frank -d twstock -f stockselect/sql/mv_stock_snapshot.sql
-- 每晚資料更新後刷新（nightly 之後）：
--   REFRESH MATERIALIZED VIEW CONCURRENTLY mv_stock_snapshot;

DROP MATERIALIZED VIEW IF EXISTS mv_stock_snapshot;

CREATE MATERIALIZED VIEW mv_stock_snapshot AS
WITH d AS (SELECT max(trade_date) AS td FROM price_daily),

-- 近 400 日價格，標記每檔由新到舊的序號（rn=1 為最新交易日）
ranked AS (
    SELECT p.stock_id, p.trade_date, p.close, p.adj_close, p.amount,
           row_number() OVER (PARTITION BY p.stock_id ORDER BY p.trade_date DESC) AS rn
    FROM price_daily p CROSS JOIN d
    WHERE p.trade_date > d.td - 400
),
-- 價格類特徵：最新價、均線（N 交易日）、52 週高低、量能、交易天數、各回顧點收盤
px AS (
    SELECT stock_id,
        max(close)     FILTER (WHERE rn = 1)   AS close,
        max(adj_close) FILTER (WHERE rn = 1)   AS c0,
        max(adj_close) FILTER (WHERE rn = 21)  AS c1m,
        max(adj_close) FILTER (WHERE rn = 63)  AS c3m,
        max(adj_close) FILTER (WHERE rn = 126) AS c6m,
        max(adj_close) FILTER (WHERE rn = 252) AS c12m,
        avg(adj_close) FILTER (WHERE rn <= 20)  AS ma20,
        avg(adj_close) FILTER (WHERE rn <= 60)  AS ma60,
        avg(adj_close) FILTER (WHERE rn <= 120) AS ma120,
        max(adj_close) FILTER (WHERE rn <= 252) AS high_52w,
        min(adj_close) FILTER (WHERE rn <= 252) AS low_52w,
        avg(amount)    FILTER (WHERE rn <= 20)  AS amt20,
        count(*)                                AS trading_days
    FROM ranked GROUP BY stock_id
),
-- 大盤 TAIEX 回顧點（算相對強弱）
idx AS (
    SELECT max(close) FILTER (WHERE rn = 1)   AS i0,
           max(close) FILTER (WHERE rn = 126) AS i6m
    FROM (SELECT mi.close, row_number() OVER (ORDER BY mi.trade_date DESC) AS rn
          FROM market_index mi CROSS JOIN d
          WHERE mi.index_id = 'TAIEX' AND mi.trade_date > d.td - 400) z
),
-- 基本面（最新一期）
fq AS (SELECT DISTINCT ON (stock_id) stock_id, roe, eps, gross_margin, net_margin, debt_ratio
       FROM fundamentals_quarterly ORDER BY stock_id, period_date DESC),
-- 月營收（最新一月）
rev AS (SELECT DISTINCT ON (stock_id) stock_id, yoy_pct AS rev_yoy, mom_pct AS rev_mom
        FROM monthly_revenue ORDER BY stock_id, revenue_month DESC),
-- 估值（最新一日）
val AS (SELECT DISTINCT ON (stock_id) stock_id, per, pbr, dividend_yield
        FROM valuation_daily ORDER BY stock_id, trade_date DESC),
-- 籌碼：三大法人近 20 交易日淨買超（股）
inst AS (
    SELECT stock_id, sum(net) FILTER (WHERE rn <= 20) AS inst_net_20d
    FROM (SELECT it.stock_id,
                 coalesce(it.foreign_net,0)+coalesce(it.foreign_dealer_net,0)
                 +coalesce(it.trust_net,0)+coalesce(it.dealer_self_net,0)
                 +coalesce(it.dealer_hedge_net,0) AS net,
                 row_number() OVER (PARTITION BY it.stock_id ORDER BY it.trade_date DESC) AS rn
          FROM inst_trades it CROSS JOIN d
          WHERE it.trade_date > d.td - 60) z
    GROUP BY stock_id
),
-- 籌碼：融資餘額最新值與近 20 交易日變化（張）
mg AS (
    SELECT stock_id,
           max(margin_balance) FILTER (WHERE rn = 1)  AS margin_balance,
           max(margin_balance) FILTER (WHERE rn = 1)
             - max(margin_balance) FILTER (WHERE rn = 20) AS margin_chg_20d
    FROM (SELECT mt.stock_id, mt.margin_balance,
                 row_number() OVER (PARTITION BY mt.stock_id ORDER BY mt.trade_date DESC) AS rn
          FROM margin_trading mt CROSS JOIN d
          WHERE mt.trade_date > d.td - 60) z
    GROUP BY stock_id
),
-- 籌碼：外資持股比率（最新）
sh AS (SELECT DISTINCT ON (stock_id) stock_id, foreign_ratio
       FROM shareholding ORDER BY stock_id, trade_date DESC),
-- 籌碼：千張大戶持股比例（集保 level 15，最新週；集保有資料後才有值）
big AS (SELECT DISTINCT ON (stock_id) stock_id, pct AS big1000_pct
        FROM shareholding_dist WHERE level = 15 ORDER BY stock_id, data_date DESC)

SELECT
    s.stock_id, s.name, s.market, s.industry,
    (SELECT td FROM d)                              AS as_of_date,
    px.close,
    -- 動能（報酬）
    px.c0 / NULLIF(px.c1m,0)  - 1                   AS ret_1m,
    px.c0 / NULLIF(px.c3m,0)  - 1                   AS ret_3m,
    px.c0 / NULLIF(px.c6m,0)  - 1                   AS ret_6m,
    px.c0 / NULLIF(px.c12m,0) - 1                   AS ret_12m,
    px.c1m / NULLIF(px.c12m,0) - 1                  AS ret_12_1,   -- 經典 12-1 動能
    -- 均線 / 位置
    round(px.ma20,4) AS ma20, round(px.ma60,4) AS ma60, round(px.ma120,4) AS ma120,
    (px.c0 > px.ma60)                               AS above_ma60,
    (px.ma20 > px.ma60 AND px.ma60 > px.ma120)      AS ma_bull,
    px.c0 / NULLIF(px.high_52w,0) - 1               AS dist_52w_high,
    -- 相對強弱 vs 大盤（6 月）
    (px.c0/NULLIF(px.c6m,0)) / NULLIF((SELECT i0/NULLIF(i6m,0) FROM idx),0) - 1 AS rs_6m,
    -- 量能 / 品質
    round(px.amt20)                                 AS amt20,
    px.trading_days,
    -- 基本面
    fq.roe, fq.eps, fq.gross_margin, fq.net_margin, fq.debt_ratio,
    rev.rev_yoy, rev.rev_mom,
    -- 估值
    val.per, val.pbr, val.dividend_yield,
    -- 籌碼
    inst.inst_net_20d, mg.margin_balance, mg.margin_chg_20d,
    sh.foreign_ratio, big.big1000_pct,
    -- 母體便利旗標（流動性 + 上市天數；選股可自行覆寫門檻）
    (px.trading_days >= 60 AND px.amt20 >= 5000000) AS in_universe
FROM stock s
JOIN px            ON px.stock_id = s.stock_id      -- 有近期價格才納入
LEFT JOIN fq       ON fq.stock_id = s.stock_id
LEFT JOIN rev      ON rev.stock_id = s.stock_id
LEFT JOIN val      ON val.stock_id = s.stock_id
LEFT JOIN inst     ON inst.stock_id = s.stock_id
LEFT JOIN mg       ON mg.stock_id = s.stock_id
LEFT JOIN sh       ON sh.stock_id = s.stock_id
LEFT JOIN big      ON big.stock_id = s.stock_id;

-- CONCURRENTLY 刷新需唯一索引
CREATE UNIQUE INDEX IF NOT EXISTS uq_snapshot_stock ON mv_stock_snapshot (stock_id);
CREATE INDEX IF NOT EXISTS idx_snapshot_universe ON mv_stock_snapshot (in_universe);
