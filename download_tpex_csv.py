r"""download_tpex_csv.py — 給上櫃股票代碼，下載 TPEx「個股日成交資訊」每月資料，存成 CSV。

來源：TPEx 新站 tradingStock endpoint（回傳 JSON，本程式轉成 CSV）
存放：<輸出根目錄>\<股號>\<股號>_<YYYYMM>.csv（預設 H:\data，與上市下載器同結構）
編碼：UTF-8(BOM)，Excel 可直接正確顯示中文；數字已去掉千分位逗號
特性：禮貌限流 + 重試、可重跑續抓（已存在月份自動跳過）。純標準函式庫，免裝套件。
      沒抓到資料的月份 → 記到 <輸出根目錄>\<股號>.txt（每行一個 YYYY/MM，放在 data 那層）。

注意：
- 這是「上櫃(TPEx)」；上市股請用 download_twse_csv.py。
- 欄位單位：成交股數=「張」、成交金額=「千元」（與 TWSE 的 股/元 不同，原樣保留）。

用法：
  python download_tpex_csv.py 5483
  python download_tpex_csv.py 5483 6488 3105 --start 2015-01
  python download_tpex_csv.py 5483 --out "H:\\data" --delay 4
"""
import argparse
import csv
import json
import os
import random
import ssl
import time
import urllib.request
from datetime import date

BASE = "https://www.tpex.org.tw/www/zh-tw/afterTrading/tradingStock"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
HEADER = ["日期", "成交股數(張)", "成交金額(千元)", "開盤", "最高", "最低", "收盤", "漲跌", "成交筆數"]

# 與 TWSE 同：對政府公開站關掉 SSL 驗證，避免憑證瑕疵造成失敗
SSL_CTX = ssl.create_default_context()
SSL_CTX.check_hostname = False
SSL_CTX.verify_mode = ssl.CERT_NONE


def fetch_month(code, y, m, retries=4):
    """回傳該月資料列 list（無資料回 []，連線失敗重試後回 None）。"""
    url = f"{BASE}?code={code}&date={y}/{m:02d}/01&response=json"   # TPEx 用西元日期
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=30, context=SSL_CTX) as r:
                j = json.loads(r.read().decode("utf-8", errors="ignore"))
            tables = j.get("tables") or []
            return tables[0]["data"] if tables and tables[0].get("data") else []
        except Exception as e:
            wait = 5 * (attempt + 1) + random.uniform(0, 3)
            print(f"    重試 {attempt + 1}/{retries}（{e}）等 {wait:.0f}s")
            time.sleep(wait)
    return None


def save_csv(path, rows):
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(HEADER)
        for row in rows:
            w.writerow([str(c).replace(",", "").strip() for c in row])   # 去千分位逗號


def load_logged(path):
    """讀回 <股號>.txt 內已記錄的月份（取每行第一欄 YYYY/MM 當鍵），避免重跑重覆寫入。"""
    if not os.path.exists(path):
        return set()
    keys = set()
    with open(path, "r", encoding="utf-8-sig") as f:   # 讀時容忍 BOM
        for line in f:
            s = line.strip()
            if s:
                keys.add(s.split()[0])          # 第一欄 = YYYY/MM
    return keys


def download_stock(code, start_ym, end_ym, out_dir, delay):
    folder = os.path.join(out_dir, code)
    os.makedirs(folder, exist_ok=True)
    nodata_path = os.path.join(out_dir, f"{code}.txt")   # 無資料月份清單，放在 data 那層
    logged = load_logged(nodata_path)
    saved = skipped = nodata = 0
    y, m = start_ym
    while (y, m) <= end_ym:
        path = os.path.join(folder, f"{code}_{y}{m:02d}.csv")
        if os.path.exists(path):                       # 可續抓
            skipped += 1
        else:
            rows = fetch_month(code, y, m)
            if rows:
                save_csv(path, rows)
                saved += 1
                print(f"  [存]     {code} {y}/{m:02d}")
            else:
                nodata += 1
                ym = f"{y}/{m:02d}"
                if ym not in logged:               # 沒抓到 → 記到 <股號>.txt（同月只記一次）
                    note = "" if rows is not None else "\t連線失敗(重試後仍失敗)"
                    with open(nodata_path, "a", encoding="utf-8") as f:
                        f.write(ym + note + "\n")
                    logged.add(ym)
                print(f"  [無資料] {code} {y}/{m:02d}")
            time.sleep(delay + random.uniform(0, 2))   # 禮貌限流 + 抖動
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    tail = f"（無資料清單：{nodata_path}）" if nodata else ""
    print(f"== {code} 完成：新增 {saved}、跳過(已存在) {skipped}、無資料 {nodata}{tail}")


def parse_ym(s):
    y, m = s.split("-")
    return int(y), int(m)


def main():
    ap = argparse.ArgumentParser(description="下載 TPEx 上櫃個股日成交資訊每月 CSV")
    ap.add_argument("stocks", nargs="*", help="上櫃股票代碼（可多個）")
    ap.add_argument("--codes-file", default="", help="從檔案讀代碼（每行一個，可含逗號/空白），與位置參數合併")
    ap.add_argument("--start", default="2010-01", help="起始月份 YYYY-MM（預設 2010-01）")
    ap.add_argument("--end", default=None, help="結束月份 YYYY-MM（預設本月）")
    ap.add_argument("--out", default=r"H:\data", help=r"輸出根目錄（預設 H:\data）")
    ap.add_argument("--delay", type=float, default=4.0, help="每次請求間隔秒數（預設 4）")
    args = ap.parse_args()

    codes = list(args.stocks)
    if args.codes_file:
        with open(args.codes_file, encoding="utf-8") as f:
            codes += [c for c in f.read().replace(",", " ").split() if c]
    codes = sorted(dict.fromkeys(codes))              # 去重保序
    if not codes:
        ap.error("請提供代碼（位置參數或 --codes-file）")

    start_ym = parse_ym(args.start)
    end_ym = parse_ym(args.end) if args.end else (date.today().year, date.today().month)

    print(f"輸出根目錄：{args.out}")
    print(f"範圍：{start_ym[0]}/{start_ym[1]:02d} ~ {end_ym[0]}/{end_ym[1]:02d}｜間隔 {args.delay}s｜共 {len(codes)} 檔")
    for s in codes:
        print(f"\n=== 下載 {s} ===")
        download_stock(s, start_ym, end_ym, args.out, args.delay)
    print("\n全部完成。")


if __name__ == "__main__":
    main()
