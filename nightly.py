r"""nightly.py — 排程大腦：每晚無腦執行這一支，由它依「今天日期」自動判斷該跑哪些更新。

你只要每晚跑：
  python nightly.py --dsn "postgresql://frank:pwd@localhost:5432/twstock"
（或設環境變數 DATABASE_URL / FINMIND_TOKEN 後直接 python nightly.py）

補跑（例：漏了 9/3、9/4 兩個交易日）：依日期先後跑，只有最後一天讓它 refresh
（還原價是累積計算的，順序不可顛倒；mv_stock_snapshot 刷新一次要 ~200 秒，不必每天刷）：
  python nightly.py --only daily --date 2026-09-03 --skip-refresh
  python nightly.py --only daily --date 2026-09-04
連線字串請走環境變數，別寫進這個檔（本檔有進版控）：
  PowerShell：$env:DATABASE_URL = "postgresql://USER:PASSWORD@localhost:5432/twstock"
  或每條指令帶 --dsn "$env:DATABASE_URL"

它管理的工作與排程規則：
  daily      每晚都跑 daily_update.py（股價+法人+融資+PER）；非交易日腳本自己會跳過。
  holderdist 每晚檢查 update_holderdist.py（集保股權分散；TDCC 週資料，idempotent 自動抓最新週 + 存快照）。
  revenue    每月 11~20 號跑 update_revenue.py（月營收；cheap，順便補晚申報者）。
  quarterly  每晚跑 update_fundamentals_opendata.py（TWSE/櫃買 opendata 當期財報，全市場
               約 10 個請求、幾分鐘）。各家申報時間不一，每晚重跑才會陸續補齊。
  dividend   股利旺季 5~8 月「週日」每週跑一次 update_fundamentals.py --preset dividend（重工作）。
  capreduction 減資（還原價會用到）：綁季報窗口的「週日」跑，狀態檔防重（本季一次）。且**避開股利
               旺季**（5~8 月週日都被 dividend 佔用）→ 實際只在 4 月（Q4 窗口）與 11 月（Q3 窗口）
               各跑一次，一年兩次。要臨時補：python nightly.py --only capreduction

重工作為何只排週日：capreduction / dividend 走 FinMind 逐檔抓，約 2,300 檔，撞每小時上限
        （run_nightly.bat 設 FINMIND_MAX_PER_HOUR=550，帳號額度 600 留緩衝）就得睡到視窗釋放
        ≈ 4~5hr，平常上班日晚上跑不完。真的要在平日補跑：python nightly.py --only dividend
        （--only 會忽略此限制）。季報原本也是重工作（3 資料集 ≈ 6,900 次 ≈ 13hr），已改走 opendata。
        限流用量記在 finmind_rate_state.json，跨行程共用：同一晚先跑 dividend 再跑 capreduction
        不會各自重新計數，合計仍受 550/hr 約束。
  etfnav     每晚固定跑 fetch_etf_nav.py（ETF 淨值/折溢價/規模；mis.twse 單一請求，便宜）。
  renko      每晚固定跑 renko_etl.py（磚形圖/三線反轉狀態 → renko_state；全市場約 35 秒）。
               **必須排在 refresh 之前**，否則選股視圖 join 到的是昨天的狀態。
  refresh    以上跑完後，刷新選股物化視圖 mv_stock_snapshot（選股器同步最新；--skip-refresh 可略過）。

防重複：quarterly / dividend 是 FinMind 逐檔的重工作，用狀態檔 nightly_state.json 記錄
        「本季/本週已完成」，跨夜自動不重跑（daily / revenue 便宜則照排程窗口每晚跑）。

其他用法：
  python nightly.py --plan                      # 只印「今天會跑哪些、為什麼」，不執行
  python nightly.py --only quarterly --dsn ...  # 強制只跑某工作（忽略排程/狀態檔）
  python nightly.py --skip capreduction --dsn ...  # 今晚略過某支重工作，其餘照排程跑
  python nightly.py --date 2026-08-15 --plan    # 模擬某天的排程決策
"""
import argparse
import json
import os
import subprocess
import sys
from datetime import date, datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(HERE, "nightly_state.json")
DAILY = os.path.join(HERE, "daily_update.py")
REVENUE = os.path.join(HERE, "update_revenue.py")
FUND = os.path.join(HERE, "update_fundamentals.py")
FUND_OPEN = os.path.join(HERE, "update_fundamentals_opendata.py")   # 財報走 TWSE/櫃買 opendata（便宜）
HOLDERDIST = os.path.join(HERE, "update_holderdist.py")
ETFNAV = os.path.join(HERE, "fetch_etf_nav.py")
BACKTEST_DIR = os.path.join(HERE, "stockselect", "backend")   # 回測腳本在後端（需 import app）
BACKTEST = os.path.join(BACKTEST_DIR, "backtest_patterns.py")
RENKO = os.path.join(BACKTEST_DIR, "renko_etl.py")            # 磚形圖狀態（同樣需 import app）
HOLDERS_RAW = r"H:\data\Holders"           # 集保週快照封存（往後自建歷史）


