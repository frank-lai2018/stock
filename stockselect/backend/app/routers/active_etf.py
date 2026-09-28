"""主動式 ETF API：各檔每日進出（已扣全面等比例增減）、跨投信共識、單檔明細、個股被哪些主動 ETF 持有、
近 N 日每單位持股的相對加重／減輕、對照投信買賣超。

資料來源：etf_fund／etf_snapshot／etf_holding（fetch_active_etf.py 每晚抓各投信官網公告）、
etf_flow（build_etf_flow.py 相鄰兩個持股日相減）、etf_issuer_share（投信規模占比 view）、inst_trades（投信買賣超）。
說明見 主動ETF追蹤設計.md。
共識一律以「投信家數」計：同一家投信的兩檔 ETF 同時買，不算兩個獨立判斷；而且只算規模占比 ≥ 1% 的投信
（etf_issuer_share.major）。第一金、兆豐、摩根、台新照常列出，但不計入家數：回測顯示只算大投信，共識買進比較好。
"""
from collections import defaultdict
from datetime import date as Date
from statistics import median

from fastapi import APIRouter, HTTPException, Query

from .. import db

router = APIRouter(prefix="/api/active-etf", tags=["active-etf"])

BUY = ("new", "add")          # 主動買進（共識計數用）
SELL = ("exit", "cut")        # 主動賣出
BASKET_MIN = 4                # 持股籃主策略：≥4 家投信持有（backtest_etf_basket.py 的 basket4_m）
REL_WINDOW = 20               # 每單位持股變化的預設窗口（持股日）
REL_TOL = 0.10                # 每單位持股 ±10% 以上才算相對加重／減輕
LAG_DAYS, LAG_MIN_N = 40, 60  # 持股日對齊檢查：近 40 個持股日、至少 60 筆主動買賣才判斷
_LATEST = "(SELECT etf_id, max(as_of) AS as_of FROM etf_snapshot GROUP BY etf_id)"
_MAJOR = "(SELECT issuer FROM etf_issuer_share WHERE major)"      # 計入家數的投信（規模占比 ≥ 1%）
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


def _issuers():
    """{投信: {"share": 規模占比, "major": 是否計入家數}}（etf_issuer_share：各 ETF 最新持股日的淨資產依投信加總）。"""
    return {r["issuer"]: {"share": float(r["share"] or 0), "major": bool(r["major"])}
            for r in db.query("SELECT issuer, share, major FROM etf_issuer_share")}


@router.get("/overview")
def overview():
    """各 ETF 最新一日概況、涵蓋率（依 etf_daily 規模）、投信規模占比，以及可選的持股日。"""
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
    iss = _issuers()
    covered = [f for f in funds if f["adapter"]]
    for f in covered:
        f["major"] = iss.get(f["issuer"], {}).get("major", False)
    total = sum(float(f["aum"] or 0) for f in funds)
    cov = sum(float(f["aum"] or 0) for f in covered)
    dates = _flow_dates(60)
    return {
        "as_of": dates[0].isoformat() if dates else None,
        "dates": [d.isoformat() for d in dates],
        "coverage": {"n_covered": len(covered), "n_total": len(funds), "aum_covered": cov, "aum_total": total,
                     "pct": cov / total if total else None,
                     "issuers": sorted({f["issuer"] for f in covered}),
                     "issuer_share": sorted(({"issuer": k, **v} for k, v in iss.items()), key=lambda x: -x["share"]),
                     "min_share": 0.01, "basket_min": BASKET_MIN,
                     "uncovered": [{"etf_id": f["etf_id"], "name": f["name"], "issuer": f["issuer"]}
                                   for f in funds if not f["adapter"]]},
        "funds": covered,
    }


