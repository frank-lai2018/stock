"""選股 API：預設策略 + 條件篩選。"""
from fastapi import APIRouter

from .. import db
from ..filters import SORT_WHITELIST, build_where
from ..schemas import ScreenRequest

router = APIRouter(prefix="/api", tags=["screen"])

# 預設策略（一鍵套用的條件組合）
STRATEGIES = {
    "momentum": {
        "name": "動能股",
        "desc": "12-1 動能強 + 站上季線 + 法人買超 + 有量",
        "filters": {"ret_12_1_min": 0.15, "above_ma60": True,
                    "inst_net_20d_min": 0, "amt20_min": 20000000, "in_universe": True},
        "sort": "ret_12_1",
    },
    "garp": {
        "name": "價值成長 (GARP)",
        "desc": "高 ROE + 低本益比 + 營收成長 + 低負債",
        "filters": {"roe_min": 15, "per_max": 15, "rev_yoy_min": 10,
                    "debt_ratio_max": 60, "in_universe": True},
        "sort": "roe",
    },
    "dividend": {
        "name": "高息存股",
        "desc": "高殖利率 + ROE 穩健 + 低負債",
        "filters": {"dividend_yield_min": 4, "roe_min": 8,
                    "debt_ratio_max": 50, "in_universe": True},
        "sort": "dividend_yield",
    },
    "chip": {
        "name": "籌碼強勢",
        "desc": "法人買超 + 站上季線 + 融資未過熱",
        "filters": {"inst_net_20d_min": 0, "above_ma60": True,
                    "margin_chg_20d_max": 0, "in_universe": True},
        "sort": "inst_net_20d",
    },
    "minervini": {
        "name": "趨勢範本 (Minervini)",
        "desc": "《超級績效》8 條趨勢範本：站上50/150/200MA、多頭排列、200MA翻揚、距52週高<25%、RS≥70",
        "filters": {"trend_template": True, "in_universe": True},
        "sort": "rs_rating",
        "limit": 100,
    },
    "vcp": {
        "name": "VCP 收縮買點 (Minervini)",
        "desc": "《超級績效》VCP：趨勢範本成立 + 近期波動收縮(振幅≤10%且較前期更緊) + 量縮 + 貼近平台高點(樞紐)",
        "filters": {"vcp": True, "in_universe": True},
        "sort": "rs_rating",
    },
    "mf_accumulate": {
        "name": "主力承接",
        "desc": "《不說謊的價量》VPA：近20日承接訊號(停損量/承接量/測試無賣壓)淨多 + 大戶或法人進場",
        "filters": {"mf_accumulate": True, "in_universe": True},
        "sort": "vpa_accum_20d",
    },
    "mf_distribute": {
        "name": "主力出貨",
        "desc": "《不說謊的價量》VPA 警示：近20日出貨訊號(無買氣/量價背離/買盤高潮)淨多 + 大戶或法人退場 + 高檔",
        "filters": {"mf_distribute": True, "in_universe": True},
        "sort": "vpa_distrib_20d",
    },
}


@router.get("/search")
def search(q: str = "", limit: int = 30):
    """依代碼前綴或名稱關鍵字搜尋股票（代碼相符優先）。"""
    q = (q or "").strip()
    if not q:
        return []
    n = max(1, min(int(limit), 50))
    return db.query(
        "SELECT stock_id, name, market, industry FROM stock "
        "WHERE stock_id LIKE %(pfx)s OR name LIKE %(kw)s "
        "ORDER BY CASE WHEN stock_id LIKE %(pfx)s THEN 0 ELSE 1 END, stock_id "
        "LIMIT %(n)s",
        {"pfx": q + "%", "kw": "%" + q + "%", "n": n})


@router.get("/strategies")
def strategies():
    return STRATEGIES


@router.post("/screen")
def screen(req: ScreenRequest):
    where, params = build_where(req.filters)
    sort = req.sort if req.sort in SORT_WHITELIST else "ret_3m"
    order = "DESC" if req.desc else "ASC"
    limit = max(1, min(int(req.limit), 500))
    sql = (f"SELECT * FROM mv_stock_snapshot{where} "
           f"ORDER BY {sort} {order} NULLS LAST LIMIT {limit}")
    rows = db.query(sql, params)
    as_of = rows[0]["as_of_date"].isoformat() if rows and rows[0].get("as_of_date") else None
    return {"count": len(rows), "as_of": as_of, "items": rows}
