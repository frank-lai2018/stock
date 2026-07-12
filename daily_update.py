r"""daily_update.py — 每日一鍵：更新當日股價 → 重算還原價 → 增量入庫 → 更新法人/融資/PER。

把原本要手動跑的步驟串成一支，收盤後跑一次即可：
  1) update_prices.run       抓「指定交易日」全市場收盤，併入各股月檔 CSV，回傳當天有更新的股號
  2) build_adjusted_price    只對「當天有更新的個股」重算 <股號>_adj.csv（含最新日 + 還原價）
  3) load_to_db.py --since   只把當天的 price_daily upsert 進 PostgreSQL（增量，DB 寫入量最小）
  4) update_chips.py --date  by-date 抓全市場法人/融資/PER 直接入庫 + 存原始快照（inst_trades/margin_trading/valuation_daily）

為什麼要照這順序：跳過 (2)，load_to_db 讀不到當天的還原價 → price_daily 就缺這一天。
非交易日 (1) 會回空 → 自動跳過後續。全部可重跑（月檔依日期去重、DB upsert）。

連線（要入庫才需要）：設環境變數 DATABASE_URL，或帶 --dsn。
用法：
  python daily_update.py                                   # 更新「今天」→ H:\data → DB
  python daily_update.py --date 2026-07-11
  python daily_update.py --date 2026-07-11 --only 2330 5483  # 只跑幾檔（測試/補單檔）
  python daily_update.py --date 2026-07-11 --dry-run       # 做 (1)(2)；(3) 只驗證不寫 DB
  python daily_update.py --skip-load                       # 只做 (1)(2)（純 CSV，不碰 DB）
  python daily_update.py --all                             # (1) 連新股/ETF 一起建檔
"""
import argparse
import contextlib
import io
import os
import subprocess
import sys
from datetime import date

import build_adjusted_price as adj
import update_prices as up

HERE = os.path.dirname(os.path.abspath(__file__))
LOAD_SCRIPT = os.path.join(HERE, "load_to_db.py")
CHIPS_SCRIPT = os.path.join(HERE, "update_chips.py")


def main():
    ap = argparse.ArgumentParser(description="每日一鍵：更新股價 → 重算還原價 → 增量入庫")
    ap.add_argument("--date", default=date.today().isoformat(), help="交易日 YYYY-MM-DD（預設今天）")
    ap.add_argument("--out", default=r"H:\data", help=r"股價根目錄（預設 H:\data）")
    ap.add_argument("--div-root", default=r"H:\data\Fundamentals", help=r"股利根目錄（還原用；預設 H:\data\Fundamentals）")
    ap.add_argument("--dsn", default=os.environ.get("DATABASE_URL", ""), help="PostgreSQL 連線字串（或設環境變數 DATABASE_URL）")
    ap.add_argument("--tables", default="price_daily", help="入庫哪些表（傳給 load_to_db；預設只灌 price_daily）")
    ap.add_argument("--only", nargs="*", help="只跑指定代碼（預設全市場）")
    ap.add_argument("--all", action="store_true", help="更新股價時連沒下載過的新股/ETF 也建檔")
    ap.add_argument("--dry-run", action="store_true", help="入庫步驟只驗證不寫 DB（步驟 1、2 照常更新 CSV，籌碼仍存快照）")
    ap.add_argument("--skip-load", action="store_true", help="只做步驟 1、2（純 CSV，完全不碰 DB；籌碼仍存快照不入庫）")
    ap.add_argument("--skip-chips", action="store_true", help="略過步驟 4（法人/融資/PER）")
    args = ap.parse_args()

    try:                                   # 行緩衝：進度即時可見、且與子行程 load_to_db 輸出不會亂序（cron 導向 log 也對）
        sys.stdout.reconfigure(line_buffering=True)
    except (AttributeError, ValueError):
        pass

    try:
        date.fromisoformat(args.date)
    except ValueError:
        raise SystemExit("--date 需為 YYYY-MM-DD 格式")

    will_load = not args.skip_load and not args.dry_run
    if will_load and not args.dsn:
        raise SystemExit("要入庫但缺連線字串：請設環境變數 DATABASE_URL 或帶 --dsn（或改用 --skip-load / --dry-run）")

    print(f"=== 每日更新 {args.date} ｜ 根目錄 {args.out} ===")

    # 1) 更新當日股價（併入月檔）
    print("\n[1/3] 更新股價 …")
    codes = up.run(args.date, args.out, args.all, args.only)
    print(f"  當日有更新：{len(codes)} 檔")
    if not codes:
        print("  → 非交易日或端點無資料，跳過還原與入庫。")
        return

    # 2) 重算還原價（只重算當天有更新的個股；抑制逐檔輸出，只報進度與失敗）
    print(f"\n[2/3] 重算還原價（{len(codes)} 檔）…")
    ok = fail = 0
    for i, code in enumerate(codes, 1):
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                adj.build_one(code, args.out, args.div_root)
            ok += 1
        except Exception as e:
            fail += 1
            print(f"  [失敗] {code}：{e}")
        if i % 200 == 0:
            print(f"  … {i}/{len(codes)}")
    print(f"  還原完成：成功 {ok}、失敗 {fail}")

    # 3) 增量入庫股價（只灌當天的列）
    if args.skip_load:
        print("\n[3/4] --skip-load：略過股價入庫（CSV 已更新完成）。")
    else:
        print(f"\n[3/4] 入庫 {args.tables}（--since {args.date}）…")
        cmd = [sys.executable, LOAD_SCRIPT, "--root", args.out, "--since", args.date, "--tables", args.tables]
        codes_arg = ",".join(codes)
        if len(codes_arg) <= 20000:        # 只讀/灌當天有更新的檔（台股 ~1900 檔約 9千字元，遠低於命令列上限）
            cmd += ["--codes", codes_arg]
        cmd += ["--dry-run"] if args.dry_run else ["--dsn", args.dsn]
        if subprocess.run(cmd, cwd=HERE).returncode != 0:
            raise SystemExit("load_to_db 失敗，請看上方輸出。")

    # 4) 法人/融資/PER（by-date 全市場，直接入庫 + 存原始快照）
    if args.skip_chips:
        print("\n[4/4] --skip-chips：略過法人/融資/PER。")
    else:
        print(f"\n[4/4] 更新法人/融資/PER（{args.date}）…")
        cmd = [sys.executable, CHIPS_SCRIPT, "--date", args.date]
        # dry-run 或 skip-load → 只存快照不寫 DB；否則正式入庫
        cmd += ["--dry-run"] if (args.dry_run or args.skip_load) else ["--dsn", args.dsn]
        if subprocess.run(cmd, cwd=HERE).returncode != 0:
            raise SystemExit("update_chips 失敗，請看上方輸出。")

    print(f"\n=== 完成 {args.date} ===")


if __name__ == "__main__":
    main()
