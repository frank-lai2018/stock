SET client_encoding = 'UTF8';   -- 本檔為 UTF-8；Windows psql 預設 BIG5 會讀壞中文，故先宣告

-- mv_stock_snapshot — 選股特徵快照（每檔一列、最新交易日）
-- 動能/基本面/估值/籌碼 + Minervini 趨勢範本 + VCP 波動收縮買點
-- point-in-time：報酬/均線一律用 adj_close（還原價）、交易日位移（row_number）。
--
-- 建立/重建：psql -U frank -d twstock -f stockselect/sql/mv_stock_snapshot.sql
-- 每晚刷新：REFRESH MATERIALIZED VIEW CONCURRENTLY mv_stock_snapshot;

DROP MATERIALIZED VIEW IF EXISTS mv_stock_snapshot;

CREATE MATERIALIZED VIEW mv_stock_snapshot AS
SELECT v.*,
    -- VCP 波動收縮買點（《超級績效》）：趨勢範本成立 + 波動收縮 + 量縮 + 貼近平台高點(樞紐)
    (v.trend_template                                        -- 前提：8 條趨勢範本
     AND v.tight_recent <= 0.10                              -- 近 15 日振幅 ≤10%（已收斂到位）
     AND v.tight_recent < v.tight_prior * 0.85               -- 近期明顯比前期更緊（波動收縮）
     AND v.vol_dry < 0.80                                    -- 近 10 日均量 < 前期八成（量縮）
     AND v.near_pivot >= 0.90                                -- 收盤貼近 60 日平台高點（樞紐 10% 內）
    ) AS vcp
