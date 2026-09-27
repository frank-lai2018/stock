"""主動式 ETF API：各檔每日進出（已扣全面等比例增減）、跨投信共識、單檔明細、個股被哪些主動 ETF 持有。

資料來源：etf_fund／etf_snapshot／etf_holding（fetch_active_etf.py 每晚抓各投信官網公告）、
etf_flow（build_etf_flow.py 相鄰兩個持股日相減）。說明見 主動ETF追蹤設計.md。
共識一律以「投信家數」計：同一家投信的兩檔 ETF 同時買，不算兩個獨立判斷。
"""
from datetime import date as Date

from fastapi import APIRouter, HTTPException, Query

from .. import db

router = APIRouter(prefix="/api/active-etf", tags=["active-etf"])

BUY = ("new", "add")          # 主動買進（共識計數用）
SELL = ("exit", "cut")        # 主動賣出
_LATEST = "(SELECT etf_id, max(as_of) AS as_of FROM etf_snapshot GROUP BY etf_id)"
# 真部位（非佔位股）：跟 build_etf_flow.is_dust 相反——權重 ≥ 0.05%，或權重 ≥ 0.01% 且不只 1 張
_REAL = "(h.weight >= 0.05 OR (h.weight >= 0.01 AND h.shares > 1000))"


def _is_real(shares, weight):
    """同 _REAL，給已經查出來的列用。"""
    w = float(weight) if weight is not None else None
    return w is not None and (w >= 0.05 or (w >= 0.01 and float(shares or 0) > 1000))


def _flow_dates(n, until=None):
    """etf_flow 最新的 n 個持股日（新到舊），可指定截止日。"""
    return [r["trade_date"] for r in db.query(
        "SELECT DISTINCT trade_date FROM etf_flow WHERE %(u)s::date IS NULL OR trade_date <= %(u)s "
        "ORDER BY trade_date DESC LIMIT %(n)s", {"u": until, "n": n})]


@router.get("/overview")
def overview():
    """各 ETF 最新一日概況、涵蓋率（依 etf_daily 規模），以及可選的持股日。"""
    funds = db.query(
        "SELECT e.etf_id, e.issuer, e.name, e.adapter, s.as_of, s.prev_as_of, s.units, s.nav_total, s.nav_unit, "
        "       s.stock_weight, s.futures_weight, s.n_stocks, s.flow_k, s.flow_k_units, a.aum, "
        "       f.n_new, f.n_exit, f.n_add, f.n_cut, f.buy_amt, f.sell_amt, f.net_amt "
        "FROM etf_fund e "
        "LEFT JOIN LATERAL (SELECT * FROM etf_snapshot x WHERE x.etf_id = e.etf_id ORDER BY as_of DESC LIMIT 1) s ON TRUE "
        "LEFT JOIN LATERAL (SELECT aum FROM etf_daily d WHERE d.stock_id = e.etf_id "
        "                   ORDER BY trade_date DESC LIMIT 1) a ON TRUE "
        "LEFT JOIN LATERAL (SELECT count(*) FILTER (WHERE action = 'new') AS n_new, "
        "                          count(*) FILTER (WHERE action = 'exit') AS n_exit, "
        "                          count(*) FILTER (WHERE action = 'add') AS n_add, "
        "                          count(*) FILTER (WHERE action = 'cut') AS n_cut, "
        "                          sum(amount) FILTER (WHERE amount > 0) AS buy_amt, "
        "                          sum(amount) FILTER (WHERE amount < 0) AS sell_amt, "
        "                          sum(amount) AS net_amt "
        "                   FROM etf_flow f WHERE f.etf_id = e.etf_id AND f.trade_date = s.as_of) f ON TRUE "
        "WHERE e.is_active ORDER BY a.aum DESC NULLS LAST")
    covered = [f for f in funds if f["adapter"]]
    total = sum(float(f["aum"] or 0) for f in funds)
    cov = sum(float(f["aum"] or 0) for f in covered)
    dates = _flow_dates(60)
    return {
        "as_of": dates[0].isoformat() if dates else None,
        "dates": [d.isoformat() for d in dates],
        "coverage": {"n_covered": len(covered), "n_total": len(funds), "aum_covered": cov, "aum_total": total,
                     "pct": cov / total if total else None,
                     "issuers": sorted({f["issuer"] for f in covered}),
                     "uncovered": [{"etf_id": f["etf_id"], "name": f["name"], "issuer": f["issuer"]}
                                   for f in funds if not f["adapter"]]},
        "funds": covered,
    }


