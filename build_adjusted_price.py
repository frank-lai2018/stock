r"""build_adjusted_price.py — 用原始日K + 股利，算「還原股價」存回去。

為什麼要它：TWSE/TPEx 下載的收盤價是「原始價」，除權息當天會跳空下跌（其實是配息不是真跌），
   會讓均線/KD/回測全部在除權息日出錯。這支用你已有的資料自己做還原，補上 FinMind 付費才有的還原價。

輸入：
   原始日K：<price-root>\<股號>\<股號>_YYYYMM.csv （TWSE=Big5 / TPEx=UTF-8，自動判別）
   股利：  <div-root>\<股號>\<股號>_dividend.csv     （fetch_fundamentals 的 dividend）
   減資：  <div-root>\<股號>\<股號>_capreduction.csv （fetch_fundamentals 的 capreduction，選用）
輸出：
   <price-root>\<股號>\<股號>_adj.csv
   欄位：date, open/high/low/close(原始), volume, amount(成交金額), adj_open/high/low/close(還原), cumfactor(還原因子)
   注意：volume/amount 保留原始市場單位（上市=股/元、上櫃=張/千元），入庫時由 load_to_db 統一 ×1000 正規化成 股/元。

還原方法（後復權，最新一天=原始價，往前調整使序列連續）：
   除權息：參考價 = (前收 - 現金股利) / (1 + 股票股利/10)；r = 參考價/前收
   減資：  r = 恢復買賣參考價 / 停止買賣前收盤價（官方數字，直接用）
   除權息/減資日「之前」的所有價格乘上其後所有 r 的累積。
   現金股利 = CashEarningsDistribution + CashStatutorySurplus（元/股）
   股票股利 = StockEarningsDistribution + StockStatutorySurplus（元/股，÷10=配股率）

需求：pip install pandas
用法：
  python build_adjusted_price.py 2330
  python build_adjusted_price.py 2330 5483 --price-root "H:\data" --div-root "H:\data\Fundamentals"
  python build_adjusted_price.py                     # 不給代碼 → 掃 price-root 下所有個股資料夾
"""
import argparse
import csv
import glob
import io
import os
import re

import pandas as pd

DATE_ROW = re.compile(r"^\s*\d{2,3}/\d{1,2}/\d{1,2}\s*$")   # 民國日期（去引號後）


def to_num(x):
    """字串轉數字；千分位逗號、空白、'--'（無交易）都當缺值。"""
    s = str(x).replace(",", "").replace('"', "").strip()
    if s in ("", "--", "---", "X", "x"):
        return float("nan")
    try:
        return float(s)
    except ValueError:
        return float("nan")


def roc_to_iso(cell):
    """民國日期 '112/03/16' 或 ' 99/01/04' → 西元 '2023-03-16'；非日期回 None。"""
    s = str(cell).replace('"', "").strip()
    if not DATE_ROW.match(s):
        return None
    y, m, d = s.split("/")
    return f"{int(y) + 1911:04d}-{int(m):02d}-{int(d):02d}"


def parse_rows(text, skip_header):
    """通吃 TWSE/TPEx：只挑「第一欄是民國日期」的資料列，依欄位位置取 OHLCV。
       兩者欄序一致：0日期 1成交量 2成交額 3開 4高 5低 6收 ..."""
    rows = []
    reader = csv.reader(io.StringIO(text))
    for cells in reader:
        if not cells:
            continue
        d = roc_to_iso(cells[0])
        if d is None or len(cells) < 7:                # 標題/欄名/說明列 → 跳過
            continue
        rows.append({"date": d, "volume": to_num(cells[1]), "amount": to_num(cells[2]),
                     "open": to_num(cells[3]), "high": to_num(cells[4]),
                     "low": to_num(cells[5]), "close": to_num(cells[6])})
    return rows


def load_prices(folder, code):
    """讀該股所有月檔並合併；用 BOM 判斷 TPEx(UTF-8)/TWSE(Big5)。"""
    pat = re.compile(rf"{re.escape(code)}_\d{{6}}\.csv$")
    files = sorted(f for f in glob.glob(os.path.join(folder, f"{code}_*.csv"))
                   if pat.search(os.path.basename(f)))     # 排除 _adj.csv 等
    all_rows = []
    for f in files:
        raw = open(f, "rb").read()
        text = raw.decode("utf-8-sig") if raw[:3] == b"\xef\xbb\xbf" else raw.decode("big5", errors="ignore")
        all_rows.extend(parse_rows(text, skip_header=(raw[:3] == b"\xef\xbb\xbf")))
    df = pd.DataFrame(all_rows)
    if df.empty:
        return df
    return (df.dropna(subset=["close"]).drop_duplicates("date")
              .sort_values("date").reset_index(drop=True))


