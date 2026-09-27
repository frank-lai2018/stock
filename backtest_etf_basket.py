r"""backtest_etf_basket.py — 持股籃策略回測：持有「被 N 家投信的主動 ETF 同時持有」的股票、定期換股，
跟直接買 00981A、0050 比。說明見 主動ETF追蹤設計.md。

為什麼做：backtest_etf_flow.py 的結果是「跟著每日進出買」幾乎沒有超額，但「持股籃本身」每 20 日贏大盤約 2.7%
（8 家投信；只有 3 家時約 3.8%）。
這裡檢查這個效應能不能變成可執行的策略：扣掉成本與換手之後，還贏不贏直接買主動 ETF？

策略（換股日用前一交易日收盤後公布的持股選股，換股日開盤成交，只交易差額）：
  basket1_m   ≥1 家投信持有（整個持股籃）、等權、每月換股
  basket2_m   ≥2 家投信持有、等權、每月換股
  basket3_m   ≥3 家投信持有、等權、每月換股（主策略：8 家投信時約 33 檔，跟只有 3 家時的 ≥2 家差不多大）
  basket4_m   ≥4 家投信持有、等權、每月換股（00991A 上市後才有 4 家，2025-12 起）
  basket3_w   ≥3 家投信持有、等權、每週換股（看換手成本）
  basket3_vw  ≥3 家投信持有、依主動 ETF 合計持股市值加權、每月換股
  2026-09-28 從 3 家投信擴到 8 家後，≥2 家的門檻變寬（30 → 56 檔），主策略改成 ≥3 家。
基準（買進持有）：00981A 主動統一台股增長、0050 元大台灣50
成本：股票買進 0.1425%、賣出 0.1425%＋證交稅 0.3%；ETF 賣出證交稅 0.1%。券商手續費有折扣的話成本更低。
價格：還原價，並修正還原價斷點（見 backtest_etf_flow.sanitize；0050 2025-06-18 一拆四就是這種斷點）。

用法：
  python backtest_etf_basket.py                 # 計算 → 寫入 etf_basket_summary／etf_basket_curve → 印報表
  python backtest_etf_basket.py --report-only
  （backtest_etf_flow.py 跑完也會接著跑這支，nightly 的 etfbacktest 每週一次）
"""
import argparse
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

import backtest_etf_flow as bt

BUY_COST = 0.001425
SELL_COST_STOCK = 0.001425 + 0.003
SELL_COST_ETF = 0.001425 + 0.001
DAYS_PER_YEAR = 245
BENCHMARKS = {"00981A": "00981A 主動統一台股增長（買進持有）", "0050": "0050 元大台灣50（買進持有）"}
STRATEGIES = {   # key: (名稱, 最少投信家數, 換股頻率, 加權)；前端以 basket3_m 為主策略
    "basket1_m": ("持股籃 ≥1 家・月換", 1, "M", "eq"),
    "basket2_m": ("持股籃 ≥2 家・月換", 2, "M", "eq"),
    "basket3_m": ("持股籃 ≥3 家・月換", 3, "M", "eq"),
    "basket4_m": ("持股籃 ≥4 家・月換", 4, "M", "eq"),
    "basket3_w": ("持股籃 ≥3 家・週換", 3, "W", "eq"),
    "basket3_vw": ("持股籃 ≥3 家・市值加權・月換", 3, "M", "vw"),
}


# ---------- 載入 ----------

def load_holdings(cur):
    """回傳 {etf_id: (issuer, {as_of: {code: 持股市值}})}；市值＝權重 × 基金淨資產（非佔位股）。"""
    cur.execute("SELECT h.etf_id, e.issuer, h.as_of, h.code, h.weight * s.nav_total / 100 "
                "FROM etf_holding h JOIN etf_fund e USING (etf_id) "
                "JOIN etf_snapshot s ON s.etf_id = h.etf_id AND s.as_of = h.as_of "
                "WHERE h.kind = 'stock' AND (h.weight >= 0.05 OR (h.weight >= 0.01 AND h.shares > 1000))")
    out = {}
    for eid, issuer, d, code, val in cur.fetchall():
        out.setdefault(eid, (issuer, defaultdict(dict)))[1][d][code] = float(val or 0)
    return out


