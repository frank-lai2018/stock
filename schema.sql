-- 台股選股系統 schema（PostgreSQL）— 核心表（不含向量/RAG）
-- 設計說明見 資料庫設計.md
--
-- 執行順序：
--   1) 建 database：  psql -U postgres -c "CREATE DATABASE twstock ENCODING 'UTF8';"
--   2) 建核心表：     psql -U postgres -d twstock -f schema.sql
--   3) RAG 向量表：   裝好 pgvector 後再跑 schema_rag.sql
--
-- 全部 CREATE ... IF NOT EXISTS，可重複執行。

-- ========== 維度 ==========
CREATE TABLE IF NOT EXISTS stock (
    stock_id      VARCHAR(10) PRIMARY KEY,   -- 股票代碼（保留前導零）
    name          TEXT NOT NULL,             -- 中文名稱
    market        VARCHAR(16),               -- 上市 / 上櫃 / 上市臺灣創新板 …
    list_date     DATE,                      -- 上市櫃日期
    industry      TEXT,                      -- 產業別
    security_type VARCHAR(8) DEFAULT 'stock' -- stock=普通股 / etf=指數股票型基金
);
-- 既有資料庫升級：ALTER TABLE stock ADD COLUMN IF NOT EXISTS security_type VARCHAR(8) DEFAULT 'stock';

-- ETF 每日淨值 / 規模（來源：mis.twse all_etf.txt 揭露淨值；折溢價由市價與淨值計算）
CREATE TABLE IF NOT EXISTS etf_daily (
    stock_id    VARCHAR(10) NOT NULL,
    trade_date  DATE        NOT NULL,
    nav         NUMERIC,                   -- 每單位淨值（揭露/估計，收盤後≈官方）
    prev_nav    NUMERIC,                   -- 前一交易日淨值
    units       NUMERIC,                   -- 發行受益權單位數
    aum         NUMERIC,                   -- 規模 = units × nav
    nav_chg_pct NUMERIC,                   -- 淨值漲跌%
    PRIMARY KEY (stock_id, trade_date)
);

-- ========== 行情 ==========
CREATE TABLE IF NOT EXISTS price_daily (
    stock_id    VARCHAR(10) NOT NULL REFERENCES stock(stock_id),
    trade_date  DATE        NOT NULL,
    open NUMERIC(12,4), high NUMERIC(12,4), low NUMERIC(12,4), close NUMERIC(12,4),
    volume      BIGINT,        -- 成交股數（股，已正規化）
    amount      BIGINT,        -- 成交金額（元，已正規化）
    trades      INTEGER,       -- 成交筆數
    adj_open NUMERIC(12,4), adj_high NUMERIC(12,4), adj_low NUMERIC(12,4), adj_close NUMERIC(12,4),
    adj_factor  NUMERIC(16,10),-- 還原因子（cumfactor）
    PRIMARY KEY (stock_id, trade_date)
);
CREATE INDEX IF NOT EXISTS idx_price_date ON price_daily (trade_date);

CREATE TABLE IF NOT EXISTS market_index (
    index_id    VARCHAR(16) NOT NULL,      -- TAIEX 等
    trade_date  DATE        NOT NULL,
    close       NUMERIC(14,4),
    PRIMARY KEY (index_id, trade_date)
);

-- ========== 基本面 ==========
CREATE TABLE IF NOT EXISTS monthly_revenue (
    stock_id      VARCHAR(10) NOT NULL REFERENCES stock(stock_id),
    revenue_month DATE        NOT NULL,    -- 營收月份（該月1日）
    revenue       BIGINT,                  -- 當月營收（元）
    mom_pct       NUMERIC(18,2),           -- 成長率；基期近0時可能極大值
    yoy_pct       NUMERIC(18,2),
    available_date DATE,                   -- 可得日（次月10日）→ 回測防前視
    PRIMARY KEY (stock_id, revenue_month)
);

-- 長表：忠實保存 FinMind 損益/資產負債/現金流所有科目
CREATE TABLE IF NOT EXISTS financial_statement (
    stock_id     VARCHAR(10) NOT NULL REFERENCES stock(stock_id),
    period_date  DATE        NOT NULL,     -- 財報期別日（季底）
    statement    VARCHAR(10) NOT NULL,     -- income / balance / cashflow
    item         TEXT        NOT NULL,     -- FinMind 科目 key
    value        NUMERIC(20,4),
    available_date DATE,
    PRIMARY KEY (stock_id, period_date, statement, item)
);
CREATE INDEX IF NOT EXISTS idx_fs_item ON financial_statement (stock_id, item, period_date);