def load_state():
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


# ---------- 排程規則：回傳今天各工作的決策 ----------

# FinMind 逐檔抓的重工作：約 2,300 檔 × 資料集，撞每小時上限（run_nightly.bat 設
# FINMIND_MAX_PER_HOUR=550）就得睡到視窗釋放 → 平常上班日晚上跑不完，一律排週日。
#   capreduction / dividend 各 1 資料集 ≈ 2,300 次 ≈ 4~5hr（兩者同日跑則共用額度，會接著排隊）
# （季報原本 3 資料集 ≈ 6,900 次 ≈ 23hr，已改走 TWSE/櫃買 opendata，見 update_fundamentals_opendata.py）
JOBS = ["daily", "holderdist", "revenue", "quarterly", "dividend",
        "capreduction", "etfnav", "renko", "backtest"]   # --only / --skip 可指定的工作名

HEAVY_DAY = {"capreduction": 6, "dividend": 6}   # 6=週日（季報已改走 opendata，不再是重工作）
HEAVY_NAME = {"capreduction": "減資", "dividend": "股利"}
DAY_NAME = {5: "週六", 6: "週日"}


DIVIDEND_MONTHS = (5, 8)                         # 股利旺季：這幾個月每個週日都跑 dividend


def in_dividend_season(d):
    return DIVIDEND_MONTHS[0] <= d.month <= DIVIDEND_MONTHS[1]


def heavy_why(job):
    """重工作被擋掉時的說明（給 --plan 看）。"""
    return (f"{HEAVY_NAME[job]}為 FinMind 逐檔重工作，平日晚上跑不完 → "
            f"固定排{DAY_NAME[HEAVY_DAY[job]]}")


def on_heavy_day(d, job):
    return d.weekday() == HEAVY_DAY[job]


def due_quarterly(d):
    """今天是否在某季報的更新窗口 → 回傳 (季別標籤, fetch/load 起始日) 或 (None, None)。"""
    y = d.year
    if d.month == 4:                                  # 年報/Q4（前一年，3/31 公告）整個 4 月
        return f"{y - 1}Q4", f"{y - 2}-01-01"
    if d.month == 5 and d.day >= 16:                  # Q1（5/15 公告）
        return f"{y}Q1", f"{y - 1}-01-01"
    if d.month == 8 and d.day >= 15:                  # Q2（8/14 公告）
        return f"{y}Q2", f"{y - 1}-01-01"
    if d.month == 11 and d.day >= 15:                 # Q3（11/14 公告）
        return f"{y}Q3", f"{y - 1}-01-01"
    return None, None


def week_label(d):
    iso = d.isocalendar()
    return f"{iso[0]}W{iso[1]:02d}"