@router.get("/consensus")
def consensus(date: Date | None = None, days: int = Query(1, ge=1, le=20),
              side: str = Query("buy", pattern="^(buy|sell)$"), limit: int = Query(50, ge=1, le=300)):
    """跨 ETF 共識：截至 date 的最近 days 個持股日，各股被幾家投信主動買進／賣出。
    active_amount＝主動調整金額（已扣全面等比例增減）；amount＝實際買賣金額；impact＝實際買賣 ÷ 期間成交額。"""
    ds = _flow_dates(days, date)
    if not ds:
        return {"dates": [], "rows": []}
    acts = BUY if side == "buy" else SELL
    rows = db.query(
        "WITH f AS (SELECT f.*, e.issuer FROM etf_flow f JOIN etf_fund e USING (etf_id) "
        "           WHERE f.trade_date = ANY(%(ds)s) AND f.action <> 'corp'), "
        "pick AS (SELECT stock_id FROM f WHERE action = ANY(%(acts)s) GROUP BY stock_id), "
        f"held AS (SELECT h.code AS stock_id, sum(h.shares) FILTER (WHERE {_REAL}) AS held_shares, "
        f"                count(*) FILTER (WHERE {_REAL}) AS n_hold "
        f"         FROM etf_holding h JOIN {_LATEST} m USING (etf_id, as_of) "
        "         WHERE h.kind = 'stock' AND h.code IN (SELECT stock_id FROM pick) GROUP BY h.code), "
        "sh AS (SELECT p.stock_id, x.shares_issued FROM pick p "          # 每檔只取最新一筆（走 PK 索引，免掃歷史）
        "       LEFT JOIN LATERAL (SELECT shares_issued FROM shareholding s WHERE s.stock_id = p.stock_id "
        "                          ORDER BY trade_date DESC LIMIT 1) x ON TRUE), "
        "px AS (SELECT stock_id, sum(amount) AS turnover FROM price_daily "
        "       WHERE trade_date = ANY(%(ds)s) AND stock_id IN (SELECT stock_id FROM pick) GROUP BY stock_id) "
        "SELECT f.stock_id, s.name, s.industry, "
        "       count(DISTINCT f.issuer) FILTER (WHERE f.action = ANY(%(buy)s)) AS n_buy, "
        "       count(DISTINCT f.issuer) FILTER (WHERE f.action = ANY(%(sell)s)) AS n_sell, "
        "       count(DISTINCT f.etf_id) FILTER (WHERE f.action = ANY(%(acts)s)) AS n_etf, "
        "       sum(f.active_amount) AS active_amount, sum(f.amount) AS amount, "
        "       sum(f.amount) / NULLIF(max(px.turnover), 0) AS impact, "
        "       max(held.held_shares) AS held_shares, max(held.n_hold) AS n_hold, "
        "       max(held.held_shares) / NULLIF(max(sh.shares_issued), 0) AS held_pct, "
        "       max(v.close) AS close, "
        "       json_agg(json_build_object('etf_id', f.etf_id, 'issuer', f.issuer, 'date', f.trade_date, "
        "                'action', f.action, 'd_shares', f.d_shares, 'active_shares', f.active_shares, "
        "                'active_amount', f.active_amount) "
        "                ORDER BY f.trade_date DESC, abs(f.active_amount) DESC NULLS LAST) AS detail "
        "FROM f JOIN pick USING (stock_id) LEFT JOIN stock s USING (stock_id) "
        "LEFT JOIN held USING (stock_id) LEFT JOIN sh USING (stock_id) LEFT JOIN px USING (stock_id) "
        "LEFT JOIN mv_stock_snapshot v USING (stock_id) "
        "GROUP BY f.stock_id, s.name, s.industry",
        {"ds": ds, "acts": list(acts), "buy": list(BUY), "sell": list(SELL)})
    key = "n_buy" if side == "buy" else "n_sell"
    sign = -1 if side == "buy" else 1
    # 窗口拉長時，同一檔可能有人加碼、有人減碼：只列「淨主動金額」方向一致的，避免淨買 148 億的股票出現在共識賣出
    rows = [r for r in rows if sign * float(r["active_amount"] or 0) < 0]
    rows.sort(key=lambda r: (-r[key], sign * float(r["active_amount"] or 0)))
    return {"dates": [d.isoformat() for d in sorted(ds)], "side": side, "rows": rows[:limit]}


