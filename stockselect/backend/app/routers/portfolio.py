"""持股診斷 / 交易帳 API。

交易帳 trade_log（單次買/賣，記日期、手續費、證交稅）為真實來源；
持股（未平倉部位）與已實現損益皆由 ledger FIFO 引擎推導。
"""
from datetime import date

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import db, diagnose, ledger, patterns, review
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
        " shares     NUMERIC     NOT NULL,"       # 股數
        " price      NUMERIC     NOT NULL,"       # 每股價
        " fee        NUMERIC,"                    # 手續費（該筆總額）
        " tax        NUMERIC,"                    # 證交稅（賣出）
        " trade_type VARCHAR(20),"               # 交易類別（現股/當沖/融資/融券）
        " pnl        NUMERIC,"                    # 券商申報損益（賣出，匯入用）
        " note       VARCHAR(200),"
        " created_at TIMESTAMP DEFAULT now())")
    db.execute("ALTER TABLE trade_log ADD COLUMN IF NOT EXISTS trade_type VARCHAR(20)")  # 舊表補欄
    db.execute("ALTER TABLE trade_log ADD COLUMN IF NOT EXISTS pnl NUMERIC")
    db.execute("CREATE INDEX IF NOT EXISTS idx_trade_stock ON trade_log(stock_id)")
    _ensured = True


class Trade(BaseModel):
    stock_id: str
    action: str                       # buy / sell
    trade_date: str                   # YYYY-MM-DD
    shares: float
    price: float
    fee: float | None = None
    tax: float | None = None
    trade_type: str | None = None     # 現股 / 現股當沖 / 融資 / 融券…
    note: str | None = None


def _last_patterns(ids):
    if not ids:
        return {}
    rows = db.query(
        "SELECT stock_id, trade_date, adj_open AS open, adj_high AS high, adj_low AS low, adj_close AS close "
        "FROM (SELECT stock_id, trade_date, adj_open, adj_high, adj_low, adj_close, "
        "  row_number() OVER (PARTITION BY stock_id ORDER BY trade_date DESC) AS rn "
        "  FROM price_daily WHERE stock_id = ANY(%(ids)s)) z WHERE rn <= 24 ORDER BY stock_id, trade_date",
        {"ids": ids})
    by = {}
    for b in rows:
        by.setdefault(b["stock_id"], []).append(b)
    return {sid: [{"name": patterns.CATALOG[k][0], "dir": patterns.CATALOG[k][1]}
                  for k in patterns.detect(bars)] for sid, bars in by.items()}


