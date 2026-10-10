r"""支撐轉強、W 底測右底（兩份個案文件的規則）全市場回測，附文件要求的對照組與參數穩健性。

每個訊號日只用當日以前的資料，偵測直接呼叫 app.support_reclaim／app.w_bottom（每天選股用同一份程式）。
成交用 execution.simulate：盤中突破版＝隔日最高價到觸發價才買（開盤已高於觸發價用開盤價）；
收盤確認版＝確認日隔日開盤買。每邊 0.1% 滑價、0.6% 來回成本；停損盤中觸及即出（買進當天又碰到停損算停損），
收盤「跌破」10 日線隔日開盤出（進場時在線下的要先站上才算），最長 20 個交易日。
另跑兩個敏感度：買進當天碰停損改看開盤離高低點遠近、W 底改成收在 10 日線下就出。
對照組照文件：只看 20 日漲幅、只看收盤創 10 日新高、只用趨勢＋整理（沒有 K 線條件）、
W 底純價格版（拿掉量縮）、只看強漲後回檔（沒有 W 底條件）；另做參數穩健性與營收條件。
「抱20日超額」不設停損、未扣成本，只看選股本身有沒有優勢；「＋動能出場」是接進決策中心時的出場
（實際成交價下 8% 停損、20 日到期），和文件的出場分開比較。
事件研究允許訊號重疊；連續組合照決策中心的部位規則（單筆風險 0.75%、總風險 4%、最多 10 檔、
同產業 2 檔、單檔 25%），同日多檔觸發依 20 日成交金額排序（盤中先後不可知的近似）。

限制：目前主檔的存活者偏差；日 K 無法知道盤中先後；這段歷史含設計規則用的個案，不是樣本外證據。

在 backend 目錄用有 pandas 的 python 執行（訊號截止日預設同決策中心回測：最後 60 個交易日前）：
  python backtest_setups.py --start 2024-07-01 --workers 6                 # 與決策中心回測同期
  python backtest_setups.py --start 2011-01-01 --workers 6 --tag long      # 2011 年起
"""
import argparse
import io
import json
import math
import multiprocessing as mp
import sys
import time
from datetime import date, timedelta
from pathlib import Path

COST = 0.006
LEAD, TAIL = 12, 45              # 給 simulate 的 K 棒：觀察日前 12 根（10 日線）到之後 45 根
WARMUP_DAYS = 60                 # 25 根 K 棒的暖機（日曆天）
CAPITAL = 1_000_000
UNIVERSE_AMT = 20_000_000        # 超額基準的母體：同決策中心回測（20 日均額 ≥ 2,000 萬、近 275 日 ≥ 60 根）
MAIN = ("s1", "s1_close", "s1_nearest", "s1_rev", "s1_loose", "s1_pre", "base_gain20", "base_high10",
        "s2", "s2_v2", "s2_nearest", "s2_level", "s2_rev", "s2_price", "base_pullback",
        "s1_mom", "s1_loose_mom", "s1_pre_mom", "s2_mom", "base_pullback_mom")
MOMENTUM_STOP = 0.08             # 決策中心動能模式：實際成交價下 8% 停損、20 日到期、不看 10 日線
COLUMNS = ["variant", "stock_id", "signal_date", "observed_date", "status", "reason", "entry_date", "exit_date",
           "exit_timing", "ret", "r", "days", "risk", "entry", "entry_raw", "anchor", "amt20"]
# 輸出另加 excess（同期母體超額）、hold20（不設停損抱 20 日的超額，未扣成本）、regime（加權指數在 60 日線上）


