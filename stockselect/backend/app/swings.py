"""波段型態偵測：W 底 / 雙重底突破頸線（純 Python 波峰波谷 / 分形樞紐）。

單根 K 棒型態在 patterns.py；這裡處理需要「波段」的大型態。
bars：由舊到新的 list[dict]，含 high/low/close/volume（請用還原價 adj_*）。
"""
import contextlib
import contextvars

# 掃描模式：None=需確認突破（收盤穿越頸線帶量）；float=「接近突破」容許帶
# （如 0.05＝收盤已在頸線 5% 內、但尚未穿越）。以 ContextVar 保存，對併發請求安全。
_near = contextvars.ContextVar("swings_near", default=None)


@contextlib.contextmanager
def near_mode(band):
    """在此區塊內所有偵測器改用「接近但尚未突破」判定；band＝容許距離（如 0.05）。"""
    token = _near.set(band)
    try:
        yield
    finally:
        _near.reset(token)


def _near_up(closes, vols, level, band):
    """接近向上突破：最新收盤落在 [level*(1-band), level)（頸線下方 band 內、尚未站上）。"""
    n = len(closes)
    last = closes[-1]
    if level > 0 and level * (1 - band) <= last < level:
        ref = vols[max(0, n - 51):n - 1]
        avg = sum(ref) / len(ref) if ref else 0
        return n - 1, (round(vols[-1] / avg, 2) if avg > 0 else None)
    return None


def _near_down(closes, vols, level, band):
    """接近向下跌破：最新收盤落在 (level, level*(1+band)]（頸線上方 band 內、尚未跌破）。"""
    n = len(closes)
    last = closes[-1]
    if level > 0 and level < last <= level * (1 + band):
        ref = vols[max(0, n - 51):n - 1]
        avg = sum(ref) / len(ref) if ref else 0
        return n - 1, (round(vols[-1] / avg, 2) if avg > 0 else None)
    return None


def _d(x):
    return x.isoformat() if hasattr(x, "isoformat") else str(x)


def _pivots(vals, k, kind):
    """分形(fractal)樞紐索引。kind='low' 取局部最小、'high' 取局部最大。
    i 為樞紐 = vals[i] 在 [i-k, i+k] 內為極值（允許持平，但須不劣於緊鄰兩側）。
    最後 k 根因右側未定，不視為已確認樞紐。"""
    idx, n = [], len(vals)
    for i in range(k, n - k):
        w = vals[i - k:i + k + 1]
        if kind == "low" and vals[i] == min(w) and vals[i] <= vals[i - 1] and vals[i] <= vals[i + 1]:
            idx.append(i)
        elif kind == "high" and vals[i] == max(w) and vals[i] >= vals[i - 1] and vals[i] >= vals[i + 1]:
            idx.append(i)
    return idx


def _dedup_pivots(idx, vals, kind, min_sep):
    """把相隔 < min_sep 的同段樞紐併成一個（取該段極值），避免平底/寬底產生連續重複樞紐。"""
    if not idx:
        return []
    groups = [[idx[0]]]
    for i in idx[1:]:
        if i - groups[-1][-1] < min_sep:
            groups[-1].append(i)
        else:
            groups.append([i])
    pick = min if kind == "low" else max
    return [pick(g, key=lambda i: vals[i]) for g in groups]


def _swing_lows(lows, k):
    """去重後的 swing low 索引（同段併點）。"""
    return _dedup_pivots(_pivots(lows, k, "low"), lows, "low", max(k + 2, 5))


