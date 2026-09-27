r"""backtest_etf_flow.py — 主動式 ETF 進出訊號回測：跟著買（或避開）有沒有超額報酬。說明見 主動ETF追蹤設計.md。

事件（同一檔、同一持股日 T，依 etf_flow 的 action；家數以投信計，同投信兩檔 ETF 只算一家）：
  buy2   共識買進   ≥2 家投信新建倉／加碼
  buy1   單家加碼   只有 1 家新建倉／加碼、沒有人賣
  new    新建倉     任一家新建倉
  sell2  共識賣出   ≥2 家投信出清／減碼
  sell1  單家減碼   只有 1 家出清／減碼、沒有人買
  exit   出清       任一家出清
  同股同訊號 10 個交易日內只算一次（同一波連續加碼不重複計數）。
進出場：持股資料收盤後才公布 → T+1 開盤買進，持有到 T+h 收盤（h＝5／10／20 個交易日），用還原價。
基準（同樣的進出場時點）：
  大盤     當日母體（普通股、滿 60 個交易日、20 日均成交額 ≥ 2,000 萬）等權平均
  持股籃   當日所有被涵蓋主動 ETF 持有的股票等權平均：扣掉「主動 ETF 本來就挑強勢股」的效果，
           看「加碼／減碼這個動作」本身有沒有額外資訊
另外看：T 收盤 → T+1 開盤的跳空（資訊是不是開盤就被反映）、每月超額的正負是否穩定、
        申購期（加碼的 ETF 當天單位數增加 > 1%）與非申購期、買進金額占 20 日均成交額的大小。
成本：來回約 0.6%（手續費＋證交稅）。表中報酬都未扣成本，超額要明顯大於 0.6% 才有實用價值。
t 值是把每個事件當獨立樣本算的；事件在時間上會重疊（同一週很多檔），實際顯著性比表上低，要搭配「每月穩定度」看。

用法：
  python backtest_etf_flow.py                 # 計算 → 寫入 etf_signal_backtest／etf_signal_event → 印報表
  python backtest_etf_flow.py --report-only   # 只印報表，不寫 DB
前置：schema_etf_holding.sql、fetch_active_etf.py --backfill（要有歷史持股）。
"""
import argparse
import os
import sys
from collections import defaultdict
from datetime import date, datetime

import numpy as np
import pandas as pd

# key: (名稱, 說明, 預期方向)；預期方向 +1＝應該漲贏基準，-1＝應該輸給基準
SIGNALS = {
    "buy2": ("共識買進", "≥2 家投信新建倉／加碼", 1),
    "buy1": ("單家加碼", "只有 1 家新建倉／加碼", 1),
    "new": ("新建倉", "任一家新建倉", 1),
    "sell2": ("共識賣出", "≥2 家投信出清／減碼", -1),
    "sell1": ("單家減碼", "只有 1 家出清／減碼", -1),
    "exit": ("出清", "任一家出清", -1),
}
HORIZONS = (5, 10, 20)
COOLDOWN = 10               # 同股同訊號 N 個交易日內只算一次
MIN_AMT = 20_000_000        # 母體：20 日均成交額門檻（同 backtest_patterns.py）
MIN_DAYS = 60               # 母體：至少交易 60 天
INFLOW = 0.01               # 加碼的 ETF 當天單位數增加 > 1% 視為申購期
PRICE_START = date(2025, 1, 1)   # 價格多載入幾個月，母體的 60 日門檻才算得出來
SCHEMA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "schema_etf_holding.sql")


def clean_dsn(dsn):
    """容錯＋防呆：剝掉誤貼進值裡的旗標／引號，並先擋掉明顯不合法的連線字串。（同 nightly.py）"""
    dsn = (dsn or "").strip().strip('"').strip("'").strip()
    if dsn.startswith("--dsn"):                      # 誤把旗標本身貼進值裡
        dsn = dsn[5:].lstrip().lstrip("=").lstrip()
    if dsn and not (dsn.startswith(("postgresql://", "postgres://")) or "=" in dsn):
        raise SystemExit(f"連線字串格式不對：{dsn!r}；"
                         "應為 postgresql://user:pw@host:port/db（或 key=value 形式）")
    return dsn