def variants():
    """要比較的規則。kind＝偵測方式；entry＝成交方式；trade＝觸發價／停損的算法（s1：停損＝當日低點、s2：低點下一檔）。"""
    from app import support_reclaim as sr, w_bottom as wb
    S1, S2 = sr.PARAMS, wb.PARAMS
    out = []

    def add(key, name, family, kind, params, group="main", entry="buy_stop", rev=None, **trade):
        risk, gap = (S1["max_risk"], None) if family == "S1" else (S2["max_risk"], S2["max_gap"])
        out.append({"key": key, "name": name, "family": family, "kind": kind, "params": params, "group": group,
                    "entry": entry, "rev": rev, "max_risk": trade.get("max_risk", risk),
                    "max_gap": trade.get("max_gap", gap) if entry == "buy_stop" else None,
                    "ma_cross": trade.get("ma_cross", True), "order": trade.get("order"),
                    "momentum": trade.get("momentum", False)})

    add("s1", "支撐轉強（文件規則）", "S1", "s1", S1)
    add("s1_close", "支撐轉強・收盤確認版（t+1 收盤過 B 高，t+2 開盤買）", "S1", "s1", S1, entry="s1_close")
    add("s1_nearest", "支撐轉強・買進當天碰停損改看開盤離高低點遠近", "S1", "s1", S1, order="nearest")
    add("s1_rev", "支撐轉強＋月營收年增 > 50%", "S1", "s1", S1, rev=50)
    add("s1_loose", "支撐轉強・不要求 A 回測支撐與放量", "S1", "s1", {**S1, "support_tol": 9.0, "test_volume": 0.0})
    add("s1_pre", "對照：趨勢＋強度＋整理（沒有 K 線條件）", "S1", "pre", S1)
    add("base_gain20", "對照：只看 20 日漲幅 ≥ 10%", "S1", "gain20", S1)
    add("base_high10", "對照：只看收盤創 10 日新高", "S1", "high10", S1)
    for k in (8, 10, 12, 15):
        for near in (0.04, 0.05, 0.06, 0.07):
            if (k, near) != (10, 0.05):
                add(f"s1_box{k}_near{round(near * 100)}", f"整理 {k} 日・離高點 {near:.0%}", "S1", "s1",
                    {**S1, "box_days": k, "near_high": near}, group="grid")
    for key, name, change in (("tol2", "A 低點離支撐 ±2%", {"support_tol": 0.02}),
                              ("vol12", "A 量 ≥ 1.2 倍", {"test_volume": 1.2}),
                              ("vol20", "A 量 ≥ 2 倍", {"test_volume": 2.0}),
                              ("gain5", "20 日漲幅 ≥ 5%", {"min_gain20": 0.05}),
                              ("gain15", "20 日漲幅 ≥ 15%", {"min_gain20": 0.15}),
                              ("range12", "整理區間 ≤ 12%", {"max_box_range": 0.12}),
                              ("range20", "整理區間 ≤ 20%", {"max_box_range": 0.20})):
        add(f"s1_{key}", name, "S1", "s1", {**S1, **change}, group="robust")

    add("s2", "W 底測右底・版本 1（文件規則）", "S2", "s2", S2)
    add("s2_v2", "W 底測右底・版本 2（t+1 收盤突破頸線且放量，t+2 開盤買）", "S2", "s2", S2, entry="s2_v2", max_risk=None)
    add("s2_nearest", "W 底・買進當天碰停損改看開盤離高低點遠近", "S2", "s2", S2, order="nearest")
    add("s2_level", "W 底・收盤在 10 日線下就出（不必先站上）", "S2", "s2", S2, ma_cross=False)
    add("s2_rev", "W 底測右底＋月營收年增 ≥ 30%", "S2", "s2", S2, rev=30)
    add("s2_price", "W 底純價格版（拿掉量縮）", "S2", "s2", {**S2, "dry_volume": 1e9})
    add("base_pullback", "對照：強漲後回檔 8～15%（沒有 W 底條件）", "S2", "pullback", S2)
    for key, name, change in (("pull6", "回檔 6～15%", {"pull_min": 0.06}),
                              ("pull20", "回檔 8～20%", {"pull_max": 0.20}),
                              ("right2", "右底 −2%～+3%", {"right_min": -0.02}),
                              ("right5", "右底 0～+5%", {"right_max": 0.05}),
                              ("neck4", "頸線反彈 ≥ 4%", {"min_rebound": 0.04}),
                              ("neck8", "頸線反彈 ≥ 8%", {"min_rebound": 0.08}),
                              ("dry07", "量 ≤ 0.7 倍", {"dry_volume": 0.7}),
                              ("dry10", "量 ≤ 1 倍", {"dry_volume": 1.0}),
                              ("strong7", "強漲日 ≥ 7%", {"strong_day": 0.07}),
                              ("strong95", "強漲日 ≥ 9.5%", {"strong_day": 0.095}),
                              ("left15", "左底在 t−15～t−5", {"left_from": 15}),
                              ("left10", "左底在 t−10～t−4", {"left_from": 10, "left_to": 4})):
        add(f"s2_{key}", name, "S2", "s2", {**S2, **change}, group="robust")
    add("s2_nogap", "不設開盤高於觸發價 2% 的上限", "S2", "s2", S2, group="robust", max_gap=None)
    # 接進決策中心時用的是系統動能出場，不是文件的出場：同樣的進場，改成實際成交價下 8% 停損、20 日到期
    for key, base in (("s1", "s1"), ("s1_loose", "s1_loose"), ("s1_pre", "s1_pre"), ("s2", "s2"),
                      ("base_pullback", "base_pullback")):
        v = next(x for x in out if x["key"] == base)
        out.append({**v, "key": f"{key}_mom", "name": v["name"] + "＋動能出場（8% 停損、20 日）",
                    "max_risk": None, "momentum": True})
    return out


