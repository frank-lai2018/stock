r"""build_us_theme_daily.py — 算每日美股題材熱度 → us_theme_daily，並印「美台題材對照」。

用途：回答「美股哪些題材在漲？對應的台股題材跟上了沒？」。籃子在 us_theme_defs.py，每次執行先同步到 us_theme_member。
指標（籃子等權、還原價；定義同 build_theme_daily.py，美股沒有法人籌碼）：
  動能  ret_1d／ret_5d／ret_20d；ex_5d／ex_20d＝減 SPY 同期報酬
  廣度  breadth_ma20＝站上 20MA 的比例；high20_pct＝收在 20 日新高的比例
  量能  vol_ratio＝近 5 日成交金額 ÷（近 20 日成交金額 ÷4）
  熱度  heat_score＝100×(0.30·ret_20d + 0.15·ret_5d + 0.20·breadth + 0.10·high20 + 0.10·vol) ÷ 0.85
        每項先轉成「同日美股題材間」的百分位，和台股熱度一樣是相對熱度；heat_rank 為同日名次
對照（最後印出）：台股最新一天 vs 美股最新一天，兩邊各以「20 日漲幅高於同日題材中位數」為強分四個象限
  （與 analyze_us_tw_themes.py 回測的定義相同）：
  美強台強＝全球主流｜美強台弱＝台股還沒跟上或台廠沒受惠｜美弱台強＝台股自己的題材｜美弱台弱＝冷門
  回測（2024-10～2026-09）：美強的題材之後 20 個交易日，台股同題材比美弱的多約 1.2～1.6%（見 美台題材對照.md）。
  嚴格的時間對齊（美股 t 日 → 台股 t+1 日）在 v_us_tw_theme 與 analyze_us_tw_themes.py。
前置：schema_us.sql、fetch_us_prices.py、build_theme_daily.py。

用法：
  python build_us_theme_daily.py                # 算近 60 個美股交易日 → 寫入 us_theme_daily → 印對照
  python build_us_theme_daily.py --days 600     # 回補 600 個交易日
  python build_us_theme_daily.py --report-only  # 照樣計算、只印對照，不寫入 DB
"""
import argparse
import os
import sys

import numpy as np
import pandas as pd

from us_theme_defs import BENCHMARK, INDICATORS, US_THEMES, all_symbols

WEIGHTS = {"ret_20d": 0.30, "ret_5d": 0.15, "breadth_ma20": 0.20, "high20_pct": 0.10, "vol_ratio": 0.10}
QUADRANTS = ["美強台強", "美強台弱", "美弱台強", "美弱台弱", "美股無對應"]
QUADRANT_NOTE = {"美強台強": "全球主流題材", "美強台弱": "台股還沒跟上，或台廠沒受惠",
                 "美弱台強": "台股自己的題材，或短線炒作", "美弱台弱": "冷門", "美股無對應": "只看台股"}


def clean_dsn(dsn):
    """容錯＋防呆：剝掉誤貼進值裡的旗標／引號，並先擋掉明顯不合法的連線字串。（同 nightly.py）"""
    dsn = (dsn or "").strip().strip('"').strip("'").strip()
    if dsn.startswith("--dsn"):                      # 誤把旗標本身貼進值裡
        dsn = dsn[5:].lstrip().lstrip("=").lstrip()
    if dsn and not (dsn.startswith(("postgresql://", "postgres://")) or "=" in dsn):
        raise SystemExit(f"連線字串格式不對：{dsn!r}；"
                         "應為 postgresql://user:pw@host:port/db（或 key=value 形式）")
    return dsn


def load_baskets(cur):
    """回傳 {theme_id: (code, 台股題材名, [美股代號])}；只收 DB 裡有的 L3 題材、且有美股對照的。"""
    cur.execute("SELECT code, theme_id, name FROM theme WHERE layer=3 AND is_active")
    by_code = {code: (tid, name) for code, tid, name in cur.fetchall()}
    missing = [c for c in US_THEMES if c not in by_code]
    if missing:
        print(f"⚠️ us_theme_defs.py 有 {len(missing)} 個題材不在 theme 表（先跑 theme_candidates.py）：{missing}")
    return {by_code[c][0]: (c, by_code[c][1], [s for s, _ in t["symbols"]])
            for c, t in US_THEMES.items() if c in by_code and t["symbols"]}


def sync_members(cur):
    from psycopg2.extras import execute_values
    cur.execute("SELECT code, theme_id FROM theme WHERE layer=3 AND is_active")
    ids = dict(cur.fetchall())
    rows = [(ids[c], sym, name) for c, t in US_THEMES.items() if c in ids for sym, name in t["symbols"]]
    cur.execute("DELETE FROM us_theme_member")
    execute_values(cur, "INSERT INTO us_theme_member (theme_id, symbol, name) VALUES %s", rows)
    return len(rows)


