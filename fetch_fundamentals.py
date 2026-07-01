r"""fetch_fundamentals.py — 給股票代碼，抓基本面（月營收 + 綜合損益表：EPS/毛利…）存成 CSV。

來源：FinMind 開放 API（https://api.finmindtrade.com/api/v4/data）
存放：<輸出根目錄>\<股號>\（預設 H:\data\Fundamentals）
   <股號>_revenue.csv       月營收（含 月增率% / 年增率%）
   <股號>_financials.csv    綜合損益表（逐季；含 EPS、毛利率%、營業利益率%）

Token：FinMind 免費註冊可拿 token（https://finmindtrade.com）。
   無 token 也能試，但額度很低、容易被擋。建議設環境變數 FINMIND_TOKEN，或用 --token 帶入。

注意（做回測必看）：
- 月營收每月 10 日前才公告上月數字；財報 Q1≈5/15、Q2≈8/14、Q3≈11/14、年報 3/31 才公告。
  回測時別在公告日前就用該期數字，否則前視偏差。FinMind 的 date 多為「資料期別日」，非實際公告日。
- 金額單位、欄位名稱可能隨 FinMind 版本變動；對不上時請查 https://finmind.github.io/。

需求：pip install pandas
用法：
  python fetch_fundamentals.py 2330
  python fetch_fundamentals.py 2330 2317 5483 --start 2015-01-01
  python fetch_fundamentals.py 2330 --token xxxxx --out "H:\data\Fundamentals"
"""
import argparse
import json
import os
import ssl
import time
import urllib.parse
import urllib.request
from datetime import date

import pandas as pd

API = "https://api.finmindtrade.com/api/v4/data"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"

# 與其他抓取器一致：對公開資料站放寬 SSL（避免憑證瑕疵造成失敗）
SSL_CTX = ssl.create_default_context()
SSL_CTX.check_hostname = False
SSL_CTX.verify_mode = ssl.CERT_NONE


def fetch(dataset, data_id, start, end, token, retries=4):
    """打 FinMind API，回傳 DataFrame（無資料/失敗回空 DataFrame）。"""
    params = {"dataset": dataset, "data_id": data_id, "start_date": start}
    if end:
        params["end_date"] = end
    if token:
        params["token"] = token
    url = API + "?" + urllib.parse.urlencode(params)
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=60, context=SSL_CTX) as r:
                j = json.loads(r.read().decode("utf-8", errors="ignore"))
            if j.get("status") != 200:                 # 402/429：額度或限流
                print(f"    API status={j.get('status')} msg={j.get('msg', '')}")
                if j.get("status") in (402, 429):      # 限流 → 退避重試
                    time.sleep(10 * (attempt + 1))
                    continue
                return pd.DataFrame()
            return pd.DataFrame(j.get("data", []))
        except Exception as e:
            wait = 5 * (attempt + 1)
            print(f"    重試 {attempt + 1}/{retries}（{e}）等 {wait}s")
            time.sleep(wait)
    return pd.DataFrame()


def build_revenue(df):
    """月營收：依日期排序，加 月增率% / 年增率%（同月比去年）。"""
    if df.empty or "revenue" not in df.columns:
        return df
    df = df.sort_values("date").reset_index(drop=True)
    df["revenue"] = pd.to_numeric(df["revenue"], errors="coerce")
    df["月增率(%)"] = (df["revenue"].pct_change() * 100).round(2)
    df["年增率(%)"] = (df["revenue"].pct_change(12) * 100).round(2)   # 月資料連續時=去年同月
    return df


def build_financials(df):
    """綜合損益表 long(每列一個 type) → wide(每列一期)，加 毛利率% / 營業利益率%。"""
    if df.empty or "type" not in df.columns:
        return df
    wide = (df.pivot_table(index="date", columns="type", values="value", aggfunc="first")
              .reset_index())
    wide.columns.name = None
    if "Revenue" in wide.columns:                      # 有就算比率，沒有就略過（不硬掛）
        rev = pd.to_numeric(wide["Revenue"], errors="coerce")
        if "GrossProfit" in wide.columns:
            wide["毛利率(%)"] = (pd.to_numeric(wide["GrossProfit"], errors="coerce") / rev * 100).round(2)
        if "OperatingIncome" in wide.columns:
            wide["營業利益率(%)"] = (pd.to_numeric(wide["OperatingIncome"], errors="coerce") / rev * 100).round(2)
    return wide.sort_values("date").reset_index(drop=True)


def download_one(code, start, end, out_root, token, delay):
    folder = os.path.join(out_root, code)
    os.makedirs(folder, exist_ok=True)

    rev = build_revenue(fetch("TaiwanStockMonthRevenue", code, start, end, token))
    if not rev.empty:
        path = os.path.join(folder, f"{code}_revenue.csv")
        rev.to_csv(path, index=False, encoding="utf-8-sig")
        print(f"  [存] {code} 月營收 {len(rev)} 列 → {path}")
    else:
        print(f"  [無] {code} 月營收 無資料")
    time.sleep(delay)

    fin = build_financials(fetch("TaiwanStockFinancialStatements", code, start, end, token))
    if not fin.empty:
        path = os.path.join(folder, f"{code}_financials.csv")
        fin.to_csv(path, index=False, encoding="utf-8-sig")
        print(f"  [存] {code} 綜合損益表 {len(fin)} 列（含 EPS/毛利率）→ {path}")
    else:
        print(f"  [無] {code} 綜合損益表 無資料")
    time.sleep(delay)


def main():
    ap = argparse.ArgumentParser(description="抓台股基本面（月營收 + 綜合損益表）存 CSV")
    ap.add_argument("stocks", nargs="+", help="股票代碼（可多個）")
    ap.add_argument("--start", default="2010-01-01", help="起始日 YYYY-MM-DD（預設 2010-01-01）")
    ap.add_argument("--end", default=None, help="結束日 YYYY-MM-DD（預設今天）")
    ap.add_argument("--out", default=r"H:\data\Fundamentals", help=r"輸出根目錄（預設 H:\data\Fundamentals）")
    ap.add_argument("--token", default=os.environ.get("FINMIND_TOKEN", ""),
                    help="FinMind token（或設環境變數 FINMIND_TOKEN）")
    ap.add_argument("--delay", type=float, default=2.0, help="每請求間隔秒數（預設 2）")
    args = ap.parse_args()

    end = args.end or date.today().isoformat()
    if not args.token:
        print("⚠️ 未提供 FinMind token（--token 或環境變數 FINMIND_TOKEN）；免 token 額度很低、可能被擋。")
    print(f"輸出根目錄：{args.out}｜範圍 {args.start} ~ {end}｜共 {len(args.stocks)} 檔")
    for s in args.stocks:
        print(f"\n=== {s} ===")
        download_one(s, args.start, end, args.out, args.token, args.delay)
    print("\n全部完成。")


if __name__ == "__main__":
    main()
