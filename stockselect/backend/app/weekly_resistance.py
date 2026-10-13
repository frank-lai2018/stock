"""以當時已完成的週 K 前高辨識壓力帶；不使用本週高點或未來轉折。"""
from . import weekly_breakout
from .execution import day

VERSION = "weekly-resistance-v1"
PATTERN = "weekly_resistance"
LABEL = "週線前高壓力帶"
LOOKBACK_WEEKS = 52
MIN_WEEKS = 26
MIN_TOUCHES = 2
MIN_SEPARATION = 4
MIN_AGE = 6
BAND_PCT = .02
NEAR_PCT = .08


def bands(bars):
    if not bars:
        return []
    weeks, _ = weekly_breakout.weekly_bars(bars, bars[-1]["trade_date"])
    current_week = tuple(day(bars[-1]["trade_date"]).isocalendar()[:2])
    # 即使今天週五，也不讓本週突破棒反過來抬高本週的壓力。
    weeks = [w for w in weeks if w["key"] < current_week][-LOOKBACK_WEEKS:]
    if len(weeks) < MIN_WEEKS:
        return []
    # 一根已完成的右鄰週確認轉折；平頂取左端，避免連續同價算多次測試。
    peaks = [(i, w["high"]) for i, w in enumerate(weeks[1:-1], 1)
             if w["high"] > weeks[i - 1]["high"] and w["high"] >= weeks[i + 1]["high"]]
    found, seen = [], set()
    for ceiling in sorted({p for _, p in peaks}, reverse=True):
        # 上緣本身必須是一個保留的測試點，不能因間距去重而留下無測試依據的高上緣。
        anchor = next((i, price) for i, price in peaks if price == ceiling)
        touches = [anchor]
        for i, price in peaks:
            if ceiling * (1 - BAND_PCT) <= price <= ceiling:
                if all(abs(i - other) >= MIN_SEPARATION for other, _ in touches):
                    touches.append((i, price))
        touches.sort()
        if (len(touches) < MIN_TOUCHES or len(weeks) - touches[0][0] < MIN_AGE
                or any(w["close"] > ceiling for w in weeks[touches[0][0]:])):
            continue  # 已被週收盤突破的舊壓力，不拿來當全新的突破。
        indices = tuple(i for i, _ in touches)
        if indices in seen:
            continue
        seen.add(indices)
        found.append({"version": VERSION, "low": min(p for _, p in touches), "high": ceiling,
                      "touch_count": len(touches), "base_weeks": len(weeks) - touches[0][0],
                      "touches": [{"week": weeks[i]["start"].isoformat(), "price": p} for i, p in touches]})
    return found


def display(band, factor=1):
    return {**band, "low": round(band["low"] / factor, 2), "high": round(band["high"] / factor, 2),
            "touches": [{**t, "price": round(t["price"] / factor, 2)} for t in band["touches"]]}


def confirmed(bars, known_bands):
    cur, prev = bars[-1], bars[-2]
    ref = bars[-51:-1]
    average = sum(b["volume"] for b in ref) / len(ref) if ref else 0
    if cur["close"] <= prev["close"] or not average or cur["volume"] / average < 1.3:
        return []
    return [{"pattern": PATTERN, "neckline": band["high"], "pressure_band": band,
             "breakout_date": day(cur["trade_date"]).isoformat()}
            for band in known_bands if prev["close"] <= band["high"] < cur["close"]]


def observation(bars, rs, known_bands, factor=1):
    if len(bars) < 25 or rs is None or rs < 70:
        return None
    cur, prev = bars[-1], bars[-2]
    ma20 = sum(b["close"] for b in bars[-20:]) / 20
    past_ma20 = sum(b["close"] for b in bars[-25:-5]) / 20
    if not cur["close"] > ma20 > past_ma20:
        return None
    current_week = tuple(day(cur["trade_date"]).isocalendar()[:2])
    this_week = [b for b in bars[:-1] if tuple(day(b["trade_date"]).isocalendar()[:2]) == current_week]
    candidates = [b for b in known_bands
                  if b["high"] * (1 - NEAR_PCT) <= cur["close"] <= b["high"]
                  and not any(p["close"] > b["high"] for p in this_week)]
    if not candidates:
        return None
    # 同時接近多個壓力時先看反覆測試最多的帶；同次數取較高壓力，避免小頸線蓋過週線前高。
    band = max(candidates, key=lambda b: (b["touch_count"], b["high"]))
    ref = bars[-21:-1]
    avg_volume = sum(b["volume"] for b in ref) / 20
    ratio = cur["volume"] / avg_volume if avg_volume > 0 else 0
    challenge = (ratio >= 1.5 and cur["high"] >= band["low"]
                 and cur["close"] > cur["open"] and cur["close"] > prev["close"])
    return {"eligible": True, "type": "challenge" if challenge else "near",
            "label": "放量挑戰週線壓力" if challenge else "接近週線壓力",
            "pressure_band": display(band, factor),
            "extension_pct": round((cur["close"] / band["high"] - 1) * 100, 2),
            "volume_ratio": round(ratio, 2), "reason": "尚未收盤突破週線壓力；等待買點確認，不配置部位"}
