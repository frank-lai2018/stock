"""個股 API：快照 / K 線 / 籌碼 / 基本面。"""
from fastapi import APIRouter, HTTPException

from .. import db

router = APIRouter(prefix="/api/stock", tags=["stock"])


@router.get("/{stock_id}")
def detail(stock_id: str):
    rows = db.query("SELECT * FROM mv_stock_snapshot WHERE stock_id=%(id)s", {"id": stock_id})
    if not rows:
        raise HTTPException(404, "查無此股或尚無快照（確認 mv_stock_snapshot 已建/刷新）")
    return rows[0]


@router.get("/{stock_id}/prices")
def prices(stock_id: str, tf: str = "D", bars: int = 250, adj: int = 1):
    """K 線 OHLCV。tf=D/W/M（日/週/月；W/M 由日線 resample）；adj=1 用還原價。回傳由舊到新。"""
    n = max(1, min(int(bars), 5000))
    o, h, l, c = (("adj_open", "adj_high", "adj_low", "adj_close") if adj
                  else ("open", "high", "low", "close"))   # 白名單欄位，安全內插
    if tf.upper() == "D":
        sql = (f"SELECT * FROM (SELECT trade_date, {o} AS open, {h} AS high, {l} AS low, "
               f"{c} AS close, volume FROM price_daily WHERE stock_id=%(id)s "
               f"ORDER BY trade_date DESC LIMIT %(n)s) z ORDER BY trade_date")
        return db.query(sql, {"id": stock_id, "n": n})
    unit = {"W": "week", "M": "month"}.get(tf.upper())
    if not unit:
        raise HTTPException(400, "tf 需為 D / W / M")
    sql = (f"SELECT * FROM ("
           f"  SELECT date_trunc(%(u)s, trade_date)::date AS trade_date,"
           f"         (array_agg({o} ORDER BY trade_date))[1] AS open,"
           f"         max({h}) AS high, min({l}) AS low,"
           f"         (array_agg({c} ORDER BY trade_date DESC))[1] AS close,"
           f"         sum(volume) AS volume"
           f"  FROM price_daily WHERE stock_id=%(id)s GROUP BY 1 ORDER BY 1 DESC LIMIT %(n)s"
           f") z ORDER BY trade_date")
    return db.query(sql, {"id": stock_id, "u": unit, "n": n})


@router.get("/{stock_id}/chips")
def chips(stock_id: str, days: int = 60):
    """法人淨買超 + 融資餘額 時序（由舊到新）。"""
    n = max(1, min(int(days), 500))
    inst = db.query(
        "SELECT trade_date, "
        "(coalesce(foreign_net,0)+coalesce(trust_net,0)+coalesce(dealer_self_net,0)"
        "+coalesce(dealer_hedge_net,0)+coalesce(foreign_dealer_net,0)) AS inst_net, "
        "foreign_net, trust_net "
        "FROM inst_trades WHERE stock_id=%(id)s ORDER BY trade_date DESC LIMIT %(n)s",
        {"id": stock_id, "n": n})
    margin = db.query(
        "SELECT trade_date, margin_balance, short_balance FROM margin_trading "
        "WHERE stock_id=%(id)s ORDER BY trade_date DESC LIMIT %(n)s",
        {"id": stock_id, "n": n})
    return {"inst": list(reversed(inst)), "margin": list(reversed(margin))}


@router.get("/{stock_id}/margin")
def margin(stock_id: str, tf: str = "D", bars: int = 60):
    """融資融券明細：餘額 + 增減（餘額差）+ 券資比。tf=D/W/M/Q（週月季取期末餘額）。由舊到新。"""
    n = max(1, min(int(bars), 2000))
    params = {"id": stock_id, "n": n}
    tfu = tf.upper()
    if tfu == "D":
        src = "SELECT trade_date, margin_balance, short_balance FROM margin_trading WHERE stock_id=%(id)s"
    else:
        unit = {"W": "week", "M": "month", "Q": "quarter"}.get(tfu)
        if not unit:
            raise HTTPException(400, "tf 需為 D / W / M / Q")
        params["u"] = unit
        src = ("SELECT date_trunc(%(u)s, trade_date)::date AS trade_date, "
               "(array_agg(margin_balance ORDER BY trade_date DESC))[1] AS margin_balance, "
               "(array_agg(short_balance ORDER BY trade_date DESC))[1] AS short_balance "
               "FROM margin_trading WHERE stock_id=%(id)s GROUP BY 1")
    sql = (
        "SELECT trade_date, margin_balance, short_balance, margin_chg, short_chg, short_margin_ratio FROM ("
        "  SELECT trade_date, margin_balance, short_balance, "
        "    margin_balance - lag(margin_balance) OVER (ORDER BY trade_date) AS margin_chg, "
        "    short_balance - lag(short_balance) OVER (ORDER BY trade_date) AS short_chg, "
        "    CASE WHEN margin_balance>0 THEN round(short_balance::numeric/margin_balance*100,4) END AS short_margin_ratio, "
        "    row_number() OVER (ORDER BY trade_date DESC) AS rn "
        f"  FROM ({src}) g"
        ") z WHERE rn <= %(n)s ORDER BY trade_date")
    return db.query(sql, params)


@router.get("/{stock_id}/fundamentals")
def fundamentals(stock_id: str):
    """月營收 + 季度基本面（近期）。"""
    rev = db.query(
        "SELECT revenue_month, revenue, yoy_pct, mom_pct FROM monthly_revenue "
        "WHERE stock_id=%(id)s ORDER BY revenue_month DESC LIMIT 24", {"id": stock_id})
    fq = db.query(
        "SELECT period_date, revenue, net_income, eps, roe, gross_margin, net_margin, debt_ratio "
        "FROM fundamentals_quarterly WHERE stock_id=%(id)s ORDER BY period_date DESC LIMIT 12",
        {"id": stock_id})
    return {"monthly_revenue": rev, "quarterly": list(reversed(fq))}