# ───────────────────────── 工作程序：逐檔逐日偵測＋成交 ─────────────────────────
def _connect():
    import psycopg2
    from app.config import DATABASE_URL
    return psycopg2.connect(DATABASE_URL)


def _bars(ids, since):
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT stock_id,trade_date,adj_open::float8,adj_high::float8,adj_low::float8,adj_close::float8,"
                "open::float8,high::float8,low::float8,close::float8,volume::float8,amount::float8 "
                "FROM price_daily WHERE stock_id=ANY(%s) AND trade_date>=%s ORDER BY stock_id,trade_date", (ids, since))
            rows = cur.fetchall()
    finally:
        conn.close()
    keys = ("trade_date", "open", "high", "low", "close", "raw_open", "raw_high", "raw_low", "raw_close", "volume", "amount")
    grouped = {}
    for row in rows:
        grouped.setdefault(row[0], []).append(dict(zip(keys, row[1:])))
    return grouped


def _bar_plan(s, t, family):
    """對照組沒有型態：觸發價＝當日最高價上一檔，停損＝當日低點（S2 類再下一檔）。"""
    from app import support_reclaim as sr
    from app.execution import tick_down, tick_up, to_tick
    f = sr.raw_factor(s, t)
    high, low = to_tick(s["high"][t] / f), to_tick(s["low"][t] / f)
    return {"trigger": tick_up(high), "stop": low if family == "S1" else tick_down(low), "factor": f}


def _detect(v, s, t, sr, wb):
    kind, p = v["kind"], v["params"]
    if kind == "s1":
        return sr.setup(s, t, p)
    if kind == "s2":
        return wb.setup(s, t, p)
    c = s["close"]
    if kind == "pre":
        return sr.prefilter(s, t, p) and _bar_plan(s, t, "S1")
    if kind == "gain20":
        return (sum(s["amount"][t - 19:t + 1]) / 20 >= p["min_amt20"] and c[t] / c[t - 20] - 1 >= p["min_gain20"]
                and _bar_plan(s, t, "S1"))
    if kind == "high10":
        return (sum(s["amount"][t - 19:t + 1]) / 20 >= p["min_amt20"] and c[t] >= max(c[t - 9:t + 1])
                and _bar_plan(s, t, "S1"))
    if kind == "pullback":
        v20 = sum(s["volume"][t - 19:t + 1]) / 20
        pull = 1 - c[t] / max(c[t - 19:t + 1])
        return (v20 >= p["min_vol20"] and p["pull_min"] <= pull <= p["pull_max"]
                and any(c[i] / c[i - 1] - 1 >= p["strong_day"] for i in range(t - 19, t + 1))
                and _bar_plan(s, t, "S2"))
    raise ValueError(kind)


