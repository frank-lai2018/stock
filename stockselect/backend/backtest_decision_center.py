r"""決策中心 v3 歷史研究：原始規則＋動能 20 日／趨勢持有 × 各篩選條件，與連續資金組合。

每個訊號日只用當日可見資料重算 RS、趨勢模板、財報及成交條件。
突破評分不使用目前型態回測表的績效先驗，分數校準為空。
全部模式採同一批完整 60 日標籤，隔日開盤、日 K 可交易近似、每邊 0.1% 滑價及 0.6% 成本。
事件研究允許訊號重疊；連續組合另限制現金、跨日持股、產業與總停損風險。
每筆等權母體超額只是診斷（母體採出場日收盤，盤中出場時間未匹配）。
組合另對照含 ETF 成本的 0050；報酬均為還原價股利再投資口徑。

限制：目前主檔的存活者偏差、近似公告日、未保留歷史財報修訂、日 K 委託佇列近似。
此歷史已參與設計，分段結果不能當作真正樣本外證據。

在 backend 目錄執行：
  python backtest_decision_center.py --workers 4
  python backtest_decision_center.py --start 2025-01-01 --lot-size 1000
  python backtest_decision_center.py --out research/decision_picks_v3.csv
"""
import argparse
import multiprocessing as mp
import sys
import time
from bisect import bisect_right
from datetime import date, timedelta

HORIZON = 20
COST = 0.006
MIN_AMT = 20_000_000
_G = {}


def _f(x):
    return None if x is None else float(x)


def _nn(x):
    """NaN → None：評分函式的 _linear 遇到 NaN 會給滿分（min(5, nan)=5），系統遇到 NULL 是 0 分。"""
    try:
        return None if x is None or x != x else float(x)
    except (TypeError, ValueError):
        return None