@router.get("/consensus")
def consensus(date: Date | None = None, days: int = Query(1, ge=1, le=20),
              side: str = Query("buy", pattern="^(buy|sell)$"), limit: int = Query(50, ge=1, le=300)):
    """跨 ETF 共識：截至 date 的最近 days 個持股日，各股被幾家投信主動買進／賣出。
    n_buy／n_sell 只算規模占比 ≥ 1% 的投信，小投信另計在 n_buy_minor／n_sell_minor（沒有大投信的列不回傳）。
    active_amount＝主動調整金額（已扣全面等比例增減）；amount＝實際買賣金額；impact＝實際買賣 ÷ 期間成交額。"""
    ds = _flow_dates(days, date)
    if not ds:
        return {"dates": [], "rows": []}
    acts = BUY if side == "buy" else SELL
    rows = db.query(
        f"WITH f AS (SELECT f.*, e.issuer, e.issuer IN {_MAJOR} AS major FROM etf_flow f JOIN etf_fund e USING (etf_id) "
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
        "       count(DISTINCT f.issuer) FILTER (WHERE f.action = ANY(%(buy)s) AND f.major) AS n_buy, "
        "       count(DISTINCT f.issuer) FILTER (WHERE f.action = ANY(%(sell)s) AND f.major) AS n_sell, "
        "       count(DISTINCT f.issuer) FILTER (WHERE f.action = ANY(%(buy)s) AND NOT f.major) AS n_buy_minor, "
        "       count(DISTINCT f.issuer) FILTER (WHERE f.action = ANY(%(sell)s) AND NOT f.major) AS n_sell_minor, "
        "       count(DISTINCT f.etf_id) FILTER (WHERE f.action = ANY(%(acts)s)) AS n_etf, "
        "       sum(f.active_amount) AS active_amount, sum(f.amount) AS amount, "
        "       sum(f.amount) / NULLIF(max(px.turnover), 0) AS impact, "
        "       max(held.held_shares) AS held_shares, max(held.n_hold) AS n_hold, "
        "       max(held.held_shares) / NULLIF(max(sh.shares_issued), 0) AS held_pct, "
        "       max(v.close) AS close, "
        "       json_agg(json_build_object('etf_id', f.etf_id, 'issuer', f.issuer, 'major', f.major, "
        "                'date', f.trade_date, 'action', f.action, 'd_shares', f.d_shares, "
        "                'active_shares', f.active_shares, 'active_amount', f.active_amount) "
        "                ORDER BY f.trade_date DESC, abs(f.active_amount) DESC NULLS LAST) AS detail "
        "FROM f JOIN pick USING (stock_id) LEFT JOIN stock s USING (stock_id) "
        "LEFT JOIN held USING (stock_id) LEFT JOIN sh USING (stock_id) LEFT JOIN px USING (stock_id) "
        "LEFT JOIN mv_stock_snapshot v USING (stock_id) "
        "GROUP BY f.stock_id, s.name, s.industry",
        {"ds": ds, "acts": list(acts), "buy": list(BUY), "sell": list(SELL)})
    key = "n_buy" if side == "buy" else "n_sell"
    sign = -1 if side == "buy" else 1
    # 窗口拉長時，同一檔可能有人加碼、有人減碼：只列「淨主動金額」方向一致的，避免淨買 148 億的股票出現在共識賣出
    rows = [r for r in rows if r[key] > 0 and sign * float(r["active_amount"] or 0) < 0]
    rows.sort(key=lambda r: (-r[key], sign * float(r["active_amount"] or 0)))
    return {"dates": [d.isoformat() for d in sorted(ds)], "side": side, "rows": rows[:limit]}


