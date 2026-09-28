"""K 棒型態 API：型態目錄 + 型態選股。"""
from fastapi import APIRouter, HTTPException

from .. import db, patterns, price_action, swings
from ..growth import fundamental_trends, passes
from .screen import _attach_recent_eps, _split_ids

router = APIRouter(prefix="/api", tags=["patterns"])


@router.get("/patterns")
def catalog():
    return [{"key": k, "name": v[0], "dir": v[1]} for k, v in patterns.CATALOG.items()]


@router.get("/screen/pattern")
def screen_pattern(pattern: str, limit: int = 100):
    """回傳「最新交易日」出現指定型態、且在母體(in_universe)內的股票（依流動性排序）。"""
    if pattern not in patterns.CATALOG:
        raise HTTPException(400, "未知型態")
    rows = db.query(
        "SELECT stock_id, adj_open AS open, adj_high AS high, adj_low AS low, adj_close AS close "
        "FROM price_daily WHERE trade_date > (SELECT max(trade_date)-40 FROM price_daily) "   # 約一個月趨勢回看
        "ORDER BY stock_id, trade_date")
    groups = {}
    for r in rows:
        groups.setdefault(r["stock_id"], []).append(r)
    matches = [sid for sid, bars in groups.items() if pattern in patterns.detect(bars)]
    if not matches:
        return {"pattern": pattern, "name": patterns.CATALOG[pattern][0], "count": 0, "items": []}
    n = max(1, min(int(limit), 300))
    snap = db.query(
        "SELECT stock_id, name, industry, close, ret_1m, ret_3m, per, per_pctile, inst_net_20d, "
        "  big1000_pct, big1000_up_weeks, rs_rating, eps_accel, eps_yoy_accel, eps_yoy "
        "FROM mv_stock_snapshot WHERE stock_id = ANY(%(ids)s) AND in_universe "
        "ORDER BY amt20 DESC NULLS LAST LIMIT %(n)s",
        {"ids": matches, "n": n})
    _attach_recent_eps(snap)                          # 近 4 季 EPS（供前端「每季 EPS >」過濾）
    return {"pattern": pattern, "name": patterns.CATALOG[pattern][0], "count": len(snap), "items": snap}


def _chart_pattern(bars, requested, recent):
    """找近期已確認突破的多方波段型態；requested=any 時依既有優先序取第一個。"""
    if not requested:
        return None
    bull_keys = swings.BULL_KEYS
    if requested == "any":
        keys = bull_keys
    elif requested in bull_keys:
        keys = [requested]
    else:
        raise HTTPException(400, f"未知多方型態：{requested}")
    for key in keys:
        breakout = swings.ALL[key](bars, recent=recent)
        if breakout and breakout.get("dir") != "bear":
            return {"key": key, "name": swings.PATTERN_NAMES[key], "breakout": breakout}
    return None


