"""支撐轉強（實驗）：強勢股回檔，放量黑 K 回測支撐，隔日紅 K 收復黑 K 半身；再隔一日盤中過高一檔才買。

規則照「騰輝電子-KY（6672）」個案文件的 v0.2 規格，t＝修復 K（B）、A＝t−1 的下跌 K：
  前置：20 日均成交金額 ≥ 1 億；收盤在 20 日線上，20 日線高於 5 個交易日前；
        20 日收盤漲幅 ≥ 10%；近 10 日最高／最低 − 1 ≤ 15%，收盤離 10 日最高 5% 內。
  型態：A 收黑；A 低點離 A 之前 5 日最低 ±1% 內（回測支撐）；A 量 ≥ A 之前 5 日均量 1.5 倍；
        B 收紅、B 低點高於 A 低點、B 收盤高於 A 實體中點。
交易：t+1 盤中漲到 B 最高價上一檔才買（開盤已高於觸發價用開盤價），沒漲到就作廢、不順延；
      停損 B 最低價；成交價到停損超過 6% 不買；收盤跌破 10 日線隔日開盤出場（進場時在線下的，
      要先站上再跌破才算），最長 20 個交易日。
收盤確認版（文件給只能盤後下單的人）：t+1 低點高於 B 低點、收盤高於 B 高點，t+2 開盤買。
比較用還原價；觸發價、停損換回原始價並對齊升降單位。全部是待驗證的起始參數，不代表最佳值。
今日決策中心的實驗篩選條件只用這裡的進場（隔日盤中過觸發價），出場用決策中心的模式（動能＝成交價下 8% 停損、20 日）；
文件的出場在 backtest_setups.py 回測過，長短期都虧損。
"""
from .execution import number, tick_up, to_tick

KEY = "reclaim"
LABEL = "支撐轉強（實驗）"
PATTERN_NAME = "回測支撐→黑 K 半身收復"
PARAMS = {
    "min_amt20": 100_000_000,    # 20 日均成交金額（元）
    "min_gain20": 0.10,          # 20 日收盤漲幅
    "box_days": 10,              # 整理區天數
    "max_box_range": 0.15,       # 整理區最高／最低 − 1
    "near_high": 0.05,           # 收盤離整理區最高價
    "support_days": 5,           # A 之前幾天的低點算支撐
    "support_tol": 0.01,         # A 低點離支撐 ±1%
    "test_volume": 1.5,          # A 量 ≥ 之前 5 日均量的倍數
    "max_risk": 0.06,            # 成交價到停損的上限
}
BARS_NEEDED = 25                 # 20 日線要和 5 個交易日前比
EXIT_MA = 10
HORIZON = 20
NAN = float("nan")


def series(bars):
    """K 棒 → 各欄位的 float 串列（還原價）。缺值或價格 ≤ 0 記 NaN：比較一律不成立，位置仍和 K 棒對齊。"""
    prices, sizes = ("open", "high", "low", "close", "raw_close"), ("volume", "amount")
    s = {k: [] for k in prices + sizes}
    s["date"] = []
    for b in bars:
        for k in prices:
            value = number(b.get(k), NAN)
            s[k].append(value if value > 0 else NAN)
        for k in sizes:
            value = number(b.get(k), NAN)
            s[k].append(value if value >= 0 else NAN)
        s["date"].append(b.get("trade_date"))
    return s


def raw_factor(s, t):
    """還原價／原始價；沒有原始價時當作 1（呼叫端只給原始價）。"""
    raw = s["raw_close"][t]
    return s["close"][t] / raw if raw == raw and raw > 0 else 1.0


def pattern(s, t, p=PARAMS):
    """A／B 兩根 K 的條件；成立回傳 A 的支撐與量比，不成立回 None。"""
    o, h, l, c, v = s["open"], s["high"], s["low"], s["close"], s["volume"]
    a, n = t - 1, p["support_days"]
    if a - n < 0 or not (c[t] > o[t] and c[a] < o[a] and l[t] > l[a] and c[t] > (o[a] + c[a]) / 2):
        return None
    support = min(l[a - n:a])
    if not abs(l[a] / support - 1) <= p["support_tol"]:
        return None
    before = sum(v[a - n:a]) / n
    if not (before > 0 and v[a] >= p["test_volume"] * before):
        return None
    return {"support": support, "test_volume": v[a] / before, "a_mid": (o[a] + c[a]) / 2}