def _per_unit(etf_ids, until=None, window=REL_WINDOW):
    """各 ETF 近 window 個持股日的「每單位持股」變化（股數 ÷ 單位數，扣掉申購贖回）。
    期初股數換算到本日的股本基礎＝本日股數 − 期間 etf_flow 的實際買賣（d_shares 已扣配股、分割），不必另外找倍數。
      pu＝本日股數 ÷ 換算後期初股數 ÷ 單位數比例 − 1；excess＝本日股數 − 換算後期初股數 × 單位數比例
      （比「跟著申購贖回等比例增減」多買的股數）。status：new／exit／up（pu ≥ +10%）／down（≤ −10%）／flat。
    上市不滿 window 個持股日的 ETF 不算。回傳 {etf_id: {"t0", "t1", "units_ratio", "rows": {code: {...}}}}。"""
    snaps = db.query("SELECT etf_id, as_of, units FROM etf_snapshot WHERE etf_id = ANY(%(ids)s) "
                     "AND (%(u)s::date IS NULL OR as_of <= %(u)s) ORDER BY etf_id, as_of",
                     {"ids": list(etf_ids), "u": until})
    by = defaultdict(list)
    for s in snaps:
        by[s["etf_id"]].append(s)
    win = {}
    for eid, ss in by.items():
        if len(ss) > window and ss[-1]["units"] and ss[-1 - window]["units"]:
            win[eid] = (ss[-1 - window]["as_of"], ss[-1]["as_of"],
                        float(ss[-1]["units"]) / float(ss[-1 - window]["units"]))
    if not win:
        return {}
    ids = list(win)
    w = {"ids": ids, "t0": [win[e][0] for e in ids], "t1": [win[e][1] for e in ids]}
    span = "unnest(%(ids)s::text[], %(t0)s::date[], %(t1)s::date[]) AS w(etf_id, t0, t1)"
    hold = db.query(f"SELECT h.etf_id, h.as_of = w.t1 AS is_end, h.code, h.shares, h.weight FROM etf_holding h "
                    f"JOIN {span} ON w.etf_id = h.etf_id AND h.as_of IN (w.t0, w.t1) WHERE h.kind = 'stock'", w)
    flows = db.query(f"SELECT f.etf_id, f.stock_id, sum(f.d_shares) AS d FROM etf_flow f "
                     f"JOIN {span} ON w.etf_id = f.etf_id AND f.trade_date > w.t0 AND f.trade_date <= w.t1 "
                     "GROUP BY f.etf_id, f.stock_id", w)
    px = {(r["stock_id"], r["trade_date"]): float(r["close"]) for r in db.query(
        "SELECT stock_id, trade_date, close FROM price_daily WHERE stock_id = ANY(%(c)s) AND trade_date = ANY(%(d)s) "
        "AND close > 0", {"c": sorted({h["code"] for h in hold}), "d": sorted(set(w["t1"]))})}
    start, end = defaultdict(dict), defaultdict(dict)
    for h in hold:
        (end if h["is_end"] else start)[h["etf_id"]][h["code"]] = (float(h["shares"]), _is_real(h["shares"], h["weight"]))
    dsum = {(f["etf_id"], f["stock_id"]): float(f["d"] or 0) for f in flows}
    out = {}
    for eid in ids:
        t0, t1, ku = win[eid]
        rows = {}
        for code in set(start[eid]) | set(end[eid]):
            sh1, real1 = end[eid].get(code, (0.0, False))
            real0 = start[eid].get(code, (0.0, False))[1]
            if not (real0 or real1):
                continue
            base = sh1 - dsum.get((eid, code), 0.0)              # 期初股數換算到本日的股本基礎
            if real1 and (not real0 or base <= 0):
                status, pu, excess = "new", None, sh1
            elif not real1:
                status, pu, excess = "exit", -1.0, -max(base, 0.0) * ku
            else:
                pu = sh1 / base / ku - 1
                status = "up" if pu >= REL_TOL else "down" if pu <= -REL_TOL else "flat"
                excess = sh1 - base * ku
            close = px.get((code, t1))
            rows[code] = {"status": status, "pu": pu, "excess": excess,
                          "excess_amount": excess * close if close else None}
        out[eid] = {"t0": t0, "t1": t1, "units_ratio": ku, "rows": rows}
    return out


@router.get("/fund/{etf_id}")
def fund(etf_id: str, date: Date | None = None):
    """單檔 ETF：某持股日的進出與持股（含近 20 個持股日的每單位持股變化）、期貨部位，以及歷史曝險與單位數。"""
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
                "history": [], "pu_window": REL_WINDOW, "pu_base": None}
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
    pu = _per_unit([m["etf_id"]], until=d).get(m["etf_id"])
    for h in holdings:
        r = (pu or {}).get("rows", {}).get(h["stock_id"])
        h["pu"], h["pu_status"] = (r["pu"], r["status"]) if r else (None, None)
    futures = db.query("SELECT code, name, shares AS contracts, weight FROM etf_holding "
                       "WHERE etf_id = %(id)s AND as_of = %(d)s AND kind = 'futures' ORDER BY code", params)
    return {**m, "as_of": d.isoformat(), "dates": [s["as_of"].isoformat() for s in reversed(snaps)][:120],
            "snapshot": snap, "flows": flows, "holdings": holdings, "futures": futures,
            "history": snaps[-250:], "pu_window": REL_WINDOW, "pu_base": pu["t0"].isoformat() if pu else None}


