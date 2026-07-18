SET client_encoding = 'UTF8';   -- 本檔為 UTF-8；Windows psql 預設 BIG5 會讀壞中文

-- trade_log — 持股診斷頁的交易明細帳（單次買/賣，記日期、手續費、證交稅）
-- 為真實來源；未平倉持股與已實現損益皆由後端 ledger FIFO 引擎推導。
-- 後端 routers/portfolio.py 首次呼叫會自動 CREATE TABLE IF NOT EXISTS 建立；
-- 本檔僅作文件 / 手動建立參考。屬使用者資料，nightly 不更動，pg_dump 會涵蓋。
--
-- 手動建立：psql -U frank -d twstock -f stockselect/sql/trade_log.sql

CREATE TABLE IF NOT EXISTS trade_log (
    id         SERIAL PRIMARY KEY,
    stock_id   VARCHAR(16) NOT NULL,
    action     VARCHAR(4)  NOT NULL,      -- buy / sell
    trade_date DATE        NOT NULL,      -- 交易日
    lots       NUMERIC     NOT NULL,      -- 張數（1張=1000股，可零股小數）
    price      NUMERIC     NOT NULL,      -- 每股成交價
    fee        NUMERIC,                   -- 手續費（該筆總額，可留空=0）
    tax        NUMERIC,                   -- 證交稅（賣出，可留空=0）
    note       VARCHAR(200),
    created_at TIMESTAMP DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_trade_stock ON trade_log(stock_id);
