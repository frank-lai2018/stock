"""強哥選股條件：依使用者提供的日線／週線圖卡歸納，並非本人公開公式。

沿用多方波段型態的頸線；逐日重播時只把當日以前的 K 棒交給偵測器，
突破後固定當時頸線，避免用後來的走勢重畫壓力。所有數值是研究參數。
"""
from . import support_recovery, swings, weekly_resistance
from .execution import day, number

KEY = "lin_bojin"
LABEL = "強哥選股條件（圖卡研究版）"
VERSION = "lin-bojin-cards-v3"
SOURCE = "依使用者提供的日線／週線圖卡歸納；數值為系統研究設定，非強哥本人官方公式"
BARS_NEEDED = 70
HISTORY_BARS = 320
TRACK_DAYS = 20
CHASE_PCT = .05
FAIL_PCT = .02
NEAR_PCT = .05
TOUCH_PCT = .03
VOLUME_MULT = 1.5
RETEST_VOLUME_MULT = 1.2
MIN_RS = 70
EVENTS = {"first": "首次頸線突破", "retest": "回測頸線再轉強", "gap": "跳空越過頸線",
          "near": "等待頸線突破", "pullback": "等待回測轉強"}
RULES = [
    {"item": "型態與趨勢", "rule": "多方底部／整理型態；RS ≥ 70、收盤站上 20 日線且 20 日線高於五個交易日前。不要求長期均線多頭排列。"},
    {"item": "週線前高壓力帶", "rule": "取本週以前已完成週 K，最多 52 週、至少 26 週；週高點由已完成左右鄰週確認。至少兩次相隔 4 週的測試聚集於 2% 價格帶，最早測試至少 6 週前，之後尚無週收盤越過上緣。突破時固定上緣與測試紀錄，不改既有週線最高收盤策略。"},
    {"item": "提前觀察", "rule": "收盤在週壓力上緣下方 0～8%，RS ≥70、站上往上的20日線可列接近壓力；多個壓力帶先取測試次數最多者，同次數取較高上緣。另有量比 ≥1.5、紅 K 上漲且最高價碰到壓力帶時列放量挑戰。僅為觀察，尚非買點、不配置股數；通知另要求最新單季EPS>0、20日均額至少2千萬。"},
    {"item": "量縮收復支撐（實驗）", "rule": "RS≥70、站上往上的20日線；近3～15日曾有上影≥振幅50%、實體≤40%的測壓棒，測壓高點不低於此前10日高點且當時20日收盤漲幅≥10%。支撐取訊號日前5日低點、須在測壓後形成；低點短暫跌破支撐≤3%、收盤站回，紅K實體≥50%、收在振幅上方30%，量≤此前20日均量0.6倍。"},
    {"item": "支撐觀察追蹤", "rule": "訊號日起至後續3個交易日固定支撐、訊號高低點與測壓紀錄；後續低點觸及訊號低點或收盤跌回支撐即失效。越過訊號高點仍須核對成交價至訊號低點的8%結構風險與交易限制；僅列觀察、不配置部位。量比未區分處置／正常撮合，不把量縮當作惜售證據。通知仍須最新單季EPS>0及20日均額≥2千萬。"},
    {"item": "首次突破", "rule": "當日收盤由頸線下穿越到頸線上；量 ≥ 前 20 日均量 1.5 倍，紅 K 實體 ≥ 全振幅 50%、收盤位於振幅上方 30%。型態偵測另要求突破量通過其前 50 日均量門檻。"},
    {"item": "回測再轉強", "rule": "突破後第 2～20 個交易日，最近三根 K 棒至少一根低點在頸線 −2%～+3%；今日紅 K 收盤高於昨日最高價、位於振幅上方 30%，量 ≥ 前 20 日均量 1.2 倍。"},
    {"item": "跳空突破", "rule": "符合首次突破，另需昨日收盤未過頸線；今日開盤與最低價均高於昨日最高價，最低價也高於頸線，缺口當日未回補。"},
    {"item": "等待與作廢", "rule": "頸線下 5% 內可列等待突破；有效突破後尚未回測轉強則等待。突破後任一收盤低於固定頸線 2%，該次突破作廢。"},
    {"item": "進場與追價", "rule": "確認訊號只供下一個交易日開盤進場；收盤與隔日含滑價成交價均不得高於頸線 5%。超過上限就放棄，不把漲停視為一定成交。"},
    {"item": "出場與加強條件", "rule": "沿用所選模式：成交價下 8% 停損；趨勢持有為收盤跌破 50 日線後隔日開盤出場，最長 60 個交易日。籌碼、營收品質、族群另選，不預設強制通過。"},
]