@router.get("/stock/{stock_id}")
def stock(stock_id: str, days: int = Query(120, ge=20, le=500)):
    """個股被哪些主動 ETF 持有（最新）、幾家投信（只算規模 ≥1%）、是否為持股籃主策略成分股、合計占股本％、
    各 ETF 持股張數走勢、近 20 個持股日的每單位持股變化、近期主動進出，以及近 20 個持股日對照投信買賣超。"""
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
    iss = _issuers()
    for h in holders:
        h["real"] = _is_real(h["shares"], h["weight"])
        h["major"] = iss.get(h["issuer"], {}).get("major", False)
    holders = [h for h in holders if h["real"] or h["last_move"]]
    pu = _per_unit([h["etf_id"] for h in holders]) if holders else {}
    for h in holders:
        r = pu.get(h["etf_id"], {}).get("rows", {}).get(sid)
        h["pu"], h["pu_status"] = (r["pu"], r["status"]) if r else (None, None)
    total = sum(float(h["shares"] or 0) for h in holders if h["real"])
    n_major = len({h["issuer"] for h in holders if h["real"] and h["major"]})
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
    fds = _flow_dates(20)
    vs_trust = db.query(
        "SELECT d.trade_date, COALESCE(f.etf_shares, 0) AS etf_shares, i.trust_net "
        "FROM unnest(%(ds)s::date[]) AS d(trade_date) "
        "LEFT JOIN (SELECT trade_date, sum(d_shares) AS etf_shares FROM etf_flow "
        "           WHERE stock_id = %(sid)s AND trade_date = ANY(%(ds)s) GROUP BY trade_date) f USING (trade_date) "
        "LEFT JOIN inst_trades i ON i.stock_id = %(sid)s AND i.trade_date = d.trade_date ORDER BY d.trade_date",
        {"sid": sid, "ds": fds}) if fds else []
    return {"stock_id": sid, "holders": holders, "total_shares": total,
            "n_issuers": n_major, "n_issuers_minor": len({h["issuer"] for h in holders if h["real"] and not h["major"]}),
            "n_etfs": sum(1 for h in holders if h["real"]), "basket_min": BASKET_MIN, "in_basket": n_major >= BASKET_MIN,
            "held_pct": total / float(issued[0]["shares_issued"]) if issued and total else None,
            "pu_window": REL_WINDOW, "series": series, "events": events, "vs_trust": vs_trust}


@router.get("/relative")
def relative(date: Date | None = None, window: int = Query(REL_WINDOW, ge=5, le=120),
             side: str = Query("up", pattern="^(up|down)$"), limit: int = Query(40, ge=1, le=300)):
    """近 window 個持股日「每單位持股」的相對加重（side=up）／減輕（down）：股數 ÷ 單位數比期初多 ≥10%（或新建倉）
    算加重、少 ≥10%（或出清）算減輕。扣掉了申購贖回，單日「錢先進出現金、之後才買賣」的時間差也被窗口攤掉。
    家數只算規模 ≥1% 的投信；同一家投信的兩檔 ETF 合併看（淨方向要跟標記一致）。
    rel_amount＝比「跟著申購贖回等比例增減」多買（負＝少買）的股數 × 本日收盤價，各 ETF 加總。"""
    issuer_of = {f["etf_id"]: f["issuer"] for f in db.query("SELECT etf_id, issuer FROM etf_fund WHERE adapter IS NOT NULL")}
    iss = _issuers()
    res = _per_unit(list(issuer_of), until=date, window=window)
    per = defaultdict(lambda: defaultdict(lambda: {"amt": 0.0, "up": False, "down": False}))
    detail = defaultdict(list)
    for eid, wd in res.items():
        issuer = issuer_of[eid]
        for code, r in wd["rows"].items():
            p = per[code][issuer]
            p["amt"] += r["excess_amount"] or 0.0
            p["up"] |= r["status"] in ("new", "up")
            p["down"] |= r["status"] in ("exit", "down")
            if r["status"] != "flat":
                detail[code].append({"etf_id": eid, "issuer": issuer, "major": iss.get(issuer, {}).get("major", False),
                                     "status": r["status"], "pu": r["pu"], "excess_amount": r["excess_amount"],
                                     "t0": wd["t0"].isoformat(), "t1": wd["t1"].isoformat()})
    want = "up" if side == "up" else "down"
    rows = []
    for code, by in per.items():
        n = {"up": 0, "down": 0, "up_minor": 0, "down_minor": 0}
        for issuer, p in by.items():
            tail = "" if iss.get(issuer, {}).get("major", False) else "_minor"
            if p["up"] and p["amt"] > 0:
                n["up" + tail] += 1
            if p["down"] and p["amt"] < 0:
                n["down" + tail] += 1
        amt = sum(p["amt"] for p in by.values())
        if not n[want] or (amt > 0) != (want == "up"):
            continue                                              # 淨方向要一致（同 consensus）
        rows.append({"stock_id": code, "n_up": n["up"], "n_up_minor": n["up_minor"], "n_down": n["down"],
                     "n_down_minor": n["down_minor"], "rel_amount": amt,
                     "detail": sorted(detail[code], key=lambda x: -abs(x["excess_amount"] or 0))})
    rows.sort(key=lambda r: (-r["n_" + want], -abs(r["rel_amount"])))
    rows = rows[:limit]
    info = {r["stock_id"]: r for r in db.query("SELECT stock_id, name, industry FROM stock WHERE stock_id = ANY(%(ids)s)",
                                               {"ids": [r["stock_id"] for r in rows]})} if rows else {}
    for r in rows:
        r["name"], r["industry"] = (info.get(r["stock_id"]) or {}).get("name"), (info.get(r["stock_id"]) or {}).get("industry")
    ends = [wd["t1"] for wd in res.values()]
    return {"as_of": max(ends).isoformat() if ends else None, "window": window, "side": side, "tol": REL_TOL,
            "n_funds": len(res), "n_funds_total": len(issuer_of), "rows": rows}