def load_dividends(path):
    """讀 dividend.csv → {除權息日(西元): [現金股利, 股票股利]}。"""
    if not os.path.exists(path):
        return {}
    d = pd.read_csv(path, dtype=str)
    ev = {}
    for _, r in d.iterrows():
        cash = to_num(r.get("CashEarningsDistribution")) + to_num(r.get("CashStatutorySurplus"))
        cdate = str(r.get("CashExDividendTradingDate") or "").strip()
        if cdate and cash > 0:
            ev.setdefault(cdate, [0.0, 0.0])[0] += cash
        stock = to_num(r.get("StockEarningsDistribution")) + to_num(r.get("StockStatutorySurplus"))
        sdate = str(r.get("StockExDividendTradingDate") or "").strip()
        if sdate and stock > 0:
            ev.setdefault(sdate, [0.0, 0.0])[1] += stock
    return ev


def load_capreduction(path):
    """讀 capreduction.csv → {恢復買賣日(西元): r}；r = 恢復買賣參考價 / 停止買賣前收盤。"""
    if not os.path.exists(path):
        return {}
    d = pd.read_csv(path, dtype=str)
    ev = {}
    for _, row in d.iterrows():
        date = str(row.get("date") or "").strip()
        prev = to_num(row.get("ClosingPriceonTheLastTradingDay"))
        ref = to_num(row.get("PostReductionReferencePrice"))
        if date and prev > 0 and ref > 0:
            ev[date] = ev.get(date, 1.0) * (ref / prev)
    return ev


def _apply(df, ex_date, r):
    """把因子 r 乘到「除權息/減資日當列（無則其後第一個交易日）」。"""
    if not r > 0:
        return
    tgt = df.index[df["date"] >= ex_date]
    if len(tgt):
        df.loc[tgt[0], "ratio"] *= r


def adjust(df, div_events, capred_events):
    """後復權：合併除權息 + 減資，算每日 cumfactor 與還原 OHLC。"""
    df = df.copy()
    df["ratio"] = 1.0
    for ex_date, (cash, stock) in sorted(div_events.items()):
        prev = df[df["date"] < ex_date]
        if prev.empty:                                 # 除權息日早於價格資料 → 略過
            continue
        prev_close = prev["close"].iloc[-1]            # 前一交易日收盤
        if not prev_close > 0:
            continue
        _apply(df, ex_date, (prev_close - cash) / (prev_close * (1.0 + stock / 10.0)))
    for ex_date, r in sorted(capred_events.items()):   # 減資：官方 參考價/前收
        _apply(df, ex_date, r)
    rev = df["ratio"][::-1].cumprod()[::-1]            # 由後往前的累積乘積（含當列）
    df["cumfactor"] = rev.shift(-1).fillna(1.0)        # 排除當列 → =其後所有 r 之積
    for c in ["open", "high", "low", "close"]:
        df["adj_" + c] = (df[c] * df["cumfactor"]).round(4)
    return df.drop(columns="ratio")


def find_stock_codes(price_root):
    """掃 price-root 下含月K檔的資料夾當股票代碼（排除 Fundamentals/Index）。"""
    codes = []
    for name in sorted(os.listdir(price_root)):
        folder = os.path.join(price_root, name)
        if not os.path.isdir(folder) or name in ("Fundamentals", "Index"):
            continue
        if glob.glob(os.path.join(folder, f"{name}_*.csv")):
            codes.append(name)
    return codes


def build_one(code, price_root, div_root):
    folder = os.path.join(price_root, code)
    prices = load_prices(folder, code)
    if prices.empty:
        print(f"  [略] {code} 找不到原始日K")
        return
    dfolder = os.path.join(div_root, code)
    div = load_dividends(os.path.join(dfolder, f"{code}_dividend.csv"))
    capred = load_capreduction(os.path.join(dfolder, f"{code}_capreduction.csv"))
    out = adjust(prices, div, capred)
    cols = ["date", "open", "high", "low", "close", "volume", "amount",
            "adj_open", "adj_high", "adj_low", "adj_close", "cumfactor"]
    path = os.path.join(folder, f"{code}_adj.csv")
    out[cols].to_csv(path, index=False, encoding="utf-8-sig")
    print(f"  [存] {code} {len(out)} 列、除權息 {len(div)}、減資 {len(capred)} → {code}_adj.csv")


def main():
    ap = argparse.ArgumentParser(description="用原始日K+股利算還原股價，輸出 <股號>_adj.csv")
    ap.add_argument("stocks", nargs="*", help="股票代碼（可多個）；不給則掃 price-root 全部")
    ap.add_argument("--price-root", default=r"H:\data", help=r"原始日K根目錄（預設 H:\data）")
    ap.add_argument("--div-root", default=r"H:\data\Fundamentals", help=r"股利根目錄（預設 H:\data\Fundamentals）")
    args = ap.parse_args()

    codes = args.stocks or find_stock_codes(args.price_root)
    if not codes:
        raise SystemExit(f"在 {args.price_root} 找不到任何個股資料夾。")
    print(f"還原：{len(codes)} 檔｜日K {args.price_root}｜股利 {args.div_root}")
    for c in codes:
        build_one(c, args.price_root, args.div_root)
    print("完成。")


if __name__ == "__main__":
    main()