def _trade(v, s, t, plan, bars, calendar, cal_pos, sr, wb, execution):
    """回傳 (觀察日位置, simulate 結果)；收盤確認版沒確認回 (None, 原因)。"""
    if v["entry"] == "buy_stop":
        k = t
        spec = {"entry_type": "buy_stop", "trigger": plan["trigger"], "stop": plan["stop"],
                "max_gap_pct": v["max_gap"]}
    else:
        if t + 1 >= len(bars):
            return None, "資料到此為止"
        ok = sr.confirmed(s, t) if v["entry"] == "s1_close" else wb.confirmed(s, t, plan, v["params"])
        if not ok:
            return None, "隔日收盤沒有確認"
        k = t + 1
        # 停損是 t 日原始價；換到確認日（觀察日）的原始價座標
        spec = {"stop": plan["stop"] * plan["factor"] / sr.raw_factor(s, k)}
    ci = cal_pos[s["date"][k]]
    spec.update(observed_date=s["date"][k], stop_pct=None, max_risk_pct=v["max_risk"], exit_ma=sr.EXIT_MA,
                exit_ma_cross=v["ma_cross"], intraday_order=v["order"], horizon=sr.HORIZON, cost_pct=COST,
                slippage=execution.SLIPPAGE)
    if v["momentum"]:
        spec.update(stop_pct=MOMENTUM_STOP, exit_ma=0)
    return k, execution.simulate(bars[max(0, k - LEAD):k + TAIL], spec, calendar[ci:ci + TAIL])


def run_chunk(task):
    from app import execution, support_reclaim as sr, w_bottom as wb
    from backtest_decision_center import rev_at
    V = variants()
    s1_kinds = [v for v in V if v["kind"] == "s1"]
    others = [v for v in V if v["kind"] != "s1"]
    calendar = task["calendar"]
    cal_pos = {d: i for i, d in enumerate(calendar)}
    lo, hi = task["sig_start"], task["sig_end"]
    out = []
    for sid, bars in _bars(task["ids"], task["since"]).items():
        s = sr.series(bars)
        o, l, c, dates = s["open"], s["low"], s["close"], s["date"]
        rev = task["rev"].get(sid, [])
        for t in range(sr.BARS_NEEDED - 1, len(bars)):
            if not lo <= dates[t] <= hi:
                continue
            a = t - 1
            # 所有 s1 類都要 A 收黑、B 收紅、B 低點高於 A、B 收盤過 A 實體中點；先擋掉
            candle = c[t] > o[t] and c[a] < o[a] and l[t] > l[a] and c[t] > (o[a] + c[a]) / 2
            for v in (s1_kinds + others) if candle else others:
                plan = _detect(v, s, t, sr, wb)
                if not plan:
                    continue
                if v["rev"] is not None:
                    yoy = rev_at(rev, dates[t])
                    if yoy is None or not (yoy > v["rev"] if v["family"] == "S1" else yoy >= v["rev"]):
                        continue
                k, fill = _trade(v, s, t, plan, bars, calendar, cal_pos, sr, wb, execution)
                amt20 = sum(s["amount"][t - 19:t + 1]) / 20
                if k is None:
                    out.append((v["key"], sid, dates[t], None, "unconfirmed", fill) + (None,) * 10 + (amt20,))
                    continue
                risk = ((fill["actual_entry"] - fill["actual_stop"]) / fill["actual_entry"]
                        if fill["actual_entry"] and fill["actual_stop"] else None)
                out.append((v["key"], sid, dates[t], dates[k], fill["status"], fill.get("reason"),
                            fill["entry_date"], fill["exit_date"], fill.get("exit_timing"), fill["net_return"],
                            fill["outcome_r"], fill["days"], risk, fill["actual_entry"], fill["actual_entry_raw"],
                            execution.factor(bars[k]), amt20))
    return out


# ───────────────────────── 主程序 ─────────────────────────
def copy_frame(sql):
    """大量讀取用 COPY，比逐列 fetch 省記憶體。sql 只放程式組出的日期，不接外部輸入。"""
    import pandas as pd
    buf = io.StringIO()
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.copy_expert(f"COPY ({sql}) TO STDOUT WITH CSV HEADER", buf)
    finally:
        conn.close()
    buf.seek(0)
    return pd.read_csv(buf, parse_dates=["trade_date"], dtype={"stock_id": str})


def nw_t(x, lag=20):
    from backtest_decision_center import nw_t as _nw
    return _nw(x, lag=lag)