def _lag_check(since):
    """各 ETF 自 since 起的主動買賣方向 vs 投信買賣超：當天／前一天／後一天的一致率（依實際買賣金額加權）。
    持股日對齊的話當天最高；前後一天明顯比較高（+5 個百分點以上、而且至少 LAG_MIN_N 筆）就標 suspect。
    小基金的買賣占投信買賣超太小，一致率接近雜訊，所以不到 LAG_MIN_N 筆的不判斷。"""
    rows = db.query(
        "WITH td AS (SELECT d AS trade_date, lag(d) OVER (ORDER BY d) AS prv, lead(d) OVER (ORDER BY d) AS nxt "
        "            FROM (SELECT DISTINCT trade_date AS d FROM inst_trades WHERE trade_date >= %(d0)s::date - 10) x), "
        "f AS (SELECT f.etf_id, f.stock_id, f.trade_date, sign(f.d_shares) AS sg, abs(f.amount) AS w FROM etf_flow f "
        "      WHERE f.trade_date >= %(d0)s AND f.action IN ('new', 'add', 'exit', 'cut') "
        "        AND f.amount IS NOT NULL AND f.d_shares <> 0) "
        "SELECT f.etf_id, e.issuer, count(*) AS n, "
        "  sum(f.w) FILTER (WHERE f.sg = sign(i0.trust_net)) / NULLIF(sum(f.w) FILTER (WHERE i0.trust_net <> 0), 0) AS same, "
        "  sum(f.w) FILTER (WHERE f.sg = sign(ip.trust_net)) / NULLIF(sum(f.w) FILTER (WHERE ip.trust_net <> 0), 0) AS prev, "
        "  sum(f.w) FILTER (WHERE f.sg = sign(i1.trust_net)) / NULLIF(sum(f.w) FILTER (WHERE i1.trust_net <> 0), 0) AS next "
        "FROM f JOIN td USING (trade_date) JOIN etf_fund e USING (etf_id) "
        "LEFT JOIN inst_trades i0 ON i0.stock_id = f.stock_id AND i0.trade_date = f.trade_date "
        "LEFT JOIN inst_trades ip ON ip.stock_id = f.stock_id AND ip.trade_date = td.prv "
        "LEFT JOIN inst_trades i1 ON i1.stock_id = f.stock_id AND i1.trade_date = td.nxt "
        "GROUP BY f.etf_id, e.issuer ORDER BY f.etf_id", {"d0": since})
    for r in rows:
        same, other = r["same"], max(float(x) for x in (r["prev"] or 0, r["next"] or 0))
        r["suspect"] = r["n"] >= LAG_MIN_N and same is not None and other >= float(same) + 0.05
        r["enough"] = r["n"] >= LAG_MIN_N
    return rows