-- 寬表：選股常用指標（由長表 + 股本 ETL 彙整）
CREATE TABLE IF NOT EXISTS fundamentals_quarterly (
    stock_id       VARCHAR(10) NOT NULL REFERENCES stock(stock_id),
    period_date    DATE        NOT NULL,
    available_date DATE,
    revenue BIGINT, gross_profit BIGINT,
    operating_income BIGINT, pretax_income BIGINT, net_income BIGINT,
    eps NUMERIC(10,2),
    total_assets BIGINT, total_equity BIGINT, total_liabilities BIGINT,
    operating_cash_flow BIGINT,
    -- 比率欄位放寬：分母（營收/股本/權益）近0時算出的比率可能極大
    gross_margin NUMERIC(18,2), op_margin NUMERIC(18,2), net_margin NUMERIC(18,2),
    roe NUMERIC(18,2), debt_ratio NUMERIC(18,2),
    PRIMARY KEY (stock_id, period_date)
);

CREATE TABLE IF NOT EXISTS dividend (
    stock_id       VARCHAR(10) NOT NULL REFERENCES stock(stock_id),
    announce_date  DATE        NOT NULL,   -- 公告日（point-in-time 基準）
    year_label     TEXT,
    cash_dividend  NUMERIC(10,6),
    stock_dividend NUMERIC(10,6),
    ex_cash_date DATE, ex_stock_date DATE, cash_pay_date DATE,
    PRIMARY KEY (stock_id, announce_date)
);

CREATE TABLE IF NOT EXISTS capital_reduction (
    stock_id       VARCHAR(10) NOT NULL REFERENCES stock(stock_id),
    resume_date    DATE        NOT NULL,   -- 恢復買賣日
    pre_close      NUMERIC(12,4),
    post_ref_price NUMERIC(12,4),
    reason         TEXT,
    PRIMARY KEY (stock_id, resume_date)
);

-- ========== 籌碼 / 估值 ==========
CREATE TABLE IF NOT EXISTS inst_trades (   -- 三大法人淨額（股）
    stock_id     VARCHAR(10) NOT NULL REFERENCES stock(stock_id),
    trade_date   DATE        NOT NULL,
    foreign_net BIGINT, foreign_dealer_net BIGINT,
    trust_net BIGINT, dealer_self_net BIGINT, dealer_hedge_net BIGINT,
    PRIMARY KEY (stock_id, trade_date)
);
CREATE INDEX IF NOT EXISTS idx_inst_date ON inst_trades (trade_date);

CREATE TABLE IF NOT EXISTS margin_trading (
    stock_id       VARCHAR(10) NOT NULL REFERENCES stock(stock_id),
    trade_date     DATE        NOT NULL,
    margin_balance BIGINT, short_balance BIGINT,
    margin_buy BIGINT, margin_sell BIGINT,
    short_sell BIGINT, short_buy BIGINT,
    PRIMARY KEY (stock_id, trade_date)
);

CREATE TABLE IF NOT EXISTS shareholding (
    stock_id       VARCHAR(10) NOT NULL REFERENCES stock(stock_id),
    trade_date     DATE        NOT NULL,
    foreign_ratio  NUMERIC(8,2),
    foreign_shares BIGINT,
    shares_issued  BIGINT,
    PRIMARY KEY (stock_id, trade_date)
);

CREATE TABLE IF NOT EXISTS valuation_daily (
    stock_id       VARCHAR(10) NOT NULL REFERENCES stock(stock_id),
    trade_date     DATE        NOT NULL,
    per NUMERIC(10,2), pbr NUMERIC(10,2), dividend_yield NUMERIC(8,2),
    PRIMARY KEY (stock_id, trade_date)
);

-- 集保戶股權分散表（TDCC 週報）：持股分級 1~15（1=1-999股 … 15=1,000,001股以上）
-- 千張大戶=level 15；400張大戶=level 12~15。來源 TDCC opendata（update_holderdist.py）。
CREATE TABLE IF NOT EXISTS shareholding_dist (
    stock_id   VARCHAR(10) NOT NULL REFERENCES stock(stock_id),
    data_date  DATE        NOT NULL,   -- 集保資料日期（週）
    level      SMALLINT    NOT NULL,   -- 持股分級 1~15
    holders    BIGINT,                 -- 該級人數
    shares     BIGINT,                 -- 該級股數
    pct        NUMERIC(8,4),           -- 占集保庫存數比例%
    PRIMARY KEY (stock_id, data_date, level)
);
CREATE INDEX IF NOT EXISTS idx_holderdist_date ON shareholding_dist (data_date);

-- ========== 新聞（RAG 的文字部分；向量在 schema_rag.sql）==========
CREATE TABLE IF NOT EXISTS news (
    news_id      BIGSERIAL PRIMARY KEY,
    stock_id     VARCHAR(10) REFERENCES stock(stock_id),  -- 可為 NULL（總經新聞）
    title        TEXT,
    content      TEXT,
    source       TEXT,
    url          TEXT,
    published_at TIMESTAMPTZ            -- 發布時間（回測檢索防前視）
);
CREATE INDEX IF NOT EXISTS idx_news_stock_time ON news (stock_id, published_at);
