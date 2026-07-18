"""個股 API：快照 / K 線 / 籌碼 / 基本面。"""
from fastapi import APIRouter, HTTPException

from .. import db, patterns, vpa

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


@router.get("/{stock_id}/levels")
def levels(stock_id: str, bars: int = 120):
    """自動偵測近期壓力(現價上方)與頸線/支撐(現價下方)：轉折高低點群集，回傳價位。"""
    n = max(30, min(int(bars), 800))
    rows = db.query(
        "SELECT * FROM (SELECT trade_date, adj_high AS h, adj_low AS l, adj_close AS c "
        "FROM price_daily WHERE stock_id=%(id)s ORDER BY trade_date DESC LIMIT %(n)s) z "
        "ORDER BY trade_date", {"id": stock_id, "n": n})
    if len(rows) < 20:
        return []
    highs = [float(r["h"]) for r in rows]
    lows = [float(r["l"]) for r in rows]
    close = float(rows[-1]["c"])
    k = 3                                                # pivot：±k 根內的極值
    ph = [highs[i] for i in range(k, len(rows) - k) if highs[i] == max(highs[i - k:i + k + 1])]
    pl = [lows[i] for i in range(k, len(rows) - k) if lows[i] == min(lows[i - k:i + k + 1])]

    def cluster(vals):
        out = []
        for v in sorted(vals):
            if out and abs(v - out[-1]["m"]) <= 0.02 * out[-1]["m"]:   # 2% 內視為同一價位
                c = out[-1]; c["v"].append(v); c["m"] = sum(c["v"]) / len(c["v"])
            else:
                out.append({"v": [v], "m": v})
        return [{"price": round(c["m"], 2), "n": len(c["v"])} for c in out]

    res = [c for c in cluster(ph) if c["price"] > close * 1.005]        # 壓力：現價上方
    sup = [c for c in cluster(pl) if c["price"] < close * 0.995]        # 支撐群：現價下方
    out = []
    if res:
        res.sort(key=lambda c: (-c["n"], c["price"] - close))          # 多測試優先、其次最近
        out.append({"type": "resistance", "label": "壓力", "price": res[0]["price"], "touches": res[0]["n"]})
    if sup:
        neck = sorted(sup, key=lambda c: (-c["n"], close - c["price"]))[0]   # 頸線＝測試最多次
        out.append({"type": "neckline", "label": "頸線", "price": neck["price"], "touches": neck["n"]})
        near = sorted(sup, key=lambda c: close - c["price"])[0]              # 近期支撐＝離現價最近
        if abs(near["price"] - neck["price"]) > 0.02 * neck["price"]:        # 與頸線不同價才另畫
            out.append({"type": "support", "label": "近期支撐", "price": near["price"], "touches": near["n"]})
    return out


@router.get("/{stock_id}/patterns")
def stock_patterns(stock_id: str, days: int = 90):
    """近 N 交易日每根 K 棒偵測到的陰陽線型態（由舊到新）。"""
    n = max(10, min(int(days), 500))
    rows = db.query(
        "SELECT trade_date, adj_open AS open, adj_high AS high, adj_low AS low, adj_close AS close "
        "FROM price_daily WHERE stock_id=%(id)s ORDER BY trade_date DESC LIMIT %(n)s",
        {"id": stock_id, "n": n})
    bars = list(reversed(rows))
    out = []
    for i in range(len(bars)):
        window = bars[max(0, i - 8):i + 1]          # 帶前文（含趨勢判斷）
        for key in patterns.detect(window):
            d = bars[i]["trade_date"]
            out.append({"date": d.isoformat() if hasattr(d, "isoformat") else str(d),
                        "pattern": key, "name": patterns.CATALOG[key][0], "dir": patterns.CATALOG[key][1]})
    return out