# ---------- 載入 ----------

def load_prices(cur):
    """回傳還原開盤價 O、還原收盤價 C、成交金額 A（日期 × 股票；只含普通股）。"""
    cur.execute("SELECT p.stock_id, p.trade_date, p.adj_open, p.adj_close, p.amount FROM price_daily p "
                "JOIN stock s USING (stock_id) WHERE p.trade_date >= %s "
                "AND COALESCE(s.security_type, 'stock') = 'stock'", (PRICE_START,))
    px = pd.DataFrame(cur.fetchall(), columns=["sid", "d", "o", "c", "amt"])
    wide = {k: px.pivot(index="d", columns="sid", values=k).astype(float) for k in ("o", "c", "amt")}
    return wide["o"], wide["c"], wide["amt"]


def load_events(cur):
    """每 (股票, 持股日) 一列：買進／賣出的投信、是否新建倉／出清、主動金額、買方 ETF 當天的單位數變動。"""
    cur.execute("SELECT f.stock_id, f.trade_date, e.issuer, f.action, f.active_amount, s.flow_k_units "
                "FROM etf_flow f JOIN etf_fund e USING (etf_id) "
                "JOIN etf_snapshot s ON s.etf_id = f.etf_id AND s.as_of = f.trade_date "
                "WHERE f.action IN ('new', 'add', 'exit', 'cut')")
    agg = defaultdict(lambda: {"buy": set(), "sell": set(), "new": False, "exit": False,
                               "buy_amt": 0.0, "sell_amt": 0.0, "inflow": 0.0})
    for sid, d, issuer, act, amt, ku in cur.fetchall():
        a = agg[(sid, d)]
        if act in ("new", "add"):
            a["buy"].add(issuer)
            a["buy_amt"] += float(amt or 0)
            a["inflow"] = max(a["inflow"], float(ku) - 1 if ku else 0.0)
        else:
            a["sell"].add(issuer)
            a["sell_amt"] += float(amt or 0)
        a["new"] |= act == "new"
        a["exit"] |= act == "exit"
    return agg


def load_basket(cur):
    """每個持股日：被任一涵蓋 ETF 持有（非佔位股）的股票集合。"""
    cur.execute("SELECT as_of, code FROM etf_holding WHERE kind = 'stock' AND weight >= 0.01")
    b = defaultdict(set)
    for d, code in cur.fetchall():
        b[d].add(code)
    return b


# ---------- 計算 ----------

def match(key, a):
    nb, ns = len(a["buy"]), len(a["sell"])
    return {"buy2": nb >= 2, "buy1": nb == 1 and ns == 0, "new": a["new"] and ns == 0,
            "sell2": ns >= 2, "sell1": ns == 1 and nb == 0, "exit": a["exit"] and nb == 0}[key]


