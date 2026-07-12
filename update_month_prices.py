r"""update_month_prices.py — 檢查/補齊「指定月份」的個股月檔，殘缺就整月重抓覆蓋。

情境：download_twse_csv.py / download_tpex_csv.py 是「檔案已存在就跳過」，所以若某月是在
   月中下載的，月檔只會有「到當時為止」的日期、之後的交易日缺漏，重跑也不會補。
   這支專門修這個：逐檔比對「該股月檔的最大日期」與「該月最後交易日」，不足就整月重抓、覆蓋。

完整性判準（--criterion，見下）：
   先用 TWSE FMTQIK（大盤每日成交統計，1 個請求）取得該月的交易日清單，
   取最後一個交易日當基準（查當月就是「到目前為止的最後交易日」）。
   某股月檔若不存在、或其最大日期 < 該月最後交易日 → 視為不完整 → 整月重抓覆蓋。
   （上市/上櫃共用同一交易日曆，故 TWSE 的交易日清單對兩者皆適用。）

範圍：預設掃 <out> 下「已存在的個股資料夾」逐一檢查（可用 --only 限定幾檔）。
   市場別（決定重抓來源與編碼）：優先讀 code.xlsx 的「市場別」欄（需 pandas，選用）；
   讀不到就用資料夾內既有月檔的 BOM 判別（UTF-8→上櫃、Big5→上市）。

重抓來源沿用既有下載器：
   上市 → download_twse_csv.fetch_csv（存回原始 Big5 bytes）
   上櫃 → download_tpex_csv.fetch_month + save_csv（UTF-8-sig）

用法：
  python update_month_prices.py 2026-07
  python update_month_prices.py 2026-07 --out "H:\data"
  python update_month_prices.py 2026-07 --only 2330 5483    # 只查指定幾檔
  python update_month_prices.py 2026-07 --dry-run           # 只報告、不下載
"""
import argparse
import csv
import io
import os
import re
import ssl
import time
import urllib.request
from datetime import date

import download_tpex_csv as tpex
import download_twse_csv as twse

FMTQIK_URL = "https://www.twse.com.tw/exchangeReport/FMTQIK?response=csv&date={ym}01"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"

SSL_CTX = ssl.create_default_context()
SSL_CTX.check_hostname = False
SSL_CTX.verify_mode = ssl.CERT_NONE

DATE_RE = re.compile(r'^\s*"?\s*\d{2,3}/\d{1,2}/\d{1,2}')   # 民國日期資料列
CODE_RE = re.compile(r"^\d{4,6}[A-Z]?$")


def http_get(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=40, context=SSL_CTX) as r:
        return r.read()


def parse_roc(cell):
    """民國日期字串 '115/07/09'（可含引號/空白）→ (115, 7, 9)；非日期回 None。"""
    s = str(cell).replace('"', "").strip()
    parts = s.split("/")
    if len(parts) == 3 and all(p.strip().isdigit() for p in parts):
        return tuple(int(p) for p in parts)
    return None


def trading_days(y, m):
    """用 FMTQIK 取該月交易日清單 → [(roc_y, m, d), ...]（依日期排序）。"""
    raw = http_get(FMTQIK_URL.format(ym=f"{y}{m:02d}"))
    text = raw.decode("big5", errors="ignore")
    days = []
    for cells in csv.reader(io.StringIO(text)):
        if not cells or not DATE_RE.match(cells[0]):
            continue
        d = parse_roc(cells[0])
        if d:
            days.append(d)
    return sorted(days)


def read_text(path):
    """讀月檔：UTF-8(BOM)→上櫃(TPEx)，否則 Big5→上市(TWSE)。"""
    raw = open(path, "rb").read()
    if raw[:3] == b"\xef\xbb\xbf":
        return raw.decode("utf-8-sig", errors="ignore")
    return raw.decode("big5", errors="ignore")


def file_max_date(path):
    """月檔內最大的民國日期 (roc_y,m,d)；檔案不存在或無資料列回 None。"""
    if not os.path.exists(path):
        return None
    mx = None
    for cells in csv.reader(io.StringIO(read_text(path))):
        if not cells:
            continue
        d = parse_roc(cells[0])
        if d and (mx is None or d > mx):
            mx = d
    return mx


def existing_codes(out_root):
    """已下載過（有資料夾）的個股代號，排除 Fundamentals/Index。"""
    if not os.path.isdir(out_root):
        return []
    return sorted(n for n in os.listdir(out_root)
                  if os.path.isdir(os.path.join(out_root, n))
                  and CODE_RE.match(n) and n not in ("Fundamentals", "Index"))


