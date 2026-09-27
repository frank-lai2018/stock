r"""daily_update.py — 每日一鍵：更新當日股價 → 官方除權息/減資/分割 → 重算還原價 → 增量入庫 → 更新法人/融資/PER。

把原本要手動跑的步驟串成一支，收盤後跑一次即可：
  1) update_prices.run       抓「指定交易日」全市場收盤，併入各股月檔 CSV，回傳當天有更新的股號
  2) fetch_corp_actions.run  抓上市櫃官方「除權息／減資／面額變更／ETF 分割」參考價（9 個請求；還原價的主要來源）
  3) build_adjusted_price    只對「當天有更新的個股」重算 <股號>_adj.csv（含最新日 + 還原價）
  4) load_to_db.py --since   把當天的 price_daily upsert 進 PostgreSQL；還原因子有變（當天除權息、減資、分割）
                             的股票自動整段重灌（後復權：事件日之前的還原價全部要改，只灌當天會斷一截）
  5) update_chips.py --date  by-date 抓全市場法人/融資/PER 直接入庫 + 存原始快照（inst_trades/margin_trading/valuation_daily）
  6) fetch_price_index.py    抓當月 大盤(價格)/櫃買指數 併入 Index CSV → 入庫 market_index（TWSE/TPEx）

為什麼要照這順序：跳過 (3)，load_to_db 讀不到當天的還原價 → price_daily 就缺這一天；(2) 要在 (3) 之前，
當天的除權息才會用官方數字還原（(2) 失敗不中斷：當天改用 FinMind 股利後備，下次自動補抓、(4) 會整段重灌）。
非交易日 (1) 會回空 → 自動跳過後續。全部可重跑（月檔依日期去重、DB upsert）。

連線（要入庫才需要）：設環境變數 DATABASE_URL，或帶 --dsn。
用法：
  python daily_update.py                                   # 更新「今天」→ H:\data → DB
  python daily_update.py --date 2026-07-11
  python daily_update.py --date 2026-07-11 --only 2330 5483  # 只跑幾檔（測試/補單檔）
  python daily_update.py --date 2026-07-11 --dry-run       # 做 (1)(2)(3)；(4) 只驗證不寫 DB
  python daily_update.py --skip-load                       # 只做 (1)(2)(3)（純 CSV，不碰 DB）
  python daily_update.py --all                             # (1) 連新股/ETF 一起建檔
"""
import argparse
import contextlib
import io
import os
import subprocess
import sys
import time
from datetime import date

import build_adjusted_price as adj
import fetch_corp_actions as corp
import update_prices as up

HERE = os.path.dirname(os.path.abspath(__file__))
LOAD_SCRIPT = os.path.join(HERE, "load_to_db.py")
CHIPS_SCRIPT = os.path.join(HERE, "update_chips.py")
INDEX_SCRIPT = os.path.join(HERE, "fetch_price_index.py")


def _build_one(task):
    """多核 worker：重算單檔還原價（抑制輸出）。回傳 (code, 成功?, 錯誤)。"""
    code, out, div_root = task
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            adj.build_one(code, out, div_root)
        return code, True, ""
    except Exception as e:
        return code, False, str(e)[:100]


def clean_dsn(dsn):
    """容錯＋防呆：剝掉誤貼進值裡的旗標／引號，並先擋掉明顯不合法的連線字串。
    （踩過的雷：DATABASE_URL="--dsn postgresql://…" 會一路傳到 psycopg2 才爆
     invalid dsn: missing "=" after "--dsn"，而且 daily 已先燒掉數十秒的還原價計算。）"""
    dsn = (dsn or "").strip().strip('"').strip("'").strip()
    if dsn.startswith("--dsn"):                      # 誤把旗標本身貼進值裡
        dsn = dsn[5:].lstrip().lstrip("=").lstrip()
    if dsn and not (dsn.startswith(("postgresql://", "postgres://")) or "=" in dsn):
        raise SystemExit(f"連線字串格式不對：{dsn!r}；"
                         "應為 postgresql://user:pw@host:port/db（或 key=value 形式）")
    return dsn