def _iso(value):
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


def _volume_ratio(bars, window=20):
    ref = bars[-window - 1:-1]
    avg = sum(b["volume"] for b in ref) / len(ref) if ref else 0
    return bars[-1]["volume"] / avg if avg > 0 else 0


def _confirmed(bars):
    # 快速排除沒有放量上漲的日子，再跑既有型態。避免每檔重播 20 日都跑全部偵測器。
    if bars[-1]["close"] <= bars[-2]["close"] or _volume_ratio(bars, 50) < 1.3:
        return []
    found = []
    for key in swings.BULL_KEYS:
        setup = swings.ALL[key](bars[-150:], recent=1)
        if (setup and setup.get("dir") != "bear"
                and setup.get("breakout_date") == _iso(bars[-1]["trade_date"])):
            pivot = number(setup.get("neckline"))
            if pivot and bars[-2]["close"] <= pivot < bars[-1]["close"]:
                found.append(setup)
    return found


def _near(bars):
    with swings.near_mode(NEAR_PCT):
        for key in swings.BULL_KEYS:
            setup = swings.ALL[key](bars[-150:], recent=1)
            if setup and setup.get("dir") != "bear":
                yield setup


def _evaluate(bars, setup, age, rs, factor):
    pivot = number(setup.get("neckline"))
    if not pivot or pivot <= 0:
        return None
    cur, prev = bars[-1], bars[-2]
    after = bars[-age:] if age > 0 else []
    if age > 0 and any(b["close"] < pivot * (1 - FAIL_PCT) for b in after):
        return None
    spread = cur["high"] - cur["low"]
    position = (cur["close"] - cur["low"]) / spread if spread > 0 else 0
    body = (cur["close"] - cur["open"]) / spread if spread > 0 else 0
    ratio = _volume_ratio(bars)
    ma = sum(b["close"] for b in bars[-20:]) / 20
    past_ma = sum(b["close"] for b in bars[-25:-5]) / 20
    trend = cur["close"] > ma and ma > past_ma
    extension = cur["close"] / pivot - 1
    gap = (age == 0 and cur["open"] > prev["high"]
           and cur["low"] > prev["high"] and cur["low"] > pivot)
    touch = age >= 2 and any(pivot * (1 - FAIL_PCT) <= b["low"] <= pivot * (1 + TOUCH_PCT)
                            for b in bars[max(len(bars) - age, len(bars) - 3):])
    turned = touch and cur["close"] > cur["open"] and cur["close"] > prev["high"] and cur["close"] > pivot
    event = ("gap" if gap else "first") if age == 0 else "near" if age < 0 else "retest" if turned else "pullback"
    confirmed = event in ("first", "gap", "retest")
    vol_min = RETEST_VOLUME_MULT if event == "retest" else VOLUME_MULT
    checks = [
        {"label": "RS ≥ 70", "passed": rs is not None and rs >= MIN_RS,
         "reason": f"RS {rs:g}" if rs is not None else "RS 資料不足"},
        {"label": "站上往上的 20 日線", "passed": trend, "reason": "收盤與 20 日線趨勢確認" if trend else "20 日線趨勢未確認"},
        {"label": "離頸線不超過 5%", "passed": 0 <= extension <= CHASE_PCT,
         "reason": f"距頸線 {extension * 100:+.2f}%"},
        {"label": EVENTS[event], "passed": confirmed,
         "reason": "今日已確認" if confirmed else "尚需收盤突破、放量或回測後再轉強"},
        {"label": f"確認量 ≥ 前 20 日均量 {vol_min:g} 倍", "passed": ratio >= vol_min,
         "reason": f"量比 {ratio:.2f}"},
        {"label": "紅 K 收盤位於振幅上方 30%", "passed": cur["close"] > cur["open"] and position >= .7,
         "reason": f"收盤位置 {position * 100:.1f}%"},
    ]
    if event in ("first", "gap"):
        checks.append({"label": "長紅實體 ≥ 振幅 50%", "passed": body >= .5,
                       "reason": f"紅 K 實體 {body * 100:.1f}%"})
    blockers = [f"{c['label']}：{c['reason']}" for c in checks if not c["passed"]]
    status = "priority" if confirmed and not blockers else "watch" if confirmed else "waiting"
    score = round(30 + min(ratio / 2.5, 1) * 20 + max(0, min((rs or 0) / 100, 1)) * 20
                  + position * 10 + (10 if trend else 0) + (10 if 0 <= extension <= CHASE_PCT else 0), 1)
    info = {"version": VERSION, "setup_type": event, "setup_label": EVENTS[event],
            "pattern_name": weekly_resistance.LABEL if setup["pattern"] == weekly_resistance.PATTERN else
                swings.PATTERN_NAMES.get(setup["pattern"], setup["pattern"]),
            "neckline": round(pivot / factor, 2), "breakout_date": setup.get("breakout_date") if age >= 0 else None,
            "age_days": age if age >= 0 else None, "extension_pct": round(extension * 100, 2),
            "volume_ratio": round(ratio, 2), "close_position_pct": round(position * 100, 1),
            "invalid_below": round(pivot * (1 - FAIL_PCT) / factor, 2),
            "max_entry": round(pivot * (1 + CHASE_PCT) / factor, 2),
            "gap_lower": round(prev["high"] / factor, 2) if gap else None,
            "gap_upper": round(cur["low"] / factor, 2) if gap else None,
            "checks": checks, "source": SOURCE}
    if setup.get("pressure_band"):
        info["pressure_band"] = weekly_resistance.display(setup["pressure_band"], factor)
    return {"key": KEY, "label": "強哥", "score": score, "status": status,
            "signal_state": "triggered" if confirmed else "waiting", "stage": event,
            "pattern": setup["pattern"], "pattern_name": f"{EVENTS[event]}・{info['pattern_name']}",
            "signal_date": _iso(cur["trade_date"]) if confirmed else info["breakout_date"],
            "entry": cur["close"], "stop": cur["close"] * .92, "target": None,
            "risk_pct": 8, "rr": None, "max_entry": pivot * (1 + CHASE_PCT),
            "blockers": blockers, "notes": [SOURCE, "尚未完成此組合的回測與前瞻驗證"],
            "parts": {}, "pattern_prior": None, "lin_bojin": info}