# ───────────────────────── 主程序：載入資料、算逐日橫斷面特徵 ─────────────────────────
def load_all(sig_start):
    import numpy as np
    import pandas as pd
    from app import db, decision_center

    t0 = time.time()
    load_start = sig_start - timedelta(days=560)                 # 12 個月動能＋200 日均線的暖機
    meta = {r["stock_id"]: r for r in db.query(
        "SELECT stock_id, name, industry FROM stock WHERE security_type='stock'")}
    px = pd.DataFrame(db.query(
        "SELECT p.stock_id, p.trade_date, p.adj_open, p.adj_high, p.adj_low, p.adj_close, p.volume, p.amount,"
        "p.open AS raw_open,p.high AS raw_high,p.low AS raw_low,p.close AS raw_close "
        "FROM price_daily p JOIN stock s USING (stock_id) WHERE s.security_type='stock' AND p.trade_date >= %(d)s "
        "ORDER BY p.stock_id, p.trade_date", {"d": load_start}))
    for c in ("adj_open", "adj_high", "adj_low", "adj_close", "volume", "amount", "raw_open", "raw_high", "raw_low", "raw_close"):
        px[c] = px[c].astype(float)
    print(f"價格 {len(px):,} 列（{time.time()-t0:.0f}s）", flush=True)

    A = px.pivot(index="trade_date", columns="stock_id", values="adj_close").sort_index()
    O = px.pivot(index="trade_date", columns="stock_id", values="adj_open").reindex_like(A)
    AMT = px.pivot(index="trade_date", columns="stock_id", values="amount").reindex_like(A)
    dates = list(A.index)
    c1m, c3m, c6m, c12m = A.shift(20), A.shift(62), A.shift(125), A.shift(251)
    ma50 = A.rolling(50, min_periods=1).mean()
    ma150 = A.rolling(150, min_periods=1).mean()
    ma200 = A.rolling(200, min_periods=1).mean()
    ma200_1m = A.shift(21).rolling(200, min_periods=1).mean()
    hi52 = A.rolling(252, min_periods=1).max()
    lo52 = A.rolling(252, min_periods=1).min()
    amt20 = AMT.rolling(20, min_periods=1).mean()
    tdays = A.notna().rolling(275, min_periods=1).sum()             # ≈ mv 的「近 400 天有幾根」
    in_univ = (tdays >= 60) & (amt20 >= 5_000_000) & A.notna()
    tight = (A.rolling(15, min_periods=1).max() - A.rolling(15, min_periods=1).min()) / A
    rs_raw = 2 * A / c3m + A / c6m + A / c12m
    present = (tdays > 0) & A.notna()
    # percent_rank() OVER (ORDER BY rs_raw NULLS FIRST)：NULL 並列最前
    R = rs_raw.where(present).rank(axis=1, method="min")
    k = (present & rs_raw.isna()).sum(axis=1)
    n = present.sum(axis=1)
    pr = R.add(k, axis=0).sub(1).div((n - 1).clip(lower=1), axis=0)
    pr = pr.where(rs_raw.notna() & present, 0.0).where(present)
    rs_rating = np.floor(pr * 100 + 0.5)
    tt = ((A > ma150) & (A > ma200) & (ma150 > ma200) & (ma200 > ma200_1m) & (ma50 > ma150)
          & (ma50 > ma200) & (A > ma50) & (A >= lo52 * 1.3) & (A >= hi52 * 0.75) & (rs_rating >= 70))
    fwd20 = A.shift(-HORIZON) / A - 1

    inst = pd.DataFrame(db.query(
        "SELECT stock_id, trade_date, COALESCE(foreign_net,0)+COALESCE(foreign_dealer_net,0)+COALESCE(trust_net,0)"
        "+COALESCE(dealer_self_net,0)+COALESCE(dealer_hedge_net,0) AS net FROM inst_trades WHERE trade_date >= %(d)s",
        {"d": sig_start - timedelta(days=60)}))
    inst["net"] = inst["net"].astype(float)
    inst20 = (inst.pivot(index="trade_date", columns="stock_id", values="net")
              .reindex(index=A.index, columns=A.columns).fillna(0.0).rolling(20, min_periods=1).sum())

    last_ok = len(dates) - 1 - 60                                  # 各模式同一批完整 60 日觀察窗口
    sig_idx = [i for i, d in enumerate(dates) if d >= sig_start and i <= last_ok]
    scan = in_univ & (amt20 >= MIN_AMT)
    bench = fwd20.where(scan).mean(axis=1)

    idx = pd.DataFrame(db.query(
        "SELECT trade_date, close FROM market_index WHERE index_id=%(id)s AND trade_date >= %(d)s ORDER BY trade_date",
        {"id": decision_center.MARKET_INDEX, "d": load_start}))
    idx["close"] = idx["close"].astype(float)
    idx = idx.set_index("trade_date")["close"]
    ma = idx.rolling(decision_center.MARKET_MA_DAYS).mean()

    fq, rev, per = {}, {}, {}
    for r in db.query("SELECT stock_id, period_date, available_date, eps, gross_margin FROM fundamentals_quarterly "
                      "WHERE period_date >= %(d)s ORDER BY stock_id, period_date", {"d": load_start - timedelta(days=400)}):
        fq.setdefault(r["stock_id"], []).append((r["available_date"], r["period_date"], _f(r["eps"]), _f(r["gross_margin"])))
    for r in db.query("SELECT stock_id, revenue_month, available_date, yoy_pct FROM monthly_revenue "
                      "WHERE revenue_month >= %(d)s ORDER BY stock_id, revenue_month", {"d": load_start}):
        m = r["revenue_month"]
        avail = r["available_date"] or date(m.year + (m.month == 12), m.month % 12 + 1, 10)
        rev.setdefault(r["stock_id"], []).append((avail, m, _f(r["yoy_pct"])))
    v = pd.DataFrame(db.query("SELECT stock_id, trade_date, per FROM valuation_daily WHERE trade_date >= %(d)s "
                              "ORDER BY stock_id, trade_date", {"d": sig_start - timedelta(days=1110)}))
    if len(v):
        v["per"] = v["per"].astype(float)
        for sid, g in v.groupby("stock_id"):
            per[sid] = ([d.toordinal() for d in g["trade_date"]], g["per"].to_numpy())
    if not sig_idx:
        raise SystemExit("沒有完整 60 日可結算的訊號窗口")
    print(f"特徵與財報載入完成（{time.time()-t0:.0f}s）；訊號日 {len(sig_idx)} 天 "
          f"{dates[sig_idx[0]]} ~ {dates[sig_idx[-1]]}", flush=True)

    feats = {"rs_rating": rs_rating, "trend_template": tt, "ret_12_1": c1m / c12m - 1,
             "tight_recent": tight, "amt20": amt20, "inst20": inst20, "scan": scan}
    market = {}
    for d in (dates[i] for i in sig_idx):
        c = idx.get(d)
        m = ma.get(d)
        ok = c is not None and m is not None and m == m
        market[d] = {"index": decision_center.MARKET_INDEX, "date": d.isoformat(), "ma_days": decision_center.MARKET_MA_DAYS,
                     "close": float(c) if c is not None else None, "ma": float(m) if ok else None,
                     "above": bool(c > m) if ok else None}
    forward60 = A.shift(-60) / O.shift(-1) - 1
    labels = forward60.where(scan).rank(axis=1, pct=True)
    return dict(meta=meta, px=px, dates=dates, sig_idx=sig_idx, feats=feats, bench=bench, market=market,
                close_panel=A, open_panel=O, winner_rank=labels, forward60=forward60,
                fq=fq, rev=rev, per=per, backtests=decision_center._pattern_backtests())


