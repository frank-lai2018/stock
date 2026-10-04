"""週線突破（實驗）：先看週線趨勢與突破，再用日線找進場點。

週線只用已收完的週：最新一根日 K 是週五，或資料日已進入下一週，那一週才算收完。
  趨勢＝週收盤在 30 週線上，而且 30 週線比 4 週前高。
  突破＝週收盤高於前 52 週的最高週收盤（壓力線），而且那個高點至少在 6 週前（整理夠久，不是一路創新高）。
日線在突破後 4 週內找進場點：
  收盤在壓力線～壓力線 +5%（買點區），而且當天是突破週的週收盤、或收盤高於前一日高點（轉強）＝可進場；
  離壓力線超過 5% 等拉回；收盤跌破壓力線 −1 ATR＝突破失敗。
價格一律用還原價。全部是待驗證的起始參數，不代表最佳值。
"""
from datetime import date, timedelta

from .execution import day, number

LOOKBACK_WEEKS = 52        # 壓力線＝前 52 週的最高週收盤
MIN_BASE_WEEKS = 6         # 壓力線那週到突破週至少隔 6 週
MA_WEEKS = 30
SLOPE_WEEKS = 4            # 30 週線要比 4 週前高
ENTRY_WEEKS = 4            # 突破後幾週內找日線進場點
BUY_ZONE = 0.05            # 買點區上緣與隔日追價上限：壓力線 +5%
NEAR_BAND = 0.05           # 還沒突破、收盤在壓力線下 5% 內＝接近
BARS_NEEDED = 320          # 呼叫端給的日 K 根數（約 64 週，夠算 4 週前突破的 52 週壓力線）
HISTORY_DAYS = 900         # 撈 320 根時的日期下限（約 2.5 年），row_number 不必掃整段歷史


def history_start(latest):
    """撈日 K 的起日：最新交易日往前 900 天。"""
    return latest - timedelta(days=HISTORY_DAYS) if latest else date(1900, 1, 1)


def weekly_bars(bars, as_of=None):
    """日 K → 週 K（ISO 週，週一到週日）。回傳 (已收完的週, 進行中的週或 None)。
    回測每檔每天都要算一次，迴圈刻意寫得平直。"""
    weeks, key = [], None
    for b in bars:
        try:
            o, h, l, c = float(b["open"]), float(b["high"]), float(b["low"]), float(b["close"])
        except (KeyError, TypeError, ValueError):
            continue
        if not (o > 0 and h > 0 and l > 0 and c > 0):          # 也擋掉 NaN
            continue
        d = b["trade_date"]
        if not isinstance(d, date):
            d = day(d)
        vol = number(b.get("volume"), 0.0) or 0.0
        k = d.isocalendar()[:2]
        if k == key:
            w = weeks[-1]
            if h > w["high"]:
                w["high"] = h
            if l < w["low"]:
                w["low"] = l
            w["close"], w["end"] = c, d
            w["days"] += 1
            w["volume"] += vol
        else:
            key = k
            weeks.append({"key": tuple(k), "start": d, "end": d, "open": o, "high": h, "low": l,
                          "close": c, "days": 1, "volume": vol})
    if not weeks:
        return [], None
    last = weeks[-1]
    ref = day(as_of) if as_of else last["end"]
    if last["end"].weekday() == 4 or tuple(ref.isocalendar()[:2]) != last["key"]:
        return weeks, None
    return weeks[:-1], last


def _ma(closes, i, n=MA_WEEKS):
    return sum(closes[i - n + 1:i + 1]) / n if n - 1 <= i < len(closes) else None


def _trend(closes, i):
    """第 i 週的週線趨勢：週收盤在 30 週線上，且 30 週線比 4 週前高。"""
    ma, before = _ma(closes, i), _ma(closes, i - SLOPE_WEEKS)
    return ma is not None and before is not None and closes[i] > ma > before, ma, before


def _pivot(weeks, i):
    """第 i 週之前 52 週的最高週收盤，以及所在的週（同價取較近的一週，整理週數算保守）。"""
    if i < LOOKBACK_WEEKS:
        return None, None
    best = i - LOOKBACK_WEEKS
    for j in range(best, i):
        if weeks[j]["close"] >= weeks[best]["close"]:
            best = j
    return weeks[best]["close"], best


