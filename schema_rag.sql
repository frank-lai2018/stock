-- 台股選股系統 schema — RAG 向量部分（pgvector）
-- 狀態（2026-07-12）：pgvector 0.8.4 已安裝並在 twstock 啟用，本檔已執行完成，news_embedding 表已建立。
-- 重跑方式（全部 IF NOT EXISTS，可安全重複執行）：
--   psql -U postgres -d twstock -f schema_rag.sql
-- 前置：schema.sql 已建好（本檔的 news_embedding 參照 news 表）。

CREATE EXTENSION IF NOT EXISTS vector;

-- 新聞向量（與 news 一對一；embedding 維度依你用的模型調整，1536=OpenAI text-embedding-3-small）
CREATE TABLE IF NOT EXISTS news_embedding (
    news_id   BIGINT PRIMARY KEY REFERENCES news(news_id) ON DELETE CASCADE,
    model     TEXT,
    embedding vector(1536)
);

-- 近似最近鄰索引（cosine）；資料量大才需要，小量 seq scan 也可
CREATE INDEX IF NOT EXISTS idx_news_vec ON news_embedding
    USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);
