"""測壓後量縮收復支撐：只產生提前觀察，尚不授權成交或配置部位。"""
from .execution import day

PATTERN = "support_recovery"
LABEL = "量縮收復支撐（實驗）"
VERSION = "support-recovery-v1"
SUPPORT_DAYS = 5
TEST_MIN_AGE = 3
TEST_MAX_AGE = 15
MIN_GAIN20 = .10
MAX_VOLUME_RATIO = .60
MAX_UNDERCUT = .03
FOLLOW_DAYS = 3
RISK_PCT = .08
VOLUME_NOTE = "量比尚未區分處置與正常撮合；量縮原因、交易限制待核對"


def _trend(bars):
    return (len(bars) >= 25 and bars[-1]["close"] >
            sum(b["close"] for b in bars[-20:]) / 20 >
            sum(b["close"] for b in bars[-25:-5]) / 20)


def _seed(bars):
    """支撐排除訊號日，測壓棒只用當時已存在的前綴。輸入沿用強哥驗證後的還原 K。"""
    if len(bars) < 40 or not _trend(bars):
        return None
    cur = bars[-1]
    support_bar = min(bars[-SUPPORT_DAYS - 1:-1], key=lambda b: b["low"])
    support = support_bar["low"]
    spread = cur["high"] - cur["low"]
    avg_volume = sum(b["volume"] for b in bars[-21:-1]) / 20
    if not spread > 0 or not avg_volume > 0:
        return None
    ratio = cur["volume"] / avg_volume
    position = (cur["close"] - cur["low"]) / spread
    body = (cur["close"] - cur["open"]) / spread
    if not (support * (1 - MAX_UNDERCUT) <= cur["low"] < support < cur["close"]
            and body >= .5 and position >= .7 and ratio <= MAX_VOLUME_RATIO):
        return None
    for age in range(TEST_MIN_AGE, TEST_MAX_AGE + 1):
        i = len(bars) - 1 - age
        test = bars[i]
        width = test["high"] - test["low"]
        upper = (test["high"] - max(test["open"], test["close"])) / width if width > 0 else 0
        if (test["trade_date"] < support_bar["trade_date"] and upper >= .5
                and abs(test["close"] - test["open"]) / width <= .4
                and test["high"] >= max(b["high"] for b in bars[i - 10:i])
                and test["close"] / bars[i - 20]["close"] - 1 >= MIN_GAIN20
                and cur["close"] < test["high"]):
            return {"support": support, "support_date": str(day(support_bar["trade_date"])),
                    "test_date": str(day(test["trade_date"])), "test_high": test["high"],
                    "test_upper_pct": round(upper * 100, 1), "signal_high": cur["high"],
                    "signal_low": cur["low"], "signal_date": str(day(cur["trade_date"])),
                    "volume_ratio": round(ratio, 2), "close_position_pct": round(position * 100, 1),
                    "undercut_pct": round((1 - cur["low"] / support) * 100, 2)}
    return None


def observation(bars, rs, factor=1):
    if rs is None or rs < 70 or not _trend(bars):
        return None
    for age in range(FOLLOW_DAYS + 1):
        prefix = bars[:len(bars) - age]
        seed = _seed(prefix)
        if not seed:
            continue
        after = bars[-age:] if age else []
        if any(b["low"] <= seed["signal_low"] or b["close"] <= seed["support"] for b in after):
            continue
        result = dict(seed)
        for key in ("support", "test_high", "signal_high", "signal_low"):
            result[key] = round(seed[key] / factor, 2)
        # 這是結構風險的研究參考上限，不能當成可執行的委託價。
        cap = seed["signal_low"] / (1 - RISK_PCT)
        turned = bool(age and bars[-1]["close"] > seed["signal_high"])
        beyond = bars[-1]["close"] > cap
        confirmation = ("已越過訊號高點，但超過8%結構風險參考上限，暫不追價" if turned and beyond else
                        "已越過訊號高點，仍須重新確認成交價、結構風險與交易限制" if turned else
                        "等待後續越過訊號高點；須重新確認成交價、結構風險與交易限制")
        result.update(eligible=True, type=PATTERN, label=LABEL, version=VERSION, age_days=age,
                      turned=turned, risk_reference_max=round(cap / factor, 2),
                      exceeds_risk_reference=beyond, confirmation=confirmation,
                      extension_pct=round((bars[-1]["close"] / seed["test_high"] - 1) * 100, 2),
                      volume_note=VOLUME_NOTE,
                      reason="量縮收復支撐僅列提前觀察；尚未確認買點、不配置部位")
        return result
    return None


def waiting_strategy(observed, key, version, source):
    """不填進場／停損／頸線，避免共用配置流程把研究參考誤當可成交計畫。"""
    checks = [{"label": "RS ≥70、站上往上的20日線", "passed": True, "reason": "趨勢條件通過"},
              {"label": "強漲後上影測壓", "passed": True, "reason": observed["test_date"]},
              {"label": "量縮收復前五日支撐", "passed": True,
               "reason": f"支撐 {observed['support']:g}；訊號量比 {observed['volume_ratio']:g}"},
              {"label": "買點與交易限制確認", "passed": False, "reason": observed["confirmation"]}]
    info = {"version": version, "setup_type": PATTERN, "setup_label": LABEL,
            "pattern_name": LABEL, "neckline": None, "breakout_date": None,
            "age_days": None, "extension_pct": None, "volume_ratio": observed["volume_ratio"],
            "close_position_pct": observed["close_position_pct"], "invalid_below": None,
            "max_entry": None, "checks": checks, "source": source}
    return {"key": key, "label": "強哥", "score": 60, "status": "waiting", "signal_state": "waiting",
            "stage": PATTERN, "pattern": PATTERN, "pattern_name": LABEL, "signal_date": None,
            "entry": None, "stop": None, "target": None, "risk_pct": None, "rr": None,
            "max_entry": None, "blockers": [observed["reason"], observed["confirmation"]],
            "notes": [source, VOLUME_NOTE, "尚未完成此分支的回測與前瞻驗證"],
            "parts": {}, "pattern_prior": None, "lin_bojin": info}
