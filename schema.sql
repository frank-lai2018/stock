-- 台股選股系統最小 schema（PostgreSQL + pgvector）
-- 對應 stock_screener.md 的「三個地基工程」：還原價、point-in-time、發布時間

CREATE EXTENSION IF NOT EXISTS vector;

-- 日K（地基①：存「還原股價」，算報酬/技術指標才不會錯）
CREATE TABLE IF NOT EXISTS price_daily (
  stock_id TEXT             NOT NULL,
  date     DATE             NOT NULL,
  open     DOUBLE PRECISION,
  high     DOUBLE PRECISION,
  low      DOUBLE PRECISION,
  close    DOUBLE PRECISION,            -- 還原收盤價
  volume   BIGINT,
  PRIMARY KEY (stock_id, date)
);

-- 月營收（地基②：point-in-time，務必記「公告日」announce_date 供回測對齊）
CREATE TABLE IF NOT EXISTS monthly_revenue (
  stock_id      TEXT   NOT NULL,
  revenue_month DATE   NOT NULL,        -- 營收所屬月份(月初, 如 2026-05-01)
  revenue       BIGINT,                 -- 當月營收(元)
  announce_date DATE,                   -- 實際公告日(近似~次月10日;回測只能用此日後的資料)
  PRIMARY KEY (stock_id, revenue_month)
);

-- 三大法人買賣超
CREATE TABLE IF NOT EXISTS institutional (
  stock_id TEXT   NOT NULL,
  date     DATE   NOT NULL,
  investor TEXT   NOT NULL,             -- Foreign_Investor / Investment_Trust / Dealer...
  net      BIGINT,                      -- 買賣超(股) = buy - sell
  PRIMARY KEY (stock_id, date, investor)
);

-- 新聞/法說會文本 + 向量（地基③：published_at 供 point-in-time 檢索）
CREATE TABLE IF NOT EXISTS news (
  id           BIGSERIAL PRIMARY KEY,
  stock_id     TEXT       NOT NULL,
  published_at TIMESTAMP  NOT NULL,     -- 發布時間：回測別偷看未來新聞
  title        TEXT,
  content      TEXT,
  embedding    vector(1536)             -- text-embedding-3-small 的維度
);

-- 向量索引（cosine 距離）；資料量大才需要，小資料 seq scan 也行
CREATE INDEX IF NOT EXISTS idx_news_embedding
  ON news USING hnsw (embedding vector_cosine_ops);