def load_etf_prices(cur, ids):
    cur.execute("SELECT stock_id, trade_date, adj_open, adj_close FROM price_daily "
                "WHERE stock_id = ANY(%s) AND trade_date >= %s", (list(ids), bt.PRICE_START))
    px = pd.DataFrame(cur.fetchall(), columns=["sid", "d", "o", "c"])
    O = px.pivot(index="d", columns="sid", values="o").astype(float)
    C = px.pivot(index="d", columns="sid", values="c").astype(float)
    O, C, _ = bt.sanitize(O, C)
    return O, C


# ---------- 選股與模擬 ----------

def select(holdings, t, min_issuers, weighting):
    """持股日 t（含）以前 7 天內、各 ETF 最新一份持股 → 被 ≥min_issuers 家投信持有的股票與權重。"""
    issuers, value = defaultdict(set), defaultdict(float)
    for eid, (issuer, by_date) in holdings.items():
        ds = [d for d in by_date if t - timedelta(days=7) <= d <= t]
        if not ds:
            continue
        for code, v in by_date[max(ds)].items():
            issuers[code].add(issuer)
            value[code] += v
    pick = [c for c, s in issuers.items() if len(s) >= min_issuers]
    if weighting == "vw":
        tot = sum(value[c] for c in pick)
        return {c: value[c] / tot for c in pick} if tot else {}
    return {c: 1 / len(pick) for c in pick} if pick else {}


def schedule_days(dates, freq, start):
    """換股日：每月第一個交易日（M）或每 5 個交易日（W），從 start 起。"""
    ds = [d for d in dates if d >= start]
    if freq == "W":
        return ds[::5]
    out, seen = [], set()
    for d in ds:
        if (d.year, d.month) not in seen:
            seen.add((d.year, d.month))
            out.append(d)
    return out


def simulate(O, C, dates, plan):
    """plan：[(換股日, {股票: 權重})]。換股日開盤按目標權重成交（只交易差額、扣成本），之後逐日以收盤價計值。
    回傳 (淨值 Series, 每次換股的賣出比例, 每次成本占淨值比例, 每次檔數)。"""
    Of, Cf = O.ffill(), C.ffill()
    pos = {d: i for i, d in enumerate(dates)}
    shares, nav, turn, costs, ns = {}, {}, [], [], []
    value = 1.0
    for k, (rd, tgt) in enumerate(plan):
        tgt = {c: w for c, w in tgt.items() if c in Of.columns and not np.isnan(O.at[rd, c])}   # 當天有開盤價才買得到
        s = sum(tgt.values())
        tgt = {c: w / s for c, w in tgt.items()} if s else {}
        cur = {c: sh * Of.at[rd, c] for c, sh in shares.items()}
        if cur:
            value = sum(cur.values())
        target = {c: w * value for c, w in tgt.items()}
        keys = set(cur) | set(target)
        buys = sum(max(0.0, target.get(c, 0) - cur.get(c, 0)) for c in keys)
        sells = sum(max(0.0, cur.get(c, 0) - target.get(c, 0)) for c in keys)
        cost = buys * BUY_COST + sells * SELL_COST_STOCK
        if cur:
            turn.append(sells / value)
        costs.append(cost / value)
        ns.append(len(tgt))
        value -= cost
        shares = {c: w * value / Of.at[rd, c] for c, w in tgt.items()}
        end = pos[plan[k + 1][0]] if k + 1 < len(plan) else len(dates)
        for i in range(pos[rd], end):
            nav[dates[i]] = sum(sh * Cf.iat[i, Cf.columns.get_loc(c)] for c, sh in shares.items()) if shares else value
    return pd.Series(nav), turn, costs, ns


def buy_hold(O, C, sid, start):
    """ETF 買進持有：start 開盤買進（扣買進手續費），逐日收盤計值。"""
    c = C[sid].loc[start:].ffill()
    return c / O.at[start, sid] * (1 - BUY_COST)


