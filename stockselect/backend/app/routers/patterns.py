"""K 棒型態 API：型態目錄 + 型態選股。"""
from fastapi import APIRouter, HTTPException

from .. import db, patterns, price_action
from .screen import _attach_recent_eps

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


@router.get("/screen/price-action")
def screen_price_action(security_type: str = "stock", min_amt: int = 20000000,
                        lookback: int = 5, expiry: int = 5, limit: int = 300):
    """裸 K 決策：只用 OHLC 評估結構、位置、訊號、確認與風險報酬。"""
    cond = ["in_universe", "amt20 >= %(amt)s"]
    params = {"amt": max(0, int(min_amt))}
    if security_type in ("stock", "etf"):
        cond.append("security_type = %(st)s"); params["st"] = security_type
    snap = {r["stock_id"]: r for r in db.query(
        "SELECT stock_id,name,industry,security_type,close,amt20,as_of_date "
        f"FROM mv_stock_snapshot WHERE {' AND '.join(cond)}", params)}
    if not snap:
        return {"count": 0, "as_of": None, "summary": {}, "items": []}
    rows = db.query(
        "SELECT stock_id,trade_date,adj_open AS open,adj_high AS high,adj_low AS low,adj_close AS close "
        "FROM (SELECT stock_id,trade_date,adj_open,adj_high,adj_low,adj_close,"
        " row_number() OVER (PARTITION BY stock_id ORDER BY trade_date DESC) rn "
        "FROM price_daily WHERE stock_id=ANY(%(ids)s)) z WHERE rn<=140 ORDER BY stock_id,trade_date",
        {"ids": list(snap)})
    grouped = {}
    for row in rows:
        grouped.setdefault(row["stock_id"], []).append(row)
    out = []
    for sid, bars in grouped.items():
        decision = price_action.analyze(bars, max(1, min(int(lookback), 10)), max(1, min(int(expiry), 10)))
        if decision:
            out.append({**dict(snap[sid]), "decision": decision})
    cr = {"priority": 3, "waiting": 2, "watch": 1, "skip": 0}
    out.sort(key=lambda r: (cr[r["decision"]["conclusion"]], r["decision"]["score"],
                            r.get("amt20") or 0), reverse=True)
    out = out[:max(1, min(int(limit), 500))]
    summary = {k: sum(1 for r in out if r["decision"]["conclusion"] == k)
               for k in ("priority", "waiting", "watch", "skip")}
    as_of = out[0]["as_of_date"].isoformat() if out and out[0].get("as_of_date") else None
    return {"count": len(out), "as_of": as_of, "summary": summary,
            "method": "裸K規則評分 v1；只使用 OHLC，不含成交量、指標、基本面與籌碼", "items": out}