def plan_jobs(d, state, only, skip=()):
    """回傳 [(job, 理由, 是否執行, cmd_extra)]；cmd_extra 供組指令用。
    skip：本次要略過的工作（--skip）；用於「今晚不想跑某支重工作，但其他照跑」。"""
    jobs = []

    # daily：每晚都跑
    run = only in (None, "daily")
    jobs.append(("daily", "每晚固定（非交易日自動跳過）", run, {}))

    # holderdist：集保股權分散（TDCC 週資料；便宜且 idempotent，每晚檢查自動抓最新週）
    run = only in (None, "holderdist")
    jobs.append(("holderdist", "每晚檢查（TDCC 週更新，自動抓最新一週 + 存快照）", run, {}))

    # revenue：每月 11~20 號
    in_win = 11 <= d.day <= 20
    if only == "revenue":
        run, why = True, "強制 --only revenue"
    elif only is None:
        run = in_win
        why = "在每月 11~20 號窗口" if in_win else f"不在營收窗口（今天 {d.day} 號，需 11~20）"
    else:
        run, why = False, "本次 --only 指定其他工作"
    jobs.append(("revenue", why, run, {}))

    # quarterly：改走 TWSE/櫃買 opendata（全市場當期，約 10 個請求）→ 便宜到可以每晚跑。
    # 不再綁公告窗口/狀態檔：各家申報時間不一，每晚重跑才會把陸續公告的補進來（upsert 冪等）。
    q_label, q_start = due_quarterly(d)
    run = only in (None, "quarterly")
    jobs.append(("quarterly", "每晚檢查（opendata 當期財報，全市場約 10 個請求）", run,
                 {"label": q_label, "start": q_start}))

    # capreduction：減資（還原價會用到）；綁季報窗口跑，狀態檔防重（本季只跑一次）
    # 且避開股利旺季：5~8 月每個週日都被 dividend 佔滿，兩支各 ~4.5hr 的重工作若同晚跑會拖到 ~9hr。
    # → 減資只排「dividend 不跑」的兩個窗口：4 月（Q4）與 11 月（Q3），一年更新兩次。
    #   4 月/11 月都用 --refresh 從 q_start 全量重抓，所以中間發生的減資不會漏，只是延後入庫。
    #   急著要（例如某檔剛減資、要算還原價）：python nightly.py --only capreduction
    done_c = state.get("capreduction")
    if only == "capreduction":
        run, why = True, "強制 --only capreduction"
        c_label = q_label or f"{d.year}Q?"
        c_start = q_start or f"{d.year - 2}-01-01"
    elif only is None:
        c_label, c_start = q_label, q_start
        if q_label is None:
            run, why = False, "不在季報窗口（減資綁季報窗口跑）"
        elif in_dividend_season(d):
            run, why = False, (f"股利旺季（{DIVIDEND_MONTHS[0]}~{DIVIDEND_MONTHS[1]} 月）週日都被 dividend 佔用 → "
                               f"減資只排 4 月／11 月窗口（避免同晚兩支重工作）")
        elif done_c == q_label:
            run, why = False, f"{q_label} 本季已跑過減資（狀態檔）"
        elif not on_heavy_day(d, "capreduction"):
            run, why = False, f"{heavy_why('capreduction')}；{q_label} 窗口內，等最近的週日"
        else:
            run, why = True, f"季報窗口 {q_label}（週日），跑減資"
    else:
        run, why = False, "本次 --only 指定其他工作"
        c_label, c_start = q_label, q_start
    jobs.append(("capreduction", why, run, {"label": c_label, "start": c_start or f"{d.year - 2}-01-01"}))

    # dividend：5~8 月每週一次 + 狀態檔防重
    wl = week_label(d)
    done_w = state.get("dividend")
    if only == "dividend":
        run, why = True, "強制 --only dividend"
    elif only is None:
        if not in_dividend_season(d):
            run, why = False, f"不在股利旺季（{DIVIDEND_MONTHS[0]}~{DIVIDEND_MONTHS[1]} 月）"
        elif not on_heavy_day(d, "dividend"):
            run, why = False, heavy_why("dividend")
        elif done_w == wl:
            run, why = False, f"本週 {wl} 已跑過（狀態檔）"
        else:
            run, why = True, f"股利旺季週日，本週 {wl} 尚未跑"
    else:
        run, why = False, "本次 --only 指定其他工作"
    jobs.append(("dividend", why, run, {"label": wl, "start": f"{d.year}-01-01"}))

    # etfnav：ETF 每日淨值/折溢價/規模（mis.twse 單一請求，便宜；每晚固定，收盤後）
    run = only in (None, "etfnav")
    jobs.append(("etfnav", "每晚固定（ETF 淨值/規模，單一請求）", run, {}))

    # renko：磚形圖/三線反轉狀態（全市場 ~35 秒，冪等）。要在 refresh 之前跑完，
    # 選股視圖才 join 得到今天的狀態。非交易日重跑結果相同，成本低就不特別擋。
    run = only in (None, "renko")
    jobs.append(("renko", "每晚固定（磚形圖狀態 → renko_state，約 35 秒；須早於 refresh）", run, {}))

    # backtest：型態回測，每週一次（週日跑；重工作 ~數十分鐘，狀態檔防重）
    done_bt = state.get("backtest")
    if only == "backtest":
        run, why = True, "強制 --only backtest"
    elif only is None:
        if d.weekday() != 6:
            run, why = False, "型態回測每週日跑（重工作）"
        elif done_bt == wl:
            run, why = False, f"本週 {wl} 已跑過回測（狀態檔）"
        else:
            run, why = True, f"週日型態回測，本週 {wl} 尚未跑"
    else:
        run, why = False, "本次 --only 指定其他工作"
    jobs.append(("backtest", why, run, {"label": wl}))

    if skip:            # --skip 最後統一蓋掉，語意單純：不管排程怎麼判，指定的就是不跑
        jobs = [(j, "--skip 指定略過" if j in skip else why, run and j not in skip, e)
                for j, why, run, e in jobs]
    return jobs


