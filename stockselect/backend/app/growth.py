"""成長過濾：月營收、季營收與毛利率的連續成長次數。

裸 K 決策、突破決策與自選股的決策檢視共用；只用來篩選和顯示，不算進任何分數。
"""
from . import db
from .price_action import growth_streak


def fundamental_trends(stock_ids):
    """回傳月營收、季營收與毛利率的連續成長次數（最新一期往回算）。"""
    if not stock_ids:
        return {}
    monthly = db.query(
        "SELECT stock_id,revenue_month,revenue FROM ("
        " SELECT stock_id,revenue_month,revenue,row_number() OVER "
        " (PARTITION BY stock_id ORDER BY revenue_month DESC) rn FROM monthly_revenue "
        " WHERE stock_id=ANY(%(ids)s)) z WHERE rn<=7 ORDER BY stock_id,revenue_month DESC",
        {"ids": stock_ids})
    quarterly = db.query(
        "SELECT stock_id,period_date,revenue,gross_margin FROM ("
        " SELECT stock_id,period_date,revenue,gross_margin,row_number() OVER "
        " (PARTITION BY stock_id ORDER BY period_date DESC) rn FROM fundamentals_quarterly "
        " WHERE stock_id=ANY(%(ids)s)) z WHERE rn<=5 ORDER BY stock_id,period_date DESC",
        {"ids": stock_ids})
    months, quarters = {}, {}
    for row in monthly:
        months.setdefault(row["stock_id"], []).append(row)
    for row in quarterly:
        quarters.setdefault(row["stock_id"], []).append(row)
    out = {}
    for sid in stock_ids:
        mr, qr = months.get(sid, []), quarters.get(sid, [])
        out[sid] = {
            "revenue_month_streak": growth_streak([r["revenue"] for r in mr]),
            "revenue_quarter_streak": growth_streak([r["revenue"] for r in qr]),
            "gross_margin_quarter_streak": growth_streak([r["gross_margin"] for r in qr]),
            "revenue_month": mr[0]["revenue_month"].isoformat() if mr else None,
            "financial_quarter": qr[0]["period_date"].isoformat() if qr else None,
        }
    return out


def passes(trend, month_n=0, quarter_n=0, margin_n=0):
    """連增次數都達門檻（0＝不限）。"""
    trend = trend or {}
    return (trend.get("revenue_month_streak", 0) >= month_n
            and trend.get("revenue_quarter_streak", 0) >= quarter_n
            and trend.get("gross_margin_quarter_streak", 0) >= margin_n)
