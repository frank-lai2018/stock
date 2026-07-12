r"""update_prices.py — 更新「指定交易日」的全市場股價，併入各股月檔。

用當日全市場端點（各 1 個請求）抓某一交易日的全部收盤，然後把「當天那一列」併進
各股既有的 <輸出根目錄>\<股號>\<股號>_YYYYMM.csv（依日期去重、排序；已存在同日則覆蓋）。
   上市：TWSE MI_INDEX（type=ALLBUT0999）→ Big5 月檔格式
   上櫃：TPEx otc（type=EW）→ 換算成「張/千元」對齊月檔，UTF-8 月檔格式

預設只更新「已存在資料夾」的個股（＝你已下載過的普通股）；加 --all 則連新股/ETF 一起建檔。

特性：純標準函式庫（免 pip）、2 個請求更新全市場、可重跑（同日覆蓋、不重複）、遇非交易日自動略過。
用法：
  python update_prices.py                       # 更新「今天」→ H:\data
  python update_prices.py --date 2026-07-03
  python update_prices.py --date 2026-07-03 --out "H:\data" --all
"""
import argparse
import csv
import io
import json
import os
import re
import ssl
import urllib.request
from datetime import date

TWSE_URL = "https://www.twse.com.tw/exchangeReport/MI_INDEX?response=csv&date={ymd}&type=ALLBUT0999"
TPEX_URL = "https://www.tpex.org.tw/www/zh-tw/afterTrading/otc?date={y}/{m:02d}/{d:02d}&type=EW&response=json"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"

SSL_CTX = ssl.create_default_context()
SSL_CTX.check_hostname = False
SSL_CTX.verify_mode = ssl.CERT_NONE

DATE_RE = re.compile(r"^\s*\d{2,3}/\d{1,2}/\d{1,2}")   # 民國日期資料列
CODE_RE = re.compile(r"^\d{4,6}[A-Z]?$")               # 股票/ETF 代號

TWSE_HEADER = '"日期","成交股數","成交金額","開盤價","最高價","最低價","收盤價","漲跌價差","成交筆數","註記",'
TPEX_HEADER = "日期,成交股數(張),成交金額(千元),開盤,最高,最低,收盤,漲跌,成交筆數"


def http_get(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=40, context=SSL_CTX) as r:
        return r.read()


# ---------- 解析當日全市場 ----------

def parse_twse_daily(raw):
    """MI_INDEX CSV → {代號: cells}（只取個股區段）。"""
    text = raw.decode("big5", errors="ignore")
    rows, in_sec = {}, False
    for cells in csv.reader(io.StringIO(text)):
        if not cells:
            continue
        c0 = cells[0].strip().lstrip("=").strip('"').strip()
        if c0 == "證券代號":                        # 進入個股區段
            in_sec = True
            continue
        if in_sec and CODE_RE.match(c0) and len(cells) >= 11:
            rows[c0] = cells
    return rows


def parse_tpex_daily(raw):
    """TPEx otc JSON → {代號: row}。"""
    j = json.loads(raw.decode("utf-8", errors="ignore"))
    t = (j.get("tables") or [{}])[0]
    return {str(r[0]).strip(): r for r in (t.get("data") or []) if r}


# ---------- 組月檔資料列 ----------

def build_twse_line(roc_date, c):
    """MI_INDEX 一列 → 月檔 10 欄（日期,股數,金額,開,高,低,收,漲跌價差,筆數,註記）。"""
    if c[8].strip() in ("", "--", "---"):            # 無收盤（未交易）→ 不寫
        return None
    sign, mag = c[9].strip(), c[10].strip()
    ud = (sign + mag) if sign in ("+", "-") else mag
    vals = [roc_date, c[2], c[4], c[5], c[6], c[7], c[8], ud, c[3], ""]
    return ",".join('"' + str(v).strip() + '"' for v in vals) + ","


def _div1000(s):
    """股→張 / 元→千元；整數就印整數，非整數保留小數（避免科學記號 1e+07）。"""
    if not s:
        return "0"
    v = float(s) / 1000
    return str(int(v)) if v == int(v) else f"{v:.3f}".rstrip("0").rstrip(".")


def build_tpex_line(roc_date, r):
    """TPEx otc 一列（股/元）→ 月檔 9 欄，成交量換算成 張/千元。"""
    def n(x):
        return str(x).replace(",", "").strip()
    if n(r[2]) in ("", "--", "---"):                 # 無收盤
        return None
    vals = [roc_date, _div1000(n(r[7])), _div1000(n(r[8])),
            n(r[4]), n(r[5]), n(r[6]), n(r[2]), n(r[3]), n(r[9])]
    return ",".join(vals)


