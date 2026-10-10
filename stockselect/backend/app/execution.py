"""共用成交模型：盤後訊號、次交易日開盤（或盤中突破觸發）、跳空／鎖停、固定價格座標。

K 棒的 open/high/low/close 是還原價，raw_* 是原始價。每次重播用觀察日
最新還原因子把整段還原價轉回觀察日座標；除權息重灌不會改變已保存計畫。
報酬採含公司行動的還原價（股利再投資口徑），不是券商現金股利帳。
"""
from datetime import date
from math import isfinite

EXECUTION_VERSION = "next-open-v1"
BUY_STOP_VERSION = "buy-stop-v1"
SLIPPAGE = 0.001                 # 每邊 0.1%，另扣現有 0.6% 來回成本


def tick_size(price):
    """股票升降單位（證交所營業細則第 62 條，上櫃股票相同）。"""
    p = float(price)
    return 0.01 if p < 10 else 0.05 if p < 50 else 0.1 if p < 100 else 0.5 if p < 500 else 1.0 if p < 1000 else 5.0


def to_tick(price):
    """對齊最近的合法價位；還原價換回原始價時用來去掉計算誤差。"""
    step = tick_size(price)
    return round(round(float(price) / step) * step, 2)


def tick_up(price):
    """往上一檔，例如 309.5 → 310、99.9 → 100。"""
    p = to_tick(price)
    return round(p + tick_size(p), 2)


def tick_down(price):
    """往下一檔，例如 298.5 → 298、100 → 99.9。"""
    p = to_tick(price)
    return round(p - tick_size(p - 1e-9), 2)


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

    signal.entry/stop/target/max_entry/trigger 都在觀察日原始價座標；actual_entry 是
    換算回此座標的實際成交，actual_entry_raw 才是成交日原始價。
    calendar 使用市場交易日，停牌不會延長到期日；到期但鎖跌停會延後出場。
    entry_type="buy_stop"：次交易日最高價到 trigger 才買，開盤已高於 trigger 用開盤價，
    沒到就作廢不順延；max_gap_pct＝開盤高於 trigger 超過此比例就放棄。
    max_risk_pct 以實際成交價（含滑價）到停損的距離檢查，超過就不買。
    盤中買進當天又碰到停損，日 K 分不出先後，預設保守當作先買進、再以停損價出場；
    intraday_order="nearest"（研究用）改成開盤離最低價較近就當作先跌到最低、再漲過觸發價。
    exit_ma_cross＝均線出場要「跌破」：觀察日或之後先收在均線上，之後收盤跌到線下才出場。
    """
    observed = day(signal["observed_date"])
    cutoff = day(as_of) if as_of else None
    bars = sorted((b for b in bars if not cutoff or day(b["trade_date"]) <= cutoff),
                  key=lambda b: day(b["trade_date"]))
    by_date = {day(b["trade_date"]): b for b in bars}
    calendar = sorted({day(d) for d in (calendar or by_date) if day(d) > observed
                       and (not cutoff or day(d) <= cutoff)})
    buy_stop = signal.get("entry_type") == "buy_stop"
    base = {"status": "pending", "execution_state": "waiting", "reason": None,
            "entry_date": None, "actual_entry": None, "actual_entry_raw": None,
            "actual_stop": None, "exit_price": None, "exit_price_raw": None,
            "exit_date": None, "days": 0, "net_return": None, "outcome_r": None,
            "current_r": None, "mark_return": None,
            "execution_version": BUY_STOP_VERSION if buy_stop else EXECUTION_VERSION}
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
    if buy_stop:
        trigger = number(signal.get("trigger"))
        opened = number(first.get("open")) / anchor
        if not trigger or number(first.get("high")) / anchor < trigger * (1 - 1e-6):
            return {**base, "status": "skipped", "execution_state": "skipped",
                    "reason": "次交易日沒有漲到觸發價，掛單作廢", "exit_date": entry_day}
        gap = number(signal.get("max_gap_pct"))
        if gap is not None and opened > trigger * (1 + gap):
            return {**base, "status": "skipped", "execution_state": "skipped",
                    "reason": f"開盤高於觸發價超過 {gap * 100:g}%，放棄", "exit_date": entry_day}
        entry = max(opened, trigger) * (1 + slip)
        raw_entry = entry * anchor / factor(first)
        late_fill = opened < trigger                     # 開盤後才漲到觸發價
    else:
        entry = number(first.get("open")) / anchor * (1 + slip)
        raw_entry = number(first.get("raw_open")) * (1 + slip)
        late_fill = False
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
    max_risk = number(signal.get("max_risk_pct"))
    if max_risk is not None and risk > max_risk:
        return {**base, "status": "skipped", "execution_state": "skipped",
                "reason": f"成交價到停損 {risk * 100:.1f}%，超過上限 {max_risk * 100:g}%", "exit_date": entry_day}
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
    observed_ma = ma_by_date.get(observed)
    armed = not signal.get("exit_ma_cross") or (
        observed_ma is not None and number(by_date[observed].get("close"), 0) >= observed_ma)
    nearest = signal.get("intraday_order") == "nearest"

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
        hit = l <= stop
        if hit and i == 1 and late_fill and nearest and o - l < h - o and c > stop:
            hit = False                          # 視為最低價在買進之前，買進後沒跌回停損
        if state != "down":
            if o <= stop and not (buy_stop and i == 1):    # 盤中才買進，開盤時還沒有部位
                price, outcome, timing = o, "loss", "open"
            elif due:
                price, outcome, timing = o, due, "open"
            elif hit:
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
        if hit:
            due = "loss"                         # 鎖跌停不能假設停損成交
        elif i >= horizon:
            due = "timeout"
        elif ma_days and d in ma_by_date:
            if number(bar["close"]) < ma_by_date[d]:
                if armed:
                    due = "trend"                # 收盤確認，下個交易日開盤出場
            else:
                armed = True
    return result