def _atr14(bars):
    rows = [b for b in bars[-15:] if number(b.get("close"))]
    if len(rows) < 15:
        return None
    trs = [max(number(c["high"]) - number(c["low"]), abs(number(c["high"]) - number(p["close"])),
               abs(number(c["low"]) - number(p["close"]))) for p, c in zip(rows[:-1], rows[1:])]
    return sum(trs) / len(trs)


def _weekly_info(weeks, closes, i, pivot, p, current=None):
    """給頁面顯示的週線數字；i＝突破週（或最近一個收完的週），p＝壓力線所在週。"""
    ma30, ma30_before = _ma(closes, i), _ma(closes, i - SLOPE_WEEKS)
    ma10 = _ma(closes, i, 10)
    between = weeks[p + 1:i] or weeks[p:p + 1]
    base_low = min(w["low"] for w in between)
    prior = weeks[max(0, i - 10):i]
    per_day = [w["volume"] / w["days"] for w in prior if w["days"]]
    week = weeks[i]
    ratio = (week["volume"] / week["days"]) / (sum(per_day) / len(per_day)) \
        if per_day and week["days"] and sum(per_day) > 0 else None
    span = weeks[max(0, i - LOOKBACK_WEEKS + 1):i + 1] + ([current] if current else [])
    return {
        "pivot": round(pivot, 4), "pivot_week": weeks[p]["start"], "base_weeks": i - p,
        "base_low": round(base_low, 4), "base_depth_pct": round((1 - base_low / pivot) * 100, 1),
        "week_start": week["start"], "week_end": week["end"], "week_close": round(week["close"], 4),
        "ma10w": round(ma10, 4) if ma10 else None, "ma30w": round(ma30, 4) if ma30 else None,
        "ma30w_slope_pct": round((ma30 / ma30_before - 1) * 100, 2) if ma30 and ma30_before else None,
        "ma10_above_ma30": bool(ma10 and ma30 and ma10 > ma30),
        "week_volume_ratio": round(ratio, 2) if ratio is not None else None,
        "high_52w": round(max(w["high"] for w in span), 4),
    }


def _notes(info, price):
    notes = []
    ratio = info.get("week_volume_ratio")
    if ratio is not None and ratio < 1.2:
        notes.append(f"突破週日均量只有前 10 週的 {ratio:.2f} 倍，量能未放大")
    elif ratio is not None and ratio >= 1.5:
        notes.append(f"突破週放量：日均量為前 10 週的 {ratio:.2f} 倍")
    if not info.get("ma10_above_ma30"):
        notes.append("10 週線仍在 30 週線下（剛從回檔回升）")
    if info["base_depth_pct"] > 35:
        notes.append(f"整理期最深回檔 {info['base_depth_pct']:.0f}%，屬深回檔後的回升，風險較高")
    if info["high_52w"] > price * 1.03:
        notes.append(f"上方 52 週最高價 {info['high_52w']:.2f}（離收盤 +{(info['high_52w'] / price - 1) * 100:.1f}%）")
    notes.append("尚待樣本外驗證")
    return notes


def _result(status, name, price, pivot, atr, blockers, notes, info, signal_date, stage):
    stop = pivot - atr if pivot and atr else None
    return {
        "key": "weekly", "label": "週線突破（實驗）",
        "score": {"priority": 80, "waiting": 70, "watch": 60}.get(status, 0), "status": status,
        "signal_state": "triggered" if status == "priority" else "waiting",
        "pattern": "weekly_breakout", "pattern_name": name, "stage": stage,
        "entry": round(price, 4), "stop": round(stop, 4) if stop else None, "target": None,
        "risk_pct": round((price - stop) / price * 100, 2) if stop and price > stop else None,
        "rr": None, "blockers": blockers, "notes": notes, "parts": {}, "pattern_prior": None,
        "signal_date": signal_date,
        "max_entry": round(pivot * (1 + BUY_ZONE), 4) if pivot else None,
        "pivot": round(pivot, 4) if pivot else None, "atr14": round(atr, 4) if atr else None,
        "extension_pct": round((price / pivot - 1) * 100, 2) if pivot else None,
        "weekly": info,
    }