def detect_double_bottom(bars, k=3, recent=3, tol=0.05, min_depth=0.08,
                         vol_mult=1.5, min_gap=8, max_gap=90):
    """偵測 W 底 / 雙重底突破頸線帶量，回傳資訊 dict 或 None。

    條件：
      1. 最近兩個 swing low（分形樞紐）深度相近（|Δ| ≤ tol，預設 5%）
      2. 兩底間隔 min_gap~max_gap 根（預設 8~90）
      3. 兩底之間的最高點＝頸線，型態要有深度（頸線 / 底 − 1 ≥ min_depth，預設 8%）
      4. 最近 recent 根內，收盤『由 ≤頸線 轉為 >頸線』（新鮮突破）
      5. 突破當根量 ≥ 前 50 日均量 × vol_mult（預設 1.5）
    另附量測滿足價（頸線 + 型態高度）。
    """
    n = len(bars)
    if n < 40:
        return None
    lows = [float(b["low"]) for b in bars]
    highs = [float(b["high"]) for b in bars]
    closes = [float(b["close"]) for b in bars]
    vols = [float(b["volume"] or 0) for b in bars]

    piv = _swing_lows(lows, k)
    if len(piv) < 2:
        return None
    i1, i2 = piv[-2], piv[-1]                    # 最近兩個底（i1 較早）
    lo1, lo2 = lows[i1], lows[i2]
    if lo1 <= 0 or not (min_gap <= i2 - i1 <= max_gap):
        return None
    if abs(lo2 - lo1) / lo1 > tol:               # 兩底相近
        return None
    neck = max(highs[i1:i2 + 1])                 # 兩底之間最高點 = 頸線
    base = min(lo1, lo2)
    if neck / base - 1 < min_depth:              # 型態要有深度
        return None

    b = _breakout(closes, vols, neck, recent, vol_mult)   # 收盤突破頸線帶量（接近模式改判逼近）
    if not b:
        return None
    cross, vr = b
    return {
        "pattern": "double_bottom",
        "neckline": round(neck, 2),
        "bottom1": {"date": _d(bars[i1]["trade_date"]), "price": round(lo1, 2)},
        "bottom2": {"date": _d(bars[i2]["trade_date"]), "price": round(lo2, 2)},
        "breakout_date": _d(bars[cross]["trade_date"]),
        "breakout_close": round(closes[cross], 2),
        "vol_ratio": vr,
        "target": round(neck + (neck - base), 2),   # 量測滿足：頸線 + 型態高度
    }


# ======================================================================
# 共用工具 + 其餘底部反轉型態
# 這些偵測器皆為「啟發式」，用於全市場初篩，訊號隨參數敏感、必有假訊號 —— 需人工看圖確認。
# ======================================================================

def _series(bars):
    return ([float(b["low"]) for b in bars], [float(b["high"]) for b in bars],
            [float(b["close"]) for b in bars], [float(b["volume"] or 0) for b in bars])


def _breakout(closes, vols, level, recent=3, vol_mult=1.5):
    """最近 recent 根內收盤『由 ≤level 轉為 >level』且突破當根量 ≥ 前 50 日均量×mult。
    回傳 (cross_idx, vol_ratio) 或 None。接近模式下改判「收盤逼近 level 但尚未站上」。"""
    band = _near.get()
    if band is not None:
        return _near_up(closes, vols, level, band)
    n = len(closes)
    for j in range(max(1, n - recent), n):
        if closes[j] > level and closes[j - 1] <= level:
            ref = vols[max(0, j - 50):j]
            avg = sum(ref) / len(ref) if ref else 0
            if avg > 0 and vols[j] >= vol_mult * avg:
                return j, round(vols[j] / avg, 2)
    return None


def _out(pattern, level, base, bars, closes, j, vr, points):
    """標準化輸出：level=突破價(頸線/杯口)、base=型態最低、j=突破根索引。"""
    return {
        "pattern": pattern,
        "neckline": round(level, 2),
        "breakout_date": _d(bars[j]["trade_date"]),
        "breakout_close": round(closes[j], 2),
        "vol_ratio": vr,
        "target": round(level + (level - base), 2),      # 量測滿足：突破價 + 型態高度
        "points": [{"date": _d(bars[i]["trade_date"]), "price": round(p, 2), "label": lab}
                   for (i, p, lab) in points],
    }


def detect_triple_bottom(bars, k=3, recent=3, tol=0.05, min_depth=0.08,
                         vol_mult=1.5, min_gap=6, max_gap=60):
    """三重底：最近三個相近 swing low + 上緣頸線，收盤突破頸線帶量。"""
    n = len(bars)
    if n < 50:
        return None
    lows, highs, closes, vols = _series(bars)
    piv = _swing_lows(lows, k)
    if len(piv) < 3:
        return None
    i1, i2, i3 = piv[-3], piv[-2], piv[-1]
    l1, l2, l3 = lows[i1], lows[i2], lows[i3]
    base = min(l1, l2, l3)
    if base <= 0 or max(l1, l2, l3) / base - 1 > tol:            # 三底相近
        return None
    if not (min_gap <= i2 - i1 <= max_gap and min_gap <= i3 - i2 <= max_gap):
        return None
    neck = max(max(highs[i1:i2 + 1]), max(highs[i2:i3 + 1]))     # 兩波反彈高的上緣
    if neck / base - 1 < min_depth:
        return None
    b = _breakout(closes, vols, neck, recent, vol_mult)
    if not b:
        return None
    j, vr = b
    return _out("triple_bottom", neck, base, bars, closes, j, vr,
                [(i1, l1, "底1"), (i2, l2, "底2"), (i3, l3, "底3")])


