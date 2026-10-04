"""今日決策中心的「進場條件」：等待確認的訊號要發生什麼事才會變成可執行，寫成可以照著看盤的文字。

只做說明，不影響選股、入選與回測（所以不在研究指紋裡）。規則照 price_action.evaluate_signal／_decision、
decision_center.scan_market 的 lookback／expiry，以及 weekly_breakout.analyze；那幾支的門檻改了，這裡要跟著改。
"""
from datetime import timedelta

from . import db
from .execution import day, number

PA_PRIORITY_SCORE = 75     # 裸 K 觸發後 ≥ 75 分、沒有紅字才是 priority
PA_CONFIRM_BONUS = 7       # 確認分：等待 8 分 → 觸發 15 分
PA_CHASE_PCT = 3           # 觸發當天收盤比進場價高超過 3% 會被擋（避免追價）


def _mmdd(value):
    d = day(value)
    return f"{d.month}/{d.day}"


def _price_action(s, age, lookback, expiry):
    """裸 K：訊號K高點被突破才觸發；觸發前碰到訊號K低點就作廢。
    決策中心只分析最近 lookback 根形成的訊號，所以最晚能被看到的觸發日是 age＝min(lookback−1, expiry+1)。"""
    trigger, stop, score = number(s.get("entry")), number(s.get("stop")), number(s.get("score"), 0)
    if trigger is None or stop is None or not s.get("signal_date"):
        return None
    last_age = min(lookback - 1, expiry + 1)
    left = max(0, last_age - age) if age is not None else None
    after = round(score + PA_CONFIRM_BONUS, 1)
    lines = [f"盤中突破 {trigger:.2f}（{_mmdd(s['signal_date'])} {s.get('pattern_name') or '訊號K'}的高點）就觸發，"
             f"觸發當天收盤不能比進場價高超過 {PA_CHASE_PCT}%",
             f"觸發前跌破 {stop:.2f}（訊號K低點）就作廢"]
    if left is None:
        deadline = None
    elif left > 0:
        deadline = f"剩 {left} 個交易日"
        lines.append(f"還有 {left} 個交易日可以觸發（從下一個交易日算）")
    else:
        deadline = "已到期限"
        lines.append("已到追蹤期限，下一個交易日起不再列入")
    lines.append(f"觸發後分數約 {after}" + ("" if after >= PA_PRIORITY_SCORE
                                         else f"，未達 {PA_PRIORITY_SCORE}，觸發也只會列「觀察」"))
    return {"strategy": "price_action", "label": s.get("label"), "trigger_low": trigger, "trigger_high": None,
            "invalid_below": stop, "deadline": deadline, "score_after": after, "lines": lines}


def _weekly(s):
    """週線突破：等待＝已突破、等日線進場點；觀察＝接近壓力或本週暫時站上。"""
    info = s.get("weekly") or {}
    pivot, high, stop = number(s.get("pivot")), number(s.get("max_entry")), number(s.get("stop"))
    if pivot is None or high is None:
        return None
    if s.get("status") == "waiting":
        start = day(info["week_start"])
        # 突破週後第 4 週收完就過期；最後一次能出訊號的是那週週四（週五收盤時那週已收完）
        last = start - timedelta(days=start.weekday()) + timedelta(days=28 + 3)
        lines = [f"收盤回到 {pivot:.2f}～{high:.2f}，而且收盤高於前一天最高價，就可執行；"
                 f"隔天開盤買，開盤高於 {high:.2f} 不買",
                 f"收盤跌破 {stop:.2f}（壓力線 − 1 ATR）就作廢",
                 f"最晚 {_mmdd(last)} 前要出現（突破週後第 4 週）"]
        if s.get("blockers"):
            lines.append("目前：" + s["blockers"][0])
        deadline = f"{last.isoformat()} 前"
    elif s.get("stage") == "pending_week":
        lines = [f"本週目前站上 {pivot:.2f}，要等週五收盤確認",
                 f"確認後收盤在 {pivot:.2f}～{high:.2f} 內就可執行；高於 {high:.2f} 就等拉回"]
        deadline = "本週收盤"
    else:
        lines = [f"週收盤站上 {pivot:.2f} 才算突破（" + (s.get("blockers") or ["接近壓力"])[0] + "）",
                 f"突破後再等收盤回到 {pivot:.2f}～{high:.2f} 並轉強"]
        deadline = None
    return {"strategy": "weekly", "label": s.get("label"), "trigger_low": pivot, "trigger_high": high,
            "invalid_below": stop if s.get("status") == "waiting" else None,
            "deadline": deadline, "score_after": None, "lines": lines}


def _wanted(s):
    return s.get("status") == "waiting" or (s.get("key") == "weekly" and s.get("status") == "watch")


def attach(response, lookback=5, expiry=5, calendar=None):
    """把每檔「等待確認」的策略（另含週線突破的接近壓力）的進場條件放進 item["entry_conditions"]。
    calendar：市場交易日（測試用）；沒給就讀加權指數的交易日算訊號已經過了幾天。"""
    items = response.get("items") or []
    signals = [day(s["signal_date"]) for item in items for s in item.get("strategies", [])
               if s.get("key") == "price_action" and _wanted(s) and s.get("signal_date")]
    as_of = day(response["as_of"]) if response.get("as_of") else None
    if signals and calendar is None and as_of:
        calendar = [r["trade_date"] for r in db.query(
            "SELECT trade_date FROM market_index WHERE index_id='TWSE' AND trade_date>%(s)s AND trade_date<=%(e)s",
            {"s": min(signals), "e": as_of})]
    days = sorted(day(d) for d in (calendar or []))
    gate = response.get("gate")            # 原始規則是 None：沒有篩選條件
    for item in items:
        conditions = []
        for s in item.get("strategies", []):
            if not _wanted(s):
                continue
            if s.get("key") == "price_action":
                signal = day(s["signal_date"]) if s.get("signal_date") else None
                age = sum(1 for d in days if signal < d <= as_of) if signal and as_of and days else None
                cond = _price_action(s, age, lookback, expiry)
                # 篩選條件另有要求時一併寫出，免得觸發後還以為會入選（同 decision_center._gate_ok）
                if cond and gate == "trend_template" and not item.get("trend_template"):
                    cond["lines"].append("另需趨勢模板成立（目前未成立），否則觸發了也不會入選")
                elif cond and gate == "breakout":
                    cond["lines"].append("篩選條件是型態突破：裸 K 訊號觸發了也不會入選")
            elif s.get("key") == "weekly":
                cond = _weekly(s)
            else:
                cond = None
            if cond:
                conditions.append(cond)
        item["entry_conditions"] = conditions
    return response
