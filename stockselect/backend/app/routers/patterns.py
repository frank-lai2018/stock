"""K 棒型態 API：型態目錄 + 型態選股。"""
from fastapi import APIRouter, HTTPException

from .. import db, patterns

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
        "FROM price_daily WHERE trade_date > (SELECT max(trade_date)-20 FROM price_daily) "
        "ORDER BY stock_id, trade_date")
    groups = {}
    for r in rows:
        groups.setdefault(r["stock_id"], []).append(r)
    matches = [sid for sid, bars in groups.items() if pattern in patterns.detect(bars)]
    if not matches:
        return {"pattern": pattern, "name": patterns.CATALOG[pattern][0], "count": 0, "items": []}
    n = max(1, min(int(limit), 300))
    snap = db.query(
        "SELECT stock_id, name, industry, close, ret_1m, ret_3m, per, inst_net_20d, big1000_pct "
        "FROM mv_stock_snapshot WHERE stock_id = ANY(%(ids)s) AND in_universe "
        "ORDER BY amt20 DESC NULLS LAST LIMIT %(n)s",
        {"ids": matches, "n": n})
    return {"pattern": pattern, "name": patterns.CATALOG[pattern][0], "count": len(snap), "items": snap}