@router.get("/portfolio")
def portfolio(year: int | None = None, peak_min: float = 0.10,
              giveback: float = 0.5, stop_pct: float = -0.10):
    """未平倉持股 + 每檔診斷 + 未實現損益 + 停利監控，及組合總覽。

    停利監控（回應交易復盤的發現：賠錢單中途平均曾浮盈 +3.94%，17 筆曾賺逾 10% 最後收黑）：
    以「最後一次加碼日」為起點（均價要到部位建完才成立；用最早買進日會把加碼前的高點
    當成浮盈），取期間最高價算最大浮盈，再看目前回吐了多少獲利。
      peak_min  觸發監控的最低曾浮盈（0.10＝曾賺 10% 才管）
      giveback  回吐比例達此值 → alert=trim（該考慮停利）；達 0.6 倍 → watch
      stop_pct  未實現跌破此值 → alert=stop（停損提醒）
    最高價用未還原價，與實際成交價同基準。

    未平倉部位／未實現／診斷分佈＝目前持股（當下快照）；已實現損益與總損益
    只計算 `year`（預設當年度）內賣出配對的部分。回傳 years 供前端下拉。
    """
    _ensure()
    txns = db.query("SELECT id, stock_id, action, trade_date, shares, price, fee, tax FROM trade_log")
    cur_year = date.today().year
    years = sorted({t["trade_date"].year for t in txns} | {cur_year}, reverse=True)
    year = year or cur_year
    if not txns:
        return {"items": [], "summary": None, "realized": [], "as_of": None,
                "year": year, "years": years}

    by = {}
    for t in txns:
        by.setdefault(t["stock_id"], []).append(t)
    leds = {sid: ledger.build(ts) for sid, ts in by.items()}

    open_ids = [sid for sid, l in leds.items() if l["net_shares"] > 1e-9]
    snaps = {r["stock_id"]: r for r in
             db.query("SELECT * FROM mv_stock_snapshot WHERE stock_id = ANY(%(ids)s)", {"ids": open_ids})} \
        if open_ids else {}
    pats = _last_patterns(open_ids)

    # 進場後最高價 + 那天的日期（停利監控用；未還原價，對齊實際成交價）
    peaks = {}
    spans = [(sid, leds[sid]["last_open_date"]) for sid in open_ids if leds[sid].get("last_open_date")]
    if spans:
        for r in db.query(
                "SELECT DISTINCT ON (p.stock_id) p.stock_id, p.trade_date, p.high FROM price_daily p "
                "JOIN unnest(%(ids)s::text[], %(d0)s::date[]) AS r(sid, d0) "
                "  ON p.stock_id = r.sid AND p.trade_date >= r.d0 "
                "ORDER BY p.stock_id, p.high DESC, p.trade_date",
                {"ids": [x[0] for x in spans], "d0": [x[1] for x in spans]}):
            peaks[r["stock_id"]] = (float(r["high"]), r["trade_date"].isoformat())
    alerts_cnt = {"trim": 0, "watch": 0, "stop": 0}

    items, as_of = [], None
    tot_mv = tot_cost = 0.0
    levels_cnt = {"strong": 0, "watch": 0, "reduce": 0}
    rs_sum = rs_n = accum_n = distrib_n = 0
    ind_val = {}
    for sid in open_ids:
        l = leds[sid]
        s = snaps.get(sid)
        close = float(s["close"]) if s and s.get("close") is not None else None
        net_shares, avg_cost = l["net_shares"], l["avg_cost"]
        mkt_val = net_shares * close if close is not None else None
        cost_val = net_shares * avg_cost if avg_cost is not None else None
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

        # 停利監控：曾經最多賺多少 → 現在回吐了幾成
        pk, pk_date = peaks.get(sid, (None, None))
        peak_gain = (pk / avg_cost - 1) if (pk and avg_cost) else None
        give = (peak_gain - unreal_pct) if (peak_gain is not None and unreal_pct is not None) else None
        give_ratio = (give / peak_gain) if (peak_gain and peak_gain > 0 and give is not None) else None
        alert = None
        if unreal_pct is not None and unreal_pct <= stop_pct:
            alert = "stop"                                   # 已虧損逾門檻 → 停損提醒
        elif peak_gain is not None and peak_gain >= peak_min and give_ratio is not None:
            if give_ratio >= giveback:
                alert = "trim"                               # 賺過又吐回大半 → 該考慮停利
            elif give_ratio >= giveback * 0.6:
                alert = "watch"
        if alert:
            alerts_cnt[alert] += 1

        if s and s.get("as_of_date"):
            as_of = s["as_of_date"].isoformat()
        items.append({
            "stock_id": sid, "name": s["name"] if s else sid, "industry": s.get("industry") if s else None,
            "shares": net_shares, "cost": avg_cost, "close": close,
            "market_value": mkt_val, "unrealized": unreal, "unrealized_pct": unreal_pct,
            "realized": l["realized_pnl"],
            "score": dg["score"], "level": dg["level"], "facets": dg["facets"], "reasons": dg["reasons"],
            "rs_rating": s.get("rs_rating") if s else None,
            "vpa_accum_20d": s.get("vpa_accum_20d") if s else None,
            "vpa_distrib_20d": s.get("vpa_distrib_20d") if s else None,
            "support": sup, "resistance": res,
            "last_patterns": pats.get(sid, []),
            "entry_date": leds[sid].get("first_open_date"),
            "basis_date": leds[sid].get("last_open_date"),      # 均價成立日＝最後一次加碼
            "peak_price": pk, "peak_date": pk_date, "peak_gain": peak_gain,
            "giveback": give, "giveback_ratio": give_ratio, "alert": alert,
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

    # 已實現：只取 year 當年賣出配對完成的交易（依賣出日，新到舊）
    ystr = str(year)
    realized = []
    for sid, l in leds.items():
        nm = snaps.get(sid, {}).get("name") if snaps.get(sid) else None
        if not nm:
            r = db.query("SELECT name FROM stock WHERE stock_id=%(id)s", {"id": sid})
            nm = r[0]["name"] if r else sid
        for c in l["closed"]:
            if c["sell_date"][:4] == ystr:
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
        "alerts": alerts_cnt,
        "year": year,
    }
    return {"items": items, "summary": summary, "realized": realized, "as_of": as_of,
            "year": year, "years": years}


@router.get("/trades/review")
def trades_review():
    """交易復盤：全部已實現交易（FIFO 配對）的橫切分析。

    回傳 summary（勝率/賺賠比/獲利因子/期望值/賺賠各抱多久）、cuts（分產業・持有天數・
    進場動能・進場本益比・站季線與否・交易類別）、periods（年月損益與累計）、items（明細）。
    進場情境用買進日當下的資料重算（point-in-time），不吃今天的快照。
    """
    _ensure()
    txns = db.query("SELECT id, stock_id, action, trade_date, shares, price, fee, tax, trade_type "
                    "FROM trade_log")
    if not txns:
        return {"count": 0, "summary": None, "cuts": {}, "periods": {"months": [], "years": []}, "items": []}

    by = {}
    for t in txns:
        by.setdefault(t["stock_id"], []).append(t)
    closed = []
    for sid, ts in by.items():
        for c in ledger.build(ts)["closed"]:
            closed.append({**c, "stock_id": sid})
    if not closed:
        return {"count": 0, "summary": None, "cuts": {}, "periods": {"months": [], "years": []}, "items": []}

    # 每檔只載自己的交易區間（買進日往前 400 天供均線/52週高，到最後賣出日）
    span = {}
    for c in closed:
        a, b = span.get(c["stock_id"], (c["buy_date"], c["sell_date"]))
        span[c["stock_id"]] = (min(a, c["buy_date"]), max(b, c["sell_date"]))
    ids = sorted(span)
    d0 = [span[i][0] for i in ids]
    d1 = [span[i][1] for i in ids]
    rng = {"ids": ids, "d0": d0, "d1": d1}
    meta = {r["stock_id"]: r for r in
            db.query("SELECT stock_id, name, industry FROM stock WHERE stock_id = ANY(%(ids)s)", {"ids": ids})}
    px, val = {}, {}
    for r in db.query(
            "SELECT p.stock_id, p.trade_date, p.close, p.high, p.low, p.adj_close FROM price_daily p "
            "JOIN unnest(%(ids)s::text[], %(d0)s::date[], %(d1)s::date[]) AS r(sid, a, b) "
            "  ON p.stock_id = r.sid AND p.trade_date BETWEEN r.a - 400 AND r.b "
            "ORDER BY p.stock_id, p.trade_date", rng):
        p = px.setdefault(r["stock_id"], {"d": [], "c": [], "h": [], "l": [], "a": []})
        p["d"].append(r["trade_date"].isoformat())
        p["c"].append(float(r["close"] or 0)); p["h"].append(float(r["high"] or 0))
        p["l"].append(float(r["low"] or 0)); p["a"].append(float(r["adj_close"] or 0))
    for r in db.query(
            "SELECT v.stock_id, v.trade_date, v.per FROM valuation_daily v "
            "JOIN unnest(%(ids)s::text[], %(d0)s::date[], %(d1)s::date[]) AS r(sid, a, b) "
            "  ON v.stock_id = r.sid AND v.trade_date BETWEEN r.a - 10 AND r.b "
            "ORDER BY v.stock_id, v.trade_date", rng):
        v = val.setdefault(r["stock_id"], {"d": [], "per": []})
        v["d"].append(r["trade_date"].isoformat()); v["per"].append(r["per"])

    rows = review.enrich(closed, px, val, meta)
    rows.sort(key=lambda r: r["sell_date"], reverse=True)
    return {"count": len(rows), "summary": review.summarize(rows), "cuts": review.cuts(rows),
            "periods": review.by_period(rows), "items": rows}


@router.get("/trades")
def list_trades(year: int | None = None, stock_id: str | None = None):
    """原始交易明細（新到舊）。給 year 只回該年度、給 stock_id 只回該檔；不給則全部。"""
    _ensure()
    conds, params = [], {}
    if year:
        conds.append("extract(year FROM t.trade_date) = %(y)s")
        params["y"] = year
    if stock_id:
        conds.append("t.stock_id = %(sid)s")
        params["sid"] = stock_id.strip()
    where = ("WHERE " + " AND ".join(conds)) if conds else ""
    rows = db.query(
        "SELECT t.id, t.stock_id, s.name, t.action, t.trade_date, t.shares, t.price, "
        "t.fee, t.tax, t.trade_type, t.pnl, t.note "
        "FROM trade_log t LEFT JOIN stock s USING(stock_id) "
        f"{where} ORDER BY t.trade_date DESC, t.id DESC",
        params or None)
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
    if t.shares is None or t.shares <= 0 or t.price is None or t.price < 0:
        raise HTTPException(400, "股數需 >0、價格需 ≥0")
    rows = db.execute(
        "INSERT INTO trade_log (stock_id, action, trade_date, shares, price, fee, tax, trade_type, note) "
        "VALUES (%(id)s, %(act)s, %(d)s::date, %(shares)s, %(price)s, %(fee)s, %(tax)s, %(tt)s, %(note)s) "
        "RETURNING id",
        {"id": sid, "act": act, "d": t.trade_date, "shares": t.shares, "price": t.price,
         "fee": t.fee, "tax": t.tax, "tt": t.trade_type, "note": t.note}, returning=True)
    return {"ok": True, "id": rows[0]["id"]}


@router.delete("/trades/{trade_id}")
def delete_trade(trade_id: int):
    _ensure()
    n = db.execute("DELETE FROM trade_log WHERE id=%(id)s", {"id": trade_id})
    return {"ok": True, "deleted": n}
