"""波段型態偵測：W 底 / 雙重底突破頸線（純 Python 波峰波谷 / 分形樞紐）。

單根 K 棒型態在 patterns.py；這裡處理需要「波段」的大型態。
bars：由舊到新的 list[dict]，含 high/low/close/volume（請用還原價 adj_*）。
"""


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

    piv = _pivots(lows, k, "low")
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

    cross = None                                 # 最近 recent 根內向上穿越頸線
    for j in range(max(i2 + 1, n - recent), n):
        if closes[j] > neck and closes[j - 1] <= neck:
            cross = j
            break
    if cross is None:
        return None

    ref = vols[max(0, cross - 50):cross]         # 突破前 50 日均量
    avg = sum(ref) / len(ref) if ref else 0
    if not (avg > 0 and vols[cross] >= vol_mult * avg):
        return None

    return {
        "pattern": "double_bottom",
        "neckline": round(neck, 2),
        "bottom1": {"date": _d(bars[i1]["trade_date"]), "price": round(lo1, 2)},
        "bottom2": {"date": _d(bars[i2]["trade_date"]), "price": round(lo2, 2)},
        "breakout_date": _d(bars[cross]["trade_date"]),
        "breakout_close": round(closes[cross], 2),
        "vol_ratio": round(vols[cross] / avg, 2),
        "target": round(neck + (neck - base), 2),   # 量測滿足：頸線 + 型態高度
    }
