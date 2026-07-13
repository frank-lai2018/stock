"""大盤 API：指數、市場概況、漲跌榜。"""
from fastapi import APIRouter

from .. import db

router = APIRouter(prefix="/api/market", tags=["market"])

# 近 15 日視窗內、每檔最新一日的當日漲跌（用還原價算，等同交易所漲跌幅參考）
_LATEST = (
    "SELECT stock_id, trade_date, close, amount, "
    "  adj_close/NULLIF(lag(adj_close) OVER (PARTITION BY stock_id ORDER BY trade_date),0)-1 AS chg, "
    "  row_number() OVER (PARTITION BY stock_id ORDER BY trade_date DESC) AS rn "
    "FROM price_daily WHERE trade_date > (SELECT max(trade_date)-15 FROM price_daily)"
)


@router.get("/overview")
def overview():
    idx = db.query("SELECT trade_date, close FROM market_index "
                   "WHERE index_id='TAIEX' ORDER BY trade_date DESC LIMIT 2")
    taiex = None
    if idx:
        c0 = float(idx[0]["close"])
        c1 = float(idx[1]["close"]) if len(idx) > 1 else None
        taiex = {"date": idx[0]["trade_date"].isoformat(), "close": c0,
                 "change": (c0 - c1) if c1 else None,
                 "pct": ((c0 / c1 - 1) * 100) if c1 else None}
    br = db.query(
        "SELECT max(trade_date) AS as_of, "
        "count(*) FILTER (WHERE chg>0) AS up, "
        "count(*) FILTER (WHERE chg<0) AS down, "
        "count(*) FILTER (WHERE chg=0) AS flat, "
        "sum(amount) AS total_amount "
        f"FROM ({_LATEST}) z WHERE rn=1")
    b = br[0] if br else {}
    return {
        "taiex": taiex,
        "breadth": {"up": b.get("up"), "down": b.get("down"), "flat": b.get("flat")},
        "total_amount": b.get("total_amount"),
        "as_of": b["as_of"].isoformat() if b.get("as_of") else None,
    }


@router.get("/index")
def index_series(days: int = 120):
    n = max(1, min(int(days), 2000))
    return db.query(
        "SELECT * FROM (SELECT trade_date, close FROM market_index "
        "WHERE index_id='TAIEX' ORDER BY trade_date DESC LIMIT %(n)s) z ORDER BY trade_date",
        {"n": n})


@router.get("/movers")
def movers(type: str = "gainers", limit: int = 15):
    n = max(1, min(int(limit), 50))
    order = {"gainers": "chg DESC", "losers": "chg ASC",
             "active": "amount DESC NULLS LAST"}.get(type, "chg DESC")   # 白名單
    sql = (
        "SELECT z.stock_id, s.name, s.industry, round((z.chg*100)::numeric,2) AS chg_pct, "
        "z.close, z.amount "
        f"FROM ({_LATEST}) z JOIN stock s USING(stock_id) "
        "WHERE z.rn=1 AND z.amount >= 20000000 AND z.chg IS NOT NULL "
        f"ORDER BY {order} LIMIT %(n)s")
    return db.query(sql, {"n": n})
