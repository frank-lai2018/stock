r"""fetch_price_index.py — 抓「價格版」大盤指數存 CSV（供 load_to_db 灌 market_index）。

  上市 加權股價指數（價格）：TWSE FMTQIK 月檔的「發行量加權股價指數」欄 → Index\TWSE.csv
  上櫃 櫃買指數：            TPEx tradingIndex 月檔的「櫃買指數」欄        → Index\TPEx.csv

免 FinMind、by-month（每月一請求）。輸出 date,price 兩欄，供 load_to_db.load_index 讀取。
（現有 Index\TAIEX.csv 是 FinMind「報酬指數」，本檔不覆蓋，另存 TWSE/TPEx 兩條價格指數。）

用法：
  python fetch_price_index.py --start 2010-01
  python fetch_price_index.py --start 2010-01 --ids TWSE,TPEx --out "H:\data\Index"
"""
import argparse
import csv
import io
import json
import os
import ssl
import time
import urllib.request
from datetime import date

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE

TWSE_URL = "https://www.twse.com.tw/rwd/zh/afterTrading/FMTQIK?date={y}{m:02d}01&response=csv"
TPEX_URL = "https://www.tpex.org.tw/www/zh-tw/afterTrading/tradingIndex?date={y}/{m:02d}/01&response=json"


def http_get(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(url=req, timeout=45, context=CTX) as r:
        return r.read()


def roc_to_iso(s):
    p = str(s).strip().split("/")
    if len(p) == 3 and all(x.strip().isdigit() for x in p):
        return f"{int(p[0]) + 1911:04d}-{int(p[1]):02d}-{int(p[2]):02d}"
    return None


def _num(x):
    return str(x).replace(",", "").strip()


def month_range(start_y, start_m):
    today = date.today()
    y, m = start_y, start_m
    while (y, m) <= (today.year, today.month):
        yield y, m
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)


def fetch_twse(y, m):
    """FMTQIK：回 [(date_iso, index)]；第 5 欄=發行量加權股價指數。"""
    out = []
    try:
        raw = http_get(TWSE_URL.format(y=y, m=m))
    except Exception as e:
        print(f"    TWSE {y}-{m:02d} 失敗：{str(e)[:60]}"); return out
    for c in csv.reader(io.StringIO(raw.decode("big5", "ignore"))):
        if len(c) < 5:
            continue
        d = roc_to_iso(c[0])
        v = _num(c[4])
        if d and v:
            try:
                float(v)
            except ValueError:
                continue
            out.append((d, v))
    return out


def fetch_tpex(y, m):
    """tradingIndex：回 [(date_iso, index)]；欄位[4]=櫃買指數。"""
    out = []
    try:
        raw = http_get(TPEX_URL.format(y=y, m=m))
        j = json.loads(raw.decode("utf-8", "ignore"))
    except Exception as e:
        print(f"    TPEx {y}-{m:02d} 失敗：{str(e)[:60]}"); return out
    data = (j.get("tables") or [{}])[0].get("data") or []
    for r in data:
        if len(r) < 5:
            continue
        d = roc_to_iso(str(r[0]))
        if d and r[4] not in (None, ""):
            out.append((d, _num(r[4])))
    return out


def main():
    ap = argparse.ArgumentParser(description="抓價格版大盤/櫃買指數（by-month）")
    ap.add_argument("--start", default="2010-01", help="起始月份 YYYY-MM（預設 2010-01）")
    ap.add_argument("--ids", default="TWSE,TPEx", help="要抓哪些（TWSE=上市價格指數, TPEx=櫃買指數）")
    ap.add_argument("--out", default=r"H:\data\Index", help=r"輸出根目錄（預設 H:\data\Index）")
    ap.add_argument("--delay", type=float, default=1.5, help="每請求間隔秒")
    args = ap.parse_args()

    sy, sm = (int(x) for x in args.start.split("-"))
    os.makedirs(args.out, exist_ok=True)
    ids = [x.strip() for x in args.ids.split(",") if x.strip()]
    fetchers = {"TWSE": fetch_twse, "TPEx": fetch_tpex}

    for iid in ids:
        fn = fetchers.get(iid)
        if not fn:
            print(f"跳過未知指數 {iid}"); continue
        path = os.path.join(args.out, f"{iid}.csv")
        rows = {}
        if os.path.exists(path):                         # 先讀既有 → 合併（每晚只抓當月也不丟歷史）
            with open(path, encoding="utf-8-sig") as f:
                for r in csv.DictReader(f):
                    if r.get("date") and r.get("price"):
                        rows[r["date"]] = r["price"]
        print(f"=== {iid} 從 {args.start} 起（既有 {len(rows)} 日）===")
        for y, m in month_range(sy, sm):
            for d, v in fn(y, m):
                rows[d] = v
            time.sleep(args.delay)
        with open(path, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f)
            w.writerow(["date", "price"])
            for d in sorted(rows):
                w.writerow([d, rows[d]])
        print(f"  → {path}｜{len(rows)} 個交易日")
    print("完成。之後入庫：python load_to_db.py --tables market_index --dsn \"...\"")


if __name__ == "__main__":
    main()