def analyze(bars, rs_rating=None):
    """輸入僅截至掃描日的還原日 K；回傳最佳買點或等待狀態，不使用外部／未來資料。"""
    bars = bars[-HISTORY_BARS:]
    if len(bars) < BARS_NEEDED:
        return None
    normalized = []
    last_date = None
    for bar in bars:
        values = {key: number(bar.get(key)) for key in ("open", "high", "low", "close", "volume")}
        stamp = _iso(bar.get("trade_date"))
        if (not bar.get("trade_date") or (last_date is not None and stamp <= last_date)
                or any(values[k] is None or values[k] <= 0 for k in ("open", "high", "low", "close"))
                or values["volume"] is None or values["volume"] < 0
                or not values["low"] <= min(values["open"], values["close"]) <= max(values["open"], values["close"]) <= values["high"]):
            return None
        normalized.append({**bar, **values})
        last_date = stamp
    if normalized[-1]["volume"] <= 0:
        return None
    raw = number(bars[-1].get("raw_close"), normalized[-1]["close"])
    if raw is None or raw <= 0:
        return None
    factor = normalized[-1]["close"] / raw
    rs = number(rs_rating)
    week_cache = {}

    def known_bands(prefix):
        key = tuple(day(prefix[-1]["trade_date"]).isocalendar()[:2])
        if key not in week_cache:
            week_cache[key] = weekly_resistance.bands(prefix)
        return week_cache[key]

    options = []
    # 當日突破、以及過去 20 個交易日的有效突破。每次都只用那一天以前的前綴。
    for age in range(min(TRACK_DAYS, min(150, len(normalized)) - BARS_NEEDED) + 1):
        prefix = normalized[:len(normalized) - age]
        if age > 0:
            last = prefix[-1]
            spread = last["high"] - last["low"]
            if (spread <= 0 or (last["close"] - last["open"]) / spread < .5
                    or (last["close"] - last["low"]) / spread < .7):
                continue  # 回測買點的起點也必須是當時已確認的放量長紅突破。
        # 原日線分支仍以掃描日150根窗口的前綴重播；新增週線分支才使用320根歷史。
        for setup in _confirmed(prefix[-(150 - age):]) + weekly_resistance.confirmed(prefix, known_bands(prefix)):
            if _volume_ratio(prefix) < VOLUME_MULT:
                continue
            result = _evaluate(normalized, setup, age, rs, factor)
            if result:
                options.append(result)
    if not options:
        for setup in _near(normalized):
            result = _evaluate(normalized, setup, -1, rs, factor)
            if result:
                options.append(result)
    observed = weekly_resistance.observation(normalized, rs, known_bands(normalized), factor)
    recovered = support_recovery.observation(normalized, rs, factor)
    if recovered and not options:
        options.append(support_recovery.waiting_strategy(recovered, KEY, VERSION, SOURCE))
    if observed and not options:
        # 純觀察者也能進決策表；signal_state=waiting 不累積成可成交回測樣本。
        band = observed["pressure_band"]
        setup = {"pattern": weekly_resistance.PATTERN, "neckline": band["high"] * factor}
        options.append(_evaluate(normalized, setup, -1, rs, factor))
    rank = {"priority": 3, "waiting": 2, "watch": 1}
    best = max(options, key=lambda s: (rank[s["status"]], s["score"],
                                      -(s["lin_bojin"]["age_days"] or 0)), default=None)
    if best:
        observations = [x for x in (observed, recovered) if x]
        if observations:
            observations.sort(key=lambda x: observation_rank(x), reverse=True)
            best["lin_bojin"]["observation"] = observations[0]
            best["lin_bojin"]["observations"] = observations
    return best


