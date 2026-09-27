r"""build_theme_daily.py — 計算每日族群熱度 → theme_daily，並印出熱度排行、領頭羊與落後補漲候選。

用途：回答「現在哪些族群最熱？誰在領漲？誰還沒漲？」。L2 產業鏈節點與 L3 市場題材各自排名。
成分：只取 in_universe 的成分股（交易滿 60 天、20 日均成交額 ≥ 500 萬，同 TagStandard 母體），族群至少 5 檔才算。
      L2 節點的成分含其子節點；L3 取 status 為 confirmed／seed（起手名單未複核也先算，排行上會標示）。
指標（成分股等權，價格用還原價，金額用原始收盤）：
  動能  ret_1d／ret_5d／ret_20d
  廣度  breadth_ma20＝站上 20MA 的比例；high20_pct＝收在 20 日新高的比例
  量能  vol_ratio＝族群近 5 日成交額 ÷（近 20 日成交額 ÷4），1＝持平、>1＝放量
  籌碼  inst_ratio＝族群三大法人 20 日淨買超金額 ÷ 20 日成交額
  熱度  heat_score＝100×(0.30·ret_20d + 0.15·ret_5d + 0.20·breadth + 0.10·high20 + 0.10·vol + 0.15·inst)
        每項先轉成「同日、同層級」的百分位，所以是相對熱度；heat_rank 為同日同層級名次
限制：用「目前有效」的成分回算近 N 日，適合看排行與輪動；回測請改用 stock_theme 的 valid_from／valid_to 當時成分。
前置：schema_theme.sql、fetch_tpex_chain.py、theme_candidates.py 先跑過。

用法：
  python build_theme_daily.py                 # 算近 30 個交易日 → 寫入 theme_daily → 印排行
  python build_theme_daily.py --days 60       # 算近 60 個交易日
  python build_theme_daily.py --report-only   # 照樣計算、只印排行，不寫入 DB
"""
import argparse
import os
import sys

import pandas as pd

WEIGHTS = {"ret_20d": 0.30, "ret_5d": 0.15, "breadth_ma20": 0.20,
           "high20_pct": 0.10, "vol_ratio": 0.10, "inst_ratio": 0.15}
MIN_MEMBERS = 5


def clean_dsn(dsn):
    """容錯＋防呆：剝掉誤貼進值裡的旗標／引號，並先擋掉明顯不合法的連線字串。（同 nightly.py）"""
    dsn = (dsn or "").strip().strip('"').strip("'").strip()
    if dsn.startswith("--dsn"):                      # 誤把旗標本身貼進值裡
        dsn = dsn[5:].lstrip().lstrip("=").lstrip()
    if dsn and not (dsn.startswith(("postgresql://", "postgres://")) or "=" in dsn):
        raise SystemExit(f"連線字串格式不對：{dsn!r}；"
                         "應為 postgresql://user:pw@host:port/db（或 key=value 形式）")
    return dsn


def load_themes(cur):
    """回傳 {theme_id: (code, name, layer, {成分股})}；L2 節點含子節點成分、不含鏈本身。"""
    cur.execute("SELECT theme_id, code, name, layer, parent_code FROM theme WHERE is_active")
    rows = cur.fetchall()
    by_code = {code: (tid, name, layer, parent) for tid, code, name, layer, parent in rows}
    cur.execute("""SELECT st.theme_id, st.stock_id FROM stock_theme st JOIN theme t USING (theme_id)
                    WHERE st.valid_to IS NULL AND t.is_active
                      AND ((t.layer=2 AND st.status='confirmed') OR (t.layer=3 AND st.status IN ('confirmed','seed')))""")
    direct = {}
    for tid, sid in cur.fetchall():
        direct.setdefault(tid, set()).add(sid)
    members = {tid: set(direct.get(tid, ())) for tid, *_ in rows}
    for code, (tid, _, layer, parent) in by_code.items():          # 子節點成分往上併到節點
        if layer == 2 and parent and parent in by_code and by_code[parent][3] is not None:
            members[by_code[parent][0]] |= direct.get(tid, set())
    chain_name = {c: v[1] for c, v in by_code.items() if v[2] == 2 and v[3] is None}
    out = {}
    for code, (tid, name, layer, parent) in by_code.items():
        if layer == 2 and parent is None:                           # 鏈本身太粗（≈官方產業別），不排
            continue
        if layer == 2:                                              # 顯示時帶上所屬鏈：「鋼鐵 > 製管」
            name = f"{chain_name.get(':'.join(code.split(':')[:2]), '?')} > {name}"
        out[tid] = (code, name, layer, members[tid])
    cur.execute("""SELECT t.theme_id, count(*) FILTER (WHERE st.status='seed') FROM theme t
                     JOIN stock_theme st USING (theme_id) WHERE t.layer=3 AND st.valid_to IS NULL GROUP BY 1""")
    seeds = dict(cur.fetchall())
    return out, seeds