def main():
    ap = argparse.ArgumentParser(description="每日一鍵：更新股價 → 官方除權息/減資/分割 → 重算還原價 → 增量入庫")
    ap.add_argument("--date", default=date.today().isoformat(), help="交易日 YYYY-MM-DD（預設今天）")
    ap.add_argument("--out", default=r"H:\data", help=r"股價根目錄（預設 H:\data）")
    ap.add_argument("--div-root", default=r"H:\data\Fundamentals", help=r"股利根目錄（還原用；預設 H:\data\Fundamentals）")
    ap.add_argument("--dsn", default=os.environ.get("DATABASE_URL", ""), help="PostgreSQL 連線字串（或設環境變數 DATABASE_URL）")
    ap.add_argument("--tables", default="price_daily", help="入庫哪些表（傳給 load_to_db；預設只灌 price_daily）")
    ap.add_argument("--only", nargs="*", help="只跑指定代碼（預設全市場）")
    ap.add_argument("--all", action="store_true", help="更新股價時連沒下載過的新股/ETF 也建檔")
    ap.add_argument("--dry-run", action="store_true", help="入庫步驟只驗證不寫 DB（步驟 1~3 照常更新 CSV，籌碼仍存快照）")
    ap.add_argument("--skip-load", action="store_true", help="只做步驟 1~3（純 CSV，完全不碰 DB；籌碼仍存快照不入庫）")
    ap.add_argument("--skip-corp", action="store_true", help="略過步驟 2（官方除權息/減資/分割；還原價用上次抓到的＋FinMind 後備）")
    ap.add_argument("--skip-chips", action="store_true", help="略過步驟 5（法人/融資/PER）")
    ap.add_argument("--skip-index", action="store_true", help="略過步驟 6（大盤/櫃買價格指數）")
    ap.add_argument("--workers", type=int, default=0, help="步驟 3 重算還原價的平行核心數（0=自動 CPU 數）")
    args = ap.parse_args()
    args.dsn = clean_dsn(args.dsn)

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
    laps, _t = {}, time.time()

    def lap(name):
        nonlocal _t
        dt = time.time() - _t
        laps[name] = dt
        print(f"  ⏱ {name} 耗時 {dt:.1f}s")     # 每步即時印出
        _t = time.time()

    # 1) 更新當日股價（併入月檔）
    print("\n[1/6] 更新股價 …")
    codes = up.run(args.date, args.out, args.all, args.only)
    print(f"  當日有更新：{len(codes)} 檔")
    lap("1 股價")
    if not codes:
        print("  → 非交易日或端點無資料，跳過還原與入庫。")
        return

    # 2) 官方除權息／減資／面額變更／ETF 分割（還原價的主要來源；失敗不中斷，當天改用 FinMind 後備）
    if args.skip_corp:
        print("\n[2/6] --skip-corp：略過官方除權息/減資/分割。")
    else:
        print("\n[2/6] 官方除權息／減資／面額變更／ETF 分割 …")
        try:
            _, failed = corp.run(out=os.path.join(args.out, adj.CORP_DIR),
                                 dsn="" if (args.dry_run or args.skip_load) else args.dsn)
            if failed:
                print(f"  ⚠️ {failed} 沒抓到：這些來源的新事件先用 FinMind 後備，下次自動補抓")
        except Exception as e:
            print(f"  ⚠️ 官方事件抓取失敗（{str(e)[:100]}）：先用上次抓到的＋FinMind 後備，下次自動補抓")
    lap("2 官方事件")

    # 3) 重算還原價（只重算當天有更新的個股；多核平行、抑制逐檔輸出）
    import multiprocessing as mp
    workers = max(1, min(args.workers or (os.cpu_count() or 4), 16))
    print(f"\n[3/6] 重算還原價（{len(codes)} 檔，{workers} 核平行）…")
    ok = fail = 0
    tasks = [(c, args.out, args.div_root) for c in codes]
    with mp.Pool(workers) as pool:
        for i, (code, good, err) in enumerate(pool.imap_unordered(_build_one, tasks, chunksize=8), 1):
            if good:
                ok += 1
            else:
                fail += 1
                print(f"  [失敗] {code}：{err}")
            if i % 400 == 0:
                print(f"  … {i}/{len(codes)}")
    print(f"  還原完成：成功 {ok}、失敗 {fail}")
    lap("3 還原價")

    # 4) 增量入庫股價（當天的列；還原因子有變的股票 load_to_db 會自動整段重灌）
    if args.skip_load:
        print("\n[4/6] --skip-load：略過股價入庫（CSV 已更新完成）。")
    else:
        print(f"\n[4/6] 入庫 {args.tables}（--since {args.date}；還原因子有變的整段重灌）…")
        cmd = [sys.executable, LOAD_SCRIPT, "--root", args.out, "--since", args.date, "--tables", args.tables]
        codes_arg = ",".join(codes)
        if len(codes_arg) <= 20000:        # 只讀/灌當天有更新的檔（台股 ~1900 檔約 9千字元，遠低於命令列上限）
            cmd += ["--codes", codes_arg]
        cmd += ["--dry-run"] if args.dry_run else ["--dsn", args.dsn]
        if subprocess.run(cmd, cwd=HERE).returncode != 0:
            raise SystemExit("load_to_db 失敗，請看上方輸出。")
    lap("4 股價入庫")

    # 5) 法人/融資/PER（by-date 全市場，直接入庫 + 存原始快照）
    if args.skip_chips:
        print("\n[5/6] --skip-chips：略過法人/融資/PER。")
    else:
        print(f"\n[5/6] 更新法人/融資/PER（{args.date}）…")
        cmd = [sys.executable, CHIPS_SCRIPT, "--date", args.date]
        # dry-run 或 skip-load → 只存快照不寫 DB；否則正式入庫
        cmd += ["--dry-run"] if (args.dry_run or args.skip_load) else ["--dsn", args.dsn]
        if subprocess.run(cmd, cwd=HERE).returncode != 0:
            raise SystemExit("update_chips 失敗，請看上方輸出。")
    lap("5 法人/融資/PER/外資")

    # 6) 大盤/櫃買價格指數（抓當月 by-month → 併入 Index CSV → 增量入庫 market_index）
    if args.skip_index:
        print("\n[6/6] --skip-index：略過大盤/櫃買指數。")
    else:
        print(f"\n[6/6] 更新大盤/櫃買指數（{args.date[:7]}）…")
        cmd = [sys.executable, INDEX_SCRIPT, "--start", args.date[:7], "--out", os.path.join(args.out, "Index")]
        if subprocess.run(cmd, cwd=HERE).returncode != 0:
            print("  ⚠️ 指數抓取失敗（不中斷後續）。")
        elif not args.skip_load and not args.dry_run:
            cmd = [sys.executable, LOAD_SCRIPT, "--root", args.out, "--tables", "market_index",
                   "--since", args.date, "--dsn", args.dsn]
            if subprocess.run(cmd, cwd=HERE).returncode != 0:
                print("  ⚠️ 指數入庫失敗。")
    lap("6 指數")

    print(f"\n=== 完成 {args.date}（各步耗時）===")
    for name, sec in laps.items():
        print(f"  {name:22} {sec:6.1f}s")
    print(f"  {'合計':22} {sum(laps.values()):6.1f}s")


if __name__ == "__main__":
    main()
