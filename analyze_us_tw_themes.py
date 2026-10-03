r"""analyze_us_tw_themes.py — 美股題材隔夜漲跌，台股同題材隔天跟不跟？（美台題材對照的錯開一天分析）

問題：us_theme_defs.py 的對照，哪些真的有用？
對齊：美股收盤是台灣清晨，美股 t 日影響台股 t+1 日。每個台股交易日 d 配「d 之前最後一個美股交易日」u(d)，
      台股 d 日報酬（前一交易日收盤 → d 收盤）對上美股 u(前一個台股交易日) → u(d) 的累積報酬
      （台股連假時美股可能開了好幾天，一併算進去；中間沒有美股交易的日子不算）。
兩種看法：
  整體  台股題材報酬 vs 美股籃子報酬（含兩邊大盤一起漲跌）
  題材  台股題材超額（減台股等權大盤）vs 美股籃子超額（減 SPY）：扣掉大盤後，題材本身有沒有傳過來
指標：相關係數；β＝台股題材超額對美股籃子超額的斜率與 t 值；美股籃子超額前 10%（大漲）與後 10%（大跌）的隔天，
      台股題材平均超額。「扣費半 t」＝同時放進費半超額後，美股籃子還剩多少解釋力：
      低於 2 代表這個題材跟的是美股半導體整體，看費半就夠，專屬對照沒多出資訊。
四象限（附帶）：20 日超額各自高於同日題材中位數＝強，看之後 20 個交易日台股題材的超額，回答「美強台弱會不會補漲」。
      另做「同題材自己比」：先扣掉每個題材整段期間的平均，排除「這個題材本來就一直強」的選股偏誤。
限制：台股題材報酬用 build_theme_daily.py 同一套算法（in_universe 成分股等權），但成分是「目前」的
      （2026-09／10 才定、偏向近期贏家），所以只看相對關係，不看絕對報酬。美股籃子也是今天挑的。

結果寫入 us_theme_follow（整批換掉），「美台題材」頁的跟隨度標籤讀這張表；建議每季、或改了 us_theme_defs.py 後重跑。

用法：
  python analyze_us_tw_themes.py                     # 近 500 個台股交易日（約兩年）→ 寫入 us_theme_follow
  python analyze_us_tw_themes.py --report-only       # 只印結果，不寫 DB
  python analyze_us_tw_themes.py --days 250 --csv 跟隨度.csv
"""
import argparse
import os
import sys
from bisect import bisect_left
from datetime import timedelta

import numpy as np
import pandas as pd

import build_theme_daily as btd
from us_theme_defs import BENCHMARK, US_THEMES

HORIZON = 20


def nw_t(x, lag=HORIZON):
    """序列平均的 Newey-West t 值（20 日報酬相鄰日重疊）。"""
    x = np.asarray([v for v in x if v == v])
    n = len(x)
    if n < 30:
        return float("nan")
    e = x - x.mean()
    var = (e @ e) / n
    for k in range(1, lag + 1):
        var += 2 * (1 - k / (lag + 1)) * (e[k:] @ e[:-k]) / n
    return float(x.mean() / np.sqrt(var / n))


def ols(x, y):
    """斜率、t 值、相關係數。"""
    x, y = np.asarray(x, float), np.asarray(y, float)
    n = len(x)
    if n < 30:
        return (float("nan"),) * 3
    xd, yd = x - x.mean(), y - y.mean()
    beta = (xd @ yd) / (xd @ xd)
    resid = yd - beta * xd
    se = np.sqrt((resid @ resid) / (n - 2) / (xd @ xd))
    return beta, beta / se, float(np.corrcoef(x, y)[0, 1])


def partial_t(x, z, y):
    """y = a + b·x + c·z 中 b 的 t 值：控制 z（費半）之後，x（美股籃子）還有沒有額外解釋力。"""
    x, z, y = (np.asarray(v, float) for v in (x, z, y))
    X = np.column_stack([np.ones(len(x)), x, z])
    coef, *_ = np.linalg.lstsq(X, y, rcond=None)
    resid = y - X @ coef
    cov = (resid @ resid) / (len(y) - 3) * np.linalg.inv(X.T @ X)
    return float(coef[1] / np.sqrt(cov[1, 1]))


def tw_panel(cur, days):
    """台股 L3 題材日報酬（寬表：日期 × theme_id）、台股等權大盤日報酬、題材名稱。"""
    themes, _ = btd.load_themes(cur)
    themes = {tid: v for tid, v in themes.items() if v[2] == 3}
    wide = btd.load_panel(cur, days)
    out, f, universe = btd.compute(wide, themes, days)
    ret = out.pivot(index="trade_date", columns="theme_id", values="ret_1d").astype(float).sort_index()
    mkt = f["ret_1d"].where(universe).mean(axis=1).reindex(ret.index)
    return ret, mkt, {tid: (code, name) for tid, (code, name, _, _) in themes.items()}, list(wide["adj"].index)


