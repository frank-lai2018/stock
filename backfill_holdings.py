r"""backfill_holdings.py — 補齊「持股(trade_log)有、但價量資料缺」的個股。

每日更新(update_prices)只更新 H:\data 已有資料夾的股；你在交易紀錄裡新買、卻從沒
下載過歷史的股不會被涵蓋，個股 K 線圖 / 持股診斷會缺資料。本工具：
  1) 讀 trade_log 全部持股代碼
  2) 比對 DB price_daily 有無資料、H:\data 有無資料夾 → 找出「缺資料」的股
  3) 預設只「列出」；加 --run 才實跑補資料：下載歷史 → 重算還原 → 入庫

下載依 stock.market 自動分流（上市→TWSE、上櫃→TPEx）。不在 stock 主檔的代碼
（可能已下市 / 主檔未更新）會標記、不自動下載。補完記得 REFRESH 快照才會在 App 顯示。

連線：環境變數 DATABASE_URL 或 --dsn。
用法：
  python backfill_holdings.py --dsn "postgresql://frank:pwd@localhost:5432/twstock"        # 只列出缺哪些
  python backfill_holdings.py --dsn "..." --run                                            # 實際補（下載→還原→入庫）
  python backfill_holdings.py --dsn "..." --run --start 2010-01
"""
import argparse
import os
import subprocess
import sys

import build_adjusted_price as adj

HERE = os.path.dirname(os.path.abspath(__file__))
TWSE = os.path.join(HERE, "download_twse_csv.py")
TPEX = os.path.join(HERE, "download_tpex_csv.py")
LOAD = os.path.join(HERE, "load_to_db.py")


def _has_folder(out, code):
    return os.path.isdir(os.path.join(out, code))


def scan(dsn, out):
    """回傳 (all_rows, missing, unknown)。missing=主檔內但缺價量/資料夾；unknown=不在 stock 主檔。"""
    import psycopg2
    from psycopg2.extras import RealDictCursor
    conn = psycopg2.connect(dsn)
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute(
        "SELECT t.stock_id, s.name, s.market, s.security_type, "
        "  EXISTS(SELECT 1 FROM price_daily p WHERE p.stock_id = t.stock_id) AS has_price "
        "FROM (SELECT DISTINCT stock_id FROM trade_log) t "
        "LEFT JOIN stock s USING (stock_id) "
        "ORDER BY t.stock_id")
    rows = cur.fetchall()
    cur.close(); conn.close()
    unknown = [r for r in rows if r["market"] is None]
    known = [r for r in rows if r["market"] is not None]
    missing = [r for r in known if not r["has_price"] or not _has_folder(out, r["stock_id"])]
    return rows, missing, unknown


def main():
    ap = argparse.ArgumentParser(description="補齊持股缺失的價量資料")
    ap.add_argument("--dsn", default=os.environ.get("DATABASE_URL", ""), help="PostgreSQL 連線字串")
    ap.add_argument("--out", default=r"H:\data", help=r"股價根目錄（預設 H:\data）")
    ap.add_argument("--div-root", default=r"H:\data\Fundamentals", help=r"股利根目錄（還原用）")
    ap.add_argument("--start", default="2010-01", help="下載起始月份 YYYY-MM（預設 2010-01）")
    ap.add_argument("--delay", type=float, default=4.0, help="下載請求間隔秒（預設 4；別調太低會被封）")
    ap.add_argument("--run", action="store_true", help="實際執行補資料（預設只列出）")
    args = ap.parse_args()
    if not args.dsn:
        raise SystemExit("需要 --dsn 或環境變數 DATABASE_URL")

    rows, missing, unknown = scan(args.dsn, args.out)
    if not rows:
        print("trade_log 沒有任何持股代碼。")
        return

    known_n = len(rows) - len(unknown)
    print(f"持股 {len(rows)} 檔｜主檔內 {known_n}｜缺資料 {len(missing)}｜主檔外 {len(unknown)}")
    print("-" * 64)
    for r in missing:
        flags = []
        if not r["has_price"]:
            flags.append("DB無價量")
        if not _has_folder(args.out, r["stock_id"]):
            flags.append("無資料夾")
        print(f"  {r['stock_id']:<7} {(r['name'] or ''):<12} {r['market']:<4} "
              f"{(r['security_type'] or ''):<5} {'、'.join(flags)}")
    for r in unknown:
        print(f"  {r['stock_id']:<7} （不在 stock 主檔，可能已下市 / 主檔未更新，跳過）")

    if not missing:
        print("\n沒有需要補的個股。")
        return
    if not args.run:
        print("\n（僅列出。加 --run 實際補：下載歷史 → 重算還原 → 入庫）")
        return

    # 依市場分流下載（「櫃」→ 上櫃 TPEx，其餘 → 上市 TWSE；ETF 亦走 TWSE STOCK_DAY）
    codes = [r["stock_id"] for r in missing]
    tw = [r["stock_id"] for r in missing if "櫃" not in (r["market"] or "")]
    tp = [r["stock_id"] for r in missing if "櫃" in (r["market"] or "")]

    def dl(script, cs, label):
        if not cs:
            return
        print(f"\n=== 下載歷史（{label}）{len(cs)} 檔：{' '.join(cs)} ===")
        cmd = [sys.executable, script, *cs, "--start", args.start,
               "--out", args.out, "--delay", str(args.delay)]
        subprocess.run(cmd, cwd=HERE)

    dl(TWSE, tw, "上市 TWSE")
    dl(TPEX, tp, "上櫃 TPEx")

    print(f"\n=== 重算還原價 {len(codes)} 檔 ===")
    for c in codes:
        try:
            adj.build_one(c, args.out, args.div_root)
            print(f"  [還原] {c}")
        except Exception as e:
            print(f"  [還原失敗] {c}：{str(e)[:100]}")

    print(f"\n=== 入庫 price_daily {len(codes)} 檔 ===")
    cmd = [sys.executable, LOAD, "--root", args.out, "--tables", "price_daily",
           "--codes", ",".join(codes), "--dsn", args.dsn]
    if subprocess.run(cmd, cwd=HERE).returncode != 0:
        raise SystemExit("load_to_db 失敗，請看上方輸出。")

    print("\n完成。最後重整快照，App / 選股才看得到：")
    print('  psql -U frank -d twstock -c "REFRESH MATERIALIZED VIEW CONCURRENTLY mv_stock_snapshot;"')


if __name__ == "__main__":
    main()
