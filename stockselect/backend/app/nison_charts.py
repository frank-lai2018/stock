r"""nison_charts.py — 日本「與時間無關」圖表轉換：磚形圖(Renko) 與 三線反轉(Three-Line Break)。

出處：Steve Nison《Beyond Candlesticks》（繁中《股票K線戰法》，寰宇 1997）。

和 patterns.py 的差別：那邊做「逐根 K 棒的型態判讀」，這裡做**重新取樣**——
把日線壓成「價格走滿某個幅度才記一筆」的序列，**時間軸被丟掉**，等於一層降噪濾波。
構圖規則是嚴格數學定義的，所以可編碼、可回測（不像逐根 bar 的主觀判讀）。

bars：由舊到新的 list[dict]，需含 close；ATR 相關另需 high/low。
      **請用還原價**（adj_close/adj_high/adj_low），否則除權息當天會被當成一次大跳空。
全部是純函式、無外部相依，方便單元測試與 backtest 重用。

典型用法：
    brick = atr_brick(bars, n=14, k=1.0)      # 磚高綁波動度，跨個股才可比
    bricks = renko(bars, brick)
    lines  = three_line_break(bars)
    for f in flips(lines):                    # 方向翻轉的那幾筆 → 當特徵或進出場訊號
        ...
"""

__all__ = ["renko", "three_line_break", "flips", "atr", "atr_brick", "pct_brick", "compression"]


# ---------- 磚高（brick size）決定方式 ----------
# 台股價位從個位數到四位數都有，**固定金額的磚高沒有跨股比較意義**（5 元對 20 元的股票是 25%，
# 對 1000 元的股票是 0.5%）。所以磚高一律用「相對」的方式算：ATR 或百分比。

def atr(bars, n=14):
    """Wilder ATR（真實波幅均值）。bars 需含 high/low/close。不足 n+1 根回傳 None。"""
    if len(bars) < n + 1:
        return None
    trs = []
    for prev, cur in zip(bars, bars[1:]):
        pc = float(prev["close"])
        h, l = float(cur["high"]), float(cur["low"])
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    a = sum(trs[:n]) / n                       # 前 n 筆用簡單平均起頭
    for tr in trs[n:]:                         # 之後用 Wilder 平滑
        a = (a * (n - 1) + tr) / n
    return a


def atr_brick(bars, n=14, k=1.0):
    """磚高 = k × ATR(n)（取全期最後一筆 ATR，**整段固定**）。

    刻意不用逐根變動的 ATR：磚高一變，整張圖的歷史就會跟著重畫，回測會前視。
    """
    a = atr(bars, n)
    return None if a is None else a * k


def pct_brick(bars, pct=3.0, ref="last"):
    """磚高 = 參考價 × pct%。ref: last=最後收盤 / mean=全期平均收盤。

    注意：這同樣是**整段固定**的絕對金額。若要真正的等比例刻度，該做的是對數座標
    （把 close 取 log 再跑 renko），而不是讓磚高浮動。
    """
    cs = [float(b["close"]) for b in bars]
    if not cs:
        return None
    base = cs[-1] if ref == "last" else sum(cs) / len(cs)
    return base * pct / 100.0


# ---------- 磚形圖 Renko ----------