def load_panel(cur, days):
    """近 days+30 個美股交易日（以 SPY 的交易日為準）的寬表：還原價、成交金額。"""
    cur.execute("SELECT trade_date FROM us_price_daily WHERE symbol=%s ORDER BY 1 DESC LIMIT %s",
                (BENCHMARK, days + 30))
    dates = sorted(r[0] for r in cur.fetchall())
    if not dates:
        raise SystemExit(f"us_price_daily 沒有 {BENCHMARK}：先跑 fetch_us_prices.py --range 5y")
    cur.execute("""SELECT symbol, trade_date, adj_close, close, volume FROM us_price_daily
                    WHERE symbol = ANY(%s) AND trade_date >= %s""", (all_symbols(), dates[0]))
    df = pd.DataFrame(cur.fetchall(), columns=["symbol", "trade_date", "adj", "close", "volume"])
    df["amount"] = df["close"].astype(float) * df["volume"].astype(float)
    idx = pd.Index(dates, name="trade_date")
    adj = df.pivot(index="trade_date", columns="symbol", values="adj").astype(float).reindex(idx)
    amt = df.pivot(index="trade_date", columns="symbol", values="amount").astype(float).reindex(idx)
    return adj, amt


def features(adj, amt):
    ma20 = adj.rolling(20, min_periods=20).mean()
    hi20 = adj.rolling(20, min_periods=20).max()
    ok = adj.notna() & ma20.notna()
    return {
        "ret_1d": adj.pct_change(1, fill_method=None),
        "ret_5d": adj.pct_change(5, fill_method=None),
        "ret_20d": adj.pct_change(20, fill_method=None),
        "above": (adj > ma20).astype(float).where(ok),
        "high20": (adj >= hi20).astype(float).where(ok),
        "amt5": amt.rolling(5, min_periods=5).sum(),
        "amt20": amt.rolling(20, min_periods=20).sum(),
    }


def compute(adj, f, baskets, days):
    keep = adj.index[-days:]
    bench = {k: f[k].loc[keep, BENCHMARK] for k in ("ret_5d", "ret_20d")}
    recs = []
    for tid, (code, name, syms) in baskets.items():
        cols = [s for s in syms if s in adj.columns]
        if not cols:
            continue
        m = lambda k: f[k].loc[keep, cols]                                    # noqa: E731
        amt20 = m("amt20").sum(axis=1, min_count=1)
        df = pd.DataFrame({
            "theme_id": tid, "n_members": adj.loc[keep, cols].notna().sum(axis=1),
            "ret_1d": m("ret_1d").mean(axis=1), "ret_5d": m("ret_5d").mean(axis=1),
            "ret_20d": m("ret_20d").mean(axis=1),
            "breadth_ma20": m("above").mean(axis=1), "high20_pct": m("high20").mean(axis=1),
            "vol_ratio": (m("amt5").sum(axis=1, min_count=1) / (amt20 / 4)).replace([np.inf, -np.inf], np.nan),
        })
        df["ex_5d"] = df["ret_5d"] - bench["ret_5d"]
        df["ex_20d"] = df["ret_20d"] - bench["ret_20d"]
        recs.append(df[df["n_members"] > 0])
    out = pd.concat(recs).reset_index()
    pct = out.groupby("trade_date")[list(WEIGHTS)].rank(pct=True).fillna(0.5)   # 缺值（剛上市）當中位
    out["heat_score"] = 100 * sum(pct[k] * w for k, w in WEIGHTS.items()) / sum(WEIGHTS.values())
    out["heat_rank"] = out.groupby("trade_date")["heat_score"].rank(ascending=False, method="first").astype(int)
    return out


def save(cur, out):
    from psycopg2.extras import execute_values
    cols = ["theme_id", "trade_date", "n_members", "ret_1d", "ret_5d", "ret_20d", "ex_5d", "ex_20d",
            "breadth_ma20", "high20_pct", "vol_ratio", "heat_score", "heat_rank"]
    cur.execute("DELETE FROM us_theme_daily WHERE trade_date = ANY(%s)", (list(out["trade_date"].unique()),))
    rows = [tuple(None if pd.isna(v) else (v.item() if hasattr(v, "item") else v) for v in r)
            for r in out[cols].itertuples(index=False)]
    execute_values(cur, f"INSERT INTO us_theme_daily ({','.join(cols)}) VALUES %s", rows, page_size=2000)
    return len(rows)


def pct(x, signed=True):
    if x is None or pd.isna(x):
        return "    -  "
    return f"{x * 100:+6.1f}%" if signed else f"{x * 100:6.1f}%"


