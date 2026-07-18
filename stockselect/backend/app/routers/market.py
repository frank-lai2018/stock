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


INDEX_IDS = {"TAIEX", "TWSE", "TPEx"}    # 報酬指數 / 加權股價指數 / 櫃買指數


def _quote(index_id):
    """某指數最新收盤 + 當日漲跌/漲跌幅。"""
    idx = db.query("SELECT trade_date, close FROM market_index "
                   "WHERE index_id=%(id)s ORDER BY trade_date DESC LIMIT 2", {"id": index_id})
    if not idx:
        return None
    c0 = float(idx[0]["close"])
    c1 = float(idx[1]["close"]) if len(idx) > 1 else None
    return {"date": idx[0]["trade_date"].isoformat(), "close": c0,
            "change": (c0 - c1) if c1 else None,
            "pct": ((c0 / c1 - 1) * 100) if c1 else None}


@router.get("/overview")
def overview():
    taiex = _quote("TAIEX")
    tpex = _quote("TPEx")            # 櫃買指數
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
        "tpex": tpex,
        "breadth": {"up": b.get("up"), "down": b.get("down"), "flat": b.get("flat")},
        "total_amount": b.get("total_amount"),
        "as_of": b["as_of"].isoformat() if b.get("as_of") else None,
    }


@router.get("/index")
def index_series(days: int = 120, index_id: str = "TAIEX"):
    n = max(1, min(int(days), 2000))
    iid = index_id if index_id in INDEX_IDS else "TAIEX"     # 白名單
    return db.query(
        "SELECT * FROM (SELECT trade_date, close FROM market_index "
        "WHERE index_id=%(id)s ORDER BY trade_date DESC LIMIT %(n)s) z ORDER BY trade_date",
        {"id": iid, "n": n})


@router.get("/sectors")
def sectors(market: str = "上市"):
    """細產業當日漲跌（成員股等權平均）+ 代表股（漲最多者）。market=上市/上櫃。"""
    mkt = "上市%" if market == "上市" else "上櫃"     # 上市含臺灣創新板
    sql = (
        "SELECT s.industry, round(avg(z.chg*100)::numeric,2) AS avg_pct, count(*) AS n, "
        "(array_agg(s.stock_id ORDER BY z.chg DESC))[1] AS top_id, "
        "(array_agg(s.name ORDER BY z.chg DESC))[1] AS top_name, "
        "round((max(z.chg)*100)::numeric,2) AS top_pct "
        f"FROM ({_LATEST}) z JOIN stock s USING(stock_id) "
        "WHERE z.rn=1 AND z.chg IS NOT NULL AND s.market LIKE %(mkt)s AND s.industry IS NOT NULL "
        "GROUP BY s.industry ORDER BY avg_pct DESC")
    return db.query(sql, {"mkt": mkt})


@router.get("/moneyflow")
def moneyflow(market: str = "上市"):
    """各細產業成交值佔比 + 較前一日變化（百分點）。market=上市/上櫃。"""
    mkt = "上市%" if market == "上市" else "上櫃"
    sql = (
        "SELECT industry, "
        "round((today_amt::numeric / NULLIF(sum(today_amt) OVER (),0) * 100),2) AS share_pct, "
        "round(((today_amt::numeric/NULLIF(sum(today_amt) OVER (),0)) "
        "     - (prev_amt::numeric/NULLIF(sum(prev_amt) OVER (),0)))*100,2) AS chg_pct "
        "FROM (SELECT s.industry, "
        "        sum(p.amount) FILTER (WHERE p.rn=1) AS today_amt, "
        "        sum(p.amount) FILTER (WHERE p.rn=2) AS prev_amt "
        "      FROM (SELECT stock_id, amount, row_number() OVER (PARTITION BY stock_id ORDER BY trade_date DESC) rn "
        "            FROM price_daily WHERE trade_date > (SELECT max(trade_date)-15 FROM price_daily)) p "
        "      JOIN stock s USING(stock_id) "
        "      WHERE s.market LIKE %(mkt)s AND s.industry IS NOT NULL AND p.rn<=2 "
        "      GROUP BY s.industry) z "
        "ORDER BY share_pct DESC")
    return db.query(sql, {"mkt": mkt})


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
