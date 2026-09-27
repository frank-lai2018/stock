-- 台股選股系統 schema — 族群／題材分類（L2 產業鏈節點 + L3 市場題材）與每日族群熱度
-- 設計說明見 族群分類設計.md
--
-- 三層分類：
--   L1 官方產業別：沿用 stock.industry（一檔一類，不在本檔）
--   L2 產業鏈節點：櫃買中心「產業價值鏈資訊平台」鏈→節點→子節點（fetch_tpex_chain.py 每月抓）
--   L3 市場題材：CPO、CoWoS、記憶體、AI 伺服器…（theme_defs.py 定義，theme_candidates.py 產生候選、人工確認）
--
-- 重跑方式（全部 IF NOT EXISTS，可安全重複執行）：
--   psql -U postgres -d twstock -f schema_theme.sql
-- 前置：schema.sql 已建好（stock 表）。

-- 族群本體：L2 節點與 L3 題材共用一張表，用 parent_code 串成樹
CREATE TABLE IF NOT EXISTS theme (
    theme_id    SERIAL PRIMARY KEY,
    code        TEXT UNIQUE NOT NULL,     -- L2: 'tpex:D000'（鏈）/'tpex:D000:D100'（節點）/'tpex:D000:D120'（子節點）；L3: 'cpo'
    name        TEXT NOT NULL,            -- 顯示名稱，如「IC設計 > 光通訊IC」或「CPO／矽光子」
    layer       SMALLINT NOT NULL,        -- 2=產業鏈節點（櫃買）　3=市場題材
    parent_code TEXT,                     -- 上層 code；鏈本身為 NULL
    keywords    TEXT[],                   -- L3 對應 L2 節點用的關鍵字
    note        TEXT,
    is_active   BOOLEAN NOT NULL DEFAULT true,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_theme_layer  ON theme (layer);
CREATE INDEX IF NOT EXISTS idx_theme_parent ON theme (parent_code);

-- 個股 ↔ 族群（多對多，帶時效）
--   valid_from／valid_to：「我們何時得知這檔屬於這個族群」。回測務必用當時有效的成分，
--   否則會用到未來資訊（今天才被歸為 CPO 的股票，2025 年時市場未必這樣看它）。
--   同一 (stock_id, theme_id) 同時只會有一筆 valid_to IS NULL 的開啟列（見下方 partial unique index）。
CREATE TABLE IF NOT EXISTS stock_theme (
    stock_id    VARCHAR(10) NOT NULL,
    theme_id    INT         NOT NULL REFERENCES theme(theme_id) ON DELETE CASCADE,
    status      TEXT        NOT NULL,     -- confirmed=已確認 / seed=起手名單未複核 / candidate=系統建議 / rejected=已否決
    source      TEXT        NOT NULL,     -- tpex=櫃買產業鏈 / seed=起手名單 / node=L2 節點對應 / corr=股價相關 / manual=人工
    role        TEXT,                     -- L3：核心／受惠／沾邊；L2：櫃買市場分組（本國上市公司…）
    confidence  NUMERIC(4,3),             -- 0~1
    evidence    TEXT,                     -- 為什麼貼這個標籤（節點路徑、相關係數、來源網址）
    valid_from  DATE        NOT NULL DEFAULT CURRENT_DATE,
    valid_to    DATE,                     -- NULL = 目前仍有效
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (stock_id, theme_id, valid_from)
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_stock_theme_open ON stock_theme (stock_id, theme_id) WHERE valid_to IS NULL;
CREATE INDEX IF NOT EXISTS idx_stock_theme_theme ON stock_theme (theme_id) WHERE valid_to IS NULL;

-- 每日族群熱度（build_theme_daily.py 產生；只算 in_universe 成分股 ≥5 檔的族群）
CREATE TABLE IF NOT EXISTS theme_daily (
    theme_id     INT  NOT NULL REFERENCES theme(theme_id) ON DELETE CASCADE,
    trade_date   DATE NOT NULL,
    n_members    INT,                     -- 納入計算的成分股數（in_universe 且當日有價）
    ret_1d       NUMERIC(8,4),            -- 成分股等權平均報酬
    ret_5d       NUMERIC(8,4),
    ret_20d      NUMERIC(8,4),
    breadth_ma20 NUMERIC(6,4),            -- 站上月線（20MA）比例
    high20_pct   NUMERIC(6,4),            -- 收在 20 日新高的比例
    vol_ratio    NUMERIC(8,3),            -- 近 5 日均成交額 ÷ 近 20 日均成交額（族群合計）
    inst_ratio   NUMERIC(8,4),            -- 法人 20 日淨買超金額 ÷ 20 日成交額（族群合計）
    heat_score   NUMERIC(6,2),            -- 0~100，同日同層級內的相對熱度
    heat_rank    INT,                     -- 同日同層級內排名（1=最熱）
    PRIMARY KEY (theme_id, trade_date)
);
CREATE INDEX IF NOT EXISTS idx_theme_daily_date ON theme_daily (trade_date);
