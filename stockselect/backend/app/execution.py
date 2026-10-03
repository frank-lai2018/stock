"""共用成交模型：盤後訊號、次交易日開盤、跳空／鎖停、固定價格座標。

K 棒的 open/high/low/close 是還原價，raw_* 是原始價。每次重播用觀察日
最新還原因子把整段還原價轉回觀察日座標；除權息重灌不會改變已保存計畫。
報酬採含公司行動的還原價（股利再投資口徑），不是券商現金股利帳。
"""
from datetime import date
from math import isfinite

EXECUTION_VERSION = "next-open-v1"
SLIPPAGE = 0.001                 # 每邊 0.1%，另扣現有 0.6% 來回成本


def number(value, default=None):
    try:
        value = float(value)
        return value if isfinite(value) else default
    except (TypeError, ValueError):
        return default


def day(value):
    return value if isinstance(value, date) else date.fromisoformat(str(value)[:10])


def factor(bar):
    raw = number(bar.get("raw_close"))
    adj = number(bar.get("close"))
    return adj / raw if raw and adj and raw > 0 and adj > 0 else None


def locked(bar, previous=None):
    """一般股票一價鎖停的保守近似；不聲稱僅靠日 K 能重建委託佇列。"""
    values = [number(bar.get("raw_" + k)) for k in ("open", "high", "low", "close")]
    adjusted = [number(bar.get(k)) for k in ("open", "high", "low", "close")]
    if any(v is None or v <= 0 for v in values + adjusted):
        return "missing"
    if number(bar.get("volume"), 0) <= 0 or number(bar.get("amount"), 0) <= 0:
        return "missing"
    if max(values) - min(values) > 0.0001 or not previous:
        return None
    current_factor = factor(bar)
    reference = number(previous.get("close")) / current_factor if current_factor else None
    if not reference or reference <= 0:
        return "missing"
    change = values[0] / reference - 1
    return "up" if change >= 0.095 else "down" if change <= -0.095 else None


def simulate(bars, signal, calendar=None, as_of=None):
    """重播單筆交易；尚無下一交易日或未到出場時回 pending。

    signal.entry/stop/target/max_entry 都在觀察日原始價座標；actual_entry 是
    換算回此座標的實際成交，actual_entry_raw 才是成交日原始價。
    calendar 使用市場交易日，停牌不會延長到期日；到期但鎖跌停會延後出場。
    """
    observed = day(signal["observed_date"])
    cutoff = day(as_of) if as_of else None
    bars = sorted((b for b in bars if not cutoff or day(b["trade_date"]) <= cutoff),
                  key=lambda b: day(b["trade_date"]))
    by_date = {day(b["trade_date"]): b for b in bars}
    calendar = sorted({day(d) for d in (calendar or by_date) if day(d) > observed
                       and (not cutoff or day(d) <= cutoff)})
    base = {"status": "pending", "execution_state": "waiting", "reason": None,
            "entry_date": None, "actual_entry": None, "actual_entry_raw": None,
            "actual_stop": None, "exit_price": None, "exit_price_raw": None,
            "exit_date": None, "days": 0, "net_return": None, "outcome_r": None,
            "current_r": None, "mark_return": None, "execution_version": EXECUTION_VERSION}
    if not calendar:
        return base
    anchor = factor(by_date.get(observed, {}))
    if not anchor:
        return {**base, "reason": "觀察日缺原始／還原價格，無法固定價格座標"}
    entry_day = calendar[0]
    first = by_date.get(entry_day)
    if not first or locked(first, by_date.get(observed)) in ("up", "down", "missing"):
        return {**base, "status": "skipped", "execution_state": "skipped",
                "reason": "次交易日缺可成交資料或一價鎖停", "exit_date": entry_day}
    slip = max(0.0, number(signal.get("slippage"), SLIPPAGE))
    cost = max(0.0, number(signal.get("cost_pct"), 0.006))
    entry = number(first.get("open")) / anchor * (1 + slip)
    raw_entry = number(first.get("raw_open")) * (1 + slip)
    cap = number(signal.get("max_entry"))
    if cap is not None and entry > cap:
        return {**base, "status": "skipped", "execution_state": "skipped",
                "reason": "隔日成交價超過追價上限", "exit_date": entry_day}
    pct = number(signal.get("stop_pct"))
    stop = entry * (1 - pct) if pct is not None else number(signal.get("stop"))
    target = number(signal.get("target"))
    if not stop or not 0 < stop < entry or (target is not None and target <= entry):
        return {**base, "status": "skipped", "execution_state": "skipped",
                "reason": "隔日開盤後停損／目標已無有效風險空間", "exit_date": entry_day}
    risk = (entry - stop) / entry
    result = {**base, "execution_state": "open", "entry_date": entry_day,
              "actual_entry": entry, "actual_entry_raw": raw_entry, "actual_stop": stop}
    horizon = max(1, int(signal.get("horizon") or 20))
    ma_days = max(0, int(signal.get("exit_ma") or 0))
    ma_by_date = {}
    if ma_days:
        window = []
        for b in bars:
            close = number(b.get("close"))
            if close is not None:
                window.append(close)
                if len(window) >= ma_days:
                    ma_by_date[day(b["trade_date"])] = sum(window[-ma_days:]) / ma_days

    due = None
    previous = by_date.get(observed)
    for i, d in enumerate(calendar, 1):
        bar = by_date.get(d)
        if not bar:
            if i >= horizon:
                due = "timeout"
            continue
        state = locked(bar, previous)
        previous = bar
        if state == "missing":
            if i >= horizon:
                due = "timeout"
            continue
        o, h, l, c = (number(bar[k]) / anchor for k in ("open", "high", "low", "close"))
        result.update(days=i, mark_return=c / entry - 1 - cost,
                      current_r=(c / entry - 1 - cost) / risk)
        price, outcome, timing = None, None, None
        if state != "down":
            if o <= stop:
                price, outcome, timing = o, "loss", "open"
            elif due:
                price, outcome, timing = o, due, "open"
            elif l <= stop:
                price, outcome, timing = stop, "loss", "intraday"     # 同根同碰先算停損
            elif target is not None and h >= target and state != "up":
                price, outcome, timing = max(o, target), "win", "open" if o >= target else "intraday"
            elif i >= horizon:
                price, outcome, timing = c, "timeout", "close"
        if price is not None:
            price *= 1 - slip
            raw_exit = price * anchor / factor(bar)
            net = price / entry - 1 - cost
            return {**result, "status": outcome, "execution_state": "closed",
                    "exit_date": d, "exit_price": price, "exit_price_raw": raw_exit,
                    "exit_timing": timing,
                    "net_return": net, "outcome_r": net / risk}
        if l <= stop:
            due = "loss"                         # 鎖跌停不能假設停損成交
        elif i >= horizon:
            due = "timeout"
        elif ma_days and d in ma_by_date and number(bar["close"]) < ma_by_date[d]:
            due = "trend"                        # 收盤確認，下個交易日開盤出場
    return result
