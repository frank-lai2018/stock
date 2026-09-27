-- schema_corp_action.sql — 官方除權息／減資／面額變更／ETF 分割參考價（fetch_corp_actions.py 寫入）
-- 還原價（build_adjusted_price.py）以這些官方事件為主；同一份資料也存 H:\data\CorpActions\corp_actions.csv。
-- fetch_corp_actions.py 每次執行都會先跑這支（冪等），不必手動建。

CREATE TABLE IF NOT EXISTS corp_action (
    stock_id    VARCHAR(10)    NOT NULL,
    action_date DATE           NOT NULL,   -- 除權息日／恢復買賣日（還原因子乘在這天「之前」的價格）
    kind        VARCHAR(8)     NOT NULL,   -- div 除權息／capred 減資／par 面額變更／split ETF 分割・反分割
    source      VARCHAR(16)    NOT NULL,   -- 官方表代號：TWT49U、exDailyQ、TWTAUU、revivt、TWTB8U、pvChgRslt、TWTCAU、etfSplitRslt、etfRvsRslt
    market      VARCHAR(4),                -- 上市／上櫃
    name        VARCHAR(40),
    prev_close  NUMERIC(14,4),             -- 除權息（停止買賣）前收盤價
    ref_price   NUMERIC(14,4),             -- 除權息參考價／恢復買賣參考價
    value       NUMERIC(14,6),             -- 除權息：權值＋息值（＝前收盤 − 參考價，6 位小數精確值）
    ratio       NUMERIC(16,10) NOT NULL,   -- 還原比例 r：除權息 (前收−權值息值)/前收，其他 參考價/前收
    note        VARCHAR(40),               -- 權/息、減資原因、分割/反分割
    fetched_at  TIMESTAMP      NOT NULL DEFAULT now(),
    PRIMARY KEY (stock_id, action_date, kind, source)
);
CREATE INDEX IF NOT EXISTS idx_corp_action_date ON corp_action (action_date);