def event_stats(g, n_signals, unconfirmed=0):
    """一組規則的事件研究；g＝全部訊號紀錄（含沒成交的）。"""
    import numpy as np
    import pandas as pd
    done = g[g["ret"].notna()]
    skipped = g[g["status"] == "skipped"]
    reasons = skipped["reason"].fillna("")
    no_trigger = int(reasons.str.contains("沒有漲到觸發價").sum())
    row = {"候選": int(n_signals), "沒確認": int(unconfirmed), "沒觸發": no_trigger,
           "風險／跳空放棄": int(reasons.str.contains("超過上限|放棄").sum()),
           "其他略過": int(len(skipped) - no_trigger - reasons.str.contains("超過上限|放棄").sum()),
           "成交": int(len(done))}
    if not len(done):
        return row
    gains, losses = done.loc[done["ret"] > 0, "ret"].sum(), -done.loc[done["ret"] < 0, "ret"].sum()
    daily = done.groupby("observed_date")["excess"].mean()
    dt = pd.to_datetime(done["observed_date"])
    half = dt.dt.year.astype(str) + np.where(dt.dt.month <= 6, "H1", "H2")
    hx = done.groupby(half.to_numpy())["excess"].mean()
    row.update({
        "每筆淨報酬%": round(done["ret"].mean() * 100, 2), "中位數%": round(done["ret"].median() * 100, 2),
        "勝率%": round((done["ret"] > 0).mean() * 100, 1), "平均R": round(done["r"].mean(), 3),
        "停損%": round((done["status"] == "loss").mean() * 100, 1),
        "破10日線%": round((done["status"] == "trend").mean() * 100, 1),
        "到期%": round((done["status"] == "timeout").mean() * 100, 1),
        "平均天數": round(done["days"].mean(), 1), "平均風險%": round(done["risk"].mean() * 100, 2),
        "獲利因子": round(gains / losses, 2) if losses > 0 else None,
        "超額%": round(done["excess"].mean() * 100, 2), "NW t": round(nw_t(daily.to_numpy()), 2),
        "抱20日超額%": round(done["hold20"].mean() * 100, 2), "抱20日超額中位數%": round(done["hold20"].median() * 100, 2),
        "超額為負的半年": f"{int((hx < 0).sum())}/{len(hx)}", "最差%": round(done["ret"].min() * 100, 1)})
    return row


def portfolio(trades, calendar, panels, industry, start, end):
    """決策中心的部位規則：單筆風險 0.75%、總風險 4%、最多 10 檔、同產業 2 檔、單檔 25%。"""
    from app import research_results
    trades = trades.sort_values(["entry_date", "amt20"], ascending=[True, False])
    by_entry = {d: g for d, g in trades.groupby("entry_date")}
    prices = {}                                  # 只展開有交易的股票：{股票: ({日: 開}, {日: 收})}
    cash, held, curve, taken, skipped = float(CAPITAL), {}, [], 0, 0

    def mark(pos, d, field):
        px = prices[pos["sid"]][field].get(d)
        if px == px and px is not None:
            pos["mark"] = px
        return pos["notional"] * pos["mark"] / pos["adj_entry"]

    def close(pos):
        nonlocal cash
        cash += pos["notional"] * (1 + pos["ret"] + COST / 2)
        del held[pos["sid"]]

    for d in calendar:
        if d <= start:
            continue
        for pos in [p for p in held.values() if p["exit_date"] == d and p["timing"] == "open"]:
            close(pos)
        for tr in by_entry.get(d, trades.iloc[:0]).itertuples():
            if tr.stock_id in held:
                skipped += 1
                continue
            equity = cash + sum(mark(p, d, 0) for p in held.values())
            budget = max(0.0, min(equity * 0.0075, equity * 0.04 - sum(p["risk"] for p in held.values())))
            raw = tr.entry_raw
            shares = math.floor(min(budget / (raw * tr.risk), equity * 0.25 / raw, cash / (raw * (1 + COST / 2))))
            ind = industry.get(tr.stock_id, "其他")
            if shares <= 0 or len(held) >= 10 or sum(p["industry"] == ind for p in held.values()) >= 2:
                skipped += 1
                continue
            if tr.stock_id not in prices:
                prices[tr.stock_id] = (panels["open"][tr.stock_id].to_dict(), panels["close"][tr.stock_id].to_dict())
            notional = shares * raw
            cash -= notional * (1 + COST / 2)
            adj_entry = tr.entry * tr.anchor
            held[tr.stock_id] = {"sid": tr.stock_id, "industry": ind, "notional": notional, "risk": notional * tr.risk,
                                 "ret": tr.ret, "exit_date": tr.exit_date, "timing": tr.exit_timing,
                                 "adj_entry": adj_entry, "mark": adj_entry}
            taken += 1
        for pos in [p for p in held.values() if p["exit_date"] == d]:
            close(pos)
        value = sum(mark(p, d, 1) for p in held.values())
        curve.append({"date": d, "equity": cash + value, "exposure": value / (cash + value)})
        if d > end and not held:
            break
    out = research_results.metrics(curve, CAPITAL)
    out.update(trades=taken, skipped=skipped, mean_exposure=sum(r["exposure"] for r in curve) / len(curve) if curve else 0)
    return out, curve