def load_panel(cur, days):
    """近 days+80 個交易日的寬表：還原價、原始收盤、成交額、法人淨買超（股）。"""
    cur.execute("SELECT DISTINCT trade_date FROM price_daily ORDER BY 1 DESC LIMIT %s", (days + 80,))
    dates = sorted(r[0] for r in cur.fetchall())
    cur.execute("""SELECT p.stock_id, p.trade_date, p.adj_close, p.close, p.amount
                     FROM price_daily p JOIN stock s USING (stock_id)
                    WHERE s.security_type='stock' AND p.trade_date >= %s""", (dates[0],))
    df = pd.DataFrame(cur.fetchall(), columns=["stock_id", "trade_date", "adj", "close", "amount"])
    wide = {c: df.pivot(index="trade_date", columns="stock_id", values=c).astype(float).sort_index()
            for c in ("adj", "close", "amount")}
    cur.execute("""SELECT stock_id, trade_date,
                          COALESCE(foreign_net,0)+COALESCE(foreign_dealer_net,0)+COALESCE(trust_net,0)
                          +COALESCE(dealer_self_net,0)+COALESCE(dealer_hedge_net,0)
                     FROM inst_trades WHERE trade_date >= %s""", (dates[0],))
    inst = pd.DataFrame(cur.fetchall(), columns=["stock_id", "trade_date", "net"])
    wide["inst"] = (inst.pivot(index="trade_date", columns="stock_id", values="net").astype(float)
                    .reindex(index=wide["adj"].index, columns=wide["adj"].columns).fillna(0.0))
    return wide


def compute(wide, themes, days):
    adj, close, amt = wide["adj"], wide["close"], wide["amount"].fillna(0.0)
    f = {
        "ret_1d": adj.pct_change(1, fill_method=None),
        "ret_5d": adj.pct_change(5, fill_method=None),
        "ret_20d": adj.pct_change(20, fill_method=None),
        "above": (adj > adj.rolling(20).mean()).astype(float),
        "high20": (adj >= adj.rolling(20).max()).astype(float),
        "amt5": amt.rolling(5).sum(),
        "amt20": amt.rolling(20).sum(),
        "inst20": (wide["inst"] * close).rolling(20).sum(),
    }
    universe = (adj.notna().rolling(60).sum() >= 60) & (f["amt20"] / 20 >= 5_000_000)
    keep_dates = adj.index[-days:]
    recs = []
    for tid, (code, name, layer, mem) in themes.items():
        cols = [s for s in mem if s in adj.columns]
        if len(cols) < MIN_MEMBERS:
            continue
        u = universe.loc[keep_dates, cols]
        n = u.sum(axis=1)
        m = lambda k: f[k].loc[keep_dates, cols].where(u)                      # noqa: E731
        amt20 = m("amt20").sum(axis=1)
        df = pd.DataFrame({
            "theme_id": tid, "layer": layer, "n_members": n,
            "ret_1d": m("ret_1d").mean(axis=1), "ret_5d": m("ret_5d").mean(axis=1),
            "ret_20d": m("ret_20d").mean(axis=1),
            "breadth_ma20": m("above").mean(axis=1), "high20_pct": m("high20").mean(axis=1),
            "vol_ratio": m("amt5").sum(axis=1) / (amt20 / 4),
            "inst_ratio": m("inst20").sum(axis=1) / amt20,
        })
        recs.append(df[df["n_members"] >= MIN_MEMBERS])
    out = pd.concat(recs).rename_axis("trade_date").reset_index()
    pct = out.groupby(["trade_date", "layer"])[list(WEIGHTS)].rank(pct=True)
    out["heat_score"] = 100 * sum(pct[k] * w for k, w in WEIGHTS.items())
    out["heat_rank"] = (out.groupby(["trade_date", "layer"])["heat_score"]
                        .rank(ascending=False, method="first").astype(int))
    return out, f, universe


def save(cur, out):
    from psycopg2.extras import execute_values
    cols = ["theme_id", "trade_date", "n_members", "ret_1d", "ret_5d", "ret_20d", "breadth_ma20",
            "high20_pct", "vol_ratio", "inst_ratio", "heat_score", "heat_rank"]
    cur.execute("DELETE FROM theme_daily WHERE trade_date = ANY(%s)", (list(out["trade_date"].unique()),))
    rows = [tuple(None if pd.isna(v) else (v.item() if hasattr(v, "item") else v) for v in r)
            for r in out[cols].itertuples(index=False)]
    execute_values(cur, f"INSERT INTO theme_daily ({','.join(cols)}) VALUES %s", rows, page_size=2000)
    return len(rows)


