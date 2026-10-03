"""族群 API：L2 產業鏈節點／L3 市場題材的熱度排行、輪動熱力圖、成分明細、個股所屬族群。

資料來源：theme／stock_theme（fetch_tpex_chain.py、theme_candidates.py）、theme_daily（build_theme_daily.py 每晚產生）。
成分直查 stock_theme，你在 CSV 確認／否決後立即生效，不必等 mv_stock_snapshot 刷新。說明見 族群分類設計.md。
"""
from datetime import timedelta
from statistics import median

from fastapi import APIRouter, HTTPException, Query

from .. import db

router = APIRouter(prefix="/api/themes", tags=["themes"])

# L2 顯示名稱帶上所屬鏈：「半導體 > IC設計 > 光通訊IC」（theme.name 本身只存節點名）
_NAME = "CASE WHEN t.layer = 2 AND c.name IS NOT NULL THEN c.name || ' > ' || t.name ELSE t.name END"
_CHAIN = ("LEFT JOIN theme c ON t.layer = 2 AND c.parent_code IS NULL "
          "AND c.code = split_part(t.code, ':', 1) || ':' || split_part(t.code, ':', 2) AND c.code <> t.code")
# 有效成分：L2 取櫃買確認的；L3 取已確認＋起手名單（尚未複核）
_ACTIVE = ("st.valid_to IS NULL AND ((t.layer = 2 AND st.status = 'confirmed') "
           "OR (t.layer = 3 AND st.status IN ('confirmed', 'seed')))")


def _dates(n):
    """theme_daily 最新的 n 個交易日（新到舊）。"""
    return [r["trade_date"] for r in db.query(
        "SELECT DISTINCT trade_date FROM theme_daily ORDER BY trade_date DESC LIMIT %(n)s", {"n": n})]


AETF_DAYS = 5                 # 主動 ETF 淨買：最近幾個持股日


def _aetf_dates():
    """etf_flow 最新 AETF_DAYS 個持股日（主動 ETF 表還沒建時回空，族群頁照常顯示）。"""
    if not db.query("SELECT to_regclass('public.etf_flow') AS t")[0]["t"]:
        return []
    return [r["trade_date"] for r in db.query(
        "SELECT DISTINCT trade_date FROM etf_flow ORDER BY trade_date DESC LIMIT %(n)s", {"n": AETF_DAYS})]


def _aetf_by_theme(codes, ds):
    """{族群代碼: 主動 ETF 近 N 個持股日的淨買}：成分股（同 members：含子節點）的實際買賣金額加總（amount＝股數變化
    × 收盤價），另附主動調整金額（扣掉申購贖回的等比例增減）、淨買／淨賣檔數、占成分股同期成交額的比例。"""
    if not codes or not ds:
        return {}
    rows = db.query(
        "WITH ff AS (SELECT stock_id, sum(amount) AS amt, sum(active_amount) AS act FROM etf_flow "
        "            WHERE trade_date = ANY(%(ds)s) AND action <> 'corp' GROUP BY stock_id), "
        "mem AS (SELECT DISTINCT t.code, st.stock_id FROM theme t "
        "        JOIN theme k ON (k.code = t.code OR k.parent_code = t.code) AND k.is_active "
        "        JOIN stock_theme st ON st.theme_id = k.theme_id AND st.valid_to IS NULL "
        "             AND ((k.layer = 2 AND st.status = 'confirmed') OR (k.layer = 3 AND st.status IN ('confirmed', 'seed'))) "
        "        WHERE t.code = ANY(%(codes)s)), "
        "tv AS (SELECT stock_id, sum(amount) AS turnover FROM price_daily "
        "       WHERE trade_date = ANY(%(ds)s) AND stock_id IN (SELECT stock_id FROM mem) GROUP BY stock_id) "
        "SELECT mem.code, sum(ff.amt) AS aetf_net, sum(ff.act) AS aetf_active, "
        "       count(*) FILTER (WHERE ff.amt > 0) AS aetf_n_buy, count(*) FILTER (WHERE ff.amt < 0) AS aetf_n_sell, "
        "       sum(ff.amt) / NULLIF(sum(tv.turnover), 0) AS aetf_ratio "
        "FROM mem LEFT JOIN ff USING (stock_id) LEFT JOIN tv USING (stock_id) GROUP BY mem.code",
        {"ds": ds, "codes": list(codes)})
    return {r["code"]: r for r in rows}