def detect_hs_bottom(bars, k=3, recent=3, tol=0.06, min_depth=0.08,
                     vol_mult=1.5, min_gap=5, max_gap=60):
    """頭肩底：左肩–頭(最低)–右肩，肩相近、頭明顯較低，收盤突破頸線帶量。"""
    n = len(bars)
    if n < 50:
        return None
    lows, highs, closes, vols = _series(bars)
    piv = _swing_lows(lows, k)
    if len(piv) < 3:
        return None
    i1, i2, i3 = piv[-3], piv[-2], piv[-1]
    ls, hd, rs = lows[i1], lows[i2], lows[i3]                    # 左肩 / 頭 / 右肩
    if hd <= 0 or ls <= 0:
        return None
    if not (hd < ls * 0.98 and hd < rs * 0.98):                 # 頭要明顯最低
        return None
    if abs(rs - ls) / ls > tol:                                 # 兩肩相近
        return None
    if not (min_gap <= i2 - i1 <= max_gap and min_gap <= i3 - i2 <= max_gap):
        return None
    neck = max(max(highs[i1:i2 + 1]), max(highs[i2:i3 + 1]))     # 頸線＝兩反彈高上緣
    base = hd
    if neck / base - 1 < min_depth:
        return None
    b = _breakout(closes, vols, neck, recent, vol_mult)
    if not b:
        return None
    j, vr = b
    return _out("hs_bottom", neck, base, bars, closes, j, vr,
                [(i1, ls, "左肩"), (i2, hd, "頭"), (i3, rs, "右肩")])