@router.get("/fund/{etf_id}")
def fund(etf_id: str, date: Date | None = None):
    """單檔 ETF：某持股日的進出與持股、期貨部位，以及歷史曝險（股票％＋期貨％）與單位數。"""
    meta = db.query("SELECT e.etf_id, e.issuer, e.name, e.adapter, s.list_date FROM etf_fund e "
                    "LEFT JOIN stock s ON s.stock_id = e.etf_id WHERE e.etf_id = %(id)s", {"id": etf_id.upper()})
    if not meta:
        raise HTTPException(404, f"找不到主動式 ETF {etf_id}")
    m = meta[0]
    snaps = db.query("SELECT as_of, prev_as_of, units, nav_total, nav_unit, stock_weight, futures_weight, n_stocks, "
                     "       flow_k, flow_k_units FROM etf_snapshot WHERE etf_id = %(id)s ORDER BY as_of",
                     {"id": m["etf_id"]})
    if not snaps:
        return {**m, "as_of": None, "dates": [], "snapshot": None, "flows": [], "holdings": [], "futures": [],
                "history": []}
    have = {s["as_of"] for s in snaps}
    d = date if date in have else snaps[-1]["as_of"]
    snap = next(s for s in snaps if s["as_of"] == d)
    params = {"id": m["etf_id"], "d": d}
    flows = db.query(
        "SELECT f.stock_id, COALESCE(s.name, h.name, h0.name, f.stock_id) AS name, s.industry, f.prev_date, "
        "       f.shares_prev, f.shares, f.d_shares, f.active_shares, f.close, f.amount, f.active_amount, "
        "       f.weight_prev, f.weight, f.action "
        "FROM etf_flow f LEFT JOIN stock s USING (stock_id) "
        "LEFT JOIN etf_holding h ON h.etf_id = f.etf_id AND h.as_of = f.trade_date AND h.kind = 'stock' "
        "     AND h.code = f.stock_id "
        "LEFT JOIN etf_holding h0 ON h0.etf_id = f.etf_id AND h0.as_of = f.prev_date AND h0.kind = 'stock' "
        "     AND h0.code = f.stock_id "
        "WHERE f.etf_id = %(id)s AND f.trade_date = %(d)s ORDER BY abs(f.active_amount) DESC NULLS LAST", params)
    holdings = db.query(
        "SELECT h.code AS stock_id, COALESCE(s.name, h.name) AS name, s.industry, h.shares, h.weight, "
        f"       (NOT {_REAL}) AS dust, f.action, f.d_shares, f.active_shares "
        "FROM etf_holding h LEFT JOIN stock s ON s.stock_id = h.code "
        "LEFT JOIN etf_flow f ON f.etf_id = h.etf_id AND f.trade_date = h.as_of AND f.stock_id = h.code "
        "WHERE h.etf_id = %(id)s AND h.as_of = %(d)s AND h.kind = 'stock' "
        "ORDER BY h.weight DESC NULLS LAST, h.shares DESC", params)
    futures = db.query("SELECT code, name, shares AS contracts, weight FROM etf_holding "
                       "WHERE etf_id = %(id)s AND as_of = %(d)s AND kind = 'futures' ORDER BY code", params)
    return {**m, "as_of": d.isoformat(), "dates": [s["as_of"].isoformat() for s in reversed(snaps)][:120],
            "snapshot": snap, "flows": flows, "holdings": holdings, "futures": futures,
            "history": snaps[-250:]}