# ───────────────────────── 工作程序：逐檔逐日跑系統的策略函式 ─────────────────────────
def fund_at(rows, t):
    """同 mv 的 fq CTE，但只看 available_date ≤ t 的季報。"""
    vis = sorted((r for r in rows if r[0] is not None and r[0] <= t), key=lambda r: r[1], reverse=True)
    g = lambda i, k: (vis[i][k] if len(vis) > i else None)          # noqa: E731
    eps, p1, p2, y1, y1p1 = g(0, 2), g(1, 2), g(2, 2), g(4, 2), g(5, 2)
    gm, gm1 = g(0, 3), g(1, 3)
    yoy = (eps - y1) / abs(y1) * 100 if eps is not None and y1 not in (None, 0) else None
    yoy_p = (p1 - y1p1) / abs(y1p1) * 100 if p1 is not None and y1p1 not in (None, 0) else None
    return {"eps": eps, "eps_yoy": yoy,
            "eps_accel": bool(eps is not None and p1 is not None and p2 is not None and eps > p1 > p2 and eps > 0),
            "eps_yoy_accel": bool(yoy is not None and yoy_p is not None and yoy > yoy_p and yoy > 0),
            "gross_margin_chg": (gm - gm1) if gm is not None and gm1 is not None else None}


def rev_at(rows, t):
    vis = [r for r in rows if r[0] <= t]
    return max(vis, key=lambda r: r[1])[2] if vis else None


def per_pct_at(series, t):
    if not series:
        return None
    ords, vals = series
    j = bisect_right(ords, t.toordinal())
    if j == 0 or not vals[j - 1] or vals[j - 1] <= 0:
        return None
    w = vals[bisect_right(ords, t.toordinal() - 1100):j]
    w = w[w > 0]
    return round(100.0 * (w <= vals[j - 1]).sum() / len(w), 1) if len(w) else None


def run_chunk(payload):
    from app import consolidation, decision_center, swings, weekly_breakout
    G = payload
    dates, feats, meta = G["dates"], G["feats"], G["meta"]
    sig_set = {dates[i] for i in G["sig_idx"]}
    pos_market = {d: i for i, d in enumerate(dates)}
    out = []
    for sid, g in G["bars"].items():
        bars = g
        m = meta.get(sid, {})
        for j, bar in enumerate(bars):
            t = bar["trade_date"]
            if t not in sig_set or j + 1 < 30:
                continue
            mi = pos_market[t]
            if not feats["scan"][sid][mi]:
                continue
            win = bars[max(0, j - 149): j + 1]
            snap = {"stock_id": sid, "name": m.get("name"), "industry": m.get("industry"), "security_type": "stock",
                    "close": bar["close"], "rs_rating": _nn(feats["rs_rating"][sid][mi]),
                    "raw_close": bar["raw_close"], "price_factor": bar["close"] / bar["raw_close"], "price_date": t,
                    "ret_12_1": _nn(feats["ret_12_1"][sid][mi]), "tight_recent": _nn(feats["tight_recent"][sid][mi]),
                    "trend_template": bool(feats["trend_template"][sid][mi]), "amt20": _nn(feats["amt20"][sid][mi]),
                    "inst_net_20d": _nn(feats["inst20"][sid][mi]), "big1000_chg": None}
            strategies = []
            if any((b := swings.ALL[key](win, recent=3)) and b.get("dir") != "bear" for key in swings.BULL_KEYS):
                snap.update(fund_at(G["fq"].get(sid, []), t))
                snap["rev_yoy"] = rev_at(G["rev"].get(sid, []), t)
                snap["per_pctile"] = per_pct_at(G["per"].get(sid), t)
                bk = decision_center._breakout_strategy(snap, win, 3, G["backtests"])
                if bk:
                    strategies.append(bk)
            pa = decision_center._price_action_strategy(win, 5, 5)
            if pa:
                strategies.append(pa)
            setup = consolidation.analyze(win)
            if setup:
                strategies.append(setup)
            # 週線突破要約 64 週的日 K（同 scan_market）；載入資料已往前多抓 560 天
            weekly = decision_center._weekly_strategy(bars[max(0, j - weekly_breakout.BARS_NEEDED + 1): j + 1], t)
            if weekly:
                strategies.append(weekly)
            if strategies:
                out.append((t, decision_center._candidate(snap, strategies, bar["close"])))
    return out