def compute(O, C, A, agg, basket):
    """回傳 (事件表, 持股籃 vs 大盤的逐日表)。"""
    dates = list(C.index)
    pos = {d: i for i, d in enumerate(dates)}
    amt20 = A.rolling(20, min_periods=20).mean()
    hist = C.notna().astype(int).rolling(MIN_DAYS, min_periods=1).sum()
    uni = (amt20 >= MIN_AMT) & (hist >= MIN_DAYS)                 # 當日母體
    entry = O.shift(-1)                                            # T+1 開盤
    fwd = {h: C.shift(-h) / entry - 1 for h in HORIZONS}          # T+1 開盤 → T+h 收盤
    gap = entry / C - 1                                            # T 收盤 → T+1 開盤
    mkt = {h: fwd[h].where(uni).mean(axis=1) for h in HORIZONS}
    mkt_gap = gap.where(uni).mean(axis=1)
    bsk = {h: {} for h in HORIZONS}
    for d, codes in basket.items():
        cols = [c for c in codes if c in C.columns]
        if d in pos and cols:
            for h in HORIZONS:
                bsk[h][d] = fwd[h].loc[d, cols].mean()
    daily = pd.DataFrame({"trade_date": sorted(d for d in basket if d in pos)})   # 持股籃本身 vs 大盤
    for h in HORIZONS:
        daily[f"xm{h}"] = [bsk[h].get(d, np.nan) - mkt[h].get(d, np.nan) for d in daily["trade_date"]]

    events = []
    for key in SIGNALS:
        last = {}
        for (sid, d) in sorted(agg, key=lambda x: x[1]):
            a = agg[(sid, d)]
            if sid not in C.columns or d not in pos or not match(key, a):
                continue
            i = pos[d]
            if i - last.get(sid, -10 ** 9) < COOLDOWN:
                continue
            if pd.isna(entry.at[d, sid]):
                continue
            last[sid] = i
            buy_side = SIGNALS[key][2] > 0
            a20 = amt20.at[d, sid]
            side_amt = a["buy_amt"] if buy_side else a["sell_amt"]
            ev = {"signal": key, "stock_id": sid, "trade_date": d,
                  "n_issuers": len(a["buy"] if buy_side else a["sell"]),
                  "inflow": a["inflow"] > INFLOW,
                  "impact": side_amt / a20 if a20 and a20 > 0 else None,        # 主動金額 ÷ 20 日均成交額
                  "gap_ex": gap.at[d, sid] - mkt_gap.get(d, np.nan)}
            for h in HORIZONS:
                r = fwd[h].at[d, sid]
                ev[f"r{h}"] = r
                ev[f"xm{h}"] = r - mkt[h].get(d, np.nan)
                b = bsk[h].get(d)
                ev[f"xb{h}"] = (r - b) if b is not None else np.nan
            events.append(ev)
    return pd.DataFrame(events), daily


def basket_stats(daily, h):
    """持股籃 vs 大盤：每 h 日取一個不重疊樣本（逐日重疊的平均會高估樣本數）。"""
    x = daily.dropna(subset=[f"xm{h}"]).iloc[::h]
    if x.empty:
        return None
    month = x.assign(m=pd.to_datetime(x["trade_date"]).dt.to_period("M")).groupby("m")[f"xm{h}"].mean()
    sd = x[f"xm{h}"].std(ddof=1)
    return {"n": len(x), "avg_ret": None, "avg_excess_mkt": x[f"xm{h}"].mean(), "avg_excess_basket": None,
            "median_excess_basket": None, "win_excess_basket": (x[f"xm{h}"] > 0).mean(),
            "t_stat": x[f"xm{h}"].mean() / sd * np.sqrt(len(x)) if sd and len(x) > 2 else None,
            "months": len(month), "month_pos": int((month > 0).sum()), "gap_ex": None,
            "date_from": x["trade_date"].min(), "date_to": x["trade_date"].max()}


def stats(df, h):
    """一組事件在持有 h 日的統計（只取 h 日後已有價格的事件）。"""
    x = df.dropna(subset=[f"r{h}"])
    if x.empty:
        return None
    xb = x[f"xb{h}"].dropna()
    month = x.assign(m=pd.to_datetime(x["trade_date"]).dt.to_period("M")).groupby("m")[f"xb{h}"].mean().dropna()
    sd = xb.std(ddof=1)
    return {"n": len(x), "avg_ret": x[f"r{h}"].mean(), "avg_excess_mkt": x[f"xm{h}"].mean(),
            "avg_excess_basket": xb.mean(), "median_excess_basket": xb.median(),
            "win_excess_basket": (xb > 0).mean(), "t_stat": xb.mean() / sd * np.sqrt(len(xb)) if sd and len(xb) > 2 else None,
            "months": len(month), "month_pos": int((month > 0).sum()),
            "gap_ex": x["gap_ex"].mean()}