# ---------- 併入月檔 ----------

def merge(path, encoding, new_date, new_line, new_prefix):
    """把 new_line 併入月檔：依日期去重、排序，保留標題/欄名/尾註。"""
    rows, prefix, trailing, seen = {}, [], [], False
    if os.path.exists(path):
        for l in http_read(path, encoding):
            if not l.strip():
                continue
            c0 = l.split(",")[0].strip().strip('"').strip()
            if DATE_RE.match(c0):
                seen = True
                rows[c0] = l
            elif not seen:
                prefix.append(l)                     # 標題/欄名
            else:
                trailing.append(l)                   # 尾註
    else:
        prefix = new_prefix
    rows[new_date] = new_line                        # 新增/覆蓋當日
    out = prefix + [rows[k] for k in sorted(rows)] + trailing
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(("\r\n".join(out) + "\r\n").encode(encoding, errors="ignore"))


def http_read(path, encoding):
    with open(path, "rb") as f:
        return f.read().decode(encoding, errors="ignore").splitlines()


def existing_codes(out_root):
    """已下載過（有資料夾）的個股代號。"""
    if not os.path.isdir(out_root):
        return set()
    return {n for n in os.listdir(out_root)
            if os.path.isdir(os.path.join(out_root, n)) and CODE_RE.match(n)}


def run(date_iso, out=r"H:\data", include_all=False, only=None):
    """更新指定交易日全市場股價、併入各股月檔；回傳「有更新的股號」sorted list。
       include_all=True 連新股/ETF 建檔；only=可迭代股號 → 只更新這幾檔（其餘略過）。
       供 daily_update.py 直接呼叫（拿回更新清單，只對這些檔重算還原/入庫）。"""
    y, m, d = (int(x) for x in date_iso.split("-"))
    ymd = f"{y}{m:02d}{d:02d}"
    ym = f"{y}{m:02d}"
    roc = y - 1911
    roc_date = f"{roc}/{m:02d}/{d:02d}"
    keep = None if include_all else existing_codes(out)
    only = set(only) if only else None
    updated = []

    def want(code):
        return (only is None or code in only) and (keep is None or code in keep)

    # 上市（TWSE）
    tw = parse_twse_daily(http_get(TWSE_URL.format(ymd=ymd)))
    for code, cells in tw.items():
        if not want(code):
            continue
        line = build_twse_line(roc_date, cells)
        if line is None:
            continue
        name = cells[1].strip()
        title = f'"{roc}年{m:02d}月 {code} {name}            各日成交資訊"'
        merge(os.path.join(out, code, f"{code}_{ym}.csv"), "big5",
              roc_date, line, [title, TWSE_HEADER])
        updated.append(code)

    # 上櫃（TPEx）
    tp = parse_tpex_daily(http_get(TPEX_URL.format(y=y, m=m, d=d)))
    for code, row in tp.items():
        if not want(code):
            continue
        line = build_tpex_line(roc_date, row)
        if line is None:
            continue
        merge(os.path.join(out, code, f"{code}_{ym}.csv"), "utf-8-sig",
              roc_date, line, [TPEX_HEADER])
        updated.append(code)

    return sorted(set(updated))


def main():
    ap = argparse.ArgumentParser(description="更新指定交易日全市場股價，併入各股月檔")
    ap.add_argument("--date", default=date.today().isoformat(), help="交易日 YYYY-MM-DD（預設今天）")
    ap.add_argument("--out", default=r"H:\data", help=r"股價根目錄（預設 H:\data）")
    ap.add_argument("--all", action="store_true", help="連沒下載過的新股/ETF 也一起建檔（預設只更新已存在資料夾）")
    ap.add_argument("--only", nargs="*", help="只更新指定代碼（預設全市場）")
    args = ap.parse_args()

    if not args.all and args.only is None:
        print(f"只更新已存在的 {len(existing_codes(args.out))} 檔（--all 可含新股）")
    codes = run(args.date, args.out, args.all, args.only)
    y = int(args.date[:4])
    print(f"{args.date}（民國 {y - 1911}/{args.date[5:7]}/{args.date[8:10]}）：更新 {len(codes)} 檔"
          + ("" if codes else "（可能非交易日或端點無資料）"))


if __name__ == "__main__":
    main()
