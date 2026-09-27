"""族群 API：L2 產業鏈節點／L3 市場題材的熱度排行、輪動熱力圖、成分明細、個股所屬族群。

資料來源：theme／stock_theme（fetch_tpex_chain.py、theme_candidates.py）、theme_daily（build_theme_daily.py 每晚產生）。
成分直查 stock_theme，你在 CSV 確認／否決後立即生效，不必等 mv_stock_snapshot 刷新。說明見 族群分類設計.md。
"""
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


@router.get("/ranking")
def ranking(layer: int = Query(3, ge=2, le=3), limit: int = Query(50, ge=1, le=300)):
    """最新一日熱度排行；rank_chg＝與 5 個交易日前相比的名次變化（正＝上升）。"""
    ds = _dates(6)
    if not ds:
        return {"as_of": None, "prev": None, "rows": []}
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
    return {"as_of": as_of.isoformat(), "prev": prev.isoformat(), "rows": rows}


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
    return {"code": m["code"], "name": m["name"], "layer": m["layer"], "keywords": m["keywords"],
            "note": m["note"], "as_of": ds[0].isoformat() if ds else None,
            "daily": daily[0] if daily else None, "median_ret_20d": med, "rows": rows}


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