# ---------- 報表 ----------

def pct(v, d=2):
    return "   —" if v is None or pd.isna(v) else f"{v * 100:+.{d}f}%"


def report(ev, daily):
    print("\n=== 主動式 ETF 進出訊號回測（T+1 開盤進場、還原價、未扣成本）===")
    print(f"事件期間 {ev['trade_date'].min()} ~ {ev['trade_date'].max()}，同股同訊號 {COOLDOWN} 日內只算一次")
    print("超額（持股籃）＝減掉當天所有主動 ETF 持股的平均；買進訊號要正、賣出訊號要負才算有效\n")
    print(f"{'訊號':<8}{'持有':>4}{'事件':>6}{'報酬':>9}{'超額(大盤)':>11}{'超額(持股籃)':>12}{'中位數':>9}"
          f"{'勝率':>7}{'t值':>7}{'月份同向':>9}{'開盤跳空':>9}")
    for h in HORIZONS:
        s = basket_stats(daily, h)
        if s:
            print(f"{'持股籃本身':<5}{h:>5}日{s['n']:>6}{'':>10}{pct(s['avg_excess_mkt']):>11}{'':>12}{'':>10}"
                  f"{s['win_excess_basket'] * 100:>6.0f}%{s['t_stat']:>+7.1f}{s['month_pos']:>6}/{s['months']:<2}")
    print("（持股籃本身＝當天所有主動 ETF 持股等權平均 vs 大盤，每 h 日取一個不重疊樣本）")
    for key, (name, _, sign) in SIGNALS.items():
        sub = ev[ev.signal == key]
        for h in HORIZONS:
            s = stats(sub, h)
            if not s:
                continue
            win = s["win_excess_basket"] if sign > 0 else 1 - s["win_excess_basket"]
            mpos = s["month_pos"] if sign > 0 else s["months"] - s["month_pos"]
            months = f"{mpos}/{s['months']}"
            t = f"{s['t_stat']:+.1f}" if s["t_stat"] is not None else "—"
            print(f"{name:<6}{h:>5}日{s['n']:>6}{pct(s['avg_ret']):>10}{pct(s['avg_excess_mkt']):>11}"
                  f"{pct(s['avg_excess_basket']):>12}{pct(s['median_excess_basket']):>10}{win * 100:>6.0f}%{t:>7}"
                  f"{months:>9}{pct(s['gap_ex']):>10}")
    print("（勝率、月份同向：買進訊號算「贏過持股籃」的比例，賣出訊號算「輸給持股籃」的比例）")

    b = ev[ev.signal == "buy2"]
    if len(b):
        print("\n--- 共識買進：分組比較（持有 20 日，超額＝相對持股籃）---")
        groups = [("申購期（買方 ETF 單位數 +1% 以上）", b[b.inflow]), ("非申購期", b[~b.inflow])]
        imp = b["impact"].dropna()
        if len(imp) >= 10:
            cut = imp.median()
            groups += [(f"買進金額 ≥ 20 日均量 {cut * 100:.0f}%（中位數以上）", b[b["impact"] >= cut]),
                       (f"買進金額 < 20 日均量 {cut * 100:.0f}%", b[b["impact"] < cut])]
        groups += [("≥3 家投信", b[b.n_issuers >= 3])]
        yr = pd.to_datetime(b["trade_date"])
        for lo, hi, label in ((date(2025, 1, 1), date(2025, 12, 31), "2025 年"),
                              (date(2026, 1, 1), date(2026, 6, 30), "2026 上半年"),
                              (date(2026, 7, 1), date(2026, 12, 31), "2026 下半年")):
            groups.append((label, b[(yr >= pd.Timestamp(lo)) & (yr <= pd.Timestamp(hi))]))
        for label, g in groups:
            s = stats(g, 20)
            if s:
                print(f"  {label:<34} n={s['n']:>4}  超額 {pct(s['avg_excess_basket'])}  "
                      f"中位數 {pct(s['median_excess_basket'])}  勝率 {s['win_excess_basket'] * 100:.0f}%")


