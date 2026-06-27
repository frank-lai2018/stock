"""technical_signals.py — 把 technical_analysis_framework 裡「可程式化」的準則，
從 price_daily 算成技術面訊號（趨勢/均線/量能/突破/K棒型態）。

設計：
- 均線/量能/突破 → 純 pandas（零額外依賴，一定能跑）
- K棒型態        → TA-Lib（沒裝會自動略過，不影響其他訊號）
- 均線週期用台股日線慣例：月線20 / 季線60 / 半年線120（對應框架的 20/50/200 週）

用法：
  python technical_signals.py                      # 掃 price_daily 所有股票，列出最新一天觸發的訊號
  python technical_signals.py --stock 2330 --history  # 看單檔最近 20 天的訊號歷史
"""
import argparse
import os

import pandas as pd
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

load_dotenv()
engine = create_engine(os.environ["DATABASE_URL"])

try:
    import talib
    HAS_TALIB = True
except ImportError:
    HAS_TALIB = False

# 參數（台股日線慣例）
MA_SHORT, MA_MID, MA_LONG = 20, 60, 120     # 月線 / 季線 / 半年線
VOL_MA = 20                                 # 均量天數
BREAKOUT_N = 60                             # 突破 N 日高
SQUEEZE_PCT = 0.03                          # 三線聚攏：(max-min)/close < 3% = 蓄勢

STATE_COLS = ["bull_ma", "ma_squeeze", "vol_price_up", "bear_divergence"]
EVENT_COLS = ["golden_cross", "death_cross", "breakout"]
CDL_COLS = ["cdl_hammer", "cdl_engulf", "cdl_morning", "cdl_shooting", "cdl_evening"]
LABEL = {
    "bull_ma": "均線多頭排列", "ma_squeeze": "三線聚攏(蓄勢)", "vol_price_up": "價漲量增",
    "bear_divergence": "價高量縮(背離)", "golden_cross": "黃金叉", "death_cross": "死亡叉",
    "breakout": "帶量突破60日高", "cdl_hammer": "錘子線", "cdl_engulf": "吞噬",
    "cdl_morning": "晨星", "cdl_shooting": "射擊之星", "cdl_evening": "夜星",
}


def load_prices(stock_id=None):
    sql = "SELECT stock_id, date, open, high, low, close, volume FROM price_daily"
    if stock_id:
        sql += " WHERE stock_id = :s"
    sql += " ORDER BY stock_id, date"
    return pd.read_sql(text(sql), engine,
                       params={"s": stock_id} if stock_id else None,
                       parse_dates=["date"])


def compute_signals(g: pd.DataFrame) -> pd.DataFrame:
    """g = 單一股票、依日期排序。回傳加上訊號欄位的 g。"""
    g = g.sort_values("date").reset_index(drop=True)
    c, v = g["close"], g["volume"]
    ma_s, ma_m, ma_l = c.rolling(MA_SHORT).mean(), c.rolling(MA_MID).mean(), c.rolling(MA_LONG).mean()
    vma = v.rolling(VOL_MA).mean()

    # ① 均線多頭排列：MA20>MA60>MA120 且站上 MA20
    g["bull_ma"] = (ma_s > ma_m) & (ma_m > ma_l) & (c > ma_s)
    # ② 黃金叉 / 死亡叉（MA20 穿 MA60）
    g["golden_cross"] = (ma_s > ma_m) & (ma_s.shift(1) <= ma_m.shift(1))
    g["death_cross"] = (ma_s < ma_m) & (ma_s.shift(1) >= ma_m.shift(1))
    # ③ 三線聚攏（大波動前蓄勢）
    mas = pd.concat([ma_s, ma_m, ma_l], axis=1)
    g["ma_squeeze"] = ((mas.max(axis=1) - mas.min(axis=1)) / c) < SQUEEZE_PCT
    # ④ 帶量突破 N 日新高（量 > 1.5×均量）
    g["breakout"] = (c > c.shift(1).rolling(BREAKOUT_N).max()) & (v > 1.5 * vma)
    # ⑤ 價漲量增
    g["vol_price_up"] = (c > c.shift(1)) & (v > vma)
    # ⑥ 價創 20 日新高但量縮 → 追高小心（簡化版背離）
    g["bear_divergence"] = (c >= c.rolling(20).max()) & (v < vma)
    # ⑦ K棒型態（TA-Lib；沒裝則全 0）
    if HAS_TALIB:
        o, h, l = g["open"], g["high"], g["low"]
        g["cdl_hammer"] = talib.CDLHAMMER(o, h, l, c)
        g["cdl_engulf"] = talib.CDLENGULFING(o, h, l, c)
        g["cdl_morning"] = talib.CDLMORNINGSTAR(o, h, l, c)
        g["cdl_shooting"] = talib.CDLSHOOTINGSTAR(o, h, l, c)
        g["cdl_evening"] = talib.CDLEVENINGSTAR(o, h, l, c)
    return g


def fired_labels(row) -> list:
    out = [LABEL[c] for c in STATE_COLS + EVENT_COLS if row.get(c)]
    for c in CDL_COLS:                       # CDL: +100 多 / -100 空 / 0 無
        val = row.get(c, 0)
        if val:
            out.append(LABEL[c] + ("↑" if val > 0 else "↓"))
    return out


def scan_latest():
    df = load_prices()
    if df.empty:
        print("price_daily 沒資料 → 先跑 fetch_finmind.py")
        return
    if not HAS_TALIB:
        print("（提示：未裝 TA-Lib，K棒型態略過；裝了可多 5 種型態訊號。）\n")
    hits = []
    for sid, g in df.groupby("stock_id"):
        last = compute_signals(g).iloc[-1]
        labels = fired_labels(last)
        if labels:
            hits.append((sid, last["date"].date(), "、".join(labels)))
    if not hits:
        print("最新一天沒有股票觸發訊號。")
        return
    print(f"{'股票':<8}{'日期':<12}觸發訊號")
    for sid, d, labels in sorted(hits):
        print(f"{sid:<8}{str(d):<12}{labels}")


def show_history(stock_id, n=20):
    df = load_prices(stock_id)
    if df.empty:
        print(f"{stock_id} 無資料")
        return
    for _, row in compute_signals(df).tail(n).iterrows():
        labels = fired_labels(row)
        if labels:
            print(f"{row['date'].date()}  收 {row['close']:.1f}  {'、'.join(labels)}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stock")
    ap.add_argument("--history", action="store_true")
    args = ap.parse_args()
    if args.history and args.stock:
        show_history(args.stock)
    else:
        scan_latest()


if __name__ == "__main__":
    main()
