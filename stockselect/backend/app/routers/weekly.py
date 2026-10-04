"""週線突破（實驗）選股頁：先看週線趨勢與突破，再看日線進場點。規則在 app/weekly_breakout.py。"""
from fastapi import APIRouter, HTTPException

from .. import db, weekly_breakout
from .screen import _split_ids

router = APIRouter(prefix="/api", tags=["weekly"])

STATUS_ORDER = {"priority": 3, "waiting": 2, "watch": 1, "skip": 0}
SNAP_COLS = ("name", "industry", "security_type", "rs_rating", "amt20", "trend_template",
             "eps", "eps_yoy", "rev_yoy", "gross_margin_chg")


@router.get("/screen/weekly-breakout")
def weekly_breakout_screen(security_type: str = "stock", min_amt: int = 20_000_000,
                           stock_id: str = "", stock_ids: str = "", limit: int = 500):
    """全市場（in_universe、20 日均額 ≥ min_amt）找週線突破：可進場、等回測、接近壓力。
    stock_id＝指定個股分析、stock_ids＝逗號分隔多檔；兩者不套母體、流動性與證券類別，
    不成立的也列出原因。只有 stock_id 找不到時回 404。"""
    sid = stock_id.strip().upper()
    picked = [sid] if sid else _split_ids(stock_ids)
    if picked:
        cond, params = ["stock_id = ANY(%(sids)s)"], {"sids": picked}
    else:
        cond, params = ["in_universe", "amt20 >= %(amt)s"], {"amt": max(0, int(min_amt))}
        if security_type in ("stock", "etf"):
            cond.append("security_type = %(st)s")
            params["st"] = security_type
    snaps = {r["stock_id"]: r for r in
             db.query(f"SELECT * FROM mv_stock_snapshot WHERE {' AND '.join(cond)}", params)}
    if not snaps and sid:
        raise HTTPException(404, f"找不到股票代號：{sid}")
    latest = db.query("SELECT max(trade_date) AS d FROM price_daily")[0]["d"]
    by = {}
    if snaps:
        for r in db.query(
                "SELECT stock_id,trade_date,adj_open AS open,adj_high AS high,adj_low AS low,"
                " adj_close AS close,close AS raw_close,volume FROM ("
                " SELECT stock_id,trade_date,adj_open,adj_high,adj_low,adj_close,close,volume,"
                " row_number() OVER (PARTITION BY stock_id ORDER BY trade_date DESC) rn"
                " FROM price_daily WHERE stock_id=ANY(%(ids)s) AND trade_date>=%(since)s) z"
                " WHERE rn<=%(n)s ORDER BY stock_id,trade_date",
                {"ids": list(snaps), "n": weekly_breakout.BARS_NEEDED,
                 "since": weekly_breakout.history_start(latest)}):
            by.setdefault(r["stock_id"], []).append(r)

    items = []
    for code, snap in snaps.items():
        bars = by.get(code, [])
        stale = bool(bars) and bars[-1]["trade_date"] != latest
        result = weekly_breakout.analyze(bars, latest) if bars else None
        if not picked and (stale or not result or result["status"] == "skip"):
            continue
        reason = ("沒有價格資料" if not bars else
                  f"行情停在 {bars[-1]['trade_date']}（可能停牌）" if stale else
                  "週線資料不足 53 週（上市未滿一年）" if not result else None)
        items.append({
            "stock_id": code, **{k: snap.get(k) for k in SNAP_COLS},
            "close": float(bars[-1]["close"]) if bars else None,
            "raw_close": float(bars[-1]["raw_close"]) if bars else None,
            "price_date": bars[-1]["trade_date"] if bars else None,
            "weekly": result, "reason": reason,
        })
    items.sort(key=lambda r: (STATUS_ORDER.get((r["weekly"] or {}).get("status"), -1),
                              r.get("rs_rating") or 0), reverse=True)
    items = items[:max(1, min(int(limit), 1000))]
    out = {
        "as_of": latest, "count": len(items), "items": items,
        "summary": {k: sum(1 for r in items if (r["weekly"] or {}).get("status") == k)
                    for k in ("priority", "waiting", "watch", "skip")},
        "method": "週線突破（實驗）：週線看趨勢與突破、日線找進場點；規則是待驗證的起始參數，"
                  "成效見「今日決策中心 → 策略研究」的週線突破組合",
    }
    if sid:
        hit = items[0] if items else None
        out["analysis"] = {"stock_id": sid, "status": (hit["weekly"] or {}).get("status") if hit else None,
                           "reason": (hit["reason"] or "；".join((hit["weekly"] or {}).get("blockers") or []))
                           if hit else "找不到資料"}
    return out
