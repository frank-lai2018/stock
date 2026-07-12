r"""update_fundamentals.py — 低頻基本面一鍵更新：串「FinMind 逐檔抓取 → 入庫」。

適用季/年更新的資料（財報/EPS、股利），逐檔成本一年只付幾次可接受。
每個 preset 會：
  1) 呼叫 fetch_fundamentals.py 對全部（或指定）代碼、以 --refresh + --start 重抓對應資料集 CSV
  2) 呼叫 load_to_db.py --since 把對應表增量 upsert 進 DB

preset 與對應資料集/表：
  quarterly    → FinMind: financials,balance,cashflow → 表: financial_statement, fundamentals_quarterly
  dividend     → FinMind: dividend                    → 表: dividend
  revenue      → FinMind: revenue                     → 表: monthly_revenue（另有 update_revenue.py 走 by-date，較快）
  capreduction → FinMind: capreduction                → 表: capital_reduction（減資，偶發）

前置：FINMIND_TOKEN、DATABASE_URL（或 --token / --dsn）。
用法：
  # 季報季（抓近一年財報覆蓋，入庫近一年期別）
  python update_fundamentals.py --preset quarterly --start 2025-06-30 --dsn "postgresql://frank:pwd@localhost:5432/twstock"
  # 股利旺季（抓今年公告）
  python update_fundamentals.py --preset dividend --start 2026-01-01 --dsn ...
  python update_fundamentals.py --preset quarterly --codes 2330,2317 --start 2025-06-30 --dsn ...   # 測試幾檔
  python update_fundamentals.py --preset quarterly --start 2025-06-30 --skip-fetch --dsn ...        # 只入庫（CSV 已抓）
"""
import argparse
import os
import subprocess
import sys

import load_to_db as L

HERE = os.path.dirname(os.path.abspath(__file__))
FETCH_SCRIPT = os.path.join(HERE, "fetch_fundamentals.py")
LOAD_SCRIPT = os.path.join(HERE, "load_to_db.py")

# preset -> (FinMind 資料集 keys, 對應 DB 表)
PRESETS = {
    "quarterly":   (["financials", "balance", "cashflow"], ["financial_statement", "fundamentals_quarterly"]),
    "dividend":    (["dividend"], ["dividend"]),
    "revenue":     (["revenue"], ["monthly_revenue"]),
    "capreduction": (["capreduction"], ["capital_reduction"]),   # 減資（偶發，還原價計算也會用到）
}


def all_codes(xlsx):
    """從代碼 Excel 取全部股號（沿用 load_to_db.load_stock）。"""
    return [r[0] for r in L.load_stock(xlsx) if r[0]]


def main():
    ap = argparse.ArgumentParser(description="低頻基本面一鍵更新（FinMind 逐檔 → 入庫）")
    ap.add_argument("--preset", required=True, choices=list(PRESETS) + ["all"], help="要更新的資料類別")
    ap.add_argument("--start", required=True, help="抓取/入庫起始日 YYYY-MM-DD（fetch 的 --start 與 load 的 --since）")
    ap.add_argument("--since", default="", help="入庫門檻日（預設同 --start）")
    ap.add_argument("--dsn", default=os.environ.get("DATABASE_URL", ""), help="PostgreSQL 連線字串")
    ap.add_argument("--root", default=r"H:\data", help=r"資料根目錄（load_to_db 用；預設 H:\data）")
    ap.add_argument("--fund-out", default=r"H:\data\Fundamentals", help=r"FinMind CSV 輸出根（fetch 用）")
    ap.add_argument("--xlsx", default="台股股票代碼NEW.xlsx", help="代碼 Excel")
    ap.add_argument("--codes", default="", help="只跑指定代碼（逗號分隔；預設全部）")
    ap.add_argument("--codes-file", default="", help="從檔案讀代碼（每行一個或逗號分隔）；用於額度用完後續跑，優先於 --codes")
    ap.add_argument("--token", default=os.environ.get("FINMIND_TOKEN", ""), help="FinMind token")
    ap.add_argument("--delay", type=float, default=1.0, help="fetch 每請求間隔秒（預設 1）")
    ap.add_argument("--max-per-hour", type=int, default=600, help="每小時請求上限（滑動視窗；預設 600，0=不限）")
    ap.add_argument("--skip-fetch", action="store_true", help="略過抓取，只入庫（CSV 已存在）")
    ap.add_argument("--skip-load", action="store_true", help="只抓取不入庫")
    args = ap.parse_args()

    since = args.since or args.start
    presets = list(PRESETS) if args.preset == "all" else [args.preset]
    datasets = sorted({d for p in presets for d in PRESETS[p][0]})
    tables = sorted({t for p in presets for t in PRESETS[p][1]})

    if args.codes_file:
        txt = open(args.codes_file, encoding="utf-8").read()
        codes = [c.strip() for c in txt.replace(",", "\n").splitlines() if c.strip()]
    else:
        codes = [c.strip() for c in args.codes.split(",") if c.strip()] or all_codes(args.xlsx)
    print(f"=== update_fundamentals｜preset={args.preset}｜{len(codes)} 檔｜"
          f"資料集 {datasets} → 表 {tables}｜start={args.start} since={since} ===")

    # 1) 抓取（FinMind 逐檔）
    if args.skip_fetch:
        print("\n[1/2] --skip-fetch：略過抓取。")
    else:
        if not args.token:
            print("⚠️ 無 FinMind token（--token / FINMIND_TOKEN），額度很低可能被擋。")
        print(f"\n[1/2] FinMind 抓取（{len(codes)} 檔 × {len(datasets)} 資料集，--refresh）…")
        cmd = ([sys.executable, FETCH_SCRIPT] + codes +
               ["--datasets", ",".join(datasets), "--start", args.start,
                "--out", args.fund_out, "--delay", str(args.delay),
                "--max-per-hour", str(args.max_per_hour), "--refresh"])
        if args.token:
            cmd += ["--token", args.token]
        if subprocess.run(cmd, cwd=HERE).returncode != 0:
            raise SystemExit("fetch_fundamentals 失敗，請看上方輸出。")

    # 2) 入庫（load_to_db 增量）
    if args.skip_load:
        print("\n[2/2] --skip-load：略過入庫。")
    else:
        if not args.dsn:
            raise SystemExit("要入庫但缺 --dsn / DATABASE_URL")
        print(f"\n[2/2] 入庫 {tables}（--since {since}）…")
        cmd = [sys.executable, LOAD_SCRIPT, "--root", args.root, "--xlsx", args.xlsx,
               "--tables", ",".join(tables), "--since", since, "--dsn", args.dsn]
        if args.codes:
            cmd += ["--codes", args.codes]
        if subprocess.run(cmd, cwd=HERE).returncode != 0:
            raise SystemExit("load_to_db 失敗，請看上方輸出。")

    print(f"\n=== 完成 ===")


if __name__ == "__main__":
    main()