def renko(bars, brick, price_key="close"):
    """磚形圖：價格每走滿 brick 砌一塊磚；**反轉需要 2 塊磚**（Nison 標準）。

    只看收盤價（Nison 原著作法）。同一天走足多塊磚會一次補上多塊，trade_date 相同。

    回傳 list[dict]：
      dir        1=紅磚(上) / -1=綠磚(下)
      open/close 磚的起訖價（up: open=底 close=頂；down: open=頂 close=底）
      trade_date 觸發這塊磚的那一天
      index      觸發那根 bar 在原序列的索引（回測對齊用）
    """
    if not brick or brick <= 0:
        raise ValueError(f"brick 必須 > 0（收到 {brick}）")
    out = []
    top = bot = None
    d = 0                                       # 目前方向：0 尚未定向
    for i, b in enumerate(bars):
        p = float(b[price_key])
        if top is None:                         # 第一根當基準，不出磚
            top = bot = p
            continue
        while True:
            if d >= 0 and p >= top + brick:     # 順勢向上（含尚未定向）
                bot, top, d = top, top + brick, 1
            elif d <= 0 and p <= bot - brick:   # 順勢向下（含尚未定向）
                top, bot, d = bot, bot - brick, -1
            elif d == 1 and p <= bot - brick:   # 上轉下：要跌破「磚底再一塊」＝距磚頂 2 塊
                top, bot, d = bot, bot - brick, -1
            elif d == -1 and p >= top + brick:  # 下轉上：同理
                bot, top, d = top, top + brick, 1
            else:
                break
            out.append({"dir": d,
                        "open": bot if d == 1 else top,
                        "close": top if d == 1 else bot,
                        "trade_date": b.get("trade_date"), "index": i})
    return out


# ---------- 三線反轉 Three-Line Break ----------

def three_line_break(bars, lines=3, price_key="close"):
    """三線反轉：只用收盤價，畫「線」而非 K 棒。

    規則：
      順勢 — 收盤突破「最近一條線的頂（多）／底（空）」→ 接一條同向的新線。
      反轉 — 收盤要突破「最近 lines 條線的極值」才翻向（lines=3 即三線反轉）。
      兩者都不成立 → 當天不畫線（這就是它濾雜訊的地方）。

    lines 取「最近 lines 條線」而不分方向——趨勢延續時兩種算法等價，剛反轉時
    這樣算比較嚴格（門檻會含到反轉前那幾條），寧可慢一點也不要一日三市。

    回傳 list[dict]：dir / top / bottom / trade_date / index。
    """
    if lines < 1:
        raise ValueError(f"lines 必須 >= 1（收到 {lines}）")
    out = []
    base = None
    for i, b in enumerate(bars):
        p = float(b[price_key])
        if base is None:
            base = p
            continue
        if not out:                                       # 第一條線：跟起始收盤比
            if p > base:
                out.append(_line(1, base, p, b, i))
            elif p < base:
                out.append(_line(-1, p, base, b, i))
            continue
        last = out[-1]
        recent = out[-lines:]
        hi = max(x["top"] for x in recent)
        lo = min(x["bottom"] for x in recent)
        if last["dir"] == 1:
            if p > last["top"]:                           # 續漲
                out.append(_line(1, last["top"], p, b, i))
            elif p < lo:                                  # 破最近 n 條低點 → 翻空
                out.append(_line(-1, p, lo, b, i))
        else:
            if p < last["bottom"]:                        # 續跌
                out.append(_line(-1, p, last["bottom"], b, i))
            elif p > hi:                                  # 破最近 n 條高點 → 翻多
                out.append(_line(1, hi, p, b, i))
    return out


def _line(d, bottom, top, b, i):
    return {"dir": d, "bottom": bottom, "top": top,
            "trade_date": b.get("trade_date"), "index": i}


# ---------- 共用 ----------

def flips(series):
    """從 renko/three_line_break 的輸出取出「方向翻轉」的那幾筆——實際要用的訊號點。

    回傳 list[dict]，每筆是原序列中方向與前一筆相反的那個元素。
    """
    return [cur for prev, cur in zip(series, series[1:]) if cur["dir"] != prev["dir"]]


def compression(bars, series):
    """壓縮率：原始 K 棒數 → 轉換後筆數。用來檢查參數合不合理。

    經驗值：壓到 1/3 ~ 1/10 通常還可用；壓到剩個位數表示磚太大、看不到東西了。
    """
    n, m = len(bars), len(series)
    return {"bars": n, "units": m, "ratio": (m / n) if n else 0.0}