def clean(value):
    """NaN／inf → None，numpy 數字 → Python 數字，讓 JSON 可讀。"""
    if isinstance(value, dict):
        return {k: clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [clean(v) for v in value]
    if hasattr(value, "item") and not isinstance(value, (str, bytes)):
        value = value.item()
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def main():
    ap = argparse.ArgumentParser(description="支撐轉強／W 底測右底全市場回測")
    ap.add_argument("--start", default="2024-07-01", help="訊號起日")
    ap.add_argument("--end", default="", help="訊號截止日（預設同決策中心回測：最後 60 個交易日前）")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--tag", default="v1", help="輸出檔名後綴")
    ap.add_argument("--limit", type=int, default=0, help="只跑前 N 檔（測試用）")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    import numpy as np
    import pandas as pd
    from app import db, support_reclaim as sr, w_bottom as wb

    t0 = time.time()
    sig_start = date.fromisoformat(args.start)
    since = sig_start - timedelta(days=WARMUP_DAYS)
    calendar = [r["d"] for r in db.query(
        "SELECT DISTINCT trade_date AS d FROM price_daily WHERE trade_date>=%(d)s ORDER BY 1", {"d": since - timedelta(days=500)})]
    sig_end = date.fromisoformat(args.end) if args.end else calendar[-61]
    meta = {r["stock_id"]: r for r in db.query("SELECT stock_id,name,industry FROM stock WHERE security_type='stock'")}
    ids = sorted(meta)[:args.limit] if args.limit else sorted(meta)
    rev = {}
    for r in db.query("SELECT stock_id,revenue_month,available_date,yoy_pct FROM monthly_revenue WHERE revenue_month>=%(d)s",
                      {"d": since - timedelta(days=120)}):
        m = r["revenue_month"]
        avail = r["available_date"] or date(m.year + (m.month == 12), m.month % 12 + 1, 10)
        rev.setdefault(r["stock_id"], []).append((avail, m, None if r["yoy_pct"] is None else float(r["yoy_pct"])))
    V = variants()
    print(f"訊號 {sig_start} ~ {sig_end}；{len(ids)} 檔、{len(V)} 組規則", flush=True)

    chunks = [ids[i::args.workers * 6] for i in range(args.workers * 6)]
    tasks = [{"ids": ch, "since": since, "sig_start": sig_start, "sig_end": sig_end, "calendar": calendar,
              "rev": {s: rev.get(s, []) for s in ch}} for ch in chunks if ch]
    records = []
    with mp.Pool(max(1, args.workers)) as pool:
        for i, rows in enumerate(pool.imap_unordered(run_chunk, tasks), 1):
            records.extend(rows)
            if i % 6 == 0 or i == len(tasks):
                print(f"  掃描 {i}/{len(tasks)} 組（{time.time() - t0:.0f}s，紀錄 {len(records):,}）", flush=True)
    R = pd.DataFrame(records, columns=COLUMNS)

    # 超額＝同日母體（20 日均額 ≥ 2,000 萬）在實際進場日開盤到出場日收盤的等權報酬
    px = copy_frame("SELECT p.stock_id,p.trade_date,p.adj_open,p.adj_close,p.amount FROM price_daily p "
                    f"JOIN stock s USING (stock_id) WHERE s.security_type='stock' AND p.trade_date>='{since - timedelta(days=420)}'")
    px["trade_date"] = px["trade_date"].dt.date
    A = px.pivot(index="trade_date", columns="stock_id", values="adj_close").sort_index()
    O = px.pivot(index="trade_date", columns="stock_id", values="adj_open").reindex_like(A)
    AMT = px.pivot(index="trade_date", columns="stock_id", values="amount").reindex_like(A)
    universe = (A.notna().rolling(275, min_periods=1).sum() >= 60) & (AMT.rolling(20, min_periods=1).mean() >= UNIVERSE_AMT) & A.notna()
    a_np, o_np, u_np = A.to_numpy(), O.to_numpy(), universe.to_numpy()
    row = {d: i for i, d in enumerate(A.index)}
    cache = {}

    def bench(r):
        key = (r.observed_date, r.entry_date, r.exit_date)
        if key not in cache:
            with np.errstate(divide="ignore", invalid="ignore"):
                ret = a_np[row[r.exit_date]] / o_np[row[r.entry_date]] - 1
            cache[key] = np.nanmean(np.where(u_np[row[r.observed_date]] & np.isfinite(ret), ret, np.nan))
        return cache[key]

    done = R["ret"].notna()
    R["excess"] = np.nan
    R.loc[done, "excess"] = R.loc[done, "ret"].to_numpy() - np.array([bench(r) for r in R[done].itertuples()])
    # 不管停損與出場、未扣成本：成交後第 20 個交易日收盤相對成交價，減同期母體；看選股本身有沒有優勢
    a_ff = A.ffill().to_numpy()
    col = {sid: j for j, sid in enumerate(A.columns)}
    hold = {}

    def hold20(r):
        i = row.get(r.entry_date)
        if i is None or i + 19 >= len(a_ff) or r.stock_id not in col:
            return np.nan
        key = (r.observed_date, i)
        if key not in hold:
            with np.errstate(divide="ignore", invalid="ignore"):
                ret = a_np[i + 19] / o_np[i] - 1
            hold[key] = np.nanmean(np.where(u_np[row[r.observed_date]] & np.isfinite(ret), ret, np.nan))
        return a_ff[i + 19, col[r.stock_id]] / (r.entry * r.anchor) - 1 - hold[key]

    filled = R["entry_date"].notna() & R["entry"].notna()
    R["hold20"] = np.nan
    R.loc[filled, "hold20"] = [hold20(r) for r in R[filled].itertuples()]
    idx = pd.DataFrame(db.query("SELECT trade_date,close FROM market_index WHERE index_id='TWSE' ORDER BY trade_date"))
    idx = idx.set_index("trade_date")["close"].astype(float)
    above = (idx > idx.rolling(60).mean()).to_dict()
    R["regime"] = R["observed_date"].map(lambda d: None if d is None else above.get(d))
    print(f"超額與市況完成（{time.time() - t0:.0f}s）", flush=True)

    panels = {"open": O, "close": A}
    industry = {sid: (m.get("industry") or "其他") for sid, m in meta.items()}
    first_entry = next(d for d in calendar if d > sig_start)
    bench0050 = [r for r in db.query("SELECT trade_date,adj_open,adj_close FROM price_daily WHERE stock_id='0050' "
                                     "AND trade_date>=%(d)s ORDER BY trade_date", {"d": first_entry})]
    rows, report = [], []
    from app import research_results
    for v in V:
        g = R[R["variant"] == v["key"]]
        unconfirmed = int((g["status"] == "unconfirmed").sum())
        stats = event_stats(g[g["status"] != "unconfirmed"], len(g), unconfirmed)
        done_g = g[g["ret"].notna()]
        entry = {"key": v["key"], "name": v["name"], "family": v["family"], "group": v["group"], "event": stats}
        if v["group"] == "main" and len(done_g):
            years = pd.to_datetime(done_g["observed_date"]).dt.year
            entry["by_year"] = {int(y): {"n": int(len(x)), "mean_r": round(float(x["r"].mean()), 3),
                                         "mean_ret_pct": round(float(x["ret"].mean()) * 100, 2),
                                         "excess_pct": round(float(x["excess"].mean()) * 100, 2)}
                                for y, x in done_g.groupby(years)}
            entry["by_regime"] = {("above" if k else "below"): {"n": int(len(x)), "mean_r": round(float(x["r"].mean()), 3),
                                                                "mean_ret_pct": round(float(x["ret"].mean()) * 100, 2),
                                                                "win_rate": round(float((x["ret"] > 0).mean()) * 100, 1)}
                                  for k, x in done_g.groupby("regime")}
            perf, curve = portfolio(done_g, calendar, panels, industry, sig_start, sig_end)
            entry["portfolio"] = perf
            entry["curve"] = [{"date": r["date"], "equity": round(r["equity"])} for r in curve[::5]]
        rows.append({"規則": v["name"], **stats,
                     **({"組合總報酬%": round(entry["portfolio"]["total_return"] * 100, 1),
                         "組合最大回撤%": round(entry["portfolio"]["max_drawdown"] * 100, 1),
                         "組合交易": entry["portfolio"]["trades"]} if entry.get("portfolio") else {})})
        report.append(entry)

    pd.set_option("display.width", 320)
    pd.set_option("display.max_columns", 40)
    pd.set_option("display.unicode.east_asian_width", True)
    table = pd.DataFrame(rows).set_index("規則")
    keys = [v["group"] for v in V]
    for group, title in (("main", "主要規則與對照組"), ("grid", "支撐轉強：整理天數 × 離高點"), ("robust", "單一參數變動")):
        sub = table[[k == group for k in keys]]
        cols = [c for c in sub.columns if sub[c].notna().any()]
        print(f"\n【{title}】訊號 {sig_start} ~ {sig_end}；每筆已扣 0.6% 來回成本與每邊 0.1% 滑價\n" + sub[cols].to_string())
    for entry in report:
        if entry.get("by_regime"):
            print(f"  {entry['name']}：市況 {entry['by_regime']}；逐年平均 R "
                  f"{ {y: x['mean_r'] for y, x in entry['by_year'].items()} }")

    benchmark = {}
    if bench0050:
        entry_px = float(bench0050[0]["adj_open"]) * 1.001
        curve0050 = [{"date": r["trade_date"], "equity": CAPITAL * (float(r["adj_close"]) * 0.999 / entry_px - 0.00385)}
                     for r in bench0050 if r["trade_date"] <= sig_end + timedelta(days=40)]
        benchmark = {"name": "0050（還原價、扣 ETF 成本與滑價）", **research_results.metrics(curve0050, CAPITAL)}
        print(f"\n0050 同期：總報酬 {benchmark['total_return']:.1%}、最大回撤 {benchmark['max_drawdown']:.1%}")
    result = {"computed_at": time.strftime("%Y-%m-%d %H:%M"), "signal_start": sig_start, "signal_end": sig_end,
              "data_end": calendar[-1], "stocks": len(ids), "variants": report, "benchmark": benchmark,
              "rules": {"support_reclaim": sr.PARAMS, "w_bottom": wb.PARAMS,
                        "exit": "停損盤中觸及即出；收盤跌破 10 日線隔日開盤出；最長 20 個交易日",
                        "portfolio": "單筆風險 0.75%、總風險 4%、最多 10 檔、同產業 2 檔、單檔 25%"},
              "limitations": ["目前主檔存在存活者偏差", "日 K 無法知道盤中先後：同日觸發又碰停損一律算停損",
                              "同日多檔觸發依 20 日成交金額排序，實際掛單先後不可知",
                              "這段歷史含設計規則用的個案（6672、2492），不是樣本外證據"]}
    out = Path(__file__).resolve().parent / "research" / f"setups_backtest_{args.tag}.json"
    out.write_text(json.dumps(clean(result), ensure_ascii=False, default=str, indent=1, allow_nan=False),
                   encoding="utf-8")
    R[R["variant"].isin(MAIN)].to_csv(out.with_name(f"setups_trades_{args.tag}.csv"), index=False, encoding="utf-8-sig")
    print(f"\n研究結果 → {out}（{time.time() - t0:.0f}s）")


if __name__ == "__main__":
    main()