def metrics(nav, sell_cost, bench_nav=None):
    """總報酬（含最後賣出成本）、年化、波動、最大回檔、報酬/波動；有基準時算月報酬贏基準的月數。"""
    nav = nav.dropna()
    total = nav.iloc[-1] * (1 - sell_cost) - 1                   # 起始本金 1，最後全部賣出扣成本
    n = len(nav)
    cagr = (1 + total) ** (DAYS_PER_YEAR / max(n - 1, 1)) - 1
    daily = nav.pct_change().dropna()
    vol = daily.std() * np.sqrt(DAYS_PER_YEAR)
    mdd = (nav / nav.cummax() - 1).min()
    out = {"date_from": nav.index[0], "date_to": nav.index[-1], "days": n, "total_ret": total, "cagr": cagr,
           "vol": vol, "mdd": mdd, "ret_vol": cagr / vol if vol else None}
    if bench_nav is not None:
        idx = pd.to_datetime(nav.index)
        m = pd.Series(nav.values, index=idx).resample("ME").last()
        b = pd.Series(bench_nav.reindex(nav.index).values, index=idx).resample("ME").last()
        mr, br = m.pct_change(), b.pct_change()
        mr.iloc[0] = m.iloc[0] / nav.iloc[0] - 1                      # 第一個月從起始日算
        br.iloc[0] = b.iloc[0] / bench_nav.reindex(nav.index).iloc[0] - 1
        both = pd.DataFrame({"m": mr, "b": br}).dropna()
        diff = both.m - both.b
        out["months"] = len(both)
        out["months_beat"] = int((diff > 0).sum())
        out["excess_sum"] = float(diff.sum())                      # 每月超額（百分點）加總
        out["excess_ex_top2"] = float(diff.sum() - diff.nlargest(2).sum()) if len(diff) > 2 else None   # 拿掉最好兩個月
    return out


def compute(cur, O=None, C=None):
    """回傳 (summary 列表, {strategy: 淨值 Series})。O、C 可由 backtest_etf_flow 傳入（已修正斷點的股票價）。"""
    if O is None or C is None:
        O, C, _ = bt.load_prices(cur)
    holdings = load_holdings(cur)
    EO, EC = load_etf_prices(cur, BENCHMARKS)
    dates = [d for d in C.index if d in EC.index]
    first = {}                                                     # 每個投信家數門檻最早可用的持股日
    for n in sorted({v[1] for v in STRATEGIES.values()} | {2}):
        for d in sorted({d for _, (_, by) in holdings.items() for d in by}):
            if len(select(holdings, d, n, "eq")) > 0:
                first[n] = d
                break
    curves, summary = {}, []
    # 起點要在基準 00981A 上市之後（野村 00980A 比它早上市，≥2 家從 2025-05-22 就成立，那時還沒有基準可比）
    bench0 = EO["00981A"].first_valid_index() if "00981A" in EO.columns else None
    base_start = min(d for d in dates if d > max(first.get(2, dates[0]), bench0 or dates[0]))
    for key, (name, n_iss, freq, wt) in STRATEGIES.items():
        if n_iss not in first:
            continue
        start = min(d for d in dates if d > first[n_iss])
        start = max(start, base_start)
        plan = []
        for rd in schedule_days(dates, freq, start):
            t = dates[dates.index(rd) - 1]                         # 換股日前一交易日的持股（收盤後公布）
            tgt = select(holdings, t, n_iss, wt)
            if tgt:
                plan.append((rd, tgt))
        if not plan:
            continue
        nav, turn, costs, ns = simulate(O, C, dates, plan)
        bench = buy_hold(EO, EC, "00981A", plan[0][0])
        m = metrics(nav, SELL_COST_STOCK, bench)
        m.update({"strategy": key, "name": name, "kind": "strategy", "avg_n": float(np.mean(ns)),
                  "rebalances": len(plan), "avg_turnover": float(np.mean(turn)) if turn else None,
                  "cost_total": float(sum(costs) + SELL_COST_STOCK)})
        b = metrics(bench, SELL_COST_ETF)
        m["bench_cagr"] = b["cagr"]                                 # 同一段期間 00981A 的年化，方便直接比
        summary.append(m)
        curves[key] = nav
    for sid, name in BENCHMARKS.items():
        if sid in EC.columns:
            nav = buy_hold(EO, EC, sid, base_start)
            m = metrics(nav, SELL_COST_ETF, buy_hold(EO, EC, "00981A", base_start))
            m.update({"strategy": sid, "name": name, "kind": "benchmark", "avg_n": None, "rebalances": 1,
                      "avg_turnover": None, "cost_total": BUY_COST + SELL_COST_ETF, "bench_cagr": None})
            summary.append(m)
            curves[sid] = nav
    return summary, curves


