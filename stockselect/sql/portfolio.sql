SET client_encoding = 'UTF8';   -- 本檔為 UTF-8；Windows psql 預設 BIG5 會讀壞中文

-- portfolio — 持股診斷頁的使用者持股（單一使用者，每檔一列）
-- 後端 routers/portfolio.py 首次呼叫時會自動 CREATE TABLE IF NOT EXISTS 建立；
-- 本檔僅作為文件 / 手動建立 / 重建參考。屬使用者資料，nightly 不會更動它，pg_dump 會涵蓋。
--
-- 手動建立：psql -U frank -d twstock -f stockselect/sql/portfolio.sql

CREATE TABLE IF NOT EXISTS portfolio (
    stock_id   VARCHAR(16) PRIMARY KEY,   -- 股票代碼（每檔一列，重複加入即覆蓋）
    lots       NUMERIC,                   -- 張數（1張=1000股，可為零股小數）
    cost       NUMERIC,                   -- 每股平均成本
    note       VARCHAR(200),              -- 備註
    updated_at TIMESTAMP DEFAULT now()
);
