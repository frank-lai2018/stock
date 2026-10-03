"""整理突破實驗：以昨日以前的區間與均額判斷，沒有型態歷史先驗。"""
from .execution import number


def analyze(bars):
    if len(bars) < 41:
        return None
    if any(number(b.get(k)) is None or number(b.get(k)) <= 0
           for b in bars[-41:] for k in ("high", "low", "close", "amount")):
        return None
    current = bars[-1]
    recent, prior = bars[-16:-1], bars[-41:-16]
    price = number(current.get("close"))
    if not price or price <= 0:
        return None
    pivot = max(number(b["high"], 0) for b in bars[-21:-1])
    if not pivot or price <= pivot:
        return None
    def spread(rows):
        return (max(number(b["high"], 0) for b in rows) -
                min(number(b["low"], price) for b in rows)) / price
    tight, previous_tight = spread(recent), spread(prior)
    amounts = [number(b.get("amount")) for b in bars[-21:-1]]
    if any(a is None or a <= 0 for a in amounts):
        return None
    average = sum(amounts) / 20
    ratio = number(current.get("amount"), 0) / average
    dry = (sum(number(b.get("amount"), 0) for b in recent) / 15) / (
        sum(number(b.get("amount"), 0) for b in prior) / 25 or 1)
    trs = [max(number(b["high"]) - number(b["low"]),
               abs(number(b["high"]) - number(p["close"])),
               abs(number(b["low"]) - number(p["close"])))
           for p, b in zip(bars[-15:-1], bars[-14:])]
    atr = sum(trs) / 14
    blockers = []
    if tight > 0.10 or tight >= previous_tight * 0.85:
        blockers.append("整理振幅未縮小（需 ≤10%、小於前期 85%）")
    if dry >= 0.8:
        blockers.append("整理期成交額未縮至前期 80% 以下")
    if ratio < 1.5:
        blockers.append("突破成交額未達前 20 日均額 1.5 倍")
    if price > pivot + atr:
        blockers.append("突破後離平台超過 1 ATR")
    ready = not blockers
    return {"key": "consolidation", "label": "整理突破（實驗）", "score": 80 if ready else 60,
            "status": "priority" if ready else "watch", "signal_state": "triggered",
            "pattern": "consolidation", "pattern_name": "整理後突破 20 日高",
            "entry": price, "stop": price * 0.92, "target": None, "risk_pct": 8,
            "rr": None, "blockers": blockers, "notes": ["尚待樣本外驗證"],
            "parts": {}, "pattern_prior": None, "signal_date": current["trade_date"],
            "max_entry": pivot + atr, "pivot": pivot, "atr14": atr,
            "amount_ratio": round(ratio, 2), "contraction": round(tight, 4),
            "amount_dry": round(dry, 2)}