def analyze(bars, as_of=None):
    """週線突破判斷；週線資料不足 53 週回 None。

    status：priority＝可進場、waiting＝已突破等日線進場點、watch＝接近壓力或本週暫時站上（待週收盤確認）、
    skip＝不成立（blockers 寫原因，給指定個股分析看）。
    """
    weeks, current = weekly_bars(bars, as_of)
    n = len(weeks)
    atr = _atr14(bars)
    price = number(bars[-1].get("close")) if bars else None
    if n < LOOKBACK_WEEKS + 1 or not atr or not price:
        return None
    closes = [w["close"] for w in weeks]
    today = day(bars[-1]["trade_date"])

    # 1) 最近 4 個收完的週裡，最近一次週線突破
    for i in range(n - 1, max(n - ENTRY_WEEKS, LOOKBACK_WEEKS) - 1, -1):
        pivot, p = _pivot(weeks, i)
        if pivot is None or closes[i] <= pivot or i - p < MIN_BASE_WEEKS or not _trend(closes, i)[0]:
            continue
        info = _weekly_info(weeks, closes, i, pivot, p, current)
        info["weeks_since_breakout"] = n - 1 - i
        notes = _notes(info, price)
        stop, zone_hi = pivot - atr, pivot * (1 + BUY_ZONE)
        closes_after = [number(b["close"]) for b in bars if day(b["trade_date"]) > weeks[i]["end"]]
        prev_high = number(bars[-2]["high"]) if len(bars) > 1 else None
        name = "週線突破 52 週收盤高"
        if any(c is not None and c < stop for c in closes_after):
            return _result("skip", name, price, pivot, atr,
                           [f"突破後收盤跌破壓力線 −1 ATR（{stop:.2f}），突破失敗"], notes, info, weeks[i]["end"], "failed")
        if pivot <= price <= zone_hi and (today == weeks[i]["end"] or (prev_high and price > prev_high)):
            why = "突破週收在買點區" if today == weeks[i]["end"] else "回到買點區且收盤高於前一日高點"
            return _result("priority", name, price, pivot, atr, [], [why] + notes, info, today, "entry")
        if price > zone_hi:
            reason = f"離壓力線 +{(price / pivot - 1) * 100:.1f}%，等拉回 {pivot:.2f}～{zone_hi:.2f} 再轉強"
        elif price < pivot:
            reason = f"跌回壓力線 {pivot:.2f} 下，等收回並轉強（{stop:.2f} 以下算突破失敗）"
        else:
            reason = "在買點區，等收盤高於前一日高點（轉強）"
        return _result("waiting", name, price, pivot, atr, [reason], notes, info, weeks[i]["end"], "pullback")

    # 2) 沒有有效突破：看是否接近壓力線，或本週盤中暫時站上
    pivot, p = _pivot(weeks, n)
    ok, ma30, _ = _trend(closes, n - 1)
    info = _weekly_info(weeks, closes, n - 1, pivot, p, current) if pivot else {}
    gap = price / pivot - 1 if pivot else None
    blockers = []
    if not ok:
        blockers.append("週線趨勢未成立：週收盤要在 30 週線上，且 30 週線比 4 週前高"
                        + (f"（30 週線 {ma30:.2f}）" if ma30 else ""))
    if pivot is not None and n - p < MIN_BASE_WEEKS:
        blockers.append(f"最高週收盤在 {n - 1 - p} 週前，還在一路創新高，沒有 {MIN_BASE_WEEKS} 週以上的整理")
    if gap is not None and gap < -NEAR_BAND:
        blockers.append(f"離週線壓力 {pivot:.2f} 還差 {-gap * 100:.1f}%")
    if pivot is None or blockers:
        return _result("skip", "週線突破", price, pivot, atr, blockers or ["週線資料不足"], [], info, today, "none")
    if gap > 0:
        reason = f"本週目前站上壓力線 {pivot:.2f}（+{gap * 100:.1f}%），要等週收盤確認"
        stage = "pending_week"
    else:
        reason = f"接近週線壓力 {pivot:.2f}，還差 {-gap * 100:.1f}%"
        stage = "near"
    return _result("watch", "接近週線壓力" if stage == "near" else "本週暫時突破", price, pivot, atr,
                   [reason], ["尚待樣本外驗證"], info, today, stage)
