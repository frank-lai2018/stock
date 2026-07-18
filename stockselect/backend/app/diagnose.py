"""持股診斷：由 mv_stock_snapshot 一列算出綜合評分(0-100)、三面向分數、燈號與理由。

面向：技術面(0-40)、籌碼面(0-35)、基本面(0-25)。各面向由中性基準加減後夾在範圍內。
燈號：green(續抱/強勢) / yellow(觀察) / red(減碼警示)。
"""


def _f(v, d=None):
    try:
        return float(v)
    except (TypeError, ValueError):
        return d


def _clamp(v, lo, hi):
    return max(lo, min(hi, v))


def diagnose(s):
    """s：mv_stock_snapshot 的一列 dict。回傳診斷結果 dict。"""
    trend = bool(s.get("trend_template"))
    vcp = bool(s.get("vcp"))
    accum = bool(s.get("mf_accumulate"))
    distrib = bool(s.get("mf_distribute"))
    above60 = bool(s.get("above_ma60"))
    bull_ma = bool(s.get("ma_bull"))
    rs = _f(s.get("rs_rating"), 50) or 50
    inst = _f(s.get("inst_net_20d"), 0) or 0
    bigchg = _f(s.get("big1000_chg"), 0) or 0
    va = _f(s.get("vpa_accum_20d"), 0) or 0
    vd = _f(s.get("vpa_distrib_20d"), 0) or 0
    roe = _f(s.get("roe"))
    yoy = _f(s.get("rev_yoy"))
    per = _f(s.get("per"))
    dy = _f(s.get("dividend_yield"))
    dist_high = _f(s.get("dist_52w_high"))

    # ── 技術面 0-40（基準 20）──
    tech = 20.0
    tech += 12 if trend else 0
    tech += 6 if above60 else -6
    tech += _clamp((rs - 50) / 50 * 10, -10, 10)
    tech += 4 if vcp else 0
    tech += 2 if bull_ma else 0
    tech = _clamp(tech, 0, 40)

    # ── 籌碼面 0-35（基準 17）──
    chip = 17.0
    chip += 10 if accum else 0
    chip -= 12 if distrib else 0
    chip += 5 if inst > 0 else (-5 if inst < 0 else 0)
    chip += 5 if bigchg > 0 else (-5 if bigchg < 0 else 0)
    chip += _clamp(va - vd, -5, 5)
    chip = _clamp(chip, 0, 35)

    # ── 基本面 0-25（基準 12）──
    fund = 12.0
    if roe is not None:
        fund += 6 if roe >= 15 else (3 if roe >= 8 else (-4 if roe < 0 else 0))
    if yoy is not None:
        fund += 5 if yoy >= 10 else (2 if yoy > 0 else (-3 if yoy < 0 else 0))
    if per is not None:
        fund += 3 if 0 < per <= 20 else (-2 if per > 40 else 0)
    if dy is not None and dy >= 4:
        fund += 2
    fund = _clamp(fund, 0, 25)

    score = round(tech + chip + fund)

    # ── 評級 level：strong(續抱強勢) / watch(觀察) / reduce(減碼警示) ──
    # 顏色由前端依台股慣例上色（strong=紅偏多、reduce=綠偏空）
    if distrib or score < 40 or (not above60 and (inst < 0 or bigchg < 0)):
        level = "reduce"
    elif trend or score >= 70 or (above60 and accum and rs >= 70):
        level = "strong"
    else:
        level = "watch"

    # ── 理由標籤（依重要性）──
    reasons = []

    def add(cond_bull, text_bull, cond_bear=False, text_bear=None):
        if cond_bull:
            reasons.append({"text": text_bull, "dir": "bull"})
        elif cond_bear:
            reasons.append({"text": text_bear, "dir": "bear"})

    add(distrib, "主力出貨", False)                    # 先放警示
    add(trend, "趨勢範本")
    add(vcp, "VCP收縮")
    add(accum, "主力承接")
    add(above60, "站上季線", not above60, "跌破季線")
    add(rs >= 80, f"RS強勢({int(rs)})", rs <= 30, f"RS弱勢({int(rs)})")
    add(inst > 0, "法人買超", inst < 0, "法人賣超")
    add(bigchg > 0, "大戶增持", bigchg < 0, "大戶減持")
    add(yoy is not None and yoy >= 20, "營收高成長",
        yoy is not None and yoy < 0, "營收衰退")
    if dist_high is not None and dist_high >= -0.03:
        reasons.append({"text": "逼近前高", "dir": "neutral"})

    return {
        "score": score,
        "level": level,
        "facets": {"tech": round(tech), "chip": round(chip), "fund": round(fund)},
        "reasons": reasons[:6],
    }