@router.get("/stock/{stock_id}")
def stock(stock_id: str, days: int = Query(120, ge=20, le=500)):
    """個股被哪些主動 ETF 持有（最新）、合計占股本％、各 ETF 持股張數走勢，以及近期的主動進出紀錄。"""
    sid = stock_id.upper()
    holders = db.query(
        "SELECT e.etf_id, e.issuer, e.name, m.as_of, h.shares, h.weight, "
        "       (SELECT json_build_object('date', f.trade_date, 'action', f.action, 'd_shares', f.d_shares, "
        "               'active_shares', f.active_shares, 'active_amount', f.active_amount) "
        "        FROM etf_flow f WHERE f.etf_id = e.etf_id AND f.stock_id = %(sid)s "
        "          AND f.action NOT IN ('flow', 'corp') ORDER BY f.trade_date DESC LIMIT 1) AS last_move "
        f"FROM etf_fund e JOIN {_LATEST} m USING (etf_id) "
        "LEFT JOIN etf_holding h ON h.etf_id = e.etf_id AND h.as_of = m.as_of AND h.kind = 'stock' AND h.code = %(sid)s "
        "WHERE e.adapter IS NOT NULL ORDER BY h.weight DESC NULLS LAST", {"sid": sid})
    holders = [h for h in holders if _is_real(h["shares"], h["weight"]) or h["last_move"]]
    total = sum(float(h["shares"] or 0) for h in holders if _is_real(h["shares"], h["weight"]))
    issued = db.query("SELECT shares_issued FROM shareholding WHERE stock_id = %(sid)s AND shares_issued > 0 "
                      "ORDER BY trade_date DESC LIMIT 1", {"sid": sid})
    ds = [r["as_of"] for r in db.query("SELECT DISTINCT as_of FROM etf_snapshot ORDER BY as_of DESC LIMIT %(n)s",
                                       {"n": days})]
    series = []
    if ds and holders:
        pts = db.query(f"SELECT etf_id, as_of, CASE WHEN {_REAL} THEN shares ELSE 0 END AS shares "
                       "FROM etf_holding h WHERE code = %(sid)s AND kind = 'stock' AND as_of >= %(d0)s "
                       "AND etf_id = ANY(%(ids)s) ORDER BY as_of", {"sid": sid, "d0": min(ds),
                                                                     "ids": [h["etf_id"] for h in holders]})
        by = {}
        for p in pts:
            by.setdefault(p["etf_id"], {})[p["as_of"]] = float(p["shares"])
        dates = sorted(ds)
        for h in holders:
            got = by.get(h["etf_id"], {})
            series.append({"etf_id": h["etf_id"], "name": h["name"],
                           "points": [[d.isoformat(), got.get(d, 0.0)] for d in dates]})
    events = db.query(
        "SELECT f.etf_id, e.issuer, f.trade_date, f.action, f.d_shares, f.active_shares, f.active_amount, f.close "
        "FROM etf_flow f JOIN etf_fund e USING (etf_id) WHERE f.stock_id = %(sid)s AND f.action NOT IN ('flow', 'corp') "
        "ORDER BY f.trade_date DESC, f.etf_id LIMIT 60", {"sid": sid})
    return {"stock_id": sid, "holders": holders, "total_shares": total,
            "held_pct": total / float(issued[0]["shares_issued"]) if issued and total else None,
            "series": series, "events": events}


_SIGNAL_ORDER = ["basket", "buy2", "buy1", "new", "sell2", "sell1", "exit"]
COST = 0.006                  # 來回成本（手續費＋證交稅）：超額要大過它才有實用價值


def _verdict(r):
    """判讀：往預期方向超過成本、|t|≥2、六成以上月份同方向 → effective；明顯反方向 → reverse；其餘 none。"""
    x = r["avg_excess_mkt"] if r["signal"] == "basket" else r["avg_excess_basket"]
    if x is None or r["t_stat"] is None:
        return "none"
    sign = r["sign"] or 1
    dx, dt = sign * float(x), sign * float(r["t_stat"])
    ok = r["months_ok"] / r["months"] if r["months"] else 0
    if dx >= COST and dt >= 2 and ok >= 0.6:
        return "effective"
    if dx <= -COST and dt <= -2:
        return "reverse"
    return "none"