# ---------- 執行 ----------

def run_cmd(cmd, cwd=HERE):
    print(f"    $ {' '.join(cmd)}")
    return subprocess.run(cmd, cwd=cwd).returncode


def refresh_snapshot(dsn):
    """資料更新後刷新選股物化視圖 mv_stock_snapshot（選股器同步最新）。回傳 (成功, 錯誤訊息)。"""
    try:
        import psycopg2
        conn = psycopg2.connect(dsn)
        conn.autocommit = True                          # REFRESH … CONCURRENTLY 不可在交易內
        cur = conn.cursor()
        try:
            cur.execute("REFRESH MATERIALIZED VIEW CONCURRENTLY mv_stock_snapshot")
        except psycopg2.Error:
            cur.execute("REFRESH MATERIALIZED VIEW mv_stock_snapshot")   # 退回非並行
        cur.close(); conn.close()
        return True, None
    except Exception as e:
        return False, str(e)[:150]


def build_cmd(job, d, dsn, extra):
    di = d.isoformat()
    if job == "daily":
        return [sys.executable, DAILY, "--date", di, "--dsn", dsn]
    if job == "revenue":
        return [sys.executable, REVENUE, "--dsn", dsn]
    if job == "holderdist":
        return [sys.executable, HOLDERDIST, "--dsn", dsn, "--raw-root", HOLDERS_RAW]
    if job == "quarterly":
        return [sys.executable, FUND_OPEN, "--dsn", dsn]        # opendata：全市場當期，約 10 個請求
    if job == "dividend":
        return [sys.executable, FUND, "--preset", "dividend", "--start", extra["start"], "--dsn", dsn]
    if job == "capreduction":
        return [sys.executable, FUND, "--preset", "capreduction", "--start", extra["start"], "--dsn", dsn]
    if job == "etfnav":
        return [sys.executable, ETFNAV, "--dsn", dsn]
    if job == "renko":
        return [sys.executable, RENKO]             # 同 backtest：cwd=BACKTEST_DIR 才 import 得到 app
    if job == "backtest":
        return [sys.executable, BACKTEST]          # 讀 backend/.env 的 DATABASE_URL；cwd=BACKTEST_DIR
    raise ValueError(job)


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
    ap = argparse.ArgumentParser(description="排程大腦：依今天日期自動決定該跑哪些更新")
    ap.add_argument("--dsn", default=os.environ.get("DATABASE_URL", ""), help="PostgreSQL 連線字串")
    ap.add_argument("--date", default=date.today().isoformat(), help="模擬日期 YYYY-MM-DD（預設今天）")
    ap.add_argument("--only", choices=JOBS, help="強制只跑某工作")
    ap.add_argument("--skip", default="", help="本次略過某些工作（逗號分隔，如 --skip capreduction）；"
                                               "其餘照排程跑。適合「今晚不想跑某支重工作」")
    ap.add_argument("--skip-refresh", action="store_true", help="跑完不刷新 mv_stock_snapshot 選股視圖")
    ap.add_argument("--plan", action="store_true", help="只印排程決策，不執行")
    args = ap.parse_args()
    args.dsn = clean_dsn(args.dsn)

    try:
        sys.stdout.reconfigure(line_buffering=True)
    except (AttributeError, ValueError):
        pass

    try:
        d = date.fromisoformat(args.date)
    except ValueError:
        raise SystemExit("--date 需為 YYYY-MM-DD 格式")

    skip = {s.strip() for s in args.skip.split(",") if s.strip()}
    if skip - set(JOBS):
        raise SystemExit(f"--skip 有無效工作名：{sorted(skip - set(JOBS))}；可選：{JOBS}")

    state = load_state()
    jobs = plan_jobs(d, state, args.only, skip)

    print(f"=== nightly {args.date}（{['一','二','三','四','五','六','日'][d.weekday()]}）"
          f" @ {datetime.now():%H:%M:%S} ===")
    print("排程決策：")
    for job, why, run, _ in jobs:
        print(f"  [{'✓ 跑' if run else '– 略'}] {job:10} {why}")
    if not args.skip_refresh:
        print(f"  [✓ 跑] {'refresh':10} 執行後刷新 mv_stock_snapshot（選股器同步最新）")

    if args.plan:
        print("\n(--plan：僅顯示決策，未執行)")
        return

    to_run = [(j, e) for j, why, run, e in jobs if run]
    if not to_run:
        print("\n今天沒有要執行的工作。")
        return

    if not args.dsn:
        raise SystemExit("需要 --dsn 或環境變數 DATABASE_URL")

    print("\n開始執行：")
    results = []
    for job, extra in to_run:
        print(f"\n----- {job} -----")
        try:
            cwd = BACKTEST_DIR if job in ("backtest", "renko") else HERE
            rc = run_cmd(build_cmd(job, d, args.dsn, extra), cwd=cwd)
        except Exception as e:
            rc, msg = 1, str(e)[:120]
            print(f"    例外：{msg}")
        ok = rc == 0
        results.append((job, ok))
        if ok and job in ("quarterly", "dividend", "capreduction", "backtest"):   # 重工作成功才記狀態，避免跨夜重跑
            state[job] = extra["label"]
            state.setdefault("last_run", {})[job] = f"{args.date} {datetime.now():%H:%M:%S}"
            save_state(state)
        print(f"    → {'成功' if ok else f'失敗 (rc={rc})'}")

    # 刷新選股視圖（讓選股器同步今天更新的資料）
    if not args.skip_refresh:
        print("\n----- refresh mv_stock_snapshot -----")
        ok, err = refresh_snapshot(args.dsn)
        results.append(("refresh", ok))
        print("    → " + ("已刷新 ✓" if ok else f"刷新失敗（{err}）；若尚未建視圖請先跑 stockselect/sql/mv_stock_snapshot.sql"))

    print("\n=== nightly 完成 ===")
    for job, ok in results:
        print(f"  {job:10} {'✓' if ok else '✗ 失敗'}")
    if any(not ok for _, ok in results):
        sys.exit(1)                                      # 有失敗 → 非 0 離開，方便排程器告警


if __name__ == "__main__":
    main()
