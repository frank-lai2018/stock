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
    # 主力 VPA
    "mf_accumulate":     ("mf_accumulate = %(mf_accumulate)s", _BOOL),
    "mf_distribute":     ("mf_distribute = %(mf_distribute)s", _BOOL),
    "vpa_accum_min":     ("vpa_accum_20d >= %(vpa_accum_min)s", _NUM),
    "vpa_distrib_min":   ("vpa_distrib_20d >= %(vpa_distrib_min)s", _NUM),
    # Renko / 三線反轉（《Beyond Candlesticks》磚形圖；由 renko_etl.py 算進 renko_state）
    # 這是「狀態特徵」不是訊號——單獨用翻轉進出場實測勝率約 50%，請當過濾層疊在其他因子上
    # 布林一律綁參數：前端 cleanFilters 會把 false 送上來，開關關掉要能表達「反向」而非硬套條件
    "renko_bull":        ("(renko_dir = 1) = %(renko_bull)s", _BOOL),         # 磚形圖為多(false=為空)
    "renko_fresh_bull":  ("renko_fresh_bull = %(renko_fresh_bull)s", _BOOL),  # 多方且10日內剛翻
    "renko_run_min":     ("renko_run >= %(renko_run_min)s", _NUM),            # 連續同向≥N塊（動能強度）
    "renko_flip_days_max": ("renko_flip_days <= %(renko_flip_days_max)s", _NUM),  # 翻轉在N日內
    "tlb_bull":          ("(tlb_dir = 1) = %(tlb_bull)s", _BOOL),             # 三線反轉為多
    "renko_tlb_agree":   ("(renko_dir = tlb_dir) = %(renko_tlb_agree)s", _BOOL),  # 兩圖同向（第二確認）
    # 基本面
    "roe_min":           ("roe >= %(roe_min)s", _NUM),
    "eps_min":           ("eps >= %(eps_min)s", _NUM),
    "eps_ttm_min":       ("eps_ttm >= %(eps_ttm_min)s", _NUM),
    "gross_margin_min":  ("gross_margin >= %(gross_margin_min)s", _NUM),
    "op_margin_min":     ("op_margin >= %(op_margin_min)s", _NUM),
    "net_margin_min":    ("net_margin >= %(net_margin_min)s", _NUM),
    "debt_ratio_max":    ("debt_ratio <= %(debt_ratio_max)s", _NUM),
    "rev_yoy_min":       ("rev_yoy >= %(rev_yoy_min)s", _NUM),
    # 財報成長 / 盈餘加速
    "eps_qoq_min":       ("eps_qoq >= %(eps_qoq_min)s", _NUM),
    "eps_yoy_min":       ("eps_yoy >= %(eps_yoy_min)s", _NUM),
    "eps_accel":         ("eps_accel = %(eps_accel)s", _BOOL),          # 連兩季 EPS 季增
    "eps_yoy_accel":     ("eps_yoy_accel = %(eps_yoy_accel)s", _BOOL),  # EPS 年增率逐季擴大
    "gross_margin_chg_min": ("gross_margin_chg >= %(gross_margin_chg_min)s", _NUM),
    "op_margin_chg_min": ("op_margin_chg >= %(op_margin_chg_min)s", _NUM),
    # 估值
    "per_min":           ("per >= %(per_min)s", _NUM),
    "per_max":           ("(per <= %(per_max)s AND per > 0)", _NUM),
    "per_pctile_max":    ("per_pctile <= %(per_pctile_max)s", _NUM),    # 本益比在近3年的位置（低=便宜）
    "per_pctile_min":    ("per_pctile >= %(per_pctile_min)s", _NUM),
    "pbr_max":           ("pbr <= %(pbr_max)s", _NUM),
    "dividend_yield_min":("dividend_yield >= %(dividend_yield_min)s", _NUM),
    # 籌碼
    "inst_net_20d_min":  ("inst_net_20d >= %(inst_net_20d_min)s", _NUM),
    "margin_chg_20d_max":("margin_chg_20d <= %(margin_chg_20d_max)s", _NUM),
    "margin_util_max":   ("margin_util <= %(margin_util_max)s", _NUM),           # 融資使用率%（低=籌碼乾淨）
    "margin_util_min":   ("margin_util >= %(margin_util_min)s", _NUM),
    "short_margin_ratio_min": ("short_margin_ratio >= %(short_margin_ratio_min)s", _NUM),  # 券資比%（高=軋空題材）
    "short_margin_ratio_max": ("short_margin_ratio <= %(short_margin_ratio_max)s", _NUM),
    "foreign_ratio_min": ("foreign_ratio >= %(foreign_ratio_min)s", _NUM),
    "big1000_pct_min":   ("big1000_pct >= %(big1000_pct_min)s", _NUM),
    "big1000_chg_min":   ("big1000_chg >= %(big1000_chg_min)s", _NUM),
    "big1000_up_weeks_min": ("big1000_up_weeks >= %(big1000_up_weeks_min)s", _NUM),   # 大戶連 N 週增加
    "retail_chg_max":    ("retail_chg <= %(retail_chg_max)s", _NUM),                  # 散戶佔比變化（負=散戶退場）
    # 品質 / 母體
    "amt20_min":         ("amt20 >= %(amt20_min)s", _NUM),
    "industry":          ("industry = %(industry)s", _STR),
    "market":            ("market = %(market)s", _STR),
    "security_type":     ("security_type = %(security_type)s", _STR),   # stock / etf
    "in_universe":       ("in_universe = %(in_universe)s", _BOOL),
}

# 可排序欄位白名單
SORT_WHITELIST = {
    "ret_1m", "ret_3m", "ret_6m", "ret_12m", "ret_12_1", "rs_6m", "rs_rating", "pct_from_low",
    "near_pivot", "tight_recent",
    "roe", "eps", "eps_ttm", "gross_margin", "op_margin", "net_margin", "rev_yoy",
    "eps_qoq", "eps_yoy", "gross_margin_chg", "op_margin_chg",
    "per", "pbr", "dividend_yield", "per_pctile",
    "inst_net_20d", "margin_chg_20d", "margin_util", "short_margin_ratio",
    "foreign_ratio", "big1000_pct", "big1000_chg", "big1000_up_weeks", "retail_pct", "retail_chg", "amt20",
    "vpa_accum_20d", "vpa_distrib_20d",
    "renko_run", "renko_flip_days", "tlb_run",
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