def observation_rank(observed):
    return {"challenge": 2, support_recovery.PATTERN: 1, "near": 0}.get(observed.get("type"), 0)


def observation_items(response):
    items = [x for x in response.get("items", [])
             if x.get("state") != "ready" and (x.get("lin_bojin") or {}).get("observation", {}).get("eligible")
             and (number(x.get("eps")) or 0) > 0 and (number(x.get("amt20")) or 0) >= 20_000_000]
    return sorted(items, key=lambda x: (observation_rank(x["lin_bojin"]["observation"]),
                                       number(x.get("rs_rating"), 0),
                                       x["lin_bojin"]["observation"]["extension_pct"]), reverse=True)


def attach(response, scan):
    """說明研究來源與固定門檻，不借用其他策略的校準率。"""
    codes = (scan.get("combination_filter") or {}).get("codes", [])
    if response.get("gate") != KEY and KEY not in codes:
        return response
    response["lin_bojin_config"] = {"label": LABEL, "version": VERSION, "source": SOURCE, "rules": RULES}
    observations = observation_items(response)
    response["lin_bojin_observation_count"] = len(observations)
    for item in response.get("items", []):
        if item.get("state") == "ready" or not (item.get("lin_bojin") or {}).get("observation", {}).get("eligible"):
            continue
        # 引擎只會配置 ready；也清除未配置者的試算股數，避免畫面誤顯觀察股的建議部位。
        plan = item.get("position_plan")
        if plan:
            plan.update(valid=False, suggested_shares=0, position_value=0, risk_amount=0,
                        capital_pct=0, actual_risk_pct=0, estimated_entry_cost=0,
                        reason="提前觀察，尚未確認買點，不配置部位")
    response["calibration"] = []
    response["validation"] = {"status": "unvalidated", "message": "強哥圖卡研究版尚未完成回測與前瞻驗證；其他策略的歷史績效不適用。"}
    for item in response.get("items", []):
        item["calibration"] = item["historical_reference"] = None
        for strategy in item.get("strategies", []):
            strategy["score_calibration"] = strategy["pattern_prior"] = None
    return response