@router.get("/screen/price-action")
def screen_price_action(security_type: str = "stock", min_amt: int = 20000000,
                        lookback: int = 5, expiry: int = 5, limit: int = 300,
                        chart_pattern: str = "", pattern_recent: int = 10,
                        eps_min: float = None, revenue_month_streak: int = 0,
                        revenue_quarter_streak: int = 0, gross_margin_quarter_streak: int = 0,
                        stock_id: str = "", stock_ids: str = ""):
    """型態＋裸 K 決策；基本面只做過濾，不混入裸 K 分數。
    stock_id＝指定個股分析；stock_ids＝逗號分隔的多檔（自選股用）。兩者都不套母體、流動性、
    證券類別與基本面門檻，波段型態只標示、不過濾；只有 stock_id 找不到時回 404。"""
    sid = stock_id.strip().upper()
    picked = [sid] if sid else _split_ids(stock_ids)
    if picked:
        cond, params = ["stock_id = ANY(%(sids)s)"], {"sids": picked}
    else:
        cond = ["in_universe", "amt20 >= %(amt)s"]
        params = {"amt": max(0, int(min_amt))}
        if security_type in ("stock", "etf"):
            cond.append("security_type = %(st)s"); params["st"] = security_type
        if eps_min is not None:
            cond.append("eps >= %(eps_min)s"); params["eps_min"] = float(eps_min)
    snap = {r["stock_id"]: r for r in db.query(
        "SELECT stock_id,name,industry,security_type,close,amt20,as_of_date,"
        " eps,eps_ttm,eps_qoq,eps_yoy,rev_mom,rev_yoy,gross_margin,gross_margin_chg "
        f"FROM mv_stock_snapshot WHERE {' AND '.join(cond)}", params)}
    if not snap:
        if sid:
            raise HTTPException(404, f"找不到股票代號：{sid}")
        return {"count": 0, "as_of": None, "summary": {}, "items": []}
    trends = fundamental_trends(list(snap))
    month_n = 0 if picked else max(0, min(int(revenue_month_streak), 6))
    quarter_n = 0 if picked else max(0, min(int(revenue_quarter_streak), 4))
    margin_n = 0 if picked else max(0, min(int(gross_margin_quarter_streak), 4))
    snap = {code: {**dict(row), "fundamental_trend": trends.get(code, {})}
            for code, row in snap.items() if passes(trends.get(code), month_n, quarter_n, margin_n)}
    if not snap:
        return {"count": 0, "as_of": None, "summary": {}, "items": []}
    rows = db.query(
        "SELECT stock_id,trade_date,adj_open AS open,adj_high AS high,adj_low AS low,"
        " adj_close AS close,volume "
        "FROM (SELECT stock_id,trade_date,adj_open,adj_high,adj_low,adj_close,volume,"
        " row_number() OVER (PARTITION BY stock_id ORDER BY trade_date DESC) rn "
        "FROM price_daily WHERE stock_id=ANY(%(ids)s)) z WHERE rn<=140 ORDER BY stock_id,trade_date",
        {"ids": list(snap)})
    grouped = {}
    for row in rows:
        grouped.setdefault(row["stock_id"], []).append(row)
    out = []
    pat_recent = max(1, min(int(pattern_recent), 25))
    for code, bars in grouped.items():
        chart = _chart_pattern(bars, "any" if picked else chart_pattern, pat_recent)
        if chart_pattern and not picked and not chart:
            continue
        decision = price_action.analyze(bars, max(1, min(int(lookback), 10)), max(1, min(int(expiry), 10)))
        if decision:
            row = {**dict(snap[code]), "decision": decision}
            if chart:
                row.update({"chart_pattern": chart["key"], "chart_pattern_name": chart["name"],
                            "chart_breakout": chart["breakout"]})
            out.append(row)
    cr = {"priority": 3, "waiting": 2, "watch": 1, "skip": 0}
    out.sort(key=lambda r: (cr[r["decision"]["conclusion"]], r["decision"]["score"],
                            r.get("amt20") or 0), reverse=True)
    out = out[:max(1, min(int(limit), 500))]
    summary = {k: sum(1 for r in out if r["decision"]["conclusion"] == k)
               for k in ("priority", "waiting", "watch", "skip")}
    first_snap = next(iter(snap.values()), {})
    as_of_date = out[0].get("as_of_date") if out else first_snap.get("as_of_date")
    as_of = as_of_date.isoformat() if as_of_date else None
    response = {"count": len(out), "as_of": as_of, "summary": summary,
            "method": "型態＋裸K規則評分 v1；裸K分數只使用 OHLC，波段型態與基本面為獨立過濾層",
            "items": out}
    if stock_id.strip():
        response["analysis"] = {
            "stock_id": stock_id.strip().upper(), "matched": bool(out),
            "reason": None if out else f"近 {max(1, min(int(lookback), 10))} 根 K 棒沒有可評估的裸 K 訊號",
        }
    return response