def detect_rounding_bottom(bars, recent=3, vol_mult=1.3, min_depth=0.10,
                           window=70, flat_band=0.06):
    """圓弧底(碗形)：緩降緩升的寬底 U 形，收盤突破左緣(杯口)帶量。
    以「寬底」(低點附近佔比高) 區別於 V 形；低點須落在窗口中段。"""
    n = len(bars)
    if n < 45:
        return None
    lows, highs, closes, vols = _series(bars)
    W = min(window, n - 1)
    seg_lo = lows[-W:]
    seg_hi = highs[-W:]
    off = n - W                                                 # 窗口起始的全域索引
    li = min(range(W), key=lambda i: seg_lo[i])                 # 窗口內最低點
    base = seg_lo[li]
    if base <= 0 or not (0.30 * W <= li <= 0.70 * W):           # 低點在中段
        return None
    rim = max(seg_hi[:max(1, W // 10)])                         # 左緣(杯口)＝窗口左側高
    if rim / base - 1 < min_depth:
        return None
    broad = sum(1 for v in seg_lo if v <= base * (1 + flat_band))   # 寬底：貼近底的根數
    if broad < 0.25 * W:                                        # 太尖 → 不是圓弧(可能 V)
        return None
    third = W // 3                                              # 右側須higher（回升）
    if sum(seg_lo[2 * third:]) / (W - 2 * third) <= sum(seg_lo[third:2 * third]) / third:
        return None
    b = _breakout(closes, vols, rim, recent, vol_mult)
    if not b:
        return None
    j, vr = b
    return _out("rounding_bottom", rim, base, bars, closes, j, vr,
                [(off + li, base, "圓弧底")])


def detect_v_reversal(bars, drop=0.12, rise=0.10, leg_max=15, look=45,
                      recent=3, vol_mult=1.3):
    """V 型反轉：急跌後急彈的尖底。跌腳、彈腳皆短促(≤leg_max)，收盤突破跌勢起點高帶量。"""
    n = len(bars)
    if n < 30:
        return None
    lows, highs, closes, vols = _series(bars)
    L = min(look, n - 1)
    off = n - L
    li = off + min(range(L), key=lambda i: lows[off + i])       # 近期最低點(全域索引)
    base = lows[li]
    if base <= 0 or li >= n - 1:
        return None
    pre_hi = max(highs[max(0, li - leg_max):li] or [base])      # 跌勢起點高
    post_hi = max(highs[li:min(n, li + leg_max + 1)])           # 彈升高
    if (pre_hi - base) / pre_hi < drop:                         # 跌幅足
        return None
    if (post_hi - base) / base < rise:                          # 彈幅足
        return None
    b = _breakout(closes, vols, pre_hi, recent, vol_mult)       # 收復跌勢起點高
    if not b:
        return None
    j, vr = b
    return _out("v_reversal", pre_hi, base, bars, closes, j, vr,
                [(li, base, "V底")])


def detect_cup_handle(bars, recent=3, vol_mult=1.4, min_depth=0.10,
                      cup_window=80, handle_max=15, handle_retr=0.4):
    """杯柄：圓弧杯 + 右側淺回檔(柄)，收盤突破柄高(≈杯口)帶量。"""
    n = len(bars)
    if n < 55:
        return None
    lows, highs, closes, vols = _series(bars)
    W = min(cup_window, n - 1 - handle_max)
    if W < 30:
        return None
    cup = slice(n - handle_max - W, n - handle_max)             # 杯身區段
    cl = [lows[i] for i in range(cup.start, cup.stop)]
    ch = [highs[i] for i in range(cup.start, cup.stop)]
    base = min(cl)
    if base <= 0:
        return None
    left_rim = max(ch[:max(1, len(ch) // 5)])                   # 杯左緣
    right_rim = max(ch[-max(1, len(ch) // 5):])                 # 杯右緣（須回到左緣附近）
    if left_rim / base - 1 < min_depth or right_rim < left_rim * 0.93:
        return None
    depth = left_rim - base
    handle = range(n - handle_max, n)                           # 柄：右側淺回檔
    h_lo = min(lows[i] for i in handle)
    h_hi = max(highs[i] for i in handle)
    if (right_rim - h_lo) > handle_retr * depth:               # 柄回檔要淺
        return None
    level = max(h_hi, right_rim)                                # 突破價＝柄高/杯口
    b = _breakout(closes, vols, level, recent, vol_mult)
    if not b:
        return None
    j, vr = b
    return _out("cup_handle", level, base, bars, closes, j, vr,
                [(cup.start + cl.index(base), base, "杯底")])


# ======================================================================
# 連續 / 整理型態突破（趨勢線擬合上下緣，判斜率型別 + 突破方向）
# 一樣是啟發式初篩，趨勢線隨參數/樞紐敏感，務必看圖確認。
# ======================================================================

_FLAT = 0.0015          # 相對斜率門檻（/根/價）：|rel| < 此視為水平


def _swing_highs(highs, k):
    return _dedup_pivots(_pivots(highs, k, "high"), highs, "high", max(k + 2, 5))


def _fit(points):
    """最小平方法擬合 (idx, val) → (slope, intercept)；< 2 點回 None。"""
    m = len(points)
    if m < 2:
        return None
    sx = sum(i for i, _ in points); sy = sum(v for _, v in points)
    sxx = sum(i * i for i, _ in points); sxy = sum(i * v for i, v in points)
    den = m * sxx - sx * sx
    if den == 0:
        return None
    slope = (m * sxy - sx * sy) / den
    return slope, (sy - slope * sx) / m


def _at(fit, x):
    return fit[0] * x + fit[1]


def _rel(slope, price):
    return slope / price if price else 0.0


def _channel(bars, window, k=2):
    """回傳窗口內的上緣(pivot high)/下緣(pivot low)擬合線與序列。
    用原始分形樞紐（不併點）以保留趨勢線所需的多個轉折。"""
    n = len(bars)
    lows, highs, closes, vols = _series(bars)
    W = min(window, n - 1); s = n - W
    ph = [(i, highs[i]) for i in _pivots(highs, k, "high") if i >= s]
    pl = [(i, lows[i]) for i in _pivots(lows, k, "low") if i >= s]
    return {"n": n, "s": s, "lows": lows, "highs": highs, "closes": closes, "vols": vols,
            "ph": ph, "pl": pl, "up": _fit(ph), "lo": _fit(pl)}


def _brk_line(closes, vols, fit, recent, vol_mult, up=True):
    """最近 recent 根內收盤穿越『趨勢線投影值』且帶量。回傳 (j, vr, level) 或 None。
    接近模式下改判「收盤逼近趨勢線投影值但尚未穿越」。"""
    if not fit:
        return None
    n = len(closes)
    band = _near.get()
    if band is not None:
        lvl = _at(fit, n - 1)
        last = closes[-1]
        hit = (lvl > 0 and lvl * (1 - band) <= last < lvl) if up \
            else (lvl > 0 and lvl < last <= lvl * (1 + band))
        if hit:
            ref = vols[max(0, n - 51):n - 1]
            avg = sum(ref) / len(ref) if ref else 0
            return n - 1, (round(vols[-1] / avg, 2) if avg > 0 else None), round(lvl, 2)
        return None
    for j in range(max(1, n - recent), n):
        lvl, lvlp = _at(fit, j), _at(fit, j - 1)
        ok = (closes[j] > lvl and closes[j - 1] <= lvlp) if up else (closes[j] < lvl and closes[j - 1] >= lvlp)
        if ok:
            ref = vols[max(0, j - 50):j]; avg = sum(ref) / len(ref) if ref else 0
            if avg > 0 and vols[j] >= vol_mult * avg:
                return j, round(vols[j] / avg, 2), round(lvl, 2)
    return None


def _out_cont(pattern, direction, level, height, bars, closes, j, vr, points):
    height = max(height, 0.0)
    tgt = round(level + height, 2) if direction == "bull" else round(max(0.0, level - height), 2)
    return {
        "pattern": pattern, "dir": direction, "neckline": round(level, 2),
        "breakout_date": _d(bars[j]["trade_date"]), "breakout_close": round(closes[j], 2),
        "vol_ratio": vr, "target": tgt,
        "points": [{"date": _d(bars[i]["trade_date"]), "price": round(p, 2), "label": lab}
                   for i, p, lab in points],
    }


def detect_darvas_box(bars, window=45, k=3, recent=3, vol_mult=1.5, min_h=0.03, max_h=0.20):
    """矩形／箱型(Darvas)：上下緣皆水平的箱體，突破箱頂帶量（偏多）。"""
    if len(bars) < 40:
        return None
    ch = _channel(bars, window, k)
    up, lo = ch["up"], ch["lo"]
    if not up or not lo or len(ch["ph"]) < 2 or len(ch["pl"]) < 2:
        return None
    mid = ch["closes"][-1]
    if abs(_rel(up[0], mid)) > _FLAT or abs(_rel(lo[0], mid)) > _FLAT:      # 上下皆水平
        return None
    top = max(v for _, v in ch["ph"])       # 箱頂/底取樞紐值（排除突破棒本身）
    bot = min(v for _, v in ch["pl"])
    if bot <= 0 or not (min_h <= (top - bot) / bot <= max_h):
        return None
    b = _breakout(ch["closes"], ch["vols"], top, recent, vol_mult)
    if not b:
        return None
    j, vr = b
    return _out_cont("darvas_box", "bull", top, top - bot, bars, ch["closes"], j, vr,
                     [(ch["s"], top, "箱頂"), (ch["s"], bot, "箱底")])


def detect_asc_triangle(bars, window=50, k=2, recent=3, vol_mult=1.4):
    """上升三角：上緣水平壓力、下緣低點抬升，突破上緣帶量（偏多）。"""
    ch = _channel(bars, window, k); up, lo = ch["up"], ch["lo"]
    if not up or not lo or len(ch["ph"]) < 2 or len(ch["pl"]) < 2:
        return None
    mid = ch["closes"][-1]
    if not (abs(_rel(up[0], mid)) < _FLAT and _rel(lo[0], mid) > _FLAT):
        return None
    b = _brk_line(ch["closes"], ch["vols"], up, recent, vol_mult, up=True)
    if not b:
        return None
    j, vr, lvl = b
    return _out_cont("asc_triangle", "bull", lvl, _at(up, ch["s"]) - _at(lo, ch["s"]),
                     bars, ch["closes"], j, vr,
                     [(ch["ph"][0][0], ch["ph"][0][1], "水平壓力"), (ch["pl"][0][0], ch["pl"][0][1], "低點抬升")])


def detect_desc_triangle(bars, window=50, k=2, recent=3, vol_mult=1.4):
    """下降三角：上緣高點下壓、下緣水平支撐，跌破下緣帶量（偏空）。"""
    ch = _channel(bars, window, k); up, lo = ch["up"], ch["lo"]
    if not up or not lo or len(ch["ph"]) < 2 or len(ch["pl"]) < 2:
        return None
    mid = ch["closes"][-1]
    if not (_rel(up[0], mid) < -_FLAT and abs(_rel(lo[0], mid)) < _FLAT):
        return None
    b = _brk_line(ch["closes"], ch["vols"], lo, recent, vol_mult, up=False)
    if not b:
        return None
    j, vr, lvl = b
    return _out_cont("desc_triangle", "bear", lvl, _at(up, ch["s"]) - _at(lo, ch["s"]),
                     bars, ch["closes"], j, vr,
                     [(ch["ph"][0][0], ch["ph"][0][1], "高點下壓"), (ch["pl"][0][0], ch["pl"][0][1], "水平支撐")])


def detect_sym_triangle(bars, window=50, k=2, recent=3, vol_mult=1.4):
    """對稱三角：上緣下壓、下緣抬升（收斂），突破任一邊帶量（方向依突破側）。"""
    ch = _channel(bars, window, k); up, lo = ch["up"], ch["lo"]
    if not up or not lo or len(ch["ph"]) < 2 or len(ch["pl"]) < 2:
        return None
    mid = ch["closes"][-1]
    if not (_rel(up[0], mid) < -_FLAT and _rel(lo[0], mid) > _FLAT):
        return None
    h = _at(up, ch["s"]) - _at(lo, ch["s"])
    b = _brk_line(ch["closes"], ch["vols"], up, recent, vol_mult, up=True)
    if b:
        j, vr, lvl = b
        return _out_cont("sym_triangle", "bull", lvl, h, bars, ch["closes"], j, vr,
                         [(ch["ph"][0][0], ch["ph"][0][1], "上緣"), (ch["pl"][0][0], ch["pl"][0][1], "下緣")])
    b = _brk_line(ch["closes"], ch["vols"], lo, recent, vol_mult, up=False)
    if b:
        j, vr, lvl = b
        return _out_cont("sym_triangle", "bear", lvl, h, bars, ch["closes"], j, vr,
                         [(ch["ph"][0][0], ch["ph"][0][1], "上緣"), (ch["pl"][0][0], ch["pl"][0][1], "下緣")])
    return None


def detect_wedge(bars, window=50, k=2, recent=3, vol_mult=1.4):
    """楔形：上升楔（雙線上升且收斂→偏空跌破下緣）／下降楔（雙線下降且收斂→偏多突破上緣）。"""
    ch = _channel(bars, window, k); up, lo = ch["up"], ch["lo"]
    if not up or not lo or len(ch["ph"]) < 2 or len(ch["pl"]) < 2:
        return None
    mid = ch["closes"][-1]
    ru, rl = _rel(up[0], mid), _rel(lo[0], mid)
    h = abs(_at(up, ch["s"]) - _at(lo, ch["s"]))
    if ru > _FLAT and rl > _FLAT and up[0] < lo[0]:            # 上升楔（收斂）→ 空
        b = _brk_line(ch["closes"], ch["vols"], lo, recent, vol_mult, up=False)
        if b:
            j, vr, lvl = b
            return _out_cont("wedge", "bear", lvl, h, bars, ch["closes"], j, vr,
                             [(ch["ph"][0][0], ch["ph"][0][1], "上緣"), (ch["pl"][0][0], ch["pl"][0][1], "下緣")])
    if ru < -_FLAT and rl < -_FLAT and up[0] > lo[0]:         # 下降楔（收斂）→ 多
        b = _brk_line(ch["closes"], ch["vols"], up, recent, vol_mult, up=True)
        if b:
            j, vr, lvl = b
            return _out_cont("wedge", "bull", lvl, h, bars, ch["closes"], j, vr,
                             [(ch["ph"][0][0], ch["ph"][0][1], "上緣"), (ch["pl"][0][0], ch["pl"][0][1], "下緣")])
    return None


def detect_flag(bars, flag_win=18, pole_win=12, recent=3, vol_mult=1.4, pole_move=0.15):
    """旗形／三角旗：強勢旗桿 + 小幅整理，順勢突破旗頂(多)/旗底(空)帶量。"""
    n = len(bars)
    if n < flag_win + pole_win + 5:
        return None
    lows, highs, closes, vols = _series(bars)
    fs = n - flag_win; ps = fs - pole_win
    if ps < 0 or closes[ps] <= 0:
        return None
    pole = (closes[fs] - closes[ps]) / closes[ps]
    if pole >= pole_move:                                     # 多方旗桿 → 突破旗頂
        flag_hi = max(highs[fs:])
        b = _breakout(closes, vols, flag_hi, recent, vol_mult)
        if b:
            j, vr = b
            return _out_cont("flag", "bull", flag_hi, closes[fs] - closes[ps], bars, closes, j, vr,
                             [(ps, closes[ps], "旗桿起"), (fs, flag_hi, "旗頂")])
    elif pole <= -pole_move:                                  # 空方旗桿 → 跌破旗底
        flag_lo = min(lows[fs:])
        b = _breakdown(closes, vols, flag_lo, recent, vol_mult)
        if b:
            j, vr = b
            return _out_cont("flag", "bear", flag_lo, closes[ps] - closes[fs], bars, closes, j, vr,
                             [(ps, closes[ps], "旗桿起"), (fs, flag_lo, "旗底")])
    return None


# ======================================================================
# 頭部反轉型態（底部的鏡像：抓 swing high、跌破頸線、偏空）
# ======================================================================

def _breakdown(closes, vols, level, recent=3, vol_mult=1.5):
    """最近 recent 根內收盤『由 ≥level 轉為 <level』且帶量。回傳 (j, vr) 或 None。
    接近模式下改判「收盤逼近 level 但尚未跌破」。"""
    band = _near.get()
    if band is not None:
        return _near_down(closes, vols, level, band)
    n = len(closes)
    for j in range(max(1, n - recent), n):
        if closes[j] < level and closes[j - 1] >= level:
            ref = vols[max(0, j - 50):j]; avg = sum(ref) / len(ref) if ref else 0
            if avg > 0 and vols[j] >= vol_mult * avg:
                return j, round(vols[j] / avg, 2)
    return None


def detect_double_top(bars, k=3, recent=3, tol=0.05, min_depth=0.08,
                      vol_mult=1.5, min_gap=8, max_gap=90):
    """M頭/雙重頂：最近兩個相近 swing high + 中間頸線，跌破頸線帶量（偏空）。"""
    n = len(bars)
    if n < 40:
        return None
    lows, highs, closes, vols = _series(bars)
    piv = _swing_highs(highs, k)
    if len(piv) < 2:
        return None
    i1, i2 = piv[-2], piv[-1]
    h1, h2 = highs[i1], highs[i2]
    if h1 <= 0 or not (min_gap <= i2 - i1 <= max_gap) or abs(h2 - h1) / h1 > tol:
        return None
    neck = min(lows[i1:i2 + 1])                 # 兩頂之間最低點 = 頸線
    top = max(h1, h2)
    if neck <= 0 or top / neck - 1 < min_depth:
        return None
    b = _breakdown(closes, vols, neck, recent, vol_mult)
    if not b:
        return None
    j, vr = b
    return _out_cont("double_top", "bear", neck, top - neck, bars, closes, j, vr,
                     [(i1, h1, "頂1"), (i2, h2, "頂2")])


def detect_triple_top(bars, k=3, recent=3, tol=0.05, min_depth=0.08,
                      vol_mult=1.5, min_gap=6, max_gap=60):
    """三重頂：最近三個相近 swing high + 下緣頸線，跌破頸線帶量（偏空）。"""
    n = len(bars)
    if n < 50:
        return None
    lows, highs, closes, vols = _series(bars)
    piv = _swing_highs(highs, k)
    if len(piv) < 3:
        return None
    i1, i2, i3 = piv[-3], piv[-2], piv[-1]
    h1, h2, h3 = highs[i1], highs[i2], highs[i3]
    top = max(h1, h2, h3)
    if min(h1, h2, h3) <= 0 or (top - min(h1, h2, h3)) / min(h1, h2, h3) > tol:
        return None
    if not (min_gap <= i2 - i1 <= max_gap and min_gap <= i3 - i2 <= max_gap):
        return None
    neck = min(min(lows[i1:i2 + 1]), min(lows[i2:i3 + 1]))
    if neck <= 0 or top / neck - 1 < min_depth:
        return None
    b = _breakdown(closes, vols, neck, recent, vol_mult)
    if not b:
        return None
    j, vr = b
    return _out_cont("triple_top", "bear", neck, top - neck, bars, closes, j, vr,
                     [(i1, h1, "頂1"), (i2, h2, "頂2"), (i3, h3, "頂3")])


def detect_hs_top(bars, k=3, recent=3, tol=0.06, min_depth=0.08,
                  vol_mult=1.5, min_gap=5, max_gap=60):
    """頭肩頂：左肩–頭(最高)–右肩，肩相近、頭明顯較高，跌破頸線帶量（偏空）。"""
    n = len(bars)
    if n < 50:
        return None
    lows, highs, closes, vols = _series(bars)
    piv = _swing_highs(highs, k)
    if len(piv) < 3:
        return None
    i1, i2, i3 = piv[-3], piv[-2], piv[-1]
    ls, hd, rs = highs[i1], highs[i2], highs[i3]                 # 左肩 / 頭 / 右肩
    if ls <= 0 or not (hd > ls * 1.02 and hd > rs * 1.02):      # 頭要明顯最高
        return None
    if abs(rs - ls) / ls > tol:                                 # 兩肩相近
        return None
    if not (min_gap <= i2 - i1 <= max_gap and min_gap <= i3 - i2 <= max_gap):
        return None
    neck = min(min(lows[i1:i2 + 1]), min(lows[i2:i3 + 1]))
    if neck <= 0 or hd / neck - 1 < min_depth:
        return None
    b = _breakdown(closes, vols, neck, recent, vol_mult)
    if not b:
        return None
    j, vr = b
    return _out_cont("hs_top", "bear", neck, hd - neck, bars, closes, j, vr,
                     [(i1, ls, "左肩"), (i2, hd, "頭"), (i3, rs, "右肩")])


def detect_rounding_top(bars, recent=3, vol_mult=1.3, min_depth=0.10,
                        window=70, flat_band=0.06):
    """圓弧頂：緩升緩降的寬頂倒 U 形，跌破右側(左緣支撐)帶量（偏空）。"""
    n = len(bars)
    if n < 45:
        return None
    lows, highs, closes, vols = _series(bars)
    W = min(window, n - 1)
    seg_hi = highs[-W:]; seg_lo = lows[-W:]
    off = n - W
    hi = max(range(W), key=lambda i: seg_hi[i])                 # 窗口內最高點
    top = seg_hi[hi]
    if not (0.30 * W <= hi <= 0.70 * W):                        # 高點在中段
        return None
    rim = min(seg_lo[:max(1, W // 10)])                         # 左緣支撐
    if rim <= 0 or top / rim - 1 < min_depth:
        return None
    broad = sum(1 for v in seg_hi if v >= top * (1 - flat_band))   # 寬頂：貼近頂的根數
    if broad < 0.25 * W:                                        # 太尖 → 非圓弧
        return None
    third = W // 3                                              # 右側須lower（回落）
    if sum(seg_hi[2 * third:]) / (W - 2 * third) >= sum(seg_hi[third:2 * third]) / third:
        return None
    b = _breakdown(closes, vols, rim, recent, vol_mult)
    if not b:
        return None
    j, vr = b
    return _out_cont("rounding_top", "bear", rim, top - rim, bars, closes, j, vr,
                     [(off + hi, top, "圓弧頂")])


# ======================================================================
# 型態登錄：三組（底部反轉 / 頭部反轉 / 連續整理），各有掃描優先序 + 中文名
# ======================================================================
DETECTORS = {                       # 底部反轉
    "double_bottom": detect_double_bottom,
    "triple_bottom": detect_triple_bottom,
    "hs_bottom": detect_hs_bottom,
    "cup_handle": detect_cup_handle,
    "rounding_bottom": detect_rounding_bottom,
    "v_reversal": detect_v_reversal,
}
DETECTORS_TOP = {                   # 頭部反轉
    "double_top": detect_double_top,
    "triple_top": detect_triple_top,
    "hs_top": detect_hs_top,
    "rounding_top": detect_rounding_top,
}
DETECTORS_CONT = {                  # 連續 / 整理
    "darvas_box": detect_darvas_box,
    "asc_triangle": detect_asc_triangle,
    "desc_triangle": detect_desc_triangle,
    "sym_triangle": detect_sym_triangle,
    "flag": detect_flag,
    "wedge": detect_wedge,
}
GROUPS = {"bottom": DETECTORS, "top": DETECTORS_TOP, "continuation": DETECTORS_CONT}
ALL = {**DETECTORS, **DETECTORS_TOP, **DETECTORS_CONT}
PATTERN_NAMES = {
    "double_bottom": "W底/雙重底", "triple_bottom": "三重底", "hs_bottom": "頭肩底",
    "cup_handle": "杯柄", "rounding_bottom": "圓弧底", "v_reversal": "V型反轉",
    "double_top": "M頭/雙重頂", "triple_top": "三重頂", "hs_top": "頭肩頂",
    "rounding_top": "圓弧頂",
    "darvas_box": "矩形/箱型", "asc_triangle": "上升三角", "desc_triangle": "下降三角",
    "sym_triangle": "對稱三角", "flag": "旗形/三角旗", "wedge": "楔形",
}