def load_market_map(xlsx, col="股票代碼", market_col="市場別"):
    """讀 code.xlsx → {代碼: 市場別}；沒有 pandas 或檔案不存在則回 {}。"""
    if not xlsx or not os.path.exists(xlsx):
        return {}
    try:
        import pandas as pd
    except ImportError:
        print(f"⚠️ 無 pandas，略過讀 {xlsx}，改用月檔 BOM 判別市場別。")
        return {}
    df = pd.read_excel(xlsx, dtype=str)
    if col not in df.columns or market_col not in df.columns:
        print(f"⚠️ {xlsx} 缺「{col}」或「{market_col}」欄，改用 BOM 判別。")
        return {}
    m = {}
    for _, r in df.iterrows():
        code = str(r[col]).strip()
        mk = str(r[market_col]).strip()
        if code and code.lower() != "nan":
            m[code] = mk
    return m


def resolve_market(code, folder, market_map):
    """市場別：先查 code.xlsx，再用資料夾內既有月檔 BOM 判別，最後預設上市。"""
    mk = market_map.get(code)
    if mk in ("上市", "上櫃"):
        return mk
    pat = re.compile(rf"{re.escape(code)}_\d{{6}}\.csv$")
    for f in sorted(os.listdir(folder)) if os.path.isdir(folder) else []:
        if pat.search(f):
            with open(os.path.join(folder, f), "rb") as fh:
                return "上櫃" if fh.read(3) == b"\xef\xbb\xbf" else "上市"
    return "上市"


def redownload(code, market, y, m, path):
    """整月重抓並覆蓋 path；回傳 (是否寫入, 寫入列數)。"""
    if market == "上櫃":
        rows = tpex.fetch_month(code, y, m)      # 連線失敗回 None、無資料回 []
        if rows:
            tpex.save_csv(path, rows)
            return True, len(rows)
        return False, 0
    raw = twse.fetch_csv(code, y, m)             # 原始 Big5 bytes
    if twse.has_data(raw):
        with open(path, "wb") as f:
            f.write(raw)
        return True, sum(1 for _ in csv.reader(io.StringIO(raw.decode("big5", errors="ignore")))
                         if _ and parse_roc(_[0]))
    return False, 0


def main():
    ap = argparse.ArgumentParser(description="檢查指定月份個股月檔是否完整，殘缺就整月重抓覆蓋")
    ap.add_argument("month", help="目標月份 YYYY-MM，如 2026-07")
    ap.add_argument("--out", default=r"H:\data", help=r"股價根目錄（預設 H:\data）")
    ap.add_argument("--only", nargs="*", help="只檢查指定代碼（預設掃全部已存在資料夾）")
    ap.add_argument("--xlsx", default="code.xlsx", help="市場別對照表（預設 code.xlsx，選用）")
    ap.add_argument("--delay", type=float, default=4.0, help="重抓時每請求間隔秒數（預設 4）")
    ap.add_argument("--dry-run", action="store_true", help="只報告缺哪些、不實際下載")
    args = ap.parse_args()

    y, m = (int(x) for x in args.month.split("-"))
    ym = f"{y}{m:02d}"

    days = trading_days(y, m)
    if not days:
        raise SystemExit(f"{args.month}：FMTQIK 取不到交易日（可能是未來月份或當月尚無交易）。")
    last = days[-1]
    print(f"{args.month}：交易日 {len(days)} 天，最後交易日＝民國 {last[0]}/{last[1]:02d}/{last[2]:02d}")

    codes = args.only or existing_codes(args.out)
    if not codes:
        raise SystemExit(f"在 {args.out} 找不到任何個股資料夾。")
    market_map = load_market_map(args.xlsx)
    print(f"檢查 {len(codes)} 檔｜根目錄 {args.out}"
          + ("｜(dry-run 不下載)" if args.dry_run else ""))

    complete = missing = short = fixed = still = 0
    for code in codes:
        folder = os.path.join(args.out, code)
        path = os.path.join(folder, f"{code}_{ym}.csv")
        mx = file_max_date(path)

        if mx is not None and mx >= last:            # 已到最後交易日 → 完整
            complete += 1
            continue
        if mx is None:
            missing += 1
            reason = "月檔不存在" if not os.path.exists(path) else "月檔無資料列"
        else:
            short += 1
            reason = f"最大日期 {mx[0]}/{mx[1]:02d}/{mx[2]:02d} < 最後交易日"

        if args.dry_run:
            print(f"  [缺] {code}（{reason}）")
            continue

        market = resolve_market(code, folder, market_map)
        os.makedirs(folder, exist_ok=True)
        ok, n = redownload(code, market, y, m, path)
        if ok:
            new_mx = file_max_date(path)
            reached = new_mx is not None and new_mx >= last
            fixed += 1
            if not reached:
                still += 1
            tail = "" if reached else "（重抓後仍未達最後交易日，可能停牌/下市）"
            print(f"  [重抓] {code}（{market}）{reason} → {n} 列{tail}")
        else:
            print(f"  [失敗] {code}（{market}）{reason} → 來源無資料/連線失敗")
        time.sleep(args.delay)

    print(f"\n完成：完整 {complete}、缺檔 {missing}、殘缺 {short}"
          + (f"；已重抓 {fixed}" + (f"（其中 {still} 檔重抓後仍不足）" if still else "")
             if not args.dry_run else "（dry-run 未下載）"))


if __name__ == "__main__":
    main()
