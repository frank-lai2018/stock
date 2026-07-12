r"""nightly.py — 排程大腦：每晚無腦執行這一支，由它依「今天日期」自動判斷該跑哪些更新。

你只要每晚跑：
  python nightly.py --dsn "postgresql://frank:pwd@localhost:5432/twstock"
（或設環境變數 DATABASE_URL / FINMIND_TOKEN 後直接 python nightly.py）

它管理的工作與排程規則：
  daily      每晚都跑 daily_update.py（股價+法人+融資+PER）；非交易日腳本自己會跳過。
  holderdist 每晚檢查 update_holderdist.py（集保股權分散；TDCC 週資料，idempotent 自動抓最新週 + 存快照）。
  revenue    每月 11~20 號跑 update_revenue.py（月營收；cheap，順便補晚申報者）。
  quarterly  季報公告後跑 update_fundamentals.py --preset quarterly（重工作）：
               4 月=年報/Q4、5/16 起=Q1、8/15 起=Q2、11/15 起=Q3。
  dividend   股利旺季 5~8 月每週跑一次 update_fundamentals.py --preset dividend（重工作）。

防重複：quarterly / dividend 是 FinMind 逐檔的重工作，用狀態檔 nightly_state.json 記錄
        「本季/本週已完成」，跨夜自動不重跑（daily / revenue 便宜則照排程窗口每晚跑）。

其他用法：
  python nightly.py --plan                      # 只印「今天會跑哪些、為什麼」，不執行
  python nightly.py --only quarterly --dsn ...  # 強制只跑某工作（忽略排程/狀態檔）
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
HOLDERDIST = os.path.join(HERE, "update_holderdist.py")
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


def plan_jobs(d, state, only):
    """回傳 [(job, 理由, 是否執行, cmd_extra)]；cmd_extra 供組指令用。"""
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

    # quarterly：季報窗口 + 狀態檔防重
    q_label, q_start = due_quarterly(d)
    done_q = state.get("quarterly")
    if only == "quarterly":
        run, why = True, "強制 --only quarterly"
        q_label = q_label or f"{d.year}Q?"
        q_start = q_start or f"{d.year - 1}-01-01"
    elif only is None:
        if q_label is None:
            run, why = False, "不在季報公告窗口"
        elif done_q == q_label:
            run, why = False, f"{q_label} 本季已跑過（狀態檔）"
        else:
            run, why = True, f"季報窗口 {q_label}，尚未跑"
    else:
        run, why = False, "本次 --only 指定其他工作"
    jobs.append(("quarterly", why, run, {"label": q_label, "start": q_start}))

    # dividend：5~8 月每週一次 + 狀態檔防重
    wl = week_label(d)
    done_w = state.get("dividend")
    if only == "dividend":
        run, why = True, "強制 --only dividend"
    elif only is None:
        if not (5 <= d.month <= 8):
            run, why = False, "不在股利旺季（5~8 月）"
        elif done_w == wl:
            run, why = False, f"本週 {wl} 已跑過（狀態檔）"
        else:
            run, why = True, f"股利旺季，本週 {wl} 尚未跑"
    else:
        run, why = False, "本次 --only 指定其他工作"
    jobs.append(("dividend", why, run, {"label": wl, "start": f"{d.year}-01-01"}))

    return jobs


# ---------- 執行 ----------

def run_cmd(cmd):
    print(f"    $ {' '.join(cmd)}")
    return subprocess.run(cmd, cwd=HERE).returncode


def build_cmd(job, d, dsn, extra):
    di = d.isoformat()
    if job == "daily":
        return [sys.executable, DAILY, "--date", di, "--dsn", dsn]
    if job == "revenue":
        return [sys.executable, REVENUE, "--dsn", dsn]
    if job == "holderdist":
        return [sys.executable, HOLDERDIST, "--dsn", dsn, "--raw-root", HOLDERS_RAW]
    if job == "quarterly":
        return [sys.executable, FUND, "--preset", "quarterly", "--start", extra["start"], "--dsn", dsn]
    if job == "dividend":
        return [sys.executable, FUND, "--preset", "dividend", "--start", extra["start"], "--dsn", dsn]
    raise ValueError(job)


def main():
    ap = argparse.ArgumentParser(description="排程大腦：依今天日期自動決定該跑哪些更新")
    ap.add_argument("--dsn", default=os.environ.get("DATABASE_URL", ""), help="PostgreSQL 連線字串")
    ap.add_argument("--date", default=date.today().isoformat(), help="模擬日期 YYYY-MM-DD（預設今天）")
    ap.add_argument("--only", choices=["daily", "holderdist", "revenue", "quarterly", "dividend"], help="強制只跑某工作")
    ap.add_argument("--plan", action="store_true", help="只印排程決策，不執行")
    args = ap.parse_args()

    try:
        sys.stdout.reconfigure(line_buffering=True)
    except (AttributeError, ValueError):
        pass

    try:
        d = date.fromisoformat(args.date)
    except ValueError:
        raise SystemExit("--date 需為 YYYY-MM-DD 格式")

    state = load_state()
    jobs = plan_jobs(d, state, args.only)

    print(f"=== nightly {args.date}（{['一','二','三','四','五','六','日'][d.weekday()]}）"
          f" @ {datetime.now():%H:%M:%S} ===")
    print("排程決策：")
    for job, why, run, _ in jobs:
        print(f"  [{'✓ 跑' if run else '– 略'}] {job:10} {why}")

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
            rc = run_cmd(build_cmd(job, d, args.dsn, extra))
        except Exception as e:
            rc, msg = 1, str(e)[:120]
            print(f"    例外：{msg}")
        ok = rc == 0
        results.append((job, ok))
        if ok and job in ("quarterly", "dividend"):     # 重工作成功才記狀態，避免跨夜重跑
            state[job] = extra["label"]
            state.setdefault("last_run", {})[job] = f"{args.date} {datetime.now():%H:%M:%S}"
            save_state(state)
        print(f"    → {'成功' if ok else f'失敗 (rc={rc})'}")

    print("\n=== nightly 完成 ===")
    for job, ok in results:
        print(f"  {job:10} {'✓' if ok else '✗ 失敗'}")
    if any(not ok for _, ok in results):
        sys.exit(1)                                      # 有失敗 → 非 0 離開，方便排程器告警


if __name__ == "__main__":
    main()