@router.get("/backtest")
def backtest():
    """訊號回測彙總（backtest_etf_flow.py 每週產生）：跟著主動 ETF 進出買賣，T+1 開盤進場後 5／10／20 日的超額。
    hit／months_ok 已依預期方向調整（賣出訊號算「輸給持股籃」的比例）；verdict＝判讀。"""
    # 還沒跑過回測時表不存在：先查，不用 try/except（db.query 出錯不會 rollback，連線會帶著失敗的交易回池）
    if not db.query("SELECT to_regclass('public.etf_signal_backtest') AS t")[0]["t"]:
        return {"computed_at": None, "cost": COST, "rows": []}
    rows = db.query("SELECT * FROM etf_signal_backtest")
    for r in rows:
        sign = r["sign"] or 1
        win = float(r["win_excess_basket"]) if r["win_excess_basket"] is not None else None
        r["hit"] = None if win is None else (win if sign > 0 else 1 - win)
        r["months_ok"] = (r["month_pos"] if sign > 0 else (r["months"] or 0) - (r["month_pos"] or 0)) \
            if r["months"] is not None else None
        r["verdict"] = _verdict(r)
    rows.sort(key=lambda r: (_SIGNAL_ORDER.index(r["signal"]) if r["signal"] in _SIGNAL_ORDER else 99, r["horizon"]))
    return {"computed_at": max((r["computed_at"] for r in rows), default=None), "cost": COST, "rows": rows}


@router.get("/backtest/events")
def backtest_events(signal: str, horizon: int = Query(20, ge=1, le=60), limit: int = Query(100, ge=1, le=500)):
    """某訊號的逐筆事件（新到舊）：T＝訊號持股日，報酬與超額取持有 horizon 日（還沒滿期的為 null）。"""
    key = str(horizon)
    return db.query(
        "SELECT e.stock_id, s.name, s.industry, e.trade_date, e.n_issuers, e.inflow, e.impact, e.gap_ex, "
        "       (e.rets ->> %(k)s)::numeric AS ret, (e.excess_mkt ->> %(k)s)::numeric AS excess_mkt, "
        "       (e.excess_basket ->> %(k)s)::numeric AS excess_basket "
        "FROM etf_signal_event e LEFT JOIN stock s USING (stock_id) "
        "WHERE e.signal = %(sig)s ORDER BY e.trade_date DESC, e.stock_id LIMIT %(n)s",
        {"k": key, "sig": signal, "n": limit})


_BASKET_ORDER = ["basket3_m", "basket3_vw", "basket3_w", "basket4_m", "basket2_m", "basket1_m", "00981A", "0050"]


@router.get("/basket")
def basket():
    """持股籃策略回測（backtest_etf_basket.py，每週跟 etfbacktest 一起跑）：持有被 ≥N 家投信的主動 ETF
    同時持有的股票、定期換股，跟直接買 00981A、0050 比（已扣交易成本）。回傳績效表與淨值曲線（起始＝1）。"""
    if not db.query("SELECT to_regclass('public.etf_basket_summary') AS t")[0]["t"]:
        return {"computed_at": None, "rows": [], "curves": {}}
    rows = db.query("SELECT * FROM etf_basket_summary")
    rows.sort(key=lambda r: _BASKET_ORDER.index(r["strategy"]) if r["strategy"] in _BASKET_ORDER else 99)
    curves = {}
    for p in db.query("SELECT strategy, trade_date, nav FROM etf_basket_curve ORDER BY strategy, trade_date"):
        curves.setdefault(p["strategy"], []).append([p["trade_date"].isoformat(), float(p["nav"])])
    return {"computed_at": max((r["computed_at"] for r in rows), default=None), "rows": rows, "curves": curves}


@router.get("/today")
def today(top: int = Query(5, ge=1, le=10)):
    """首頁卡片：最新持股日的共識買進／賣出前幾名，以及各 ETF 是否已更新到該日。"""
    ov = overview()
    brief = lambda r: {"stock_id": r["stock_id"], "name": r["name"], "n_buy": r["n_buy"],   # noqa: E731
                       "n_sell": r["n_sell"], "active_amount": r["active_amount"],
                       "etfs": sorted({x["etf_id"] for x in r["detail"] if x["action"] in BUY + SELL})}
    buys = consensus(days=1, side="buy", limit=top)["rows"]
    sells = consensus(days=1, side="sell", limit=top)["rows"]
    return {"as_of": ov["as_of"], "coverage": ov["coverage"],
            "updated": [f["etf_id"] for f in ov["funds"] if f["as_of"] and f["as_of"].isoformat() == ov["as_of"]],
            "n_funds": len(ov["funds"]), "buys": [brief(r) for r in buys], "sells": [brief(r) for r in sells]}
