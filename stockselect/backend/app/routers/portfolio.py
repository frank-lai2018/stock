"""持股診斷 API：持股 CRUD（存 DB）＋ 綜合體檢（技術/籌碼/基本面燈號、損益、支撐壓力）。"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import db, diagnose, patterns
from .stock import levels as compute_levels

router = APIRouter(prefix="/api/portfolio", tags=["portfolio"])

_ensured = False


def _ensure():
    """首次使用時建立 portfolio 表（單一使用者，每檔一列）。"""
    global _ensured
    if _ensured:
        return
    db.execute(
        "CREATE TABLE IF NOT EXISTS portfolio ("
        " stock_id   VARCHAR(16) PRIMARY KEY,"
        " lots       NUMERIC,"          # 張數（1張=1000股，可為零股小數）
        " cost       NUMERIC,"          # 每股平均成本
        " note       VARCHAR(200),"
        " updated_at TIMESTAMP DEFAULT now())")
    _ensured = True


class Holding(BaseModel):
    stock_id: str
    lots: float | None = None
    cost: float | None = None
    note: str | None = None


def _last_patterns(ids):
    """批次取各檔最後交易日陰陽線型態。"""
    if not ids:
        return {}
    rows = db.query(
        "SELECT stock_id, trade_date, adj_open AS open, adj_high AS high, adj_low AS low, adj_close AS close "
        "FROM (SELECT stock_id, trade_date, adj_open, adj_high, adj_low, adj_close, "
        "  row_number() OVER (PARTITION BY stock_id ORDER BY trade_date DESC) AS rn "
        "  FROM price_daily WHERE stock_id = ANY(%(ids)s)) z WHERE rn <= 12 ORDER BY stock_id, trade_date",
        {"ids": ids})
    by = {}
    for b in rows:
        by.setdefault(b["stock_id"], []).append(b)
    return {sid: [{"name": patterns.CATALOG[k][0], "dir": patterns.CATALOG[k][1]}
                  for k in patterns.detect(bars)] for sid, bars in by.items()}


@router.get("")
def list_portfolio():
    """回傳持股清單 + 每檔診斷 + 損益 + 支撐壓力，及組合總覽。"""
    _ensure()
    holds = db.query("SELECT stock_id, lots, cost, note FROM portfolio ORDER BY stock_id")
    if not holds:
        return {"items": [], "summary": None, "as_of": None}
    ids = [h["stock_id"] for h in holds]
    snaps = {r["stock_id"]: r for r in
             db.query("SELECT * FROM mv_stock_snapshot WHERE stock_id = ANY(%(ids)s)", {"ids": ids})}
    pats = _last_patterns(ids)

    items, as_of = [], None
    tot_cost = tot_val = 0.0
    levels = {"strong": 0, "watch": 0, "reduce": 0}
    rs_sum = rs_n = accum_n = distrib_n = 0
    ind_val = {}
    for h in holds:
        sid = h["stock_id"]
        s = snaps.get(sid)
        lots = float(h["lots"]) if h["lots"] is not None else None
        cost = float(h["cost"]) if h["cost"] is not None else None
        close = float(s["close"]) if s and s.get("close") is not None else None
        shares = lots * 1000 if lots is not None else None
        mkt_val = shares * close if (shares is not None and close is not None) else None
        cost_val = shares * cost if (shares is not None and cost is not None) else None
        pnl = (mkt_val - cost_val) if (mkt_val is not None and cost_val is not None) else None
        pnl_pct = (close / cost - 1) if (close is not None and cost) else None

        dg = diagnose.diagnose(s) if s else {"score": None, "level": "watch", "facets": {}, "reasons": []}
        lv = []
        try:
            lv = compute_levels(sid)
        except Exception:
            lv = []
        res = next((x["price"] for x in lv if x["type"] == "resistance"), None)
        sup = next((x["price"] for x in lv if x["type"] == "support"), None) \
            or next((x["price"] for x in lv if x["type"] == "neckline"), None)

        if s and s.get("as_of_date"):
            as_of = s["as_of_date"].isoformat()
        items.append({
            "stock_id": sid, "name": s["name"] if s else sid, "industry": s.get("industry") if s else None,
            "lots": lots, "cost": cost, "note": h.get("note"),
            "close": close, "market_value": mkt_val, "pnl": pnl, "pnl_pct": pnl_pct,
            "score": dg["score"], "level": dg["level"], "facets": dg["facets"], "reasons": dg["reasons"],
            "rs_rating": s.get("rs_rating") if s else None,
            "above_ma60": s.get("above_ma60") if s else None,
            "vpa_accum_20d": s.get("vpa_accum_20d") if s else None,
            "vpa_distrib_20d": s.get("vpa_distrib_20d") if s else None,
            "support": sup, "resistance": res,
            "last_patterns": pats.get(sid, []),
        })
        # 彙總
        levels[dg["level"]] = levels.get(dg["level"], 0) + 1
        if s and s.get("rs_rating") is not None:
            rs_sum += float(s["rs_rating"]); rs_n += 1
        if s and s.get("mf_accumulate"):
            accum_n += 1
        if s and s.get("mf_distribute"):
            distrib_n += 1
        if cost_val:
            tot_cost += cost_val
        if mkt_val:
            tot_val += mkt_val
            ind = (s.get("industry") if s else None) or "其他"
            ind_val[ind] = ind_val.get(ind, 0) + mkt_val

    top_ind = max(ind_val.items(), key=lambda kv: kv[1]) if ind_val else None
    summary = {
        "n": len(items),
        "total_cost": tot_cost or None,
        "total_value": tot_val or None,
        "total_pnl": (tot_val - tot_cost) if tot_cost else None,
        "total_pnl_pct": (tot_val / tot_cost - 1) if tot_cost else None,
        "levels": levels,
        "avg_rs": round(rs_sum / rs_n) if rs_n else None,
        "accum_n": accum_n, "distrib_n": distrib_n,
        "top_industry": top_ind[0] if top_ind else None,
        "top_industry_share": round(top_ind[1] / tot_val * 100, 1) if (top_ind and tot_val) else None,
    }
    return {"items": items, "summary": summary, "as_of": as_of}


@router.post("")
def upsert_holding(h: Holding):
    """新增/更新一檔持股（依 stock_id 覆蓋）。"""
    _ensure()
    sid = (h.stock_id or "").strip()
    if not sid:
        raise HTTPException(400, "stock_id 不可為空")
    if not db.query("SELECT 1 FROM stock WHERE stock_id=%(id)s", {"id": sid}):
        raise HTTPException(404, f"查無此股：{sid}")
    db.execute(
        "INSERT INTO portfolio (stock_id, lots, cost, note, updated_at) "
        "VALUES (%(id)s, %(lots)s, %(cost)s, %(note)s, now()) "
        "ON CONFLICT (stock_id) DO UPDATE SET "
        "lots=EXCLUDED.lots, cost=EXCLUDED.cost, note=EXCLUDED.note, updated_at=now()",
        {"id": sid, "lots": h.lots, "cost": h.cost, "note": h.note})
    return {"ok": True, "stock_id": sid}


@router.delete("/{stock_id}")
def delete_holding(stock_id: str):
    _ensure()
    n = db.execute("DELETE FROM portfolio WHERE stock_id=%(id)s", {"id": stock_id})
    return {"ok": True, "deleted": n}