# ───────────────────────── 結算與統計 ─────────────────────────
def nw_t(x, lag=HORIZON):
    """日均超額序列的 Newey-West t 值（持有 20 天，相鄰日重疊）。"""
    import numpy as np
    x = np.asarray([v for v in x if v == v]); n = len(x)
    if n < 30:
        return float("nan")
    e = x - x.mean(); var = (e @ e) / n
    for k in range(1, lag + 1):
        var += 2 * (1 - k / (lag + 1)) * (e[k:] @ e[:-k]) / n
    return float(x.mean() / np.sqrt(var / n))


def variants():
    """要比較的規則組合：原始規則＋動能模式的每個篩選條件。"""
    from app import decision_center
    return [("classic", None)] + [(mode, gate) for mode in ("momentum", "trend_hold") for gate in decision_center.GATES]


def variant_name(mode, gate):
    from app import decision_center
    return "原始規則（成交修正）" if mode == "classic" else f"{'趨勢持有60日' if mode == 'trend_hold' else '動能20日'}・{decision_center.GATES.get(gate, gate)}"


def scan_candidates(D, workers):
    """多程序逐檔逐日跑突破／裸 K，回傳 {日期: [候選]}（與模式無關，只跑一次）。"""
    px = D["px"]
    bars = price_bars(px)
    feats = {k: {sid: v[sid].to_numpy() for sid in v.columns} for k, v in D["feats"].items()}
    lo, hi = D["sig_idx"][0], D["sig_idx"][-1] + 1
    ever = [sid for sid in feats["scan"] if feats["scan"][sid][lo:hi].any() and sid in bars]
    chunks = [ever[i::workers * 4] for i in range(workers * 4)]
    tasks = [{"dates": D["dates"], "sig_idx": D["sig_idx"], "meta": {s: D["meta"].get(s, {}) for s in ch},
              "feats": {k: {s: v[s] for s in ch} for k, v in feats.items()}, "bars": {s: bars[s] for s in ch},
              "fq": {s: D["fq"].get(s, []) for s in ch}, "rev": {s: D["rev"].get(s, []) for s in ch},
              "per": {s: D["per"].get(s) for s in ch}, "backtests": D["backtests"]} for ch in chunks]
    t0 = time.time()
    by_date = {}
    with mp.Pool(workers) as pool:
        for i, rows in enumerate(pool.imap_unordered(run_chunk, tasks), 1):
            for t, cand in rows:
                by_date.setdefault(t, []).append(cand)
            if i % 8 == 0 or i == len(tasks):
                print(f"  掃描 {i}/{len(tasks)} 組（{time.time()-t0:.0f}s）", flush=True)
    return by_date


def price_bars(px):
    cols = {"adj_open": "open", "adj_high": "high", "adj_low": "low", "adj_close": "close"}
    return {sid: g.drop(columns=["stock_id"]).rename(columns=cols).to_dict("records")
            for sid, g in px.groupby("stock_id")}


def evaluate(D, by_date, combos):
    """同一成交引擎的事件研究；與連續組合績效分開呈現。"""
    import numpy as np
    import pandas as pd
    from app import decision_center, execution
    bars = price_bars(D["px"])
    empty = {"items": [], "stock_ids": set(), "industry_counts": {}}
    picks, bench_cache = [], {}
    for d in sorted(by_date):
        scan = {"as_of": d.isoformat(), "items": by_date[d], "scanned": len(by_date[d])}
        for mode, gate in combos:
            resp = decision_center.build_decision_response(scan, [], empty, mode=mode, market=D["market"].get(d),
                                                           lot_size=1, limit=500, gate=gate or decision_center.DEFAULT_GATE)
            for it in resp["items"]:
                if not it["selected"]:
                    continue
                strategy = next(s for s in it["strategies"] if s["key"] == it["lead_strategy"])
                spec = decision_center.execution_spec(it, strategy, mode)
                spec["observed_date"] = d
                fill = execution.simulate(bars[it["stock_id"]], spec, D["dates"])
                key = (d, fill["entry_date"], fill["exit_date"])
                benchmark = np.nan
                if fill["net_return"] is not None:
                    if key not in bench_cache:
                        cohort = D["feats"]["scan"].loc[d]
                        entry = D["open_panel"].loc[fill["entry_date"]]
                        exit_ = D["close_panel"].loc[fill["exit_date"]]
                        bench_cache[key] = (exit_ / entry - 1).where(cohort).mean()
                    benchmark = bench_cache[key]
                sid = it["stock_id"]
                picks.append({"variant": variant_name(mode, gate), "mode": mode, "gate": gate, "date": d,
                              "stock_id": sid, "name": it["name"], "industry": it["industry"],
                              "lead": it["lead_strategy"], "rs": it.get("rs_rating"),
                              "entry": fill["actual_entry_raw"], "entry_date": fill["entry_date"],
                              "stop": fill["actual_stop"], "target": spec["target"],
                              "ret": fill["net_return"], "outcome": fill["status"],
                              "excess": fill["net_return"] - benchmark if fill["net_return"] is not None else None,
                              "top10_60d": bool(D["winner_rank"].at[d, sid] >= 0.9),
                              "up30_60d": bool(D["forward60"].at[d, sid] >= 0.3)})
    return pd.DataFrame(picks)


