"""選股條件白名單：filter key → (參數化 SQL 片段, 轉型)。杜絕 SQL 注入。"""

_NUM = float
_BOOL = bool
_STR = str

# 只有在此白名單的 key 會被接受；值一律走參數化（%(key)s）
FILTERS = {
    # 動能
    "ret_1m_min":        ("ret_1m >= %(ret_1m_min)s", _NUM),
    "ret_3m_min":        ("ret_3m >= %(ret_3m_min)s", _NUM),
    "ret_6m_min":        ("ret_6m >= %(ret_6m_min)s", _NUM),
    "ret_12_1_min":      ("ret_12_1 >= %(ret_12_1_min)s", _NUM),
    "above_ma60":        ("above_ma60 = %(above_ma60)s", _BOOL),
    "ma_bull":           ("ma_bull = %(ma_bull)s", _BOOL),
    "dist_52w_high_min": ("dist_52w_high >= %(dist_52w_high_min)s", _NUM),
    "rs_6m_min":         ("rs_6m >= %(rs_6m_min)s", _NUM),
    # Minervini 趨勢範本
    "rs_rating_min":     ("rs_rating >= %(rs_rating_min)s", _NUM),
    "pct_from_low_min":  ("pct_from_low >= %(pct_from_low_min)s", _NUM),
    "ma200_up":          ("ma200_up = %(ma200_up)s", _BOOL),
    "trend_template":    ("trend_template = %(trend_template)s", _BOOL),
    "vcp":               ("vcp = %(vcp)s", _BOOL),
    "near_pivot_min":    ("near_pivot >= %(near_pivot_min)s", _NUM),
    # 基本面
    "roe_min":           ("roe >= %(roe_min)s", _NUM),
    "eps_min":           ("eps >= %(eps_min)s", _NUM),
    "gross_margin_min":  ("gross_margin >= %(gross_margin_min)s", _NUM),
    "net_margin_min":    ("net_margin >= %(net_margin_min)s", _NUM),
    "debt_ratio_max":    ("debt_ratio <= %(debt_ratio_max)s", _NUM),
    "rev_yoy_min":       ("rev_yoy >= %(rev_yoy_min)s", _NUM),
    # 估值
    "per_min":           ("per >= %(per_min)s", _NUM),
    "per_max":           ("(per <= %(per_max)s AND per > 0)", _NUM),
    "pbr_max":           ("pbr <= %(pbr_max)s", _NUM),
    "dividend_yield_min":("dividend_yield >= %(dividend_yield_min)s", _NUM),
    # 籌碼
    "inst_net_20d_min":  ("inst_net_20d >= %(inst_net_20d_min)s", _NUM),
    "margin_chg_20d_max":("margin_chg_20d <= %(margin_chg_20d_max)s", _NUM),
    "foreign_ratio_min": ("foreign_ratio >= %(foreign_ratio_min)s", _NUM),
    "big1000_pct_min":   ("big1000_pct >= %(big1000_pct_min)s", _NUM),
    # 品質 / 母體
    "amt20_min":         ("amt20 >= %(amt20_min)s", _NUM),
    "industry":          ("industry = %(industry)s", _STR),
    "market":            ("market = %(market)s", _STR),
    "in_universe":       ("in_universe = %(in_universe)s", _BOOL),
}

# 可排序欄位白名單
SORT_WHITELIST = {
    "ret_1m", "ret_3m", "ret_6m", "ret_12m", "ret_12_1", "rs_6m", "rs_rating", "pct_from_low",
    "near_pivot", "tight_recent",
    "roe", "eps", "gross_margin", "net_margin", "rev_yoy",
    "per", "pbr", "dividend_yield",
    "inst_net_20d", "margin_chg_20d", "foreign_ratio", "big1000_pct", "amt20",
}


def build_where(filters):
    """回傳 (WHERE 子句字串, 參數 dict)。只接受白名單 key。"""
    clauses, params = [], {}
    for k, v in (filters or {}).items():
        if k not in FILTERS or v is None:
            continue
        frag, cast = FILTERS[k]
        try:
            params[k] = cast(v)
        except (TypeError, ValueError):
            continue
        clauses.append(frag)
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    return where, params