def report(cur, out, f, baskets):
    cur.execute("""SELECT d.trade_date, t.theme_id, t.name, d.heat_rank, d.ret_5d, d.ret_20d
                     FROM theme_daily d JOIN theme t USING (theme_id)
                    WHERE t.layer=3 AND d.trade_date=(SELECT max(trade_date) FROM theme_daily)""")
    tw = pd.DataFrame(cur.fetchall(), columns=["tw_date", "theme_id", "name", "tw_rank", "tw_5d", "tw_20d"])
    if tw.empty:
        print("theme_daily 沒有資料：先跑 build_theme_daily.py")
        return
    tw[["tw_5d", "tw_20d"]] = tw[["tw_5d", "tw_20d"]].astype(float)
    us_date = out["trade_date"].max()
    us = out[out["trade_date"] == us_date].set_index("theme_id")
    tw_med, us_med = tw["tw_20d"].median(), us["ret_20d"].median()
    names = {sym: name for t in US_THEMES.values() for sym, name in t["symbols"]} | dict(INDICATORS)
    r1, r20 = f["ret_1d"].loc[us_date], f["ret_20d"].loc[us_date]

    print(f"\n==================== 美台題材對照　台股 {tw['tw_date'].iloc[0]} ／ 美股 {us_date} ====================")
    print("美股最新一日：" + "｜".join(f"{names.get(s, s)} {pct(r1.get(s)).strip()}"
                                    for s in ["SPY", "QQQ", "^SOX", "TSM"] if s in r1.index))
    print(f"象限：20 日漲幅高於同日題材中位數＝強（台股中位 {pct(tw_med).strip()}、美股中位 {pct(us_med).strip()}）；"
          "名次是各自的熱度名次。")
    rows = []
    for r in tw.itertuples(index=False):
        u = us.loc[r.theme_id] if r.theme_id in us.index else None
        if u is None:
            q = "美股無對應"
        else:
            q = ("美強" if u["ret_20d"] > us_med else "美弱") + ("台強" if r.tw_20d > tw_med else "台弱")
        lead = ""
        if r.theme_id in baskets:
            syms = [s for s in baskets[r.theme_id][2] if s in r20.index and pd.notna(r20[s])]
            top = sorted(syms, key=lambda s: r20[s], reverse=True)[:2]
            lead = "、".join(f"{names.get(s, s)} {r20[s] * 100:+.0f}%" for s in top)
        rows.append((QUADRANTS.index(q), -(u["ret_20d"] if u is not None else 0), r, u, q, lead))
    rows.sort(key=lambda x: (x[0], x[1]))
    head = (f"{'題材':<26}{'台股20日':>8}{'台股5日':>8}{'台股名次':>6}  "
            f"{'美股20日':>8}{'減SPY':>8}{'美股5日':>8}{'美股1日':>8}{'美股名次':>6}  美股領漲（20日）")
    current = None
    for _, _, r, u, q, lead in rows:
        if q != current:
            current = q
            print(f"\n【{q}】{QUADRANT_NOTE[q]}")
            print(head)
        us_part = (f"{pct(u['ret_20d'])} {pct(u['ex_20d'])}{pct(u['ret_5d'])} {pct(u['ret_1d'])}{int(u['heat_rank']):>6}"
                   if u is not None else "")
        print(f"{r.name[:24]:<26}{pct(r.tw_20d)} {pct(r.tw_5d)}{int(r.tw_rank):>6}  {us_part}  {lead}")
    counts = pd.Series([q for *_, q, _ in rows]).value_counts()
    print("\n" + "｜".join(f"{q} {counts.get(q, 0)}" for q in QUADRANTS))


def main():
    ap = argparse.ArgumentParser(description="算每日美股題材熱度 → us_theme_daily，印美台題材對照")
    ap.add_argument("--dsn", default=os.environ.get("DATABASE_URL", ""), help="PostgreSQL 連線字串")
    ap.add_argument("--days", type=int, default=60, help="回算近幾個美股交易日（預設 60）")
    ap.add_argument("--report-only", action="store_true", help="不寫 DB，只計算並印對照")
    args = ap.parse_args()
    args.dsn = clean_dsn(args.dsn)
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
            baskets = load_baskets(cur)
            adj, amt = load_panel(cur, args.days)
            f = features(adj, amt)
            out = compute(adj, f, baskets, args.days)
            if not args.report_only:
                n_members = sync_members(cur)
                n = save(cur, out)
                print(f"us_theme_member {n_members} 筆；us_theme_daily 寫入 {n} 列"
                      f"（{out['trade_date'].min()} ~ {out['trade_date'].max()}，{out['theme_id'].nunique()} 個題材）")
            report(cur, out, f, baskets)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