def prefilter(s, t, p=PARAMS):
    """流動性、趨勢、強度、整理四項；成立回傳數值，不成立回 None。"""
    if t < BARS_NEEDED - 1:
        return None
    h, l, c = s["high"], s["low"], s["close"]
    amt20 = sum(s["amount"][t - 19:t + 1]) / 20
    if not amt20 >= p["min_amt20"]:
        return None
    ma20 = sum(c[t - 19:t + 1]) / 20
    ma20_before = sum(c[t - 24:t - 4]) / 20
    gain = c[t] / c[t - 20] - 1
    if not (c[t] > ma20 > ma20_before and gain >= p["min_gain20"]):
        return None
    k = p["box_days"]
    box_high, box_low = max(h[t - k + 1:t + 1]), min(l[t - k + 1:t + 1])
    if not (box_high / box_low - 1 <= p["max_box_range"] and c[t] >= box_high * (1 - p["near_high"])):
        return None
    return {"amt20": amt20, "ma20": ma20, "gain20": gain, "box_high": box_high, "box_low": box_low}


def setup(s, t, p=PARAMS):
    """t 收盤後是否列入觀察；成立回傳隔日的觸發價、停損（原始價）與條件數值。只用到 t 以前的資料。"""
    if t < BARS_NEEDED - 1:
        return None
    shape = pattern(s, t, p)
    if not shape:
        return None
    pre = prefilter(s, t, p)
    if not pre:
        return None
    f = raw_factor(s, t)
    high, low = to_tick(s["high"][t] / f), to_tick(s["low"][t] / f)
    trigger = tick_up(high)
    return {
        "t": t, "signal_date": s["date"][t], "a_date": s["date"][t - 1],
        "trigger": trigger, "stop": low, "risk_pct": (trigger - low) / trigger,
        "executable": (trigger - low) / trigger <= p["max_risk"], "factor": f,
        "b_high": high, "b_low": low, "close": to_tick(s["close"][t] / f),
        "a_mid": round(shape["a_mid"] / f, 2), "support": to_tick(shape["support"] / f),
        "a_low": to_tick(s["low"][t - 1] / f), "test_volume": shape["test_volume"],
        "gain20": pre["gain20"], "amt20": pre["amt20"], "ma20": round(pre["ma20"] / f, 2),
        "box_high": to_tick(pre["box_high"] / f), "box_low": to_tick(pre["box_low"] / f),
        "box_range": pre["box_high"] / pre["box_low"] - 1, "from_high": 1 - s["close"][t] / pre["box_high"],
    }


def confirmed(s, t):
    """收盤確認版：t+1 低點高於 B 低點、收盤高於 B 高點（t+1 收盤後才知道）。"""
    u = t + 1
    return u < len(s["close"]) and s["low"][u] > s["low"][t] and s["close"][u] > s["high"][t]


def decision_strategy(key, label, pattern_key, pattern_name, plan, notes, max_risk, max_gap=None):
    """今日決策中心用的策略格式：成立就是可執行，動作是下一個交易日盤中漲到觸發價才買（只限那一天）。
    決策中心用自己的出場（動能模式成交價下 8% 停損），文件的停損與風險上限只寫在說明裡。
    entry／trigger／stop 和其他策略一樣用還原價座標；trigger_raw 是觀察日原始價。"""
    f = plan["factor"]
    risk = plan["risk_pct"] * 100
    doc = f"文件停損 {plan['stop']:.2f}（觸發價下 {risk:.1f}%）" + (
        "" if plan["executable"] else f"，超過文件上限 {max_risk * 100:g}%，照文件不會買")
    return {
        "key": key, "label": label, "score": 80, "status": "priority", "signal_state": "triggered",
        "pattern": pattern_key, "pattern_name": pattern_name,
        "entry_type": "buy_stop", "trigger": round(plan["trigger"] * f, 4), "trigger_raw": plan["trigger"],
        "entry": round(plan["trigger"] * f, 4), "stop": round(plan["stop"] * f, 4), "target": None,
        "risk_pct": round(risk, 2), "rr": None, "max_gap_pct": max_gap,
        "signal_date": plan["signal_date"], "blockers": [], "notes": notes + [doc, "尚待樣本外驗證"],
        "parts": {}, "pattern_prior": None, "plan": plan,
    }


def analyze(bars, p=PARAMS):
    """最新一根 K 收盤後的判斷（今日決策中心用）；不成立回 None。"""
    s = series(bars)
    plan = setup(s, len(bars) - 1, p)
    if not plan:
        return None
    notes = [f"A 黑 K {plan['a_date']} 回測支撐 {plan['support']:.2f}（量為前 5 日均量 {plan['test_volume']:.2f} 倍），"
             f"B 收 {plan['close']:.2f} 高於 A 實體中點 {plan['a_mid']:.2f}",
             f"20 日漲 {plan['gain20'] * 100:.1f}%；近 {p['box_days']} 日 {plan['box_low']:.2f}～{plan['box_high']:.2f}"
             f"（區間 {plan['box_range'] * 100:.1f}%），收盤離高點 {plan['from_high'] * 100:.1f}%"]
    if plan["box_high"] > plan["trigger"]:
        notes.append(f"上方整理區高點 {plan['box_high']:.2f} 是近期壓力")
    return decision_strategy(KEY, LABEL, "support_reclaim", PATTERN_NAME, plan, notes, p["max_risk"])