def pct(x):
    return "   -  " if pd.isna(x) else f"{x * 100:+6.1f}%"


def report(out, themes, seeds, f, universe, names, top_l2=15):
    last = out["trade_date"].max()
    dates = sorted(out["trade_date"].unique())
    prev = dates[-6] if len(dates) >= 6 else dates[0]
    cur = out[out["trade_date"] == last].set_index("theme_id")
    old = out[out["trade_date"] == prev].set_index("theme_id")["heat_rank"]

    def rows(layer, limit):
        d = cur[cur["layer"] == layer].sort_values("heat_rank").head(limit)
        print(f"{'名次':>4} {'變化':>4}  {'族群':<30}{'檔數':>4} {'1日':>7} {'5日':>7} {'20日':>7}"
              f" {'站上月線':>6} {'量比':>5} {'法人':>6} {'熱度':>5}")
        for tid, r in d.iterrows():
            code, name, _, _ = themes[tid]
            delta = old.get(tid)
            mv = "  新" if delta is None or pd.isna(delta) else f"{int(delta) - int(r['heat_rank']):+4d}"
            tag = "＊" if seeds.get(tid) else ""
            print(f"{int(r['heat_rank']):>4} {mv:>4}  {(name + tag)[:28]:<30}{int(r['n_members']):>4} "
                  f"{pct(r['ret_1d'])} {pct(r['ret_5d'])} {pct(r['ret_20d'])} "
                  f"{r['breadth_ma20'] * 100:>6.0f}% {r['vol_ratio']:>5.2f} {r['inst_ratio'] * 100:>+5.1f}% "
                  f"{r['heat_score']:>5.1f}")

    print(f"\n==================== 族群熱度排行　{last}（變化＝與 {prev} 相比的名次升降）====================")
    print("\n【L3 市場題材】（＊＝含起手名單尚未複核的成分）")
    rows(3, 99)
    print(f"\n【L2 產業鏈節點】前 {top_l2} 名（共 {int((cur['layer'] == 2).sum())} 個節點達 {MIN_MEMBERS} 檔門檻）")
    rows(2, top_l2)

    ma60 = f["_ma60"]
    print("\n【L3 題材前 3 名：領頭羊 vs 落後補漲候選】（落後＝20 日報酬低於族群中位數、但仍站上季線）")
    for tid in cur[cur["layer"] == 3].sort_values("heat_rank").head(3).index:
        code, name, _, mem = themes[tid]
        cols = [s for s in mem if s in f["ret_20d"].columns and universe.at[last, s]]
        r20 = f["ret_20d"].loc[last, cols].dropna().sort_values(ascending=False)
        lead = "、".join(f"{names.get(s, s)} {v * 100:+.0f}%" for s, v in r20.head(3).items())
        med = r20.median()
        adj_last = f["_adj"].loc[last]
        lag = [s for s in r20.index[::-1] if r20[s] < med and adj_last[s] > ma60.loc[last, s]][:3]
        lagtxt = "、".join(f"{names.get(s, s)} {r20[s] * 100:+.0f}%" for s in lag) or "（無：落後者都已跌破季線）"
        print(f"  {name}\n    領頭羊：{lead}\n    落後補漲候選：{lagtxt}")


def main():
    ap = argparse.ArgumentParser(description="計算每日族群熱度 → theme_daily，印排行")
    ap.add_argument("--dsn", default=os.environ.get("DATABASE_URL", ""), help="PostgreSQL 連線字串")
    ap.add_argument("--days", type=int, default=30, help="回算近幾個交易日（預設 30）")
    ap.add_argument("--top-l2", type=int, default=15, help="L2 節點排行印前幾名（預設 15）")
    ap.add_argument("--report-only", action="store_true", help="不寫 DB，只計算並印排行")
    args = ap.parse_args()
    args.dsn = clean_dsn(args.dsn)
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except (AttributeError, ValueError):
        pass
    if not args.dsn:
        raise SystemExit("需要 --dsn 或環境變數 DATABASE_URL")

    import psycopg2
    conn = psycopg2.connect(args.dsn)
    try:
        with conn, conn.cursor() as cur:
            themes, seeds = load_themes(cur)
            wide = load_panel(cur, args.days)
            out, f, universe = compute(wide, themes, args.days)
            f["_adj"], f["_ma60"] = wide["adj"], wide["adj"].rolling(60).mean()
            cur.execute("SELECT stock_id, name FROM stock")
            names = dict(cur.fetchall())
            if not args.report_only:
                n = save(cur, out)
                print(f"theme_daily 寫入 {n} 列（{out['trade_date'].min()} ~ {out['trade_date'].max()}，"
                      f"{out['theme_id'].nunique()} 個族群）")
        report(out, themes, seeds, f, universe, names, args.top_l2)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