FROM (
    SELECT u.*,
        -- Minervini 趨勢範本 8 條（RS 第8條用 rs_rating>=70）
        (u.adj_close > u.ma150 AND u.adj_close > u.ma200     -- 1 站上 150/200MA
         AND u.ma150 > u.ma200                               -- 2 150MA > 200MA
         AND u.ma200_up                                      -- 3 200MA 翻揚(近一月)
         AND u.ma50 > u.ma150 AND u.ma50 > u.ma200           -- 4 50MA > 150/200MA
         AND u.adj_close > u.ma50                            -- 5 站上 50MA
         AND u.adj_close >= u.low_52w * 1.30                 -- 6 高於 52 週低 ≥30%
         AND u.adj_close >= u.high_52w * 0.75                -- 7 距 52 週高 <25%
         AND u.rs_rating >= 70                               -- 8 RS 評等 ≥70
        ) AS trend_template
    FROM (
        SELECT t.*,
               round(percent_rank() OVER (ORDER BY t.rs_raw NULLS FIRST) * 100)::int AS rs_rating
        FROM (
            WITH d AS (SELECT max(trade_date) AS td FROM price_daily),
            ranked AS (
                SELECT p.stock_id, p.trade_date, p.close, p.adj_close, p.amount, p.volume,
                       row_number() OVER (PARTITION BY p.stock_id ORDER BY p.trade_date DESC) AS rn
                FROM price_daily p CROSS JOIN d
                WHERE p.trade_date > d.td - 400
            ),
            px AS (
                SELECT stock_id,
                    max(close)     FILTER (WHERE rn = 1)   AS close,
                    max(adj_close) FILTER (WHERE rn = 1)   AS c0,
                    max(adj_close) FILTER (WHERE rn = 21)  AS c1m,
                    max(adj_close) FILTER (WHERE rn = 63)  AS c3m,
                    max(adj_close) FILTER (WHERE rn = 126) AS c6m,
                    max(adj_close) FILTER (WHERE rn = 252) AS c12m,
                    avg(adj_close) FILTER (WHERE rn <= 20)  AS ma20,
                    avg(adj_close) FILTER (WHERE rn <= 50)  AS ma50,
                    avg(adj_close) FILTER (WHERE rn <= 60)  AS ma60,
                    avg(adj_close) FILTER (WHERE rn <= 120) AS ma120,
                    avg(adj_close) FILTER (WHERE rn <= 150) AS ma150,
                    avg(adj_close) FILTER (WHERE rn <= 200) AS ma200,
                    avg(adj_close) FILTER (WHERE rn BETWEEN 22 AND 221) AS ma200_1m,  -- 一個月前的200MA(近似)
                    max(adj_close) FILTER (WHERE rn <= 252) AS high_52w,
                    min(adj_close) FILTER (WHERE rn <= 252) AS low_52w,
                    avg(amount)    FILTER (WHERE rn <= 20)  AS amt20,
                    -- VCP 用：近期/前期振幅、量能、平台高點
                    max(adj_close) FILTER (WHERE rn <= 15)              AS hi15,
                    min(adj_close) FILTER (WHERE rn <= 15)              AS lo15,
                    max(adj_close) FILTER (WHERE rn BETWEEN 16 AND 40)  AS hi_prior,
                    min(adj_close) FILTER (WHERE rn BETWEEN 16 AND 40)  AS lo_prior,
                    max(adj_close) FILTER (WHERE rn <= 60)              AS hi60,
                    avg(volume)    FILTER (WHERE rn <= 10)              AS vol10,
                    avg(volume)    FILTER (WHERE rn BETWEEN 11 AND 50)  AS vol_prior,
                    count(*)                                AS trading_days
                FROM ranked GROUP BY stock_id
            ),
            idx AS (
                SELECT max(close) FILTER (WHERE rn = 1)   AS i0,
                       max(close) FILTER (WHERE rn = 126) AS i6m
                FROM (SELECT mi.close, row_number() OVER (ORDER BY mi.trade_date DESC) AS rn
                      FROM market_index mi CROSS JOIN d
                      WHERE mi.index_id = 'TAIEX' AND mi.trade_date > d.td - 400) z
            ),
            fq AS (SELECT DISTINCT ON (stock_id) stock_id, roe, eps, gross_margin, net_margin, debt_ratio
                   FROM fundamentals_quarterly ORDER BY stock_id, period_date DESC),
            rev AS (SELECT DISTINCT ON (stock_id) stock_id, yoy_pct AS rev_yoy, mom_pct AS rev_mom
                    FROM monthly_revenue ORDER BY stock_id, revenue_month DESC),
            val AS (SELECT DISTINCT ON (stock_id) stock_id, per, pbr, dividend_yield
                    FROM valuation_daily ORDER BY stock_id, trade_date DESC),
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
            sh AS (SELECT DISTINCT ON (stock_id) stock_id, foreign_ratio
                   FROM shareholding ORDER BY stock_id, trade_date DESC),
            big AS (SELECT DISTINCT ON (stock_id) stock_id, pct AS big1000_pct
                    FROM shareholding_dist WHERE level = 15 ORDER BY stock_id, data_date DESC)

            SELECT
                s.stock_id, s.name, s.market, s.industry,
                (SELECT td FROM d)                              AS as_of_date,
                px.close, round(px.c0, 4)                       AS adj_close,
                -- 動能
                px.c0 / NULLIF(px.c1m,0)  - 1                   AS ret_1m,
                px.c0 / NULLIF(px.c3m,0)  - 1                   AS ret_3m,
                px.c0 / NULLIF(px.c6m,0)  - 1                   AS ret_6m,
                px.c0 / NULLIF(px.c12m,0) - 1                   AS ret_12m,
                px.c1m / NULLIF(px.c12m,0) - 1                  AS ret_12_1,
                -- 均線
                round(px.ma20,4) AS ma20, round(px.ma50,4) AS ma50, round(px.ma60,4) AS ma60,
                round(px.ma120,4) AS ma120, round(px.ma150,4) AS ma150, round(px.ma200,4) AS ma200,
                (px.c0 > px.ma60)                               AS above_ma60,
                (px.ma20 > px.ma60 AND px.ma60 > px.ma120)      AS ma_bull,
                (px.ma200 IS NOT NULL AND px.ma200_1m IS NOT NULL AND px.ma200 > px.ma200_1m) AS ma200_up,
                round(px.high_52w,4) AS high_52w, round(px.low_52w,4) AS low_52w,
                px.c0 / NULLIF(px.high_52w,0) - 1               AS dist_52w_high,
                px.c0 / NULLIF(px.low_52w,0)  - 1               AS pct_from_low,
                (px.c0/NULLIF(px.c6m,0)) / NULLIF((SELECT i0/NULLIF(i6m,0) FROM idx),0) - 1 AS rs_6m,
                -- RS 原始分數（近季加權；外層轉百分位 rs_rating）
                (2 * px.c0/NULLIF(px.c3m,0) + px.c0/NULLIF(px.c6m,0) + px.c0/NULLIF(px.c12m,0)) AS rs_raw,
                -- VCP 特徵：近15日振幅 / 前期(16-40日)振幅 / 近10日量對前期量 / 貼近60日平台高點
                round((px.hi15 - px.lo15) / NULLIF(px.c0,0), 4)                    AS tight_recent,
                round((px.hi_prior - px.lo_prior) / NULLIF(px.c0,0), 4)            AS tight_prior,
                round(px.vol10 / NULLIF(px.vol_prior,0), 3)                        AS vol_dry,
                round(px.c0 / NULLIF(px.hi60,0), 4)                               AS near_pivot,
                -- 量能/品質
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
                (px.trading_days >= 60 AND px.amt20 >= 5000000) AS in_universe
            FROM stock s
            JOIN px            ON px.stock_id = s.stock_id
            LEFT JOIN fq       ON fq.stock_id = s.stock_id
            LEFT JOIN rev      ON rev.stock_id = s.stock_id
            LEFT JOIN val      ON val.stock_id = s.stock_id
            LEFT JOIN inst     ON inst.stock_id = s.stock_id
            LEFT JOIN mg       ON mg.stock_id = s.stock_id
            LEFT JOIN sh       ON sh.stock_id = s.stock_id
            LEFT JOIN big      ON big.stock_id = s.stock_id
        ) t
    ) u
) v;

CREATE UNIQUE INDEX IF NOT EXISTS uq_snapshot_stock ON mv_stock_snapshot (stock_id);
CREATE INDEX IF NOT EXISTS idx_snapshot_universe ON mv_stock_snapshot (in_universe);
CREATE INDEX IF NOT EXISTS idx_snapshot_trend ON mv_stock_snapshot (trend_template);
CREATE INDEX IF NOT EXISTS idx_snapshot_vcp ON mv_stock_snapshot (vcp);
