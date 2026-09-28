-- 台股選股系統 schema — 主動式 ETF 每日持股與進出（申購贖回已扣除）
-- 設計說明見 主動ETF追蹤設計.md
--
-- 資料流：
--   fetch_active_etf.py  各投信官網每日公告的持股 → etf_snapshot（每檔每日一筆表頭）＋ etf_holding（明細）
--   build_etf_flow.py    相鄰兩個持股日相減、扣掉全面等比例增減 → etf_flow（每檔 ETF × 個股 × 日）
--   backtest_etf_flow.py 跟著進出訊號買賣的歷史回測 → etf_signal_backtest（彙總）＋ etf_signal_event（逐筆）
--
-- 重跑方式（全部 IF NOT EXISTS，可安全重複執行）：
--   psql -U postgres -d twstock -f schema_etf_holding.sql
-- 前置：schema.sql 已建好（stock、etf_daily 表）。

-- ETF 與投信、官網基金代碼的對照（由 active_etf_defs.py 同步；adapter 為 NULL＝尚未支援抓取）
CREATE TABLE IF NOT EXISTS etf_fund (
    etf_id      VARCHAR(10) PRIMARY KEY,      -- 00981A
    issuer      TEXT NOT NULL,                -- 統一
    adapter     TEXT,                         -- uni / capital / fh（fetch_active_etf.py 的抓取器）；NULL＝還沒寫
    fund_code   TEXT,                         -- 投信官網的基金代碼：49YTW / 399 / ETF23
    name        TEXT,
    is_active   BOOLEAN NOT NULL DEFAULT true,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 每檔 ETF 每個持股日一筆：規模、單位數、股票／期貨曝險
--   as_of 是「持股日」（該日收盤）。申購買回清單標的是下一個交易日，例：9/29 清單＝9/24 收盤持股。
CREATE TABLE IF NOT EXISTS etf_snapshot (
    etf_id         VARCHAR(10) NOT NULL REFERENCES etf_fund(etf_id),
    as_of          DATE NOT NULL,
    list_date      DATE,                      -- 申購買回清單日（來源有給才存，僅供對照）
    units          NUMERIC(20,0),             -- 已發行受益權單位數（同一份檔案，扣申購贖回用）
    units_chg      NUMERIC(20,0),             -- 來源自己算的「與前日單位差異數」（對帳用）
    nav_total      NUMERIC(20,2),             -- 基金淨資產（元）
    nav_unit       NUMERIC(12,4),             -- 每單位淨值
    stock_weight   NUMERIC(8,4),              -- 股票合計權重 %
    futures_weight NUMERIC(8,4),              -- 期貨名目本金合計權重 %（股票＋期貨＝整體持股水位）
    n_stocks       INT,                       -- 股票檔數（含佔位股）
    source         TEXT,                      -- 抓取器與網址
    raw_file       TEXT,                      -- 原始檔（H:\data\ETF_HOLD\...），解析規則改了可重跑
    fetched_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- 以下由 build_etf_flow.py 回填
    prev_as_of     DATE,                      -- 上一個持股日（中間漏抓時會跨多日）
    flow_k         NUMERIC(12,8),             -- 持股共同比例 k（最多檔一起變動的比例；全面等比例賣 1.7% → 0.983）
    flow_k_units   NUMERIC(12,8),             -- 單位數比例＝本日單位數 ÷ 前日單位數（對照用；經理人多半不會當天等比例買賣）
    PRIMARY KEY (etf_id, as_of)
);

-- 持股明細（原樣保存，含佔位股：1 張、權重 0.00% 那種）
CREATE TABLE IF NOT EXISTS etf_holding (
    etf_id   VARCHAR(10) NOT NULL,
    as_of    DATE NOT NULL,
    kind     VARCHAR(8)  NOT NULL,            -- stock / futures
    code     VARCHAR(20) NOT NULL,            -- 股票代號；期貨＝代號＋契約年月（TX202610）
    name     TEXT,
    shares   NUMERIC(20,0) NOT NULL,          -- 股數；期貨為口數
    weight   NUMERIC(8,4),                    -- 權重 %
    amount   NUMERIC(20,2),                   -- 市值（來源有給才存）
    PRIMARY KEY (etf_id, as_of, kind, code),
    FOREIGN KEY (etf_id, as_of) REFERENCES etf_snapshot (etf_id, as_of) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_etf_holding_code ON etf_holding (code, as_of) WHERE kind = 'stock';

-- 每日進出（build_etf_flow.py 產生；只存股數有變動、或新建倉／出清的列）
--   實際增減  d_shares      ＝ 本日股數 − 前日股數（市場上真實的買賣）
--   主動調整  active_shares ＝ 本日股數 − 前日股數 × k（扣掉全面等比例的增減後，針對個股的判斷）
--   action：new 新建倉 / exit 出清 / add 加碼 / cut 減碼
--           add_rel 相對加碼（全面等比例賣時賣得比別檔少）/ cut_rel 相對減碼（全面等比例買時買得比別檔少）
--           flow 只是跟著全面等比例增減（或整張執行的零頭）/ corp 配股、減資、面額變更（沒有交易）
CREATE TABLE IF NOT EXISTS etf_flow (
    etf_id        VARCHAR(10) NOT NULL,
    trade_date    DATE NOT NULL,              -- 本期持股日
    stock_id      VARCHAR(20) NOT NULL,
    prev_date     DATE NOT NULL,              -- 比較的前一持股日
    shares_prev   NUMERIC(20,0) NOT NULL,     -- 佔位股以 0 計
    shares        NUMERIC(20,0) NOT NULL,
    d_shares      NUMERIC(20,0) NOT NULL,
    active_shares NUMERIC(20,1) NOT NULL,
    close         NUMERIC(12,4),              -- 當日收盤（price_daily；缺價時用權重回推）
    amount        NUMERIC(20,0),              -- d_shares × close
    active_amount NUMERIC(20,0),              -- active_shares × close
    weight_prev   NUMERIC(8,4),
    weight        NUMERIC(8,4),
    action        VARCHAR(8) NOT NULL,
    PRIMARY KEY (etf_id, trade_date, stock_id)
);
CREATE INDEX IF NOT EXISTS idx_etf_flow_date  ON etf_flow (trade_date);
CREATE INDEX IF NOT EXISTS idx_etf_flow_stock ON etf_flow (stock_id, trade_date);

-- 訊號回測（backtest_etf_flow.py 每週產生，整批覆蓋）：跟著主動 ETF 進出買賣，有沒有超額報酬
--   進出場：T+1 開盤買進、持有到 T+h 收盤（還原價、未扣成本）
--   基準：大盤（母體等權）與持股籃（當天所有主動 ETF 持股等權）；signal='basket' 是持股籃本身 vs 大盤
CREATE TABLE IF NOT EXISTS etf_signal_backtest (
    signal               VARCHAR(12) NOT NULL,   -- buy2 / buy1 / new / sell2 / sell1 / exit / basket
    horizon              INT NOT NULL,           -- 持有交易日數
    name                 TEXT,                   -- 共識買進…
    note                 TEXT,                   -- 訊號定義
    sign                 SMALLINT,               -- 預期方向：+1 應贏基準／-1 應輸基準
    n                    INT,                    -- 事件數（basket＝不重疊樣本數）
    avg_ret              NUMERIC,
    avg_excess_mkt       NUMERIC,                -- 相對大盤
    avg_excess_basket    NUMERIC,                -- 相對持股籃（扣掉「本來就持有強勢股」的效果）
    median_excess_basket NUMERIC,
    win_excess_basket    NUMERIC,                -- 贏持股籃的比例（basket＝贏大盤的比例）
    t_stat               NUMERIC,                -- 事件視為獨立算的 t 值，事件重疊會高估
    months               INT,                    -- 有事件的月份數
    month_pos            INT,                    -- 其中月平均超額為正的月份數
    gap_ex               NUMERIC,                -- T 收盤 → T+1 開盤的跳空（相對大盤）
    date_from            DATE,
    date_to              DATE,
    computed_at          TIMESTAMP,
    PRIMARY KEY (signal, horizon)
);

CREATE TABLE IF NOT EXISTS etf_signal_event (
    signal        VARCHAR(12) NOT NULL,
    stock_id      VARCHAR(20) NOT NULL,
    trade_date    DATE NOT NULL,                 -- 訊號持股日 T（T+1 開盤進場）
    n_issuers     INT,                           -- 買進（或賣出）的投信家數
    inflow        BOOLEAN,                       -- 買方 ETF 當天單位數增加 > 1%（申購期）
    impact        NUMERIC,                       -- 主動金額 ÷ 20 日均成交額
    gap_ex        NUMERIC,
    rets          JSONB,                         -- {持有日數: 報酬}
    excess_mkt    JSONB,
    excess_basket JSONB,
    PRIMARY KEY (signal, stock_id, trade_date)
);
CREATE INDEX IF NOT EXISTS idx_etf_signal_event_date ON etf_signal_event (signal, trade_date DESC);

-- 持股籃策略回測（backtest_etf_basket.py，跟 etfbacktest 一起每週跑，整批覆蓋）
--   持有「被 N 家投信的主動 ETF 同時持有」的股票、定期換股，對照直接買 00981A、0050
CREATE TABLE IF NOT EXISTS etf_basket_summary (
    strategy     VARCHAR(16) PRIMARY KEY,   -- basket1_m / basket2_m / basket3_m / basket2_w / basket2_vw / 00981A / 0050
    name         TEXT,
    kind         VARCHAR(10),               -- strategy / benchmark
    date_from    DATE,
    date_to      DATE,
    days         INT,
    total_ret    NUMERIC,                   -- 總報酬（已扣交易成本，含最後賣出）
    cagr         NUMERIC,                   -- 年化報酬
    vol          NUMERIC,                   -- 年化波動
    mdd          NUMERIC,                   -- 最大回檔
    ret_vol      NUMERIC,                   -- 年化報酬 ÷ 年化波動
    avg_n        NUMERIC,                   -- 平均持股檔數
    rebalances   INT,                       -- 換股次數
    avg_turnover NUMERIC,                   -- 每次換股賣掉的比例
    cost_total   NUMERIC,                   -- 交易成本合計（占淨值）
    bench_cagr   NUMERIC,                   -- 同一段期間 00981A 的年化（策略列才有）
    months       INT,                       -- 可比較的月數
    months_beat  INT,                       -- 月報酬贏 00981A 的月數
    excess_sum   NUMERIC,                   -- 每月超額（對 00981A）加總
    excess_ex_top2 NUMERIC,                 -- 拿掉最好兩個月後的每月超額加總（看超額是不是集中在少數月份）
    computed_at  TIMESTAMP
);
ALTER TABLE etf_basket_summary ADD COLUMN IF NOT EXISTS excess_sum NUMERIC;       -- 既有資料庫升級
ALTER TABLE etf_basket_summary ADD COLUMN IF NOT EXISTS excess_ex_top2 NUMERIC;

CREATE TABLE IF NOT EXISTS etf_basket_curve (
    strategy   VARCHAR(16) NOT NULL,
    trade_date DATE NOT NULL,
    nav        NUMERIC NOT NULL,            -- 起始＝1（已扣買進成本）
    PRIMARY KEY (strategy, trade_date)
);

-- 投信規模占比（2026-09-28）：各 ETF 最新持股日的基金淨資產，依投信加總（只含已支援的 ETF）。
--   共識家數、持有家數只算 major（占比 ≥ 1%）的投信：第三階段的小投信（第一金、兆豐、摩根、台新）照抓照顯示，
--   但不計入家數。回測（backtest_etf_flow.py 用事件當天的占比）顯示這樣共識買進 5／10／20 日都比較好。
CREATE OR REPLACE VIEW etf_issuer_share AS
SELECT e.issuer,
       sum(l.nav_total) AS nav_total,
       sum(l.nav_total) / NULLIF(sum(sum(l.nav_total)) OVER (), 0) AS share,
       COALESCE(sum(l.nav_total) / NULLIF(sum(sum(l.nav_total)) OVER (), 0) >= 0.01, false) AS major
FROM (SELECT DISTINCT ON (etf_id) etf_id, nav_total FROM etf_snapshot ORDER BY etf_id, as_of DESC) l
JOIN etf_fund e USING (etf_id)
WHERE e.adapter IS NOT NULL
GROUP BY e.issuer;