@router.get("/ranking")
def ranking(layer: int = Query(3, ge=2, le=3), limit: int = Query(50, ge=1, le=300)):
    """最新一日熱度排行；rank_chg＝與 5 個交易日前相比的名次變化（正＝上升）；
    aetf_*＝主動 ETF 近 5 個持股日在這個族群的淨買（見 _aetf_by_theme；不計入熱度分數）。"""
    ds = _dates(6)
    if not ds:
        return {"as_of": None, "prev": None, "rows": [], "aetf_dates": []}
    as_of, prev = ds[0], ds[-1]
    rows = db.query(
        f"SELECT t.code, {_NAME} AS name, t.layer, d.n_members, d.ret_1d, d.ret_5d, d.ret_20d, "
        "       d.breadth_ma20, d.high20_pct, d.vol_ratio, d.inst_ratio, d.heat_score, d.heat_rank, "
        "       p.heat_rank - d.heat_rank AS rank_chg, "
        "       (SELECT count(*) FROM stock_theme st WHERE st.theme_id = t.theme_id "
        "          AND st.valid_to IS NULL AND st.status = 'seed') AS n_seed "
        f"FROM theme_daily d JOIN theme t USING (theme_id) {_CHAIN} "
        "LEFT JOIN theme_daily p ON p.theme_id = d.theme_id AND p.trade_date = %(prev)s "
        "WHERE d.trade_date = %(as_of)s AND t.layer = %(layer)s "
        "ORDER BY d.heat_rank LIMIT %(limit)s",
        {"as_of": as_of, "prev": prev, "layer": layer, "limit": limit})
    fds = _aetf_dates()
    aetf = _aetf_by_theme([r["code"] for r in rows], fds)
    for r in rows:
        a = aetf.get(r["code"]) or {}
        for k in ("aetf_net", "aetf_active", "aetf_n_buy", "aetf_n_sell", "aetf_ratio"):
            r[k] = a.get(k)
    return {"as_of": as_of.isoformat(), "prev": prev.isoformat(), "rows": rows,
            "aetf_dates": [d.isoformat() for d in sorted(fds)]}


_METRICS = {"heat_score", "heat_rank", "ret_5d", "ret_20d", "breadth_ma20"}


@router.get("/heatmap")
def heatmap(layer: int = Query(3, ge=2, le=3), days: int = Query(20, ge=5, le=60),
            top: int = Query(20, ge=1, le=60), metric: str = "heat_score"):
    """輪動熱力圖：最新熱度前 top 名的族群 × 近 days 個交易日的指標值。"""
    if metric not in _METRICS:
        raise HTTPException(400, f"metric 只接受 {sorted(_METRICS)}")
    ds = _dates(days)
    if not ds:
        return {"dates": [], "themes": [], "cells": []}
    themes = db.query(
        f"SELECT t.theme_id, t.code, {_NAME} AS name FROM theme_daily d JOIN theme t USING (theme_id) {_CHAIN} "
        "WHERE d.trade_date = %(d)s AND t.layer = %(layer)s ORDER BY d.heat_rank LIMIT %(top)s",
        {"d": ds[0], "layer": layer, "top": top})
    ids = [t["theme_id"] for t in themes]
    vals = db.query(
        f"SELECT theme_id, trade_date, {metric} AS v FROM theme_daily "
        "WHERE theme_id = ANY(%(ids)s) AND trade_date >= %(since)s",
        {"ids": ids, "since": ds[-1]}) if ids else []
    dates = sorted(ds)
    xi = {d: i for i, d in enumerate(dates)}
    yi = {t["theme_id"]: i for i, t in enumerate(themes)}
    cells = [[xi[v["trade_date"]], yi[v["theme_id"]], None if v["v"] is None else float(v["v"])]
             for v in vals if v["trade_date"] in xi]
    return {"dates": [d.isoformat() for d in dates],
            "themes": [{"code": t["code"], "name": t["name"]} for t in themes],
            "metric": metric, "cells": cells}


