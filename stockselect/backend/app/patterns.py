"""K 棒型態偵測（日本陰陽線）——純用 OHLC，回傳某根 K 棒符合的型態。

參考 Steve Nison《Japanese Candlestick Charting Techniques》經典型態。
bars：由舊到新的 list[dict]，每個含 open/high/low/close（用還原價）。
detect(bars) 回傳「最後一根」符合的型態 key 清單。
"""

# 型態目錄：key -> (中文名, 方向 bull/bear/neutral)
CATALOG = {
    "hammer":          ("錘子線", "bull"),
    "inverted_hammer": ("倒錘線", "bull"),
    "hanging_man":     ("吊人線", "bear"),
    "shooting_star":   ("流星線", "bear"),
    "doji":            ("十字星", "neutral"),
    "bull_engulfing":  ("多頭吞噬", "bull"),
    "bear_engulfing":  ("空頭吞噬", "bear"),
    "piercing":        ("貫穿線", "bull"),
    "dark_cloud":      ("烏雲罩頂", "bear"),
    "bull_harami":     ("多頭孕線", "bull"),
    "bear_harami":     ("空頭孕線", "bear"),
    "morning_star":    ("晨星", "bull"),
    "evening_star":    ("夜星", "bear"),
    "three_soldiers":  ("紅三兵", "bull"),
    "three_crows":     ("黑三鴉", "bear"),
}


def _m(b):
    o, h, l, c = float(b["open"]), float(b["high"]), float(b["low"]), float(b["close"])
    rng = h - l
    body = abs(c - o)
    upper = h - max(o, c)
    lower = min(o, c) - l
    return {"o": o, "h": h, "l": l, "c": c, "rng": rng, "body": body,
            "up": upper, "lo": lower, "white": c > o, "mid": (o + c) / 2}


def _trend(bars, upto, look=5):
    """pattern 之前 look 根的趨勢：1 上升 / -1 下降 / 0 持平。upto=pattern 起始索引（不含）。"""
    cs = [float(b["close"]) for b in bars[max(0, upto - look):upto]]
    if len(cs) < 2:
        return 0
    return 1 if cs[-1] > cs[0] else (-1 if cs[-1] < cs[0] else 0)


def detect(bars):
    """回傳最後一根 K 棒符合的型態 key 清單。"""
    n = len(bars)
    if n < 1:
        return []
    a = _m(bars[-1])
    if a["rng"] <= 0:
        return []
    out = []
    small = a["body"] <= 0.4 * a["rng"]

    # ---- 單根 ----
    if a["body"] <= 0.1 * a["rng"]:
        out.append("doji")
    # 錘子家族：長下影、短上影、實體小
    if a["lo"] >= 2 * a["body"] and a["up"] <= 0.3 * a["rng"] and a["body"] > 0 and small:
        out.append("hammer" if _trend(bars, n - 1) < 0 else "hanging_man")
    # 倒錘/流星：長上影、短下影
    if a["up"] >= 2 * a["body"] and a["lo"] <= 0.3 * a["rng"] and a["body"] > 0 and small:
        out.append("inverted_hammer" if _trend(bars, n - 1) < 0 else "shooting_star")

    # ---- 兩根 ----
    if n >= 2:
        p = _m(bars[-2])
        # 吞噬：今日實體完全包住昨日實體
        if (not p["white"]) and a["white"] and a["c"] >= p["o"] and a["o"] <= p["c"] and a["body"] > p["body"]:
            out.append("bull_engulfing")
        if p["white"] and (not a["white"]) and a["o"] >= p["c"] and a["c"] <= p["o"] and a["body"] > p["body"]:
            out.append("bear_engulfing")
        # 貫穿線：跌勢中，今紅開低於昨低、收在昨實體中點以上但未過昨開
        if _trend(bars, n - 2) < 0 and (not p["white"]) and a["white"] and \
           a["o"] < p["l"] and p["mid"] < a["c"] < p["o"]:
            out.append("piercing")
        # 烏雲罩頂：漲勢中，今黑開高於昨高、收在昨實體中點以下但未破昨開
        if _trend(bars, n - 2) > 0 and p["white"] and (not a["white"]) and \
           a["o"] > p["h"] and p["o"] < a["c"] < p["mid"]:
            out.append("dark_cloud")
        # 孕線：昨長實體、今小實體被包在昨實體內
        p_long = p["body"] >= 0.6 * p["rng"] and p["rng"] > 0
        inside = max(a["o"], a["c"]) <= max(p["o"], p["c"]) and min(a["o"], a["c"]) >= min(p["o"], p["c"])
        if p_long and inside and a["body"] < p["body"]:
            if (not p["white"]) and a["white"]:
                out.append("bull_harami")
            if p["white"] and (not a["white"]):
                out.append("bear_harami")

    # ---- 三根 ----
    if n >= 3:
        b1, b2, b3 = _m(bars[-3]), _m(bars[-2]), _m(bars[-1])
        b1_long = b1["body"] >= 0.6 * b1["rng"] and b1["rng"] > 0
        b3_long = b3["body"] >= 0.6 * b3["rng"] and b3["rng"] > 0
        star = b2["body"] <= 0.4 * max(b2["rng"], 1e-9)
        # 晨星：黑→小星(跳空下)→紅收入第一根實體上半
        if _trend(bars, n - 3) < 0 and b1_long and (not b1["white"]) and star and \
           b3["white"] and b3["c"] > (b1["o"] + b1["c"]) / 2 and max(b2["o"], b2["c"]) < b1["c"]:
            out.append("morning_star")
        # 夜星：紅→小星(跳空上)→黑收入第一根實體下半
        if _trend(bars, n - 3) > 0 and b1_long and b1["white"] and star and \
           (not b3["white"]) and b3["c"] < (b1["o"] + b1["c"]) / 2 and min(b2["o"], b2["c"]) > b1["c"]:
            out.append("evening_star")
        # 紅三兵：連三紅、收盤遞增、開盤落在前一根實體內
        if all(x["white"] for x in (b1, b2, b3)) and b3["c"] > b2["c"] > b1["c"] and \
           b1["o"] < b2["o"] < b3["o"] and b2["o"] <= b1["c"] and b3["o"] <= b2["c"]:
            out.append("three_soldiers")
        # 黑三鴉：連三黑、收盤遞減
        if all(not x["white"] for x in (b1, b2, b3)) and b3["c"] < b2["c"] < b1["c"] and \
           b1["o"] > b2["o"] > b3["o"]:
            out.append("three_crows")

    return out
