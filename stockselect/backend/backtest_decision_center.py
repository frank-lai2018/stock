r"""backtest_decision_center.py — 今日決策中心歷史回測（兩種模式並排比較）。

每個交易日只用「當天以前看得到」的資料，呼叫 decision_center 本身的突破／裸 K 函式產生候選，
再交給 build_decision_response 依 momentum（動能模式）與 classic（原始規則）各挑新倉，
最後照 settle_pending 的規則結算 20 個交易日後的結果。改了決策中心的規則之後重跑這支，
就能確認新規則在歷史上是否真的比較好（結果與方法見 動能分析設計.md §9）。

點時間（point-in-time）處理：
- K 棒：t 以前 150 根還原 K（同 scan_market）。
- RS 評等、趨勢模板、ret_12_1、tight_recent、amt20、in_universe：逐日橫斷面重算（定義同 mv_stock_snapshot.sql）。
- 財報只用 available_date ≤ t 的季報；月營收用 available_date ≤ t 的最新月份；本益比百分位用 t 以前 1100 天。
- 法人 20 日淨買用 t 以前 20 個交易日。千張大戶 2026-07 才有資料，回測期間視為缺值（同系統缺值時 0 分）。
- pattern_backtest 先驗用現在的表（含回測期間，屬輕微前視，最多影響突破分數 5 分）。
- 沒有交易帳持股（產業上限只算當天新倉）；分數校準為空（歷史上當時也還沒有）。
限制：資料庫沒有下市股（存活者偏差）；超額＝每筆淨報酬 − 同日掃描母體 20 日等權報酬。

用法（在 stockselect/backend 下，需可讀 .env 的 DATABASE_URL）：
  python backtest_decision_center.py                        # 2024-07-01 起、8 個程序
  python backtest_decision_center.py --start 2025-01-01 --workers 4
  python backtest_decision_center.py --out picks.csv        # 另存每天入選明細
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
        "SELECT p.stock_id, p.trade_date, p.adj_open, p.adj_high, p.adj_low, p.adj_close, p.volume, p.amount "
        "FROM price_daily p JOIN stock s USING (stock_id) WHERE s.security_type='stock' AND p.trade_date >= %(d)s "
        "ORDER BY p.stock_id, p.trade_date", {"d": load_start}))
    for c in ("adj_open", "adj_high", "adj_low", "adj_close", "volume", "amount"):
        px[c] = px[c].astype(float)
    print(f"價格 {len(px):,} 列（{time.time()-t0:.0f}s）", flush=True)

    A = px.pivot(index="trade_date", columns="stock_id", values="adj_close").sort_index()
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

    last_ok = len(dates) - 1 - HORIZON                              # 之後要有滿 20 根才能結算
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
    return dict(meta=meta, px=px, dates=dates, sig_idx=sig_idx, feats=feats, bench=bench, market=market,
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
    from app import decision_center, swings
    G = payload
    dates, feats, meta = G["dates"], G["feats"], G["meta"]
    sig_set = {dates[i] for i in G["sig_idx"]}
    pos_market = {d: i for i, d in enumerate(dates)}
    out = []
    for sid, g in G["bars"].items():
        bars = [{"trade_date": d, "open": o, "high": h, "low": l, "close": c, "volume": v} for d, o, h, l, c, v in g]
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
            if strategies:
                out.append((t, decision_center._candidate(snap, strategies, bar["close"])))
    return out


# ───────────────────────── 結算與統計 ─────────────────────────
def settle(bars, i, entry, stop, target):
    """同 settle_pending：20 根內先碰停損（同根同碰算停損）或目標，否則第 20 根收盤到期。回傳淨報酬。"""
    H, L, C = bars
    if i + HORIZON >= len(C):
        return None, None
    for j in range(i + 1, i + HORIZON + 1):
        if L[j] <= stop:
            return stop / entry - 1 - COST, "loss"
        if target is not None and H[j] >= target:
            return target / entry - 1 - COST, "win"
    return C[i + HORIZON] / entry - 1 - COST, "timeout"


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


def main():
    import numpy as np
    import pandas as pd
    from app import decision_center

    ap = argparse.ArgumentParser(description="今日決策中心歷史回測（momentum vs classic）")
    ap.add_argument("--start", default="2024-07-01", help="訊號起日（預設 2024-07-01）")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--out", default="", help="另存入選明細 CSV")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

    D = load_all(date.fromisoformat(args.start))
    px = D["px"]
    bars = {sid: list(zip(g["trade_date"], g["adj_open"], g["adj_high"], g["adj_low"], g["adj_close"], g["volume"]))
            for sid, g in px.groupby("stock_id")}
    hlc = {sid: (g["adj_high"].to_numpy(), g["adj_low"].to_numpy(), g["adj_close"].to_numpy()) for sid, g in px.groupby("stock_id")}
    pos = {sid: {d: i for i, d in enumerate(g["trade_date"])} for sid, g in px.groupby("stock_id")}
    feats = {k: {sid: v[sid].to_numpy() for sid in v.columns} for k, v in D["feats"].items()}
    lo, hi = D["sig_idx"][0], D["sig_idx"][-1] + 1
    ever = [sid for sid in feats["scan"] if feats["scan"][sid][lo:hi].any() and sid in bars]
    chunks = [ever[i::args.workers * 4] for i in range(args.workers * 4)]
    tasks = [{"dates": D["dates"], "sig_idx": D["sig_idx"], "meta": {s: D["meta"].get(s, {}) for s in ch},
              "feats": {k: {s: v[s] for s in ch} for k, v in feats.items()}, "bars": {s: bars[s] for s in ch},
              "fq": {s: D["fq"].get(s, []) for s in ch}, "rev": {s: D["rev"].get(s, []) for s in ch},
              "per": {s: D["per"].get(s) for s in ch}, "backtests": D["backtests"]} for ch in chunks]
    t0 = time.time()
    by_date = {}
    with mp.Pool(args.workers) as pool:
        for i, rows in enumerate(pool.imap_unordered(run_chunk, tasks), 1):
            for t, cand in rows:
                by_date.setdefault(t, []).append(cand)
            if i % 8 == 0 or i == len(tasks):
                print(f"  掃描 {i}/{len(tasks)} 組（{time.time()-t0:.0f}s）", flush=True)

    empty = {"items": [], "stock_ids": set(), "industry_counts": {}}
    picks = []
    for d in sorted(by_date):
        scan = {"as_of": d.isoformat(), "items": by_date[d], "scanned": len(by_date[d])}
        for mode in decision_center.MODES:
            resp = decision_center.build_decision_response(scan, [], empty, mode=mode, market=D["market"].get(d), limit=500)
            for it in resp["items"]:
                if not it["selected"]:
                    continue
                p = it["position_plan"]
                i = pos[it["stock_id"]].get(d)
                ret, outcome = settle(hlc[it["stock_id"]], i, p["entry"], p["stop"], p["target"]) if i is not None else (None, None)
                picks.append({"mode": mode, "date": d, "stock_id": it["stock_id"], "name": it["name"],
                              "industry": it["industry"], "lead": it["lead_strategy"], "rs": it.get("rs_rating"),
                              "trend_template": it.get("trend_template"), "entry": p["entry"], "stop": p["stop"],
                              "target": p["target"], "ret": ret, "outcome": outcome,
                              "excess": None if ret is None else ret - D["bench"].get(d, np.nan)})
    P = pd.DataFrame(picks)
    days = len(by_date)
    print(f"\n回測期間 {min(by_date)} ~ {max(by_date)}（{days} 個交易日）；每筆淨報酬已扣 {COST*100:.1f}% 來回成本；"
          f"超額＝減同日掃描母體 20 日等權報酬（母體平均 {D['bench'].reindex(sorted(by_date)).mean()*100:.2f}%）")
    rows = []
    for mode, g in P.groupby("mode"):
        g = g.dropna(subset=["ret"])
        daily = g.groupby("date")["excess"].mean()
        half = pd.to_datetime(g["date"]).dt.year.astype(str) + np.where(pd.to_datetime(g["date"]).dt.month <= 6, "H1", "H2")
        hx = g.groupby(half.to_numpy())["excess"].mean()
        rows.append({"模式": mode, "有選股天數": g["date"].nunique(), "檔次": len(g),
                     "每筆淨報酬%": round(g["ret"].mean() * 100, 2), "勝率%": round((g["ret"] > 0).mean() * 100, 1),
                     "停損%": round((g["outcome"] == "loss").mean() * 100, 1),
                     "超額%": round(g["excess"].mean() * 100, 2), "NW t": round(nw_t(daily.to_numpy()), 2),
                     "超額為負的半年": f"{int((hx < 0).sum())}/{len(hx)}", "最差%": round(g["ret"].min() * 100, 1)})
    pd.set_option("display.width", 250); pd.set_option("display.unicode.east_asian_width", True)
    print(pd.DataFrame(rows).set_index("模式").to_string())
    for mode, g in P.dropna(subset=["ret"]).groupby("mode"):
        print(f"  {mode} 依領頭策略每筆淨報酬%：",
              (g.groupby("lead")["ret"].mean() * 100).round(2).to_dict(), "｜檔次", g["lead"].value_counts().to_dict())
    if args.out:
        P.to_csv(args.out, index=False, encoding="utf-8-sig")
        print(f"入選明細 → {args.out}")


if __name__ == "__main__":
    main()
