"""持股診斷 / 交易帳 API。

交易帳 trade_log（單次買/賣，記日期、手續費、證交稅）為真實來源；
持股（未平倉部位）與已實現損益皆由 ledger FIFO 引擎推導。
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import db, diagnose, ledger, patterns
from .stock import levels as compute_levels

router = APIRouter(prefix="/api", tags=["portfolio"])

_ensured = False


def _ensure():
    global _ensured
    if _ensured:
        return
    db.execute(
        "CREATE TABLE IF NOT EXISTS trade_log ("
        " id SERIAL PRIMARY KEY,"
        " stock_id   VARCHAR(16) NOT NULL,"
        " action     VARCHAR(4)  NOT NULL,"       # buy / sell
        " trade_date DATE        NOT NULL,"
        " lots       NUMERIC     NOT NULL,"       # 張（1張=1000股）
        " price      NUMERIC     NOT NULL,"       # 每股價
        " fee        NUMERIC,"                    # 手續費（該筆總額）
        " tax        NUMERIC,"                    # 證交稅（賣出）
        " note       VARCHAR(200),"
        " created_at TIMESTAMP DEFAULT now())")
    db.execute("CREATE INDEX IF NOT EXISTS idx_trade_stock ON trade_log(stock_id)")
    _ensured = True


class Trade(BaseModel):
    stock_id: str
    action: str                       # buy / sell
    trade_date: str                   # YYYY-MM-DD
    lots: float
    price: float
    fee: float | None = None
    tax: float | None = None
    note: str | None = None


def _last_patterns(ids):
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


@router.get("/portfolio")
def portfolio():
    """未平倉持股 + 每檔診斷 + 未實現損益，及組合總覽（含已實現）。"""
    _ensure()
    txns = db.query("SELECT id, stock_id, action, trade_date, lots, price, fee, tax FROM trade_log")
    if not txns:
        return {"items": [], "summary": None, "realized": [], "as_of": None}

    by = {}
    for t in txns:
        by.setdefault(t["stock_id"], []).append(t)
    leds = {sid: ledger.build(ts) for sid, ts in by.items()}

    open_ids = [sid for sid, l in leds.items() if l["net_lots"] > 1e-9]
    snaps = {r["stock_id"]: r for r in
             db.query("SELECT * FROM mv_stock_snapshot WHERE stock_id = ANY(%(ids)s)", {"ids": open_ids})} \
        if open_ids else {}
    pats = _last_patterns(open_ids)

    items, as_of = [], None
    tot_mv = tot_cost = 0.0
    levels_cnt = {"strong": 0, "watch": 0, "reduce": 0}
    rs_sum = rs_n = accum_n = distrib_n = 0
    ind_val = {}
    for sid in open_ids:
        l = leds[sid]
        s = snaps.get(sid)
        close = float(s["close"]) if s and s.get("close") is not None else None
        net_lots, avg_cost = l["net_lots"], l["avg_cost"]
        shares = net_lots * 1000
        mkt_val = shares * close if close is not None else None
        cost_val = shares * avg_cost if avg_cost is not None else None
        unreal = (mkt_val - cost_val) if (mkt_val is not None and cost_val is not None) else None
        unreal_pct = (close / avg_cost - 1) if (close is not None and avg_cost) else None

        dg = diagnose.diagnose(s) if s else {"score": None, "level": "watch", "facets": {}, "reasons": []}
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
            "lots": net_lots, "cost": avg_cost, "close": close,
            "market_value": mkt_val, "unrealized": unreal, "unrealized_pct": unreal_pct,
            "realized": l["realized_pnl"],
            "score": dg["score"], "level": dg["level"], "facets": dg["facets"], "reasons": dg["reasons"],
            "rs_rating": s.get("rs_rating") if s else None,
            "vpa_accum_20d": s.get("vpa_accum_20d") if s else None,
            "vpa_distrib_20d": s.get("vpa_distrib_20d") if s else None,
            "support": sup, "resistance": res,
            "last_patterns": pats.get(sid, []),
        })
        levels_cnt[dg["level"]] += 1
        if s and s.get("rs_rating") is not None:
            rs_sum += float(s["rs_rating"]); rs_n += 1
        if s and s.get("mf_accumulate"):
            accum_n += 1
        if s and s.get("mf_distribute"):
            distrib_n += 1
        if mkt_val:
            tot_mv += mkt_val
            ind = (s.get("industry") if s else None) or "其他"
            ind_val[ind] = ind_val.get(ind, 0) + mkt_val
        if cost_val:
            tot_cost += cost_val

    # 已實現：彙整所有配對完成的交易（新到舊）
    realized = []
    for sid, l in leds.items():
        nm = snaps.get(sid, {}).get("name") if snaps.get(sid) else None
        if not nm:
            r = db.query("SELECT name FROM stock WHERE stock_id=%(id)s", {"id": sid})
            nm = r[0]["name"] if r else sid
        for c in l["closed"]:
            realized.append({**c, "stock_id": sid, "name": nm})
    realized.sort(key=lambda x: x["sell_date"], reverse=True)
    realized_total = sum(c["pnl"] for c in realized)
    wins = sum(1 for c in realized if c["pnl"] > 0)

    top_ind = max(ind_val.items(), key=lambda kv: kv[1]) if ind_val else None
    unreal_total = tot_mv - tot_cost
    summary = {
        "n": len(items),
        "total_value": tot_mv or None,
        "total_cost": tot_cost or None,
        "unrealized": unreal_total if tot_cost else None,
        "unrealized_pct": (unreal_total / tot_cost) if tot_cost else None,
        "realized_total": realized_total,
        "total_pnl": realized_total + (unreal_total if tot_cost else 0),
        "closed_n": len(realized),
        "win_rate": round(wins / len(realized) * 100, 1) if realized else None,
        "levels": levels_cnt,
        "avg_rs": round(rs_sum / rs_n) if rs_n else None,
        "accum_n": accum_n, "distrib_n": distrib_n,
        "top_industry": top_ind[0] if top_ind else None,
        "top_industry_share": round(top_ind[1] / tot_mv * 100, 1) if (top_ind and tot_mv) else None,
    }
    return {"items": items, "summary": summary, "realized": realized, "as_of": as_of}


@router.get("/trades")
def list_trades():
    """原始交易明細（新到舊）。"""
    _ensure()
    rows = db.query(
        "SELECT t.id, t.stock_id, s.name, t.action, t.trade_date, t.lots, t.price, t.fee, t.tax, t.note "
        "FROM trade_log t LEFT JOIN stock s USING(stock_id) "
        "ORDER BY t.trade_date DESC, t.id DESC")
    return rows


@router.post("/trades")
def add_trade(t: Trade):
    _ensure()
    sid = (t.stock_id or "").strip()
    act = (t.action or "").strip().lower()
    if act not in ("buy", "sell"):
        raise HTTPException(400, "action 需為 buy 或 sell")
    if not db.query("SELECT 1 FROM stock WHERE stock_id=%(id)s", {"id": sid}):
        raise HTTPException(404, f"查無此股：{sid}")
    if t.lots is None or t.lots <= 0 or t.price is None or t.price < 0:
        raise HTTPException(400, "張數需 >0、價格需 ≥0")
    rows = db.execute(
        "INSERT INTO trade_log (stock_id, action, trade_date, lots, price, fee, tax, note) "
        "VALUES (%(id)s, %(act)s, %(d)s::date, %(lots)s, %(price)s, %(fee)s, %(tax)s, %(note)s) "
        "RETURNING id",
        {"id": sid, "act": act, "d": t.trade_date, "lots": t.lots, "price": t.price,
         "fee": t.fee, "tax": t.tax, "note": t.note}, returning=True)
    return {"ok": True, "id": rows[0]["id"]}


@router.delete("/trades/{trade_id}")
def delete_trade(trade_id: int):
    _ensure()
    n = db.execute("DELETE FROM trade_log WHERE id=%(id)s", {"id": trade_id})
    return {"ok": True, "deleted": n}
