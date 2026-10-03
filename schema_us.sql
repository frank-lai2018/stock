-- 台股選股系統 schema — 美台題材對照（美股日線、美股題材籃子、每日美股題材熱度）
-- 設計說明見 美台題材對照.md；對照名單在 us_theme_defs.py
--
-- 重跑方式（全部 IF NOT EXISTS / OR REPLACE，可安全重複執行）：
--   psql -U postgres -d twstock -f schema_us.sql
-- 前置：schema_theme.sql 已建好（theme、theme_daily）。
-- theme_id 不設外鍵：後端帳號（frank）沒有 theme 的 REFERENCES 權限，這樣用後端帳號也建得起來；
-- 題材停用或刪除時，build_us_theme_daily.py 每次重新同步籃子，v_us_tw_theme 也只 join 現有題材。

-- 美股日線（fetch_us_prices.py 從 Yahoo Finance 抓；trade_date＝美東交易日）
--   adj_close 含分割與股息調整。Yahoo 會回頭調整整段歷史，所以每晚比對重疊日，
--   對不上就整檔重抓（見 fetch_us_prices.py），庫裡同一檔永遠是同一套調整基準。
CREATE TABLE IF NOT EXISTS us_price_daily (
    symbol      VARCHAR(16) NOT NULL,     -- Yahoo 代號：NVDA、SPY、^SOX…
    trade_date  DATE        NOT NULL,
    open        NUMERIC(14,4),
    high        NUMERIC(14,4),
    low         NUMERIC(14,4),
    close       NUMERIC(14,4),            -- 已做分割調整、未做股息調整（Yahoo 的 close）
    adj_close   NUMERIC(14,4),            -- 分割＋股息調整，算報酬用這欄
    volume      BIGINT,
    PRIMARY KEY (symbol, trade_date)
);
CREATE INDEX IF NOT EXISTS idx_us_price_date ON us_price_daily (trade_date);

-- 美股題材籃子：L3 題材 → 美股代表股／ETF（build_us_theme_daily.py 每次依 us_theme_defs.py 同步）
CREATE TABLE IF NOT EXISTS us_theme_member (
    theme_id    INT         NOT NULL,     -- theme.theme_id（L3）
    symbol      VARCHAR(16) NOT NULL,
    name        TEXT,                     -- 顯示名稱，如「輝達」「iShares 航太國防 ETF」
    PRIMARY KEY (theme_id, symbol)
);

-- 每日美股題材熱度（build_us_theme_daily.py 產生；指標定義同 theme_daily，沒有法人籌碼）
CREATE TABLE IF NOT EXISTS us_theme_daily (
    theme_id     INT  NOT NULL,           -- theme.theme_id（L3）
    trade_date   DATE NOT NULL,           -- 美東交易日
    n_members    INT,                     -- 當日有價的籃子成分數
    ret_1d       NUMERIC(8,4),            -- 籃子等權平均報酬
    ret_5d       NUMERIC(8,4),
    ret_20d      NUMERIC(8,4),
    ex_5d        NUMERIC(8,4),            -- 減 SPY 同期報酬
    ex_20d       NUMERIC(8,4),
    breadth_ma20 NUMERIC(6,4),            -- 站上 20MA 比例
    high20_pct   NUMERIC(6,4),            -- 收在 20 日新高比例
    vol_ratio    NUMERIC(8,3),            -- 近 5 日成交金額 ÷（近 20 日 ÷4）
    heat_score   NUMERIC(6,2),            -- 0~100，同日美股題材間的相對熱度
    heat_rank    INT,
    PRIMARY KEY (theme_id, trade_date)
);
CREATE INDEX IF NOT EXISTS idx_us_theme_daily_date ON us_theme_daily (trade_date);

-- 隔天跟隨度：美股籃子前一晚漲跌 → 台股同題材隔天（analyze_us_tw_themes.py 寫入，每次整批換掉）
CREATE TABLE IF NOT EXISTS us_theme_follow (
    theme_id      INT PRIMARY KEY,        -- theme.theme_id（L3）
    n_days        INT,                    -- 有美股隔夜資料的台股交易日數
    corr_raw      NUMERIC(6,3),           -- 台股題材報酬 vs 美股籃子報酬（含兩邊大盤）
    corr_ex       NUMERIC(6,3),           -- 扣掉兩邊大盤後的相關（判斷用這個）
    beta_ex       NUMERIC(8,4),
    t_ex          NUMERIC(6,2),
    t_partial_sox NUMERIC(6,2),           -- 同時放進費半超額後，美股籃子的 t；< 2＝看費半就夠
    up_next       NUMERIC(8,4),           -- 美股籃子超額前 10% 的隔天，台股題材平均超額
    down_next     NUMERIC(8,4),           -- 後 10% 的隔天
    up_win        NUMERIC(6,4),           -- 大漲隔天台股題材跑贏大盤的比例
    label         TEXT,                   -- 明顯跟隨／有一點／幾乎不跟
    period_from   DATE,
    period_to     DATE,
    computed_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 美台並排：每個台股交易日配「台股開盤前最後一個已收盤的美股交易日」（美東日期 < 台股日期）。
-- 美股收盤是台灣清晨，所以美股 t 日影響的是台股 t+1 日。
CREATE OR REPLACE VIEW v_us_tw_theme AS
SELECT tw.trade_date AS tw_date, us.trade_date AS us_date, t.theme_id, t.code, t.name,
       tw.heat_rank AS tw_rank, tw.heat_score AS tw_heat,
       tw.ret_1d AS tw_ret_1d, tw.ret_5d AS tw_ret_5d, tw.ret_20d AS tw_ret_20d,
       us.heat_rank AS us_rank, us.heat_score AS us_heat,
       us.ret_1d AS us_ret_1d, us.ret_5d AS us_ret_5d, us.ret_20d AS us_ret_20d,
       us.ex_5d AS us_ex_5d, us.ex_20d AS us_ex_20d, us.n_members AS us_members
  FROM theme_daily tw
  JOIN theme t USING (theme_id)
  LEFT JOIN LATERAL (
        SELECT u.* FROM us_theme_daily u
         WHERE u.theme_id = tw.theme_id AND u.trade_date < tw.trade_date
         ORDER BY u.trade_date DESC LIMIT 1) us ON true
 WHERE t.layer = 3;