# ---------- 報表與寫入 ----------

def report(summary):
    pct = bt.pct
    print("\n=== 持股籃策略 vs 直接買 ETF（還原價、已扣交易成本）===")
    print(f"{'策略':<22}{'期間':>24}{'總報酬':>9}{'年化':>9}{'同期00981A':>11}{'波動':>8}{'最大回檔':>9}"
          f"{'報酬/波動':>9}{'檔數':>6}{'換手/次':>8}{'成本合計':>9}{'月贏00981A':>11}")
    for m in summary:
        beat = f"{m['months_beat']}/{m['months']}" if m.get("months") else "—"
        avg_n = f"{m['avg_n']:.0f}" if m["avg_n"] else "—"
        turn = pct(m["avg_turnover"], 0) if m["avg_turnover"] is not None else "—"
        bench = pct(m["bench_cagr"], 1) if m.get("bench_cagr") is not None else "—"
        print(f"{m['name']:<20}{str(m['date_from']) + '~' + str(m['date_to']):>26}{pct(m['total_ret'], 1):>9}"
              f"{pct(m['cagr'], 1):>9}{bench:>11}{pct(m['vol'], 1):>8}{pct(m['mdd'], 1):>9}"
              f"{(m['ret_vol'] or 0):>9.2f}{avg_n:>6}{turn:>8}{pct(m['cost_total'], 1):>9}{beat:>11}")
    print("（月贏00981A：每月報酬高於同期 00981A 的月數；換手/次＝每次換股賣掉的比例）")
    for m in summary:
        if m.get("excess_sum") is not None and m["kind"] == "strategy":
            print(f"  {m['name']}：每月超額加總 {m['excess_sum'] * 100:+.1f} 個百分點，拿掉最好兩個月 "
                  f"{(m['excess_ex_top2'] or 0) * 100:+.1f}")


SUM_COLS = ("strategy", "name", "kind", "date_from", "date_to", "days", "total_ret", "cagr", "vol", "mdd",
            "ret_vol", "avg_n", "rebalances", "avg_turnover", "cost_total", "bench_cagr", "months", "months_beat",
            "excess_sum", "excess_ex_top2", "computed_at")


def save(cur, summary, curves):
    from psycopg2.extras import execute_values
    cur.execute(open(bt.SCHEMA, encoding="utf-8").read())
    stamp = datetime.now()
    cur.execute("DELETE FROM etf_basket_summary")
    execute_values(cur, f"INSERT INTO etf_basket_summary ({', '.join(SUM_COLS)}) VALUES %s",
                   [tuple(bt._clean({**m, "computed_at": stamp}.get(c)) for c in SUM_COLS) for m in summary])
    cur.execute("DELETE FROM etf_basket_curve")
    execute_values(cur, "INSERT INTO etf_basket_curve (strategy, trade_date, nav) VALUES %s",
                   [(k, d, float(v)) for k, s in curves.items() for d, v in s.dropna().items()], page_size=5000)
    print(f"寫入 etf_basket_summary {len(summary)} 列、etf_basket_curve {sum(len(s) for s in curves.values())} 點")


def run(cur, O=None, C=None, write=True):
    summary, curves = compute(cur, O, C)
    report(summary)
    if write:
        save(cur, summary, curves)


def main():
    ap = argparse.ArgumentParser(description="持股籃策略回測 → etf_basket_summary／etf_basket_curve")
    ap.add_argument("--dsn", default=os.environ.get("DATABASE_URL", ""), help="PostgreSQL 連線字串")
    ap.add_argument("--report-only", action="store_true", help="只印報表，不寫 DB")
    args = ap.parse_args()
    args.dsn = bt.clean_dsn(args.dsn)
    try:
        sys.stdout.reconfigure(line_buffering=True, errors="replace")
    except (AttributeError, ValueError):
        pass
    if not args.dsn:
        raise SystemExit("需要 --dsn 或環境變數 DATABASE_URL")
    import psycopg2
    conn = psycopg2.connect(args.dsn)
    try:
        with conn, conn.cursor() as cur:
            run(cur, write=not args.report_only)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