@router.get("/members")
def members(code: str):
    """族群明細：最新熱度＋成分股（L2 節點含子節點成分），標出領頭羊與落後補漲候選。"""
    meta = db.query(
        f"SELECT t.theme_id, t.code, {_NAME} AS name, t.layer, t.keywords, t.note "
        f"FROM theme t {_CHAIN} WHERE t.code = %(code)s AND t.is_active", {"code": code})
    if not meta:
        raise HTTPException(404, f"找不到族群 {code}")
    m = meta[0]
    ds = _dates(1)
    daily = db.query(
        "SELECT n_members, ret_1d, ret_5d, ret_20d, breadth_ma20, high20_pct, vol_ratio, inst_ratio, "
        "       heat_score, heat_rank FROM theme_daily WHERE theme_id = %(id)s AND trade_date = %(d)s",
        {"id": m["theme_id"], "d": ds[0]}) if ds else []
    rows = db.query(
        "WITH mem AS ( "
        "  SELECT DISTINCT ON (st.stock_id) st.stock_id, st.role, st.status, st.source, st.evidence "
        "  FROM stock_theme st JOIN theme t USING (theme_id) "
        f"  WHERE (t.code = %(code)s OR t.parent_code = %(code)s) AND t.is_active AND {_ACTIVE} "
        "  ORDER BY st.stock_id, (st.status = 'confirmed') DESC), "
        "px AS ( "
        "  SELECT stock_id, array_agg(adj_close ORDER BY trade_date DESC) AS c FROM price_daily "
        "  WHERE stock_id IN (SELECT stock_id FROM mem) "
        "    AND trade_date > (SELECT max(trade_date) - 45 FROM price_daily) GROUP BY stock_id) "
        "SELECT mem.*, s.name, s.industry, v.close, v.rs_rating, v.in_universe, v.inst_net_20d, v.amt20, "
        "       v.trend_template, (v.adj_close > v.ma20) AS above_ma20, (v.adj_close > v.ma60) AS above_ma60, "
        "       px.c[1] / NULLIF(px.c[2], 0) - 1 AS ret_1d, "
        "       px.c[1] / NULLIF(px.c[6], 0) - 1 AS ret_5d, "
        "       px.c[1] / NULLIF(px.c[21], 0) - 1 AS ret_20d "
        "FROM mem JOIN stock s USING (stock_id) "
        "LEFT JOIN mv_stock_snapshot v USING (stock_id) LEFT JOIN px USING (stock_id) "
        "ORDER BY ret_20d DESC NULLS LAST",
        {"code": code})
    # 領頭羊＝可比較母體中 20 日報酬前 3；落後補漲＝低於族群中位數但仍站上季線
    live = [r for r in rows if r["in_universe"] and r["ret_20d"] is not None]   # rows 已依 20 日報酬排序
    live_ids = {r["stock_id"] for r in live}
    rets = sorted(float(r["ret_20d"]) for r in live)
    med = rets[len(rets) // 2] if rets else None
    leaders = {r["stock_id"] for r in live[:3]}
    for r in rows:
        r["tag"] = None
        if r["stock_id"] in leaders:
            r["tag"] = "leader"
        elif med is not None and r["stock_id"] in live_ids and float(r["ret_20d"]) < med and r["above_ma60"]:
            r["tag"] = "laggard"
    fds = _aetf_dates()                                  # 主動 ETF 近 5 個持股日的淨買（實際買賣／主動調整金額）
    ff = {x["stock_id"]: x for x in db.query(
        "SELECT stock_id, sum(amount) AS aetf_net, sum(active_amount) AS aetf_active FROM etf_flow "
        "WHERE trade_date = ANY(%(ds)s) AND action <> 'corp' AND stock_id = ANY(%(ids)s) GROUP BY stock_id",
        {"ds": fds, "ids": [r["stock_id"] for r in rows]})} if fds and rows else {}
    for r in rows:
        a = ff.get(r["stock_id"]) or {}
        r["aetf_net"], r["aetf_active"] = a.get("aetf_net"), a.get("aetf_active")
    return {"code": m["code"], "name": m["name"], "layer": m["layer"], "keywords": m["keywords"],
            "note": m["note"], "as_of": ds[0].isoformat() if ds else None,
            "daily": daily[0] if daily else None, "median_ret_20d": med, "rows": rows,
            "aetf": _aetf_by_theme([m["code"]], fds).get(m["code"]), "aetf_dates": [d.isoformat() for d in sorted(fds)]}


@router.get("/today")
def today(top: int = Query(10, ge=1, le=20), picks: int = Query(3, ge=0, le=5)):
    """首頁「今日族群熱度」：市場題材排行前 top 名、產業鏈節點前 3 名，
    以及前 picks 名題材的領頭羊／落後補漲（同 build_theme_daily.py 印在 nightly log 的那段）。"""
    r3 = ranking(layer=3, limit=top)
    r2 = ranking(layer=2, limit=3)
    brief = lambda r: {"stock_id": r["stock_id"], "name": r["name"], "ret_20d": r["ret_20d"]}   # noqa: E731
    spotlight = []
    for row in r3["rows"][:picks]:
        m = members(row["code"])
        spotlight.append({
            "code": row["code"], "name": row["name"],
            "leaders": [brief(x) for x in m["rows"] if x["tag"] == "leader"],
            "laggards": [brief(x) for x in reversed(m["rows"]) if x["tag"] == "laggard"][:3],   # 最落後的在前
        })
    return {"as_of": r3["as_of"], "prev": r3["prev"], "rows": r3["rows"],
            "nodes": r2["rows"], "spotlight": spotlight}


# ── 美台題材對照（說明見 美台題材對照.md）────────────────────────────────────────────
# 資料：fetch_us_prices.py／build_us_theme_daily.py 每晚產生；跟隨度由 analyze_us_tw_themes.py 寫入 us_theme_follow。
US_INDICATORS = [("SPY", "S&P 500"), ("QQQ", "Nasdaq 100"), ("^SOX", "費城半導體"), ("TSM", "台積電 ADR")]


def _f(v):
    return None if v is None else float(v)


def _us_returns(symbols, as_of):
    """{代號: {ret_1d, ret_5d, ret_20d}}：截至 as_of 的還原價報酬；最新一筆不是 as_of（那天沒抓到）就不算。"""
    rows = db.query(
        "SELECT symbol, max(trade_date) AS last, array_agg(adj_close ORDER BY trade_date DESC) AS c "
        "FROM us_price_daily WHERE symbol = ANY(%(s)s) AND trade_date <= %(d)s AND trade_date > %(d)s::date - 45 "
        "GROUP BY symbol", {"s": sorted(symbols), "d": as_of})
    out = {}
    for r in rows:
        c = [float(v) for v in r["c"]] if r["last"] == as_of else []
        ret = lambda k: c[0] / c[k] - 1 if len(c) > k and c[k] else None   # noqa: E731
        out[r["symbol"]] = {"ret_1d": ret(1), "ret_5d": ret(5), "ret_20d": ret(20)}
    return out


@router.get("/us-compare")
def us_compare():
    """美台題材對照：美股最新一個交易日（台灣隔天清晨收盤）各題材籃子的漲跌、台股接著那個交易日的反應、
    20 日強弱四象限（兩邊各以 20 日漲幅高於同日題材中位數＝強）與隔天跟隨度。"""
    empty = {"us_date": None, "themes": [], "indicators": []}
    if not db.query("SELECT to_regclass('public.us_theme_daily') AS t")[0]["t"]:
        return empty
    us_date = db.query("SELECT max(trade_date) AS d FROM us_theme_daily")[0]["d"]
    ds = _dates(1)
    if us_date is None or not ds:
        return empty
    tw_date = ds[0]
    # 美股這一場之後的第一個台股交易日＝台股對它的反應；週末、連假或台股還沒收盤時是 None
    react_date = db.query("SELECT min(trade_date) AS d FROM theme_daily WHERE trade_date > %(u)s",
                          {"u": us_date})[0]["d"]
    rows = db.query(
        "SELECT t.theme_id, t.code, t.name, "
        "       tw.heat_rank AS tw_rank, tw.ret_1d AS tw_ret_1d, tw.ret_5d AS tw_ret_5d, tw.ret_20d AS tw_ret_20d, "
        "       r.ret_1d AS react_ret, u.heat_rank AS us_rank, u.n_members AS us_members, u.ret_1d AS us_ret_1d, "
        "       u.ret_5d AS us_ret_5d, u.ret_20d AS us_ret_20d, u.ex_5d AS us_ex_5d, u.ex_20d AS us_ex_20d, "
        "       f.label, f.corr_ex, f.t_partial_sox, f.up_next, f.down_next, f.up_win "
        "FROM theme t "
        "LEFT JOIN theme_daily tw ON tw.theme_id = t.theme_id AND tw.trade_date = %(tw)s "
        "LEFT JOIN theme_daily r ON r.theme_id = t.theme_id AND r.trade_date = %(react)s "
        "LEFT JOIN us_theme_daily u ON u.theme_id = t.theme_id AND u.trade_date = %(us)s "
        "LEFT JOIN us_theme_follow f ON f.theme_id = t.theme_id "
        "WHERE t.layer = 3 AND t.is_active ORDER BY t.code",
        {"tw": tw_date, "react": react_date, "us": us_date})
    members = db.query("SELECT theme_id, symbol, name FROM us_theme_member ORDER BY theme_id, symbol")
    rets = _us_returns({m["symbol"] for m in members} | {s for s, _ in US_INDICATORS}, us_date)
    spy_1d = (rets.get("SPY") or {}).get("ret_1d")

    tw20 = [_f(r["tw_ret_20d"]) for r in rows if r["tw_ret_20d"] is not None]
    us20 = [_f(r["us_ret_20d"]) for r in rows if r["us_ret_20d"] is not None]
    tw_med, us_med = (median(tw20) if tw20 else None), (median(us20) if us20 else None)
    react_market = None
    if react_date:
        twse = db.query("SELECT trade_date, close FROM market_index WHERE index_id = 'TWSE' AND trade_date <= %(d)s "
                        "ORDER BY trade_date DESC LIMIT 2", {"d": react_date})
        rr = [_f(r["react_ret"]) for r in rows if r["react_ret"] is not None]
        react_market = {
            "twse": (float(twse[0]["close"]) / float(twse[1]["close"]) - 1
                     if len(twse) == 2 and twse[0]["trade_date"] == react_date else None),
            "theme_median": median(rr) if rr else None,
        }

    by_theme = {}
    for m in members:
        by_theme.setdefault(m["theme_id"], []).append({"symbol": m["symbol"], "name": m["name"],
                                                       **rets.get(m["symbol"], {})})
    themes = []
    for r in rows:
        tw = None if r["tw_ret_20d"] is None else {
            "rank": r["tw_rank"], "ret_1d": _f(r["tw_ret_1d"]), "ret_5d": _f(r["tw_ret_5d"]),
            "ret_20d": _f(r["tw_ret_20d"])}
        us = None if r["us_ret_20d"] is None else {
            "rank": r["us_rank"], "n_members": r["us_members"], "ret_1d": _f(r["us_ret_1d"]),
            "ex_1d": (_f(r["us_ret_1d"]) - spy_1d) if r["us_ret_1d"] is not None and spy_1d is not None else None,
            "ret_5d": _f(r["us_ret_5d"]), "ret_20d": _f(r["us_ret_20d"]),
            "ex_5d": _f(r["us_ex_5d"]), "ex_20d": _f(r["us_ex_20d"])}
        if tw and us:
            quadrant = ("美強" if us["ret_20d"] > us_med else "美弱") + ("台強" if tw["ret_20d"] > tw_med else "台弱")
        else:
            quadrant = "美股無對應" if tw else None
        follow = None if r["label"] is None else {
            "label": r["label"], "corr_ex": _f(r["corr_ex"]), "t_partial_sox": _f(r["t_partial_sox"]),
            # 扣掉費半後專屬對照沒有多出資訊：看費半就夠
            "sox_only": r["label"] != "幾乎不跟" and (_f(r["t_partial_sox"]) or 0) < 2,
            "up_next": _f(r["up_next"]), "down_next": _f(r["down_next"]), "up_win": _f(r["up_win"])}
        themes.append({"theme_id": r["theme_id"], "code": r["code"], "name": r["name"], "quadrant": quadrant,
                       "tw": tw, "us": us, "react_ret": _f(r["react_ret"]), "follow": follow,
                       "members": by_theme.get(r["theme_id"], [])})
    meta = db.query("SELECT min(period_from) AS period_from, max(period_to) AS period_to, max(n_days) AS n_days, "
                    "max(computed_at) AS computed_at FROM us_theme_follow")[0]
    return {
        "us_date": us_date.isoformat(), "us_close_tw": (us_date + timedelta(days=1)).isoformat(),
        "tw_date": tw_date.isoformat(), "react_date": react_date.isoformat() if react_date else None,
        "react_market": react_market,
        "indicators": [{"symbol": s, "name": n, **rets.get(s, {})} for s, n in US_INDICATORS],
        "medians": {"tw_20d": tw_med, "us_20d": us_med},
        "follow_meta": meta if meta["computed_at"] else None,
        "themes": themes,
    }


@router.get("/of")
def themes_of(stock_id: str):
    """個股所屬族群（L3 在前），附最新熱度名次。"""
    ds = _dates(1)
    return db.query(
        f"SELECT t.code, {_NAME} AS name, t.layer, st.role, st.status, d.heat_rank, d.heat_score "
        f"FROM stock_theme st JOIN theme t USING (theme_id) {_CHAIN} "
        "LEFT JOIN theme_daily d ON d.theme_id = t.theme_id AND d.trade_date = %(d)s "
        f"WHERE st.stock_id = %(sid)s AND t.is_active AND {_ACTIVE} "
        "ORDER BY t.layer DESC, d.heat_rank NULLS LAST",
        {"sid": stock_id, "d": ds[0] if ds else None})


@router.get("/options")
def options():
    """選股器下拉用：全部有效族群與成分數（L3 在前；L2 只列成分 ≥3 檔的節點）。"""
    return db.query(
        f"SELECT t.code, {_NAME} AS name, t.layer, count(DISTINCT st.stock_id) AS n "
        f"FROM theme t {_CHAIN} "
        "JOIN theme k ON (k.code = t.code OR k.parent_code = t.code) AND k.is_active "
        "JOIN stock_theme st ON st.theme_id = k.theme_id "
        "  AND st.valid_to IS NULL AND ((k.layer = 2 AND st.status = 'confirmed') "
        "   OR (k.layer = 3 AND st.status IN ('confirmed', 'seed'))) "
        "WHERE t.is_active AND NOT (t.layer = 2 AND t.parent_code IS NULL) "
        f"GROUP BY t.code, t.name, t.layer, c.name "
        "HAVING t.layer = 3 OR count(DISTINCT st.stock_id) >= 3 "
        "ORDER BY t.layer DESC, name")
