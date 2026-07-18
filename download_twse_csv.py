r"""download_twse_csv.py — 給股票代碼，下載 TWSE「個股日成交資訊」每月 CSV。

存放：<輸出根目錄>\<股號>\<股號>_<YYYYMM>.csv（預設根目錄 H:\data）
特性：純標準函式庫（免 pip install）、禮貌限流 + 重試、可重跑續抓（已存在的月份自動跳過）。
      沒抓到資料的月份 → 記到 <輸出根目錄>\<股號>.txt（每行一個 YYYY/MM，放在 data 那層）。
注意：這是「上市(TWSE)」資料；上櫃(TPEx)代碼這裡抓不到（需另寫 TPEx 版）。
      下載的 CSV 是 Big5 編碼（跟證交所下載按鈕一樣）；之後用 pandas 讀請加 encoding="big5"。

用法：
  python download_twse_csv.py 2330
  python download_twse_csv.py 2330 2317 0050 --start 2015-01
  python download_twse_csv.py 2330 --out "H:\\data" --delay 4
"""
import argparse
import os
import random
import re
import ssl
import time
import urllib.request
from datetime import date

BASE_URL = "https://www.twse.com.tw/exchangeReport/STOCK_DAY"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
ROC_ROW = re.compile(r'^"?\s*\d{2,3}/\d{2}/\d{2}')   # 民國日期資料列；\s* 容忍2位數年份前的空白，如 " 99/01/04"（2010 以前）

# TWSE 的 SSL 憑證有瑕疵（Missing Subject Key Identifier），新版 OpenSSL 會驗證失敗。
# 這是公開政府資料，對它關掉憑證驗證（TWSE 爬蟲的通用做法）。
SSL_CTX = ssl.create_default_context()
SSL_CTX.check_hostname = False
SSL_CTX.verify_mode = ssl.CERT_NONE


def month_range(start_ym, end_ym):
    y, m = start_ym
    while (y, m) <= end_ym:
        yield y, m
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)


def fetch_csv(stock_no, y, m, retries=4):
    url = f"{BASE_URL}?response=csv&date={y}{m:02d}01&stockNo={stock_no}"
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=30, context=SSL_CTX) as resp:
                return resp.read()              # 原始 bytes（Big5）
        except Exception as e:                  # 被擋/逾時 → 退避重試
            wait = 5 * (attempt + 1) + random.uniform(0, 3)
            print(f"    重試 {attempt + 1}/{retries}（{e}）等 {wait:.0f}s")
            time.sleep(wait)
    return None


def has_data(raw):
    if not raw:
        return False
    text = raw.decode("big5", errors="ignore")
    return any(ROC_ROW.match(line.strip()) for line in text.splitlines())


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


def download_stock(stock_no, start_ym, end_ym, out_dir, delay):
    folder = os.path.join(out_dir, stock_no)
    os.makedirs(folder, exist_ok=True)
    nodata_path = os.path.join(out_dir, f"{stock_no}.txt")   # 無資料月份清單，放在 data 那層
    logged = load_logged(nodata_path)
    saved = skipped = nodata = 0
    for y, m in month_range(start_ym, end_ym):
        path = os.path.join(folder, f"{stock_no}_{y}{m:02d}.csv")
        if os.path.exists(path):                # 可續抓：已抓過就跳過
            skipped += 1
            continue
        raw = fetch_csv(stock_no, y, m)
        if has_data(raw):
            with open(path, "wb") as f:
                f.write(raw)                    # 存原始 Big5 bytes（同下載按鈕）
            saved += 1
            print(f"  [存]     {stock_no} {y}/{m:02d}")
        else:
            nodata += 1
            ym = f"{y}/{m:02d}"
            if ym not in logged:                # 沒抓到 → 記到 <股號>.txt（同月只記一次）
                note = "" if raw is not None else "\t連線失敗(重試後仍失敗)"
                with open(nodata_path, "a", encoding="utf-8") as f:
                    f.write(ym + note + "\n")
                logged.add(ym)
            print(f"  [無資料] {stock_no} {y}/{m:02d}")
        time.sleep(delay + random.uniform(0, 2))   # 禮貌限流 + 抖動，避免被封
    tail = f"（無資料清單：{nodata_path}）" if nodata else ""
    print(f"== {stock_no} 完成：新增 {saved}、跳過(已存在) {skipped}、無資料 {nodata}{tail}")


def parse_ym(s):
    y, m = s.split("-")
    return int(y), int(m)


def main():
    ap = argparse.ArgumentParser(description="下載 TWSE 個股日成交資訊每月 CSV")
    ap.add_argument("stocks", nargs="*", help="股票代碼（可多個），如 2330 2317")
    ap.add_argument("--codes-file", default="", help="從檔案讀代碼（每行一個，可含逗號/空白），與位置參數合併")
    ap.add_argument("--start", default="2011-01", help="起始月份 YYYY-MM（預設 2010-01）")
    ap.add_argument("--end", default=None, help="結束月份 YYYY-MM（預設本月）")
    ap.add_argument("--out", default=r"H:\data", help=r"輸出根目錄（預設 H:\data）")
    ap.add_argument("--delay", type=float, default=4.0,
                    help="每次請求間隔秒數（預設 4；別調太低，會被 TWSE 封）")
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
