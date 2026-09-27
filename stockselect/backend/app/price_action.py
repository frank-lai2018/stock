"""裸 K（Price Action）決策引擎。

只使用 OHLC：K 棒型態、市場結構、支撐壓力、觸發/失效與風險報酬。
成交量、均線、基本面與籌碼刻意不進入分數，避免「裸 K」定義失焦。
"""
from statistics import median

from . import patterns


EXTRA_CATALOG = {
    "inside_bar": ("內包線", "neutral"),
    "outside_bull": ("多方外包線", "bull"),
    "outside_bear": ("空方外包線", "bear"),
    "pin_bull": ("多方 Pin Bar", "bull"),
    "pin_bear": ("空方 Pin Bar", "bear"),
    "fakey_bull": ("多方 Fakey", "bull"),
    "fakey_bear": ("空方 Fakey", "bear"),
    "two_bar_bull": ("兩棒多方反轉", "bull"),
    "two_bar_bear": ("兩棒空方反轉", "bear"),
    "nr7": ("NR7 窄幅整理", "neutral"),
}


def _f(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _date(value):
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


def _measure(bar):
    o, h, l, c = (_f(bar[k]) for k in ("open", "high", "low", "close"))
    span = max(h - l, 0.0)
    body = abs(c - o)
    return {"o": o, "h": h, "l": l, "c": c, "span": span, "body": body,
            "upper": h - max(o, c), "lower": min(o, c) - l, "bull": c > o}


def detect_setups(bars):
    """回傳最後一根命中的經典陰陽線＋裸 K 延伸型態。"""
    if not bars:
        return []
    found = [{"key": k, "name": patterns.CATALOG[k][0], "dir": patterns.CATALOG[k][1]}
             for k in patterns.detect(bars)]
    a = _measure(bars[-1])
    if a["span"] <= 0:
        return found
    keys = {x["key"] for x in found}

    # Pin Bar 比經典錘子家族稍寬鬆，但要求收盤落在反轉方向的一側。
    if a["lower"] >= 2 * max(a["body"], a["span"] * 0.05) and a["upper"] <= a["span"] * 0.25 \
            and a["c"] >= a["l"] + a["span"] * 0.65 and "hammer" not in keys:
        found.append({"key": "pin_bull", "name": EXTRA_CATALOG["pin_bull"][0], "dir": "bull"})
    if a["upper"] >= 2 * max(a["body"], a["span"] * 0.05) and a["lower"] <= a["span"] * 0.25 \
            and a["c"] <= a["l"] + a["span"] * 0.35 and "shooting_star" not in keys:
        found.append({"key": "pin_bear", "name": EXTRA_CATALOG["pin_bear"][0], "dir": "bear"})

    if len(bars) >= 2:
        p = _measure(bars[-2])
        if a["h"] < p["h"] and a["l"] > p["l"]:
            found.append({"key": "inside_bar", "name": EXTRA_CATALOG["inside_bar"][0], "dir": "neutral"})
        if a["h"] > p["h"] and a["l"] < p["l"]:
            key = "outside_bull" if a["c"] >= a["l"] + a["span"] * 0.6 else "outside_bear"
            found.append({"key": key, "name": EXTRA_CATALOG[key][0], "dir": EXTRA_CATALOG[key][1]})
        # 兩棒反轉：方向相反、低/高點相近，第二棒收復第一棒實體中點。
        tol = max(a["span"], p["span"]) * 0.25
        if not p["bull"] and a["bull"] and abs(a["l"] - p["l"]) <= tol and a["c"] > (p["o"] + p["c"]) / 2:
            found.append({"key": "two_bar_bull", "name": EXTRA_CATALOG["two_bar_bull"][0], "dir": "bull"})
        if p["bull"] and not a["bull"] and abs(a["h"] - p["h"]) <= tol and a["c"] < (p["o"] + p["c"]) / 2:
            found.append({"key": "two_bar_bear", "name": EXTRA_CATALOG["two_bar_bear"][0], "dir": "bear"})

    if len(bars) >= 3:
        mother, inside = _measure(bars[-3]), _measure(bars[-2])
        was_inside = inside["h"] < mother["h"] and inside["l"] > mother["l"]
        if was_inside and a["l"] < mother["l"] and a["c"] > mother["l"]:
            found.append({"key": "fakey_bull", "name": EXTRA_CATALOG["fakey_bull"][0], "dir": "bull"})
        if was_inside and a["h"] > mother["h"] and a["c"] < mother["h"]:
            found.append({"key": "fakey_bear", "name": EXTRA_CATALOG["fakey_bear"][0], "dir": "bear"})

    if len(bars) >= 7:
        spans = [_measure(b)["span"] for b in bars[-7:]]
        if spans[-1] > 0 and spans[-1] == min(spans):
            found.append({"key": "nr7", "name": EXTRA_CATALOG["nr7"][0], "dir": "neutral"})

    # 同 key 去重，保留偵測順序。
    out, seen = [], set()
    for item in found:
        if item["key"] not in seen:
            out.append(item); seen.add(item["key"])
    return out


def _pivots(values, kind, k=2):
    out = []
    for i in range(k, len(values) - k):
        window = values[i - k:i + k + 1]
        if kind == "high" and values[i] == max(window):
            out.append((i, values[i]))
        if kind == "low" and values[i] == min(window):
            out.append((i, values[i]))
    return out


def market_context(bars, signal_idx):
    """以訊號當時可見資料建立結構、區間位置與已確認支撐壓力。"""
    hist = bars[:signal_idx + 1]
    highs = [_f(b["high"]) for b in hist]
    lows = [_f(b["low"]) for b in hist]
    closes = [_f(b["close"]) for b in hist]
    ph, pl = _pivots(highs, "high"), _pivots(lows, "low")
    if len(ph) >= 2 and len(pl) >= 2:
        if ph[-1][1] > ph[-2][1] and pl[-1][1] > pl[-2][1]:
            structure = "up"
        elif ph[-1][1] < ph[-2][1] and pl[-1][1] < pl[-2][1]:
            structure = "down"
        else:
            structure = "range"
    else:
        structure = "unknown"

    price = closes[-1]
    prior = hist[:-1][-60:] or hist
    prior_high = max(_f(b["high"]) for b in prior)
    prior_low = min(_f(b["low"]) for b in prior)
    span = prior_high - prior_low
    range_pos = (price - prior_low) / span if span > 0 else None
    supports = [v for _, v in pl if v < price] + ([prior_low] if prior_low < price else [])
    resistances = [v for _, v in ph if v > price] + ([prior_high] if prior_high > price else [])
    support = max(supports) if supports else prior_low
    resistance = min(resistances) if resistances else prior_high
    prior_res_below = max([v for _, v in ph if v <= price] or [0])
    prior_sup_above = min([v for _, v in pl if v >= price] or [float("inf")])
    return {"structure": structure, "range_pos": range_pos, "support": support,
            "resistance": resistance, "prior_res_below": prior_res_below,
            "prior_sup_above": prior_sup_above, "pivot_highs": ph, "pivot_lows": pl}


def _resolve_direction(setup, context, signal):
    if setup["dir"] != "neutral":
        return setup["dir"]
    close = _f(signal["close"])
    sd = abs(_f(signal["low"]) - context["support"]) / close if close else 9
    rd = abs(context["resistance"] - _f(signal["high"])) / close if close else 9
    if sd <= 0.03 and sd < rd:
        return "bull"
    if rd <= 0.03 and rd < sd:
        return "bear"
    return "bull" if context["structure"] == "up" else "bear" if context["structure"] == "down" else "neutral"


def _location(context, signal, direction):
    close, high, low = (_f(signal[k]) for k in ("close", "high", "low"))
    pos = context["range_pos"]
    tags, score = [], 0.0
    if direction == "bull":
        dist = abs(low - context["support"]) / close if close else 9
        if dist <= 0.015: score += 14; tags.append("貼近支撐")
        elif dist <= 0.03: score += 10; tags.append("支撐附近")
        elif dist <= 0.05: score += 6; tags.append("接近支撐")
        level = context["prior_res_below"]
        if level and low <= level * 1.015 and close > level:
            score += 10; tags.append("突破回測")
        if pos is not None and pos <= 0.25: score += 8; tags.append("區間低檔")
        elif pos is not None and pos <= 0.40: score += 4
    elif direction == "bear":
        dist = abs(context["resistance"] - high) / close if close else 9
        if dist <= 0.015: score += 14; tags.append("貼近壓力")
        elif dist <= 0.03: score += 10; tags.append("壓力附近")
        elif dist <= 0.05: score += 6; tags.append("接近壓力")
        level = context["prior_sup_above"]
        if level != float("inf") and high >= level * 0.985 and close < level:
            score += 10; tags.append("跌破回測")
        if pos is not None and pos >= 0.75: score += 8; tags.append("區間高檔")
        elif pos is not None and pos >= 0.60: score += 4
    if not tags:
        tags.append("區間中段")
    return min(score, 25.0), tags


def _pattern_quality(bars, signal_idx, setup, direction):
    m = _measure(bars[signal_idx])
    multi = {"bull_engulfing", "bear_engulfing", "piercing", "dark_cloud", "morning_star",
             "evening_star", "three_soldiers", "three_crows", "fakey_bull", "fakey_bear",
             "two_bar_bull", "two_bar_bear", "outside_bull", "outside_bear"}
    score = 11.0 if setup["key"] in multi else 7.0
    if m["span"] > 0:
        close_pos = (m["c"] - m["l"]) / m["span"]
        score += (close_pos if direction == "bull" else 1 - close_pos) * 5.0
        prior_spans = [_measure(b)["span"] for b in bars[max(0, signal_idx - 20):signal_idx]]
        med = median([x for x in prior_spans if x > 0]) if any(x > 0 for x in prior_spans) else 0
        ratio = m["span"] / med if med else 1
        score += 4.0 if 0.6 <= ratio <= 1.8 else 2.0 if ratio <= 2.5 else 0.0
    return min(score, 20.0)


def evaluate_signal(bars, signal_idx, direction, expiry=5):
    """訊號形成後才允許觸發；同根同時碰停損與觸發時保守判失效。"""
    signal = bars[signal_idx]
    trigger = _f(signal["high"] if direction == "bull" else signal["low"])
    stop = _f(signal["low"] if direction == "bull" else signal["high"])
    status, trigger_date, fill = "waiting", None, None
    for bar in bars[signal_idx + 1:]:
        high, low, op = (_f(bar[k]) for k in ("high", "low", "open"))
        hit_stop = low <= stop if direction == "bull" else high >= stop
        hit_trigger = high > trigger if direction == "bull" else low < trigger
        if fill is None:
            if hit_stop:
                status = "invalid"; break
            if hit_trigger:
                status = "triggered"
                trigger_date = _date(bar.get("trade_date"))
                fill = max(trigger, op) if direction == "bull" else min(trigger, op)
        elif hit_stop:
            status = "failed"; break
    age = len(bars) - 1 - signal_idx
    if status == "waiting" and age > expiry:
        status = "expired"
    return {"status": status, "trigger": trigger, "stop": stop, "fill": fill,
            "trigger_date": trigger_date, "age": age}


def _target(context, entry, stop, direction):
    if direction == "bull":
        candidates = [v for _, v in context["pivot_highs"] if v > entry * 1.002]
        if context["resistance"] > entry * 1.002:
            candidates.append(context["resistance"])
        natural = min(candidates) if candidates else None
        return (natural, "下一道壓力") if natural else (entry + 2 * (entry - stop), "2R推估")
    candidates = [v for _, v in context["pivot_lows"] if v < entry * 0.998]
    if context["support"] < entry * 0.998:
        candidates.append(context["support"])
    natural = max(candidates) if candidates else None
    return (natural, "下一道支撐") if natural else (entry - 2 * (stop - entry), "2R推估")


def _decision(bars, signal_idx, setup, context, expiry):
    signal = bars[signal_idx]
    direction = _resolve_direction(setup, context, signal)
    if direction == "neutral":
        return None
    state = evaluate_signal(bars, signal_idx, direction, expiry)
    entry = state["fill"] if state["fill"] is not None else state["trigger"]
    stop = state["stop"]
    unit_risk = entry - stop if direction == "bull" else stop - entry
    if entry <= 0 or unit_risk <= 0:
        return None
    target, target_source = _target(context, entry, stop, direction)
    reward = target - entry if direction == "bull" else entry - target
    rr = reward / unit_risk if unit_risk else None
    risk_pct = unit_risk / entry

    structure_map = {"up": "HH-HL 多頭", "down": "LH-LL 空頭", "range": "區間整理", "unknown": "結構未明"}
    structure_score = ({"up": 30, "range": 20, "down": 10, "unknown": 15} if direction == "bull"
                       else {"down": 30, "range": 20, "up": 10, "unknown": 15})[context["structure"]]
    location_score, location_tags = _location(context, signal, direction)
    pattern_score = _pattern_quality(bars, signal_idx, setup, direction)
    confirmation_score = 15.0 if state["status"] == "triggered" else 8.0 if state["status"] == "waiting" else 0.0
    risk_score = 0.0
    if rr is not None:
        risk_score += 6 if rr >= 3 else 4 if rr >= 2 else 2 if rr >= 1.5 else 0
    risk_score += 4 if risk_pct <= 0.05 else 3 if risk_pct <= 0.08 else 1 if risk_pct <= 0.10 else 0
    parts = {"structure": structure_score, "location": round(location_score, 1),
             "pattern": round(pattern_score, 1), "confirmation": confirmation_score,
             "risk": round(risk_score, 1)}
    score = round(sum(parts.values()), 1)

    blockers, notes = [], []
    if location_score < 8:
        blockers.append("遠離有效支撐／壓力，位置不佳")
    if risk_pct > 0.08:
        blockers.append("訊號K停損距離超過 8%")
    if rr is None or rr < 2:
        blockers.append("到下一結構位的 R/R 未達 2")
    if state["status"] in ("invalid", "failed", "expired"):
        blockers.append({"invalid": "觸發前已先失效", "failed": "觸發後已碰停損",
                         "expired": "等待超過有效期限"}[state["status"]])
    current = _f(bars[-1]["close"])
    move = ((current / entry - 1) if direction == "bull" else (entry / current - 1)) if current else None
    if state["status"] == "triggered" and move is not None and move > 0.03:
        blockers.append("觸發後已走超過 3%，避免追價")
    if target_source == "2R推估":
        notes.append("前方無明確結構位，目標暫以 2R 推估")

    if state["status"] == "triggered" and score >= 75 and not blockers:
        conclusion = "priority"
    elif state["status"] == "waiting" and score >= 65 and not blockers:
        conclusion = "waiting"
    elif state["status"] == "triggered" and score >= 65 and len(blockers) <= 1:
        conclusion = "watch"
    else:
        conclusion = "skip"
    return {
        "score": score, "conclusion": conclusion, "status": state["status"], "direction": direction,
        "pattern": setup["key"], "pattern_name": setup["name"], "parts": parts,
        "structure": context["structure"], "structure_name": structure_map[context["structure"]],
        "locations": location_tags, "range_pos": round(context["range_pos"] * 100, 1) if context["range_pos"] is not None else None,
        "signal_date": _date(signal.get("trade_date")), "trigger_date": state["trigger_date"],
        "age": state["age"], "trigger": round(state["trigger"], 2), "entry": round(entry, 2),
        "stop": round(stop, 2), "target": round(target, 2), "target_source": target_source,
        "risk_pct": round(risk_pct * 100, 2), "rr": round(rr, 2) if rr is not None else None,
        "current": round(current, 2), "move_pct": round(move * 100, 2) if move is not None else None,
        "support": round(context["support"], 2), "resistance": round(context["resistance"], 2),
        "blockers": blockers, "notes": notes,
    }


def analyze(bars, lookback=5, expiry=5):
    """分析最近 lookback 根曾形成的訊號，回傳最值得關注的一個決策。"""
    if len(bars) < 30:
        return None
    candidates = []
    start = max(20, len(bars) - max(1, int(lookback)))
    for i in range(start, len(bars)):
        prefix = bars[:i + 1]
        context = market_context(bars, i)
        for setup in detect_setups(prefix):
            item = _decision(bars, i, setup, context, expiry)
            if item:
                candidates.append(item)
    if not candidates:
        return None
    state_rank = {"triggered": 3, "waiting": 2, "failed": 1, "invalid": 0, "expired": 0}
    conclusion_rank = {"priority": 3, "waiting": 2, "watch": 1, "skip": 0}
    return max(candidates, key=lambda x: (conclusion_rank[x["conclusion"]], state_rank[x["status"]],
                                          x["score"], x["signal_date"]))
