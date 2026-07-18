"""價量分析 VPA（Volume Price Analysis）——判讀主力承接/測試/出貨。

參考 Anna Coulling《不說謊的價量》（VPA，源自 Wyckoff / VSA）。
核心：用「量」驗證「價」；量價背離＝主力在動手腳。
bars：由舊到新的 list[dict]，每個含 open/high/low/close/volume（用還原價 + 原始量）。
detect(bars) 用前文計算均量/均振幅，回傳「最後一根」符合的 VPA 訊號 key 清單。
"""

# 訊號目錄：key -> (中文名, 方向 bull/bear/neutral, 階段 承接/測試/出貨)
CATALOG = {
    "stopping_vol":  ("停損量(賣壓宣洩)",   "bull",    "承接"),
    "accumulation":  ("承接量(高量收高)",   "bull",    "承接"),
    "no_supply":     ("測試無賣壓",         "bull",    "測試"),
    "no_demand":     ("無買氣(虛漲)",       "bear",    "測試"),
    "churning":      ("量價背離(換手)",     "neutral", "出貨"),
    "buying_climax": ("買盤高潮(出貨)",     "bear",    "出貨"),
}

LOOK = 20   # 均量/均振幅回看根數


def _spread(b):
    return float(b["high"]) - float(b["low"])


def detect(bars):
    """回傳最後一根 K 棒的 VPA 訊號 key 清單。bars 需含足夠前文（約 >6 根）。"""
    n = len(bars)
    if n < 7:
        return []
    prior = bars[:-1]
    ref = prior[-LOOK:]
    vol_avg = sum(float(b["volume"]) for b in ref) / len(ref)
    spr_avg = sum(_spread(b) for b in ref) / len(ref)
    if vol_avg <= 0 or spr_avg <= 0:
        return []

    cur = bars[-1]
    o, h, l, c = (float(cur["open"]), float(cur["high"]), float(cur["low"]), float(cur["close"]))
    vol, spr = float(cur["volume"]), h - l
    if spr <= 0:
        return []
    vr = vol / vol_avg                       # 量能倍數
    sr = spr / spr_avg                        # 振幅倍數
    pos = (c - l) / spr                        # 收盤在當日區間位置（0=最低,1=最高）
    prev_c = float(prior[-1]["close"])
    up_bar, down_bar = c > prev_c, c < prev_c

    # 前文趨勢：近 10 根收盤方向
    cs = [float(b["close"]) for b in prior[-10:]]
    trend = 1 if cs[-1] > cs[0] else (-1 if cs[-1] < cs[0] else 0)

    ultra, high, low = vr >= 2.0, vr >= 1.5, vr <= 0.6
    wide, narrow = sr >= 1.5, sr <= 0.6

    out = []
    # ── 承接（大戶吃貨） ─────────────────────────────
    # 停損量：跌勢中爆量寬振幅、但收在區間上半 → 恐慌賣壓被接走
    if down_bar and ultra and wide and pos >= 0.45:
        out.append("stopping_vol")
    # 承接量：高量下跌卻收在高檔 → 買方吸收賣壓
    elif down_bar and high and pos >= 0.6:
        out.append("accumulation")
    # 測試無賣壓：下跌但量縮、振幅小 → 浮額已洗清
    if down_bar and low and narrow:
        out.append("no_supply")
    # ── 測試/出貨（虛漲、換手、倒貨） ───────────────
    # 無買氣：上漲卻量縮振幅小 → 沒人追、虛漲
    if up_bar and low and narrow:
        out.append("no_demand")
    # 量價背離：大量卻推不動（振幅小）→ 高檔換手/努力無結果
    if high and narrow:
        out.append("churning")
    # 買盤高潮：漲勢中爆量寬振幅卻收在區間下半 → 追高被倒貨
    if trend > 0 and up_bar and ultra and wide and pos <= 0.5:
        out.append("buying_climax")

    return out