def report(P, D, by_date, combos):
    import numpy as np
    import pandas as pd

    days = len(by_date)
    print(f"\n回測期間 {min(by_date)} ~ {max(by_date)}（{days} 個交易日）；每筆淨報酬已扣 {COST*100:.1f}% 來回成本與每邊 0.1% 滑價；"
          f"超額＝同日母體在實際進場日至出場日的等權報酬（不同持有期不得直接視為同一超額）")
    rows = []
    order = [variant_name(m, g) for m, g in combos]
    for name in order:
        g = P[P["variant"] == name].dropna(subset=["ret"]) if len(P) else P
        if not len(g):
            rows.append({"規則": name, "有選股天數": 0, "檔次": 0})
            continue
        daily = g.groupby("date")["excess"].mean()
        half = pd.to_datetime(g["date"]).dt.year.astype(str) + np.where(pd.to_datetime(g["date"]).dt.month <= 6, "H1", "H2")
        hx = g.groupby(half.to_numpy())["excess"].mean()
        rows.append({"規則": name, "有選股天數": g["date"].nunique(), "檔次": len(g),
                     "每筆淨報酬%": round(g["ret"].mean() * 100, 2), "勝率%": round((g["ret"] > 0).mean() * 100, 1),
                     "停損%": round((g["outcome"] == "loss").mean() * 100, 1),
                     "超額%": round(g["excess"].mean() * 100, 2), "NW t": round(nw_t(daily.to_numpy(), lag=60), 2),
                     "超額為負的半年": f"{int((hx < 0).sum())}/{len(hx)}", "最差%": round(g["ret"].min() * 100, 1)})
    pd.set_option("display.width", 250); pd.set_option("display.unicode.east_asian_width", True)
    print(pd.DataFrame(rows).set_index("規則").to_string())
    for name in order:
        g = P[P["variant"] == name].dropna(subset=["ret"]) if len(P) else P
        if len(g):
            print(f"  {name} 依領頭策略每筆淨報酬%：",
                  (g.groupby("lead")["ret"].mean() * 100).round(2).to_dict(), "｜檔次", g["lead"].value_counts().to_dict())


def main():
    ap = argparse.ArgumentParser(description="今日決策中心歷史回測（原始規則 vs 動能模式各篩選條件）")
    ap.add_argument("--start", default="2024-07-01", help="訊號起日（預設 2024-07-01）")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--end", default="", help="訊號截止日；仍載入其後資料結算")
    ap.add_argument("--lot-size", type=int, choices=[1, 1000], default=1)
    ap.add_argument("--capital", type=float, default=1_000_000)
    ap.add_argument("--report-json", default="research/decision_backtest_v3.json")
    ap.add_argument("--out", default="", help="另存入選明細 CSV")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

    D = load_all(date.fromisoformat(args.start))
    if args.end:
        end = date.fromisoformat(args.end)
        D["sig_idx"] = [i for i in D["sig_idx"] if D["dates"][i] <= end]
    if not D["sig_idx"]:
        raise SystemExit("沒有完整 60 日可結算的訊號窗口")
    by_date = scan_candidates(D, max(1, args.workers))
    combos = variants()
    P = evaluate(D, by_date, combos)
    report(P, D, by_date, combos)
    from app import research_results
    result = research_results.build_report(D, by_date, combos, P, args.capital, args.lot_size)
    research_results.save_report(result, args.report_json)
    if args.out:
        P.to_csv(args.out, index=False, encoding="utf-8-sig")
        print(f"入選明細 → {args.out}")


if __name__ == "__main__":
    main()