@router.get("/vs-trust")
def vs_trust(date: Date | None = None, top: int = Query(15, ge=5, le=50), days: int = Query(60, ge=10, le=250)):
    """對照投信買賣超（inst_trades.trust_net）：主動 ETF 的實際買賣是投信買賣超的一部分。
    rows＝持股日 date 主動 ETF 實際買賣金額前 top 檔、投信買賣超、占比；series＝近 days 個持股日，每天前 top 檔的
    方向一致檔數與占比中位數；lag＝各 ETF 近 LAG_DAYS 個持股日的方向一致率（當天／前一天／後一天），檢查持股日有沒有錯開。"""
    ds = _flow_dates(days, date)
    if not ds:
        return {"as_of": None, "top": top, "rows": [], "series": [], "lag": []}
    tops = db.query(
        "WITH e AS (SELECT trade_date, stock_id, sum(d_shares) AS etf_shares, sum(amount) AS etf_amount, "
        "                  count(DISTINCT etf_id) AS n_etf FROM etf_flow "
        "           WHERE trade_date = ANY(%(ds)s) AND amount IS NOT NULL GROUP BY 1, 2 HAVING sum(d_shares) <> 0), "
        "r AS (SELECT e.*, row_number() OVER (PARTITION BY trade_date ORDER BY abs(etf_amount) DESC) AS rk FROM e) "
        "SELECT r.trade_date, r.stock_id, s.name, r.etf_shares, r.etf_amount, r.n_etf, i.trust_net "
        "FROM r LEFT JOIN inst_trades i USING (stock_id, trade_date) LEFT JOIN stock s USING (stock_id) "
        "WHERE r.rk <= %(top)s ORDER BY r.trade_date, r.rk", {"ds": ds, "top": top})
    by = defaultdict(list)
    for r in tops:
        tn = r["trust_net"]
        r["same"] = bool(tn) and (float(r["etf_shares"]) > 0) == (float(tn) > 0)
        r["share"] = float(r["etf_shares"]) / float(tn) if r["same"] else None
        by[r["trade_date"]].append(r)
    series = []
    for d in sorted(by):
        rs = [r for r in by[d] if r["trust_net"]]
        shares = [r["share"] for r in rs if r["share"] is not None]
        series.append({"date": d.isoformat(), "n": len(rs), "same": sum(1 for r in rs if r["same"]),
                       "median_share": median(shares) if shares else None})
    return {"as_of": ds[0].isoformat(), "top": top, "rows": by.get(ds[0], []), "series": series,
            "lag_days": LAG_DAYS, "lag_since": ds[min(len(ds), LAG_DAYS) - 1].isoformat(), "lag_min_n": LAG_MIN_N,
            "lag": _lag_check(ds[min(len(ds), LAG_DAYS) - 1])}


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


_BASKET_ORDER = ["basket4_m", "basket4_vw", "basket4_w", "basket5_m", "basket3_m", "basket2_m", "basket1_m",
                 "00981A", "0050"]


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
    """首頁卡片：最新持股日的共識買進／賣出前幾名（家數只算規模 ≥1% 的投信），以及各 ETF 是否已更新到該日。"""
    ov = overview()
    brief = lambda r: {"stock_id": r["stock_id"], "name": r["name"], "n_buy": r["n_buy"],   # noqa: E731
                       "n_sell": r["n_sell"], "n_buy_minor": r["n_buy_minor"], "n_sell_minor": r["n_sell_minor"],
                       "active_amount": r["active_amount"],
                       "etfs": sorted({x["etf_id"] for x in r["detail"] if x["action"] in BUY + SELL})}
    buys = consensus(days=1, side="buy", limit=top)["rows"]
    sells = consensus(days=1, side="sell", limit=top)["rows"]
    return {"as_of": ov["as_of"], "coverage": ov["coverage"],
            "updated": [f["etf_id"] for f in ov["funds"] if f["as_of"] and f["as_of"].isoformat() == ov["as_of"]],
            "n_funds": len(ov["funds"]), "buys": [brief(r) for r in buys], "sells": [brief(r) for r in sells]}