def us_panel(cur, since):
    cur.execute("SELECT symbol, trade_date, adj_close FROM us_price_daily WHERE trade_date >= %s", (since,))
    df = pd.DataFrame(cur.fetchall(), columns=["symbol", "trade_date", "adj"])
    return df.pivot(index="trade_date", columns="symbol", values="adj").astype(float).sort_index()


def align(tw_dates, all_tw_dates, us_dates):
    """台股日 d → (u(前一台股日), u(d))；u(x)＝x 之前（不含）最後一個美股交易日。"""
    pos = {d: i for i, d in enumerate(all_tw_dates)}
    u = lambda d: us_dates[bisect_left(us_dates, d) - 1] if bisect_left(us_dates, d) > 0 else None  # noqa: E731
    pairs = {}
    for d in tw_dates:
        i = pos[d]
        if i == 0:
            continue
        a, b = u(all_tw_dates[i - 1]), u(d)
        if a is not None and b is not None and b > a:      # 中間沒有美股交易就不算
            pairs[d] = (a, b)
    return pairs


def main():
    ap = argparse.ArgumentParser(description="美股題材隔夜漲跌 → 台股同題材隔天的跟隨度")
    ap.add_argument("--dsn", default=os.environ.get("DATABASE_URL", ""), help="PostgreSQL 連線字串")
    ap.add_argument("--days", type=int, default=500, help="分析近幾個台股交易日（預設 500，約兩年）")
    ap.add_argument("--csv", default="", help="另存各題材結果")
    ap.add_argument("--report-only", action="store_true", help="只印結果，不寫 us_theme_follow")
    args = ap.parse_args()
    args.dsn = btd.clean_dsn(args.dsn)
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
    except (AttributeError, ValueError):
        pass
    if not args.dsn:
        raise SystemExit("需要 --dsn 或環境變數 DATABASE_URL")

    import psycopg2
    conn = psycopg2.connect(args.dsn)
    try:
        with conn, conn.cursor() as cur:
            tw, mkt, names, all_tw = tw_panel(cur, args.days)
            us = us_panel(cur, tw.index[0] - timedelta(days=60))
            cur.execute("SELECT code, theme_id FROM theme WHERE layer=3")
            ids = dict(cur.fetchall())
    finally:
        conn.close()

    us_dates = list(us.index)
    pairs = align(list(tw.index), all_tw, us_dates)
    days = sorted(pairs)
    a_idx, b_idx = [pairs[d][0] for d in days], [pairs[d][1] for d in days]
    win = pd.DataFrame(us.loc[b_idx].to_numpy() / us.loc[a_idx].to_numpy() - 1, index=days, columns=us.columns)
    spy = win[BENCHMARK]
    sox_ex = win["^SOX"] - spy
    us20 = us.pct_change(HORIZON, fill_method=None)
    tw_mkt = mkt.loc[days]
    print(f"台股 {days[0]} ~ {days[-1]}，{len(days)} 個有美股隔夜資料的交易日"
          f"（台股報酬配前一晚美股；台股大盤＝in_universe 等權）")

    # ── 各題材隔天跟隨度 ──
    rows, panel, save_rows = [], {}, []
    for code, t in US_THEMES.items():
        tid = ids.get(code)
        syms = [s for s, _ in t["symbols"] if s in win.columns]
        if tid is None or tid not in tw.columns or not syms:
            continue
        x_raw = win[syms].mean(axis=1)
        y_raw = tw[tid].reindex(days)
        x_ex, y_ex = x_raw - spy, y_raw - tw_mkt
        ok = x_ex.notna() & y_ex.notna() & sox_ex.notna()
        b_raw, t_raw, c_raw = ols(x_raw[ok], y_raw[ok])
        b_ex, t_ex, c_ex = ols(x_ex[ok], y_ex[ok])
        hi, lo = x_ex[ok].quantile(0.9), x_ex[ok].quantile(0.1)
        up, down = y_ex[ok][x_ex[ok] >= hi], y_ex[ok][x_ex[ok] <= lo]
        c = round(c_ex, 2)                                 # 用顯示的兩位小數判斷，避免 0.0996 印成 0.10 卻歸到下一級
        label = ("明顯跟隨" if c >= 0.25 else "有一點" if c >= 0.10 else "幾乎不跟") if t_ex >= 2 else "幾乎不跟"
        t_sox = partial_t(x_ex[ok], sox_ex[ok], y_ex[ok])
        rows.append({"題材": names[tid][1], "code": code, "美股檔數": len(syms), "天數": int(ok.sum()),
                     "整體相關": round(c_raw, 2), "題材相關": c, "β": round(b_ex, 2), "t": round(t_ex, 1),
                     "扣費半t": round(t_sox, 1),
                     "美股大漲隔天%": round(up.mean() * 100, 2), "美股大跌隔天%": round(down.mean() * 100, 2),
                     "大漲隔天跑贏%": round((up > 0).mean() * 100, 0), "判斷": label})
        save_rows.append((tid, int(ok.sum()), c_raw, c_ex, b_ex, t_ex, t_sox, up.mean(), down.mean(),
                          (up > 0).mean(), label, days[0], days[-1]))
        sym_20 = us20[syms].mean(axis=1) - us20[BENCHMARK]
        panel[tid] = (y_raw, sym_20)
    res = pd.DataFrame(rows).sort_values("題材相關", ascending=False)
    pd.set_option("display.width", 250)
    pd.set_option("display.unicode.east_asian_width", True)
    print("\n【隔天跟隨度】題材相關＝扣掉兩邊大盤後的相關；大漲／大跌＝美股籃子超額前／後 10% 的隔天，台股題材平均超額")
    print(res.drop(columns=["code"]).to_string(index=False))
    print("\n判斷：題材相關 ≥ 0.25 且 t ≥ 2＝明顯跟隨；0.10～0.25＝有一點；其餘＝幾乎不跟。"
          "扣費半t < 2＝跟的是美股半導體整體，專屬對照沒多出資訊。")

    # ── 四象限：美強台弱會不會補漲 ──
    recs = []
    full = tw.index
    mkt_full = mkt.reindex(full)
    for tid, (y_raw, us_ex20) in panel.items():
        y = tw[tid].reindex(full)
        grow = lambda s: (1 + s).rolling(HORIZON).apply(np.prod, raw=True) - 1     # noqa: E731
        past = grow(y) - grow(mkt_full)
        fwd = past.shift(-HORIZON)                       # d+1～d+20 的超額
        for d in days:
            ue = us_ex20.get(pairs[d][1])
            recs.append((d, tid, past.get(d), ue, fwd.get(d)))
    q = pd.DataFrame(recs, columns=["date", "theme", "tw20", "us20", "fwd"]).dropna()
    med = q.groupby("date")[["tw20", "us20"]].transform("median")
    q["象限"] = np.where(q["us20"] > med["us20"], "美強", "美弱") + np.where(q["tw20"] > med["tw20"], "台強", "台弱")
    q["fwd_dm"] = q["fwd"] - q.groupby("theme")["fwd"].transform("mean")   # 同題材自己比
    daily = q.groupby(["date", "象限"])["fwd"].mean().unstack()
    daily_dm = q.groupby(["date", "象限"])["fwd_dm"].mean().unstack()
    print(f"\n【四象限 → 之後 {HORIZON} 個交易日台股題材超額】（強＝20 日超額高於同日題材中位數；"
          f"{q['date'].min()} ~ {q['date'].max()}，{q['date'].nunique()} 天）")
    print("  成分是事後挑的，各象限平均都偏高，只看象限之間的差距。")
    for name in ["美強台強", "美強台弱", "美弱台強", "美弱台弱"]:
        s = daily.get(name, pd.Series(dtype=float))
        print(f"  {name}：平均 {s.mean() * 100:+.2f}%（NW t {nw_t(s.to_numpy()):.2f}，{int((q['象限'] == name).sum())} 筆）")
    for a, b, what in [("美強台弱", "美弱台弱", "台股還弱時，美股強的會不會補漲"),
                       ("美強台強", "美弱台強", "台股已強時，美股也強的會不會走更久")]:
        diff = (daily[a] - daily[b]).dropna()
        diff_dm = (daily_dm[a] - daily_dm[b]).dropna()
        print(f"  {a} − {b}：{diff.mean() * 100:+.2f}%（NW t {nw_t(diff.to_numpy()):.2f}）；"
              f"同題材自己比 {diff_dm.mean() * 100:+.2f}%（NW t {nw_t(diff_dm.to_numpy()):.2f}）← {what}")

    if args.csv:
        res.to_csv(args.csv, index=False, encoding="utf-8-sig")
        print(f"\n各題材結果 → {args.csv}")
    if not args.report_only:
        from psycopg2.extras import execute_values
        conn = psycopg2.connect(args.dsn)
        try:
            with conn, conn.cursor() as cur:
                cur.execute("DELETE FROM us_theme_follow")
                execute_values(cur, "INSERT INTO us_theme_follow (theme_id, n_days, corr_raw, corr_ex, beta_ex, t_ex, "
                                    "t_partial_sox, up_next, down_next, up_win, label, period_from, period_to) VALUES %s",
                               [tuple(float(v) if isinstance(v, (np.floating, float)) else v for v in r)
                                for r in save_rows])
        finally:
            conn.close()
        print(f"\nus_theme_follow 寫入 {len(save_rows)} 個題材（{days[0]} ~ {days[-1]}）")


if __name__ == "__main__":
    main()