@router.get("/{stock_id}/vpa")
def stock_vpa(stock_id: str, days: int = 90):
    """近 N 交易日每根 K 棒的 VPA 價量訊號（主力承接/測試/出貨；由舊到新）。"""
    n = max(30, min(int(days), 500))
    rows = db.query(
        "SELECT trade_date, adj_open AS open, adj_high AS high, adj_low AS low, "
        "adj_close AS close, volume FROM price_daily WHERE stock_id=%(id)s "
        "ORDER BY trade_date DESC LIMIT %(n)s",
        {"id": stock_id, "n": n + 25})            # 多取 25 根供前文計算均量
    bars = list(reversed(rows))
    start = max(0, len(bars) - n)                  # 只回報近 n 根的訊號
    out = []
    for i in range(start, len(bars)):
        window = bars[max(0, i - 24):i + 1]        # 帶前文（均量/均振幅/趨勢）
        for key in vpa.detect(window):
            name, direction, phase = vpa.CATALOG[key]
            d = bars[i]["trade_date"]
            out.append({"date": d.isoformat() if hasattr(d, "isoformat") else str(d),
                        "signal": key, "name": name, "dir": direction, "phase": phase})
    return out


@router.get("/{stock_id}/etf")
def etf_info(stock_id: str):
    """ETF 淨值/規模 + 折溢價（折溢價 = (收盤市價 − 淨值)/淨值）。無資料回 latest=None。"""
    rows = db.query(
        "SELECT trade_date, nav, prev_nav, units, aum, nav_chg_pct FROM etf_daily "
        "WHERE stock_id=%(id)s ORDER BY trade_date DESC LIMIT 60", {"id": stock_id})
    if not rows:
        return {"latest": None, "history": []}
    latest = dict(rows[0])
    px = db.query("SELECT close FROM price_daily WHERE stock_id=%(id)s AND trade_date=%(d)s",
                  {"id": stock_id, "d": latest["trade_date"]})
    if not px:                                        # 淨值日無對應收盤 → 取最近收盤
        px = db.query("SELECT close FROM price_daily WHERE stock_id=%(id)s "
                      "ORDER BY trade_date DESC LIMIT 1", {"id": stock_id})
    close = float(px[0]["close"]) if px and px[0].get("close") is not None else None
    nav = float(latest["nav"]) if latest.get("nav") is not None else None
    latest["close"] = close
    latest["premium_discount"] = round((close - nav) / nav * 100, 3) if (close and nav) else None
    return {"latest": latest, "history": list(reversed(rows))}


@router.get("/{stock_id}/dividends")
def dividends(stock_id: str):
    """配息/配股歷史（近 3 年）+ 近 12 個月現金配息合計與年化配息率（ETF/個股皆適用）。"""
    rows = db.query(
        "SELECT announce_date, year_label, cash_dividend, stock_dividend, "
        "ex_cash_date, ex_stock_date, cash_pay_date FROM dividend "
        "WHERE stock_id=%(id)s ORDER BY COALESCE(ex_cash_date, announce_date) DESC LIMIT 36",
        {"id": stock_id})
    # 近 12 個月現金配息合計（以除息日為準）
    ttm = db.query(
        "SELECT COALESCE(sum(cash_dividend),0) AS ttm_cash, count(*) AS n "
        "FROM dividend WHERE stock_id=%(id)s AND ex_cash_date >= "
        "(SELECT max(trade_date) FROM price_daily) - 365", {"id": stock_id})
    px = db.query("SELECT close FROM price_daily WHERE stock_id=%(id)s "
                  "ORDER BY trade_date DESC LIMIT 1", {"id": stock_id})
    for r in rows:                                    # 去除 FinMind 小數雜訊（6.000036 → 6）
        for k in ("cash_dividend", "stock_dividend"):
            if r.get(k) is not None:
                r[k] = round(float(r[k]), 4)
    ttm_cash = round(float(ttm[0]["ttm_cash"]), 4) if ttm else 0.0
    close = float(px[0]["close"]) if px and px[0].get("close") else None
    yld = (ttm_cash / close * 100) if (close and ttm_cash) else None
    return {"items": rows, "ttm_cash": ttm_cash, "ttm_count": ttm[0]["n"] if ttm else 0,
            "ttm_yield": round(yld, 2) if yld is not None else None}


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
    return {"monthly_revenue": rev, "quarterly": fq}   # 皆降序（新→舊）
