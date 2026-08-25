r"""renko_etl.py — 全市場算 Renko / 三線反轉「狀態」，寫進 renko_state 表供選股視圖 join。

為什麼要獨立一張表：Renko 是**路徑相依**的（磚要一根一根走過去砌，還有「反轉需兩塊」的
狀態機），SQL 寫不出來，只能在 Python 算完落地。同 pattern_backtest 的作法。

寫出的特徵（每檔一列）：
  renko_dir / renko_run        目前磚色（1多 -1空）、已連續同向幾塊  ← 方向＋動能強度
  renko_flip_date / _days      最近一次翻轉的日期、距今幾個交易日    ← 「剛翻多」可當進場條件
  renko_bricks / brick         總磚數、用的磚高（樣本量與參數稽核用）
  tlb_dir / tlb_run / tlb_flip_* 三線反轉的同組欄位（較敏感，可當第二確認）

磚高一律 k × ATR(n)，**每檔各自算、整段固定**（見 app/nison_charts.py 的說明）。

用法（在 stockselect/backend 下，需可讀 .env 的 DATABASE_URL）：
  python renko_etl.py                      # 全母體（mv_stock_snapshot.in_universe）
  python renko_etl.py --limit 100          # 先試 100 檔
  python renko_etl.py --codes 2330,2317    # 只跑指定幾檔
  python renko_etl.py --atr-k 1.5 --lines 3
  python renko_etl.py --dry-run            # 只算不寫，印摘要

排程注意：要在 REFRESH MATERIALIZED VIEW mv_stock_snapshot **之前**跑，
        否則視圖 join 到的是昨天的狀態。
"""
import argparse
from datetime import datetime

from app import db, nison_charts as nc

BARS = 750            # 每檔取最近幾根日線（≈3 年，足夠讓磚數穩定）
CHUNK = 200           # 一次撈幾檔的日線（控制記憶體：200 檔 × 750 根 ≈ 15 萬列）
MIN_BARS = 60         # 太短算不出有意義的 ATR / 磚


def ensure_table():
    db.execute(
        "CREATE TABLE IF NOT EXISTS renko_state ("
        # 不加 stock(stock_id) 外鍵：後端跑的 frank 角色沒有 stock 表的 REFERENCES 權限
        # （同 pattern_event 的作法）。孤兒列由 MV 的 LEFT JOIN 自然忽略。
        " stock_id VARCHAR(16) PRIMARY KEY,"
        " as_of DATE,"                          # 計算基準日＝該檔最後一根日線
        " brick NUMERIC(12,4),"                 # 用的磚高（元）
        " renko_dir SMALLINT, renko_run SMALLINT, renko_bricks SMALLINT,"
        " renko_flip_date DATE, renko_flip_days SMALLINT,"
        " tlb_dir SMALLINT, tlb_run SMALLINT, tlb_lines SMALLINT,"
        " tlb_flip_date DATE, tlb_flip_days SMALLINT,"
        " updated_at TIMESTAMP)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_renko_dir ON renko_state (renko_dir)")


def universe(limit, codes):
    """母體：優先用 mv_stock_snapshot.in_universe（與選股器同一套標準）；
    視圖還沒建就退回「price_daily 有足夠天數」的股票。"""
    if codes:
        return [c.strip() for c in codes.split(",") if c.strip()]
    try:
        sql = "SELECT stock_id FROM mv_stock_snapshot WHERE in_universe ORDER BY amt20 DESC"
        if limit:
            sql += f" LIMIT {int(limit)}"
        rows = db.query(sql)
        if rows:
            return [r["stock_id"] for r in rows]
    except Exception as e:                      # 視圖不存在/欄位不符 → 退回
        print(f"  （讀不到 mv_stock_snapshot：{str(e)[:80]}；改用 price_daily 母體）")
    sql = ("SELECT stock_id FROM price_daily WHERE adj_close IS NOT NULL "
           "GROUP BY stock_id HAVING count(*) >= %(m)s ORDER BY stock_id")
    if limit:
        sql += f" LIMIT {int(limit)}"
    return [r["stock_id"] for r in db.query(sql, {"m": MIN_BARS})]


def bars_by_stock(codes):
    """一次撈一批股票的還原價日線，回傳 {stock_id: [bar,...]}（由舊到新）。
    逐檔各發一次 query 要 2,000 次來回，改成分批一次撈快很多。"""
    rows = db.query(
        "SELECT stock_id, trade_date, adj_high AS high, adj_low AS low, adj_close AS close "
        "FROM (SELECT stock_id, trade_date, adj_high, adj_low, adj_close,"
        "        row_number() OVER (PARTITION BY stock_id ORDER BY trade_date DESC) AS rn "
        "      FROM price_daily "
        "      WHERE stock_id = ANY(%(ids)s) AND adj_close IS NOT NULL) z "
        "WHERE rn <= %(n)s ORDER BY stock_id, trade_date",
        {"ids": list(codes), "n": BARS})
    out = {}
    for r in rows:
        out.setdefault(r["stock_id"], []).append(r)
    return out


def run_length(series):
    """尾端連續同向的筆數。"""
    if not series:
        return 0
    n, d = 1, series[-1]["dir"]
    for x in reversed(series[:-1]):
        if x["dir"] != d:
            break
        n += 1
    return n


def last_flip(series, bars):
    """最近一次方向翻轉：(日期, 距今幾個交易日)。從沒翻過回 (None, None)。"""
    fl = nc.flips(series)
    if not fl:
        return None, None
    f = fl[-1]
    return f["trade_date"], len(bars) - 1 - f["index"]


