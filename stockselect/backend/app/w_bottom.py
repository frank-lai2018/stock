"""W 底測右底（實驗）：先有強漲、回檔 8～15%，縮量回測左底附近（右底）；隔日盤中過高一檔才買。

規則照「華新科（2492）」個案文件，t＝掃描日（右底），區間都含兩端：
  20 日均量 ≥ 3,000 張；近 20 日至少一天收盤漲 ≥ 8%；收盤自近 20 日最高收盤回檔 8～15%；
  左底 j＝t−12～t−5 的最低低點；t 低點比左底高 0～3%；
  頸線＝j+1～t−1 的最高價，至少比左底高 6%；收盤仍在頸線下，且當日量 ≤ 20 日均量 0.8 倍（量縮）。
交易（版本 1）：t+1 盤中漲到 t 最高價上一檔才買；開盤高於觸發價 2% 以上放棄；停損 t 最低價下一檔；
      成交價到停損超過 5% 不買。文件沒定出場，比照支撐轉強：收盤跌破 10 日線隔日開盤出場，最長 20 個交易日；
      右底在 10 日線下，所以要先站上 10 日線、之後再跌破才出場。
版本 2（收盤確認）：t+1 收盤站上頸線、且量 ≥ t 的 20 日均量 1.5 倍，t+2 開盤買。
比較用還原價；觸發價、停損換回原始價並對齊升降單位。全部是待驗證的起始參數，不代表最佳值。
今日決策中心的實驗篩選條件只用版本 1 的進場（含開盤 2% 上限），出場用決策中心的模式（動能＝成交價下 8% 停損、20 日）。
"""
from .execution import tick_down, tick_up, to_tick
from .support_reclaim import EXIT_MA, HORIZON, decision_strategy, raw_factor, series  # noqa: F401  （series 給呼叫端用）

KEY = "w_bottom"
LABEL = "W 底測右底（實驗）"
PATTERN_NAME = "縮量測右底"
PARAMS = {
    "min_vol20": 3_000_000,      # 20 日均量（股）＝3,000 張
    "strong_day": 0.08,          # 近 20 日至少一天收盤漲幅
    "pull_min": 0.08,            # 自 20 日最高收盤回檔
    "pull_max": 0.15,
    "left_from": 12,             # 左底在 t−12～t−5
    "left_to": 5,
    "right_min": 0.0,            # 右底低點比左底高 0～3%
    "right_max": 0.03,
    "min_rebound": 0.06,         # 頸線比左底至少高
    "dry_volume": 0.8,           # 當日量 ≤ 20 日均量的倍數
    "max_risk": 0.05,
    "max_gap": 0.02,             # 開盤高於觸發價超過就放棄
    "confirm_volume": 1.5,       # 版本 2：突破頸線當天量 ≥ t 的 20 日均量倍數
}
BARS_NEEDED = 21


def setup(s, t, p=PARAMS):
    """t 收盤後是否列入觀察；成立回傳隔日的觸發價、停損（原始價）、頸線與條件數值。只用到 t 以前的資料。"""
    if t < BARS_NEEDED - 1 or t - p["left_from"] < 0:
        return None
    h, l, c, v = s["high"], s["low"], s["close"], s["volume"]
    vol20 = sum(v[t - 19:t + 1]) / 20
    if not (vol20 >= p["min_vol20"] and v[t] <= p["dry_volume"] * vol20):
        return None
    peak = max(c[t - 19:t + 1])
    pull = 1 - c[t] / peak
    if not p["pull_min"] <= pull <= p["pull_max"]:
        return None
    j = min(range(t - p["left_from"], t - p["left_to"] + 1), key=lambda i: l[i])
    right = l[t] / l[j] - 1
    if not p["right_min"] <= right <= p["right_max"]:
        return None
    neck = max(h[j + 1:t])
    if not (neck / l[j] - 1 >= p["min_rebound"] and c[t] < neck):
        return None
    strong = [i for i in range(t - 19, t + 1) if c[i] / c[i - 1] - 1 >= p["strong_day"]]
    if not strong:
        return None
    f = raw_factor(s, t)
    high, low = to_tick(h[t] / f), to_tick(l[t] / f)
    trigger, stop = tick_up(high), tick_down(low)
    return {
        "t": t, "signal_date": s["date"][t], "left_date": s["date"][j], "left_low": to_tick(l[j] / f),
        "trigger": trigger, "stop": stop, "risk_pct": (trigger - stop) / trigger,
        "executable": (trigger - stop) / trigger <= p["max_risk"], "factor": f,
        "high": high, "low": low, "close": to_tick(c[t] / f),
        "neckline": to_tick(neck / f), "neckline_adj": neck, "rebound": neck / l[j] - 1,
        "right_vs_left": right, "pullback": pull, "peak_close": to_tick(peak / f),
        "strong_date": s["date"][strong[-1]], "strong_gain": c[strong[-1]] / c[strong[-1] - 1] - 1,
        "vol20": vol20, "volume_ratio": v[t] / vol20,
    }


def confirmed(s, t, plan, p=PARAMS):
    """版本 2：t+1 收盤站上頸線且量 ≥ t 的 20 日均量 1.5 倍（t+1 收盤後才知道）。"""
    u = t + 1
    return (u < len(s["close"]) and s["close"][u] > plan["neckline_adj"]
            and s["volume"][u] >= p["confirm_volume"] * plan["vol20"])


def analyze(bars, p=PARAMS):
    """最新一根 K 收盤後的判斷（今日決策中心用，版本 1）；不成立回 None。"""
    s = series(bars)
    plan = setup(s, len(bars) - 1, p)
    if not plan:
        return None
    notes = [f"左底 {plan['left_date']} {plan['left_low']:.2f}，今天低點 {plan['low']:.2f}"
             f"（高 {plan['right_vs_left'] * 100:.1f}%）；頸線 {plan['neckline']:.2f}（反彈 {plan['rebound'] * 100:.1f}%）",
             f"自 20 日最高收盤 {plan['peak_close']:.2f} 回檔 {plan['pullback'] * 100:.1f}%；"
             f"{plan['strong_date']} 曾漲 {plan['strong_gain'] * 100:.1f}%；量為 20 日均量 {plan['volume_ratio']:.2f} 倍",
             f"頸線 {plan['neckline']:.2f} 是上方壓力；收盤站上頸線且放量才算 W 底完成"]
    return decision_strategy(KEY, LABEL, "w_bottom_retest", PATTERN_NAME, plan, notes, p["max_risk"], p["max_gap"])
