"""型態突破候選的二次評分。

這裡只做可解釋的規則評分，不把分數包裝成報酬保證。輸入是一列
mv_stock_snapshot + breakout 資訊；輸出包含五個子分、風險欄位與淘汰理由。
"""


def _num(value, default=None):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _linear(value, low, high, points):
    """把 value 在 low~high 間線性映射到 0~points，並截斷。"""
    value = _num(value)
    if value is None or high <= low:
        return 0.0
    return max(0.0, min(points, (value - low) / (high - low) * points))


def _pattern_edge_score(backtest):
    """20 日超額報酬經樣本數收縮後給 0~5 分，避免小樣本偶然值排第一。"""
    if not backtest:
        return 0.0
    n = int(_num(backtest.get("n"), 0) or 0)
    excess = _num(backtest.get("avg_excess"))
    if not n or excess is None:
        return 0.0
    shrunk = excess * n / (n + 500.0)       # 百分點；500 筆為溫和先驗強度
    return _linear(shrunk, -0.5, 1.0, 5.0)


def score_breakout(row, backtest=None):
    """回傳突破決策 dict；滿分 100。

    分數用於同批候選的排序。status 的硬條件另外判斷，避免高分掩蓋追價、
    停損過遠或報酬風險比不足。
    """
    bk = row.get("breakout") or {}
    rs = _num(row.get("rs_rating"), 0.0)
    ret_12_1 = _num(row.get("ret_12_1"))
    vol_ratio = _num(bk.get("vol_ratio"), 0.0)
    tight = _num(row.get("tight_recent"))

    # 1) 趨勢與相對強度（30）
    trend = rs / 100.0 * 15.0
    trend += 10.0 if row.get("trend_template") else 0.0
    trend += _linear(ret_12_1, -0.10, 0.40, 5.0)

    # 2) 突破品質（25）：量、收縮、追價程度、型態歷史超額
    breakout = _linear(vol_ratio, 1.3, 2.5, 8.0)
    if tight is not None:
        breakout += 7.0 if tight <= 0.08 else 5.0 if tight <= 0.12 else 3.0 if tight <= 0.18 else 0.0

    entry = _num(bk.get("current_adj_close"), _num(bk.get("breakout_close")))
    neckline = _num(bk.get("neckline"))
    target = _num(bk.get("target"))
    atr = _num(bk.get("atr14"))
    extension = (entry / neckline - 1.0) if entry and neckline and neckline > 0 else None
    if extension is not None:
        breakout += 5.0 if 0 <= extension <= 0.03 else 3.0 if extension <= 0.05 else 0.0
    breakout += _pattern_edge_score(backtest)

    # 3) 盈餘動能（20）
    fundamental = 7.0 if row.get("eps_yoy_accel") else 0.0
    fundamental += 5.0 if row.get("eps_accel") else 0.0
    fundamental += _linear(row.get("rev_yoy"), 0.0, 30.0, 5.0)
    gm_chg = _num(row.get("gross_margin_chg"))
    fundamental += 3.0 if gm_chg is not None and gm_chg >= 0 else 0.0

    # 4) 估值與籌碼（10）。法人只判正負，不以原始股數大小灌分。
    quality = 0.0
    per_pctile = _num(row.get("per_pctile"))
    if per_pctile is not None:
        quality += 4.0 if per_pctile <= 30 else 2.0 if per_pctile <= 50 else 0.0
    quality += 3.0 if (_num(row.get("big1000_chg"), 0.0) or 0.0) > 0 else 0.0
    quality += 3.0 if (_num(row.get("inst_net_20d"), 0.0) or 0.0) > 0 else 0.0

    # 5) 風險與可執行性（15）：頸線下 1 ATR 作為一致、可比較的參考停損。
    stop = neckline - atr if neckline and atr and atr > 0 else None
    risk_pct = ((entry - stop) / entry) if entry and stop and 0 < stop < entry else None
    rr = ((target - entry) / (entry - stop)) if target and entry and stop and entry > stop else None
    risk = 0.0
    if rr is not None:
        risk += 8.0 if rr >= 3 else 6.0 if rr >= 2 else 3.0 if rr >= 1.5 else 0.0
    if risk_pct is not None:
        risk += 4.0 if risk_pct <= 0.05 else 3.0 if risk_pct <= 0.08 else 1.0 if risk_pct <= 0.10 else 0.0
    amt = _num(row.get("amt20"), 0.0) or 0.0
    risk += 3.0 if amt >= 100_000_000 else 2.0 if amt >= 50_000_000 else 1.0 if amt >= 20_000_000 else 0.0

    parts = {
        "trend": round(min(trend, 30.0), 1),
        "breakout": round(min(breakout, 25.0), 1),
        "fundamental": round(min(fundamental, 20.0), 1),
        "quality": round(min(quality, 10.0), 1),
        "risk": round(min(risk, 15.0), 1),
    }
    score = round(sum(parts.values()), 1)

    blockers, notes = [], []
    if rs < 70:
        blockers.append("RS 評等未達 70")
    if vol_ratio < 1.5:
        blockers.append("突破量比未達 1.5")
    if extension is None:
        blockers.append("無法計算離頸線距離")
    elif extension > 0.05:
        blockers.append("離頸線超過 5%，避免追價")
    elif extension < 0:
        blockers.append("目前已回到頸線下")
    if risk_pct is None:
        blockers.append("停損距離資料不足")
    elif risk_pct > 0.08:
        blockers.append("參考停損超過 8%")
    if rr is None or rr < 2:
        blockers.append("報酬風險比未達 2")
    if target is not None and entry is not None and target <= entry:
        blockers.append("已接近或超過量測目標")
    if row.get("security_type") == "etf":
        notes.append("ETF 與個股基本面分數不可直接比較")
    if backtest and _num(backtest.get("median_ret")) is not None and _num(backtest.get("median_ret")) < 0:
        notes.append("此型態 20 日回測中位數仍為負")

    if score >= 75 and not blockers:
        status = "priority"
    elif score >= 65 and len(blockers) <= 1:
        status = "watch"
    else:
        status = "skip"

    bt = None
    if backtest:
        bt = {k: backtest.get(k) for k in ("n", "win_rate", "avg_ret", "median_ret", "avg_excess")}
    return {
        "score": score,
        "status": status,
        "parts": parts,
        "entry": round(entry, 2) if entry is not None else None,
        "stop": round(stop, 2) if stop is not None else None,
        "target": round(target, 2) if target is not None else None,
        "extension_pct": round(extension * 100, 2) if extension is not None else None,
        "risk_pct": round(risk_pct * 100, 2) if risk_pct is not None else None,
        "rr": round(rr, 2) if rr is not None else None,
        "blockers": blockers,
        "notes": notes,
        "backtest_20d": bt,
    }