def market_last_date():
    return db.query("SELECT max(trade_date) AS d FROM price_daily")[0]["d"]


def state_of(bars, args, last_td=None):
    """算單檔的狀態 dict；資料不足、算不出磚高、或報價已停滯回 None。

    停滯判斷很重要：停牌／下市的股票最後一根可能停在幾個月前，照算會輸出一個
    看起來「現在是多方」但其實是四月的狀態，混進選股結果就是假訊號。
    """
    if len(bars) < MIN_BARS:
        return None
    if last_td is not None:
        stale = (last_td - bars[-1]["trade_date"]).days
        if stale > args.max_stale:
            return None
    brick = nc.atr_brick(bars, args.atr_n, args.atr_k)
    if not brick or brick <= 0:                 # 長期無波動（例如全額交割股停滯）
        return None
    br = nc.renko(bars, brick)
    tl = nc.three_line_break(bars, lines=args.lines)
    if not br or not tl:
        return None
    r_date, r_days = last_flip(br, bars)
    t_date, t_days = last_flip(tl, bars)
    return {"as_of": bars[-1]["trade_date"], "brick": round(brick, 4),
            "renko_dir": br[-1]["dir"], "renko_run": run_length(br), "renko_bricks": len(br),
            "renko_flip_date": r_date, "renko_flip_days": r_days,
            "tlb_dir": tl[-1]["dir"], "tlb_run": run_length(tl), "tlb_lines": len(tl),
            "tlb_flip_date": t_date, "tlb_flip_days": t_days}


COLS = ["stock_id", "as_of", "brick", "renko_dir", "renko_run", "renko_bricks",
        "renko_flip_date", "renko_flip_days", "tlb_dir", "tlb_run", "tlb_lines",
        "tlb_flip_date", "tlb_flip_days", "updated_at"]


def upsert(rows):
    if not rows:
        return 0
    return db.execute_many(
        f"INSERT INTO renko_state ({','.join(COLS)}) VALUES %s "
        "ON CONFLICT (stock_id) DO UPDATE SET " +
        ",".join(f"{c}=EXCLUDED.{c}" for c in COLS if c != "stock_id"),
        rows)


def main():
    ap = argparse.ArgumentParser(description="全市場 Renko / 三線反轉狀態 → renko_state")
    ap.add_argument("--codes", default="", help="只跑指定代碼（逗號分隔）")
    ap.add_argument("--limit", type=int, default=0, help="只跑前 N 檔（依成交額）")
    ap.add_argument("--atr-n", type=int, default=14, help="ATR 期數（預設 14）")
    ap.add_argument("--atr-k", type=float, default=1.0, help="磚高 = k × ATR（預設 1.0）")
    ap.add_argument("--lines", type=int, default=3, help="幾線反轉（預設 3）")
    ap.add_argument("--max-stale", type=int, default=10,
                    help="最後一根日線落後市場最新交易日超過幾個日曆天就跳過（停牌/下市；預設 10）")
    ap.add_argument("--dry-run", action="store_true", help="只算不寫，印摘要")
    args = ap.parse_args()

    codes = universe(args.limit, args.codes)
    print(f"=== renko_etl｜母體 {len(codes)} 檔｜磚高 {args.atr_k:g}×ATR({args.atr_n})｜"
          f"{args.lines} 線反轉{'｜DRY-RUN' if args.dry_run else ''} ===")
    if not codes:
        raise SystemExit("母體是空的，先確認 price_daily 有還原價資料。")
    if not args.dry_run:
        ensure_table()

    last_td = market_last_date()
    print(f"    市場最新交易日 {last_td}；落後超過 {args.max_stale} 天的（停牌/下市）跳過")
    now = datetime.now()
    done = skipped = written = 0
    bull = bear = 0
    for i in range(0, len(codes), CHUNK):
        batch = codes[i:i + CHUNK]
        data = bars_by_stock(batch)
        rows = []
        for sid in batch:
            st = state_of(data.get(sid, []), args, last_td)
            if st is None:
                skipped += 1
                continue
            done += 1
            bull += st["renko_dir"] == 1
            bear += st["renko_dir"] == -1
            rows.append(tuple([sid] + [st[c] for c in COLS[1:-1]] + [now]))
        if not args.dry_run:
            written += upsert(rows)
        print(f"  [{min(i + CHUNK, len(codes))}/{len(codes)}] 累計 算出 {done}、跳過 {skipped}")

    # 被跳過的檔不會被 upsert 覆蓋，舊狀態會一直留著 → 主動清掉，免得選股撈到過期方向
    if not args.dry_run:
        purged = db.execute(
            "DELETE FROM renko_state WHERE as_of < %(d)s::date - %(k)s",
            {"d": last_td, "k": args.max_stale})
        if purged:
            print(f"  [清理] 刪除 {purged} 筆過期狀態（as_of 落後超過 {args.max_stale} 天）")

    print(f"\n=== 完成：算出 {done} 檔、跳過 {skipped} 檔"
          f"{'（dry-run 未寫入）' if args.dry_run else f'、寫入 {written} 列'} ===")
    if done:
        print(f"    目前 Renko 方向：多 {bull} 檔（{bull / done:.1%}）／空 {bear} 檔")
        print("    提醒：這是特徵、不是訊號——請先用回測驗證它對既有因子有沒有加分。")
    if not args.dry_run:
        print("    下一步：REFRESH MATERIALIZED VIEW CONCURRENTLY mv_stock_snapshot;")


if __name__ == "__main__":
    main()