def _clean(v):
    """numpy／NaN → Python 原生型別或 None，psycopg2 才寫得進去。"""
    if v is None:
        return None
    if isinstance(v, (float, np.floating)):
        return None if np.isnan(v) else float(v)
    if isinstance(v, np.integer):
        return int(v)
    return v


COLS = ("signal", "horizon", "name", "note", "sign", "n", "avg_ret", "avg_excess_mkt", "avg_excess_basket",
        "median_excess_basket", "win_excess_basket", "t_stat", "months", "month_pos", "gap_ex",
        "date_from", "date_to", "computed_at")


def save(cur, ev, daily):
    """整批覆蓋 etf_signal_backtest（每訊號 × 持有期一列，另含 basket＝持股籃本身 vs 大盤）與 etf_signal_event。"""
    from psycopg2.extras import Json, execute_values
    cur.execute(open(SCHEMA, encoding="utf-8").read())          # 建表（全部 IF NOT EXISTS，可重複執行）
    stamp = datetime.now()
    rows = []
    for h in HORIZONS:
        s = basket_stats(daily, h)
        if s:
            rows.append({**s, "signal": "basket", "horizon": h, "name": "持股籃本身",
                         "note": "當天所有主動 ETF 持股等權平均 vs 大盤（不重疊取樣）", "sign": 1, "computed_at": stamp})
    for key, (name, note, sign) in SIGNALS.items():
        sub = ev[ev.signal == key]
        for h in HORIZONS:
            s = stats(sub, h) if len(sub) else None
            if s:
                x = sub.dropna(subset=[f"r{h}"])
                rows.append({**s, "signal": key, "horizon": h, "name": name, "note": note, "sign": sign,
                             "date_from": x["trade_date"].min(), "date_to": x["trade_date"].max(), "computed_at": stamp})
    cur.execute("DELETE FROM etf_signal_backtest")
    execute_values(cur, f"INSERT INTO etf_signal_backtest ({', '.join(COLS)}) VALUES %s",
                   [tuple(_clean(r.get(c)) for c in COLS) for r in rows])

    def js(r, prefix):
        return Json({h: round(float(r[f"{prefix}{h}"]), 5) for h in HORIZONS if not pd.isna(r[f"{prefix}{h}"])})
    cur.execute("DELETE FROM etf_signal_event")
    execute_values(cur, "INSERT INTO etf_signal_event (signal, stock_id, trade_date, n_issuers, inflow, impact, gap_ex, "
                        "rets, excess_mkt, excess_basket) VALUES %s",
                   [(r["signal"], r["stock_id"], r["trade_date"], int(r["n_issuers"]), bool(r["inflow"]),
                     _clean(r["impact"]), _clean(r["gap_ex"]), js(r, "r"), js(r, "xm"), js(r, "xb"))
                    for _, r in ev.iterrows()], page_size=2000)
    print(f"\n寫入 etf_signal_backtest {len(rows)} 列、etf_signal_event {len(ev)} 筆事件")


def main():
    ap = argparse.ArgumentParser(description="主動式 ETF 進出訊號回測 → etf_signal_backtest／etf_signal_event")
    ap.add_argument("--dsn", default=os.environ.get("DATABASE_URL", ""), help="PostgreSQL 連線字串")
    ap.add_argument("--report-only", action="store_true", help="只印報表，不寫 DB")
    args = ap.parse_args()
    args.dsn = clean_dsn(args.dsn)
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
            O, C, A = load_prices(cur)
            agg = load_events(cur)
            basket = load_basket(cur)
            ev, daily = compute(O, C, A, agg, basket)
            if ev.empty:
                raise SystemExit("沒有事件：先跑 fetch_active_etf.py --backfill")
            report(ev, daily)
            if not args.report_only:
                save(cur, ev, daily)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
