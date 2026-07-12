r"""batch_download.py — 讀 code.xlsx，依「市場別」自動分流逐檔下載。
   上市 → download_twse_csv.py（Big5 CSV）
   上櫃 → download_tpex_csv.py（UTF-8 CSV）

每檔起抓月份會「看上市櫃日期」自動決定：
   起抓月 = max(上市櫃日期的月份, 該市場 floor)
   floor 分市場：上市 = --start（預設 2010-01）、上櫃 = --start-otc（預設 1994-01）
   → 上市櫃日期晚於 floor 就從上市櫃當月開始；早於就從 floor 開始。
   這樣不會對每檔都硬抓一堆上市前的「無資料」月份。

用法：
  python batch_download.py                       # 讀 code.xlsx，全部下載到 H:\data
  python batch_download.py --limit 2             # 只跑前 2 檔（手動測試用，推薦先這樣）
  python batch_download.py --xlsx code.xlsx --col 股票代碼 --market-col 市場別 --date-col 上市櫃日期
  python batch_download.py --out "H:\data" --start 2010-01 --start-otc 1994-01 --delay 4

提醒：
- 需要 Excel 有「市場別」欄（值為 上市/上櫃）才能自動分流；沒有的話全部當『上市』走 TWSE。
- 需要 Excel 有「上市櫃日期」欄才能依上市日起抓；沒有的話全部從各自 floor 開始（等同舊行為）。
- floor（最早回溯月份）分市場：上市 = --start（預設 2010-01）、上櫃 = --start-otc（預設 1994-01）；
  只有上市櫃日期比 floor 早的檔，才會被拉回到 floor。
- 逐檔逐月很慢：全市場 ~1900 檔會跑非常久。建議先 --limit 測。
- 可中途停、可重跑：下載器會自動跳過已下載的月份。
- 上市/上櫃單位不同（股·元 vs 張·千元），合併分析時要換算。
"""
import argparse
import os
import re
import subprocess
import sys

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
DOWNLOADERS = {                                       # 市場別 → 對應下載器
    "上市": os.path.join(HERE, "download_twse_csv.py"),
    "上櫃": os.path.join(HERE, "download_tpex_csv.py"),
}


def read_rows(xlsx, code_col, market_col, date_col):
    """回傳 ([(代碼, 市場別, 上市櫃日期)], has_date)，去重、保留順序。"""
    df = pd.read_excel(xlsx, dtype=str)               # dtype=str：保留 0050 前導零
    if code_col not in df.columns:
        raise SystemExit(f"找不到代碼欄「{code_col}」；Excel 現有欄位：{list(df.columns)}")
    has_market = market_col in df.columns
    if not has_market:
        print(f"⚠️ 找不到市場別欄「{market_col}」→ 全部當『上市』處理（走 TWSE）。")
    has_date = date_col in df.columns
    if not has_date:
        print(f"⚠️ 找不到上市櫃日期欄「{date_col}」→ 全部從 --start 開始抓。")

    seen, rows = set(), []
    for _, r in df.iterrows():
        code = str(r[code_col]).strip()
        if not code or code.lower() == "nan" or code in seen:
            continue
        market = str(r[market_col]).strip() if has_market else "上市"
        list_date = str(r[date_col]).strip() if has_date else ""
        seen.add(code)
        rows.append((code, market, list_date))
    return rows, has_date


def parse_ym(s):
    y, m = s.split("-")
    return int(y), int(m)


def effective_start(list_date, floor_ym):
    """該檔起抓月份(YYYY-MM) = max(上市櫃日期的月份, floor)。
       list_date 形如 '1994/09/05'（西元）；解析不出來就用 floor。"""
    fy, fm = floor_ym
    parts = re.split(r"[/\-.]", list_date.strip()) if list_date else []
    if len(parts) >= 2 and parts[0].isdigit() and parts[1].isdigit():
        ly, lm = int(parts[0]), int(parts[1])
        if (ly, lm) > (fy, fm):                       # 上市櫃日期較晚 → 從上市櫃當月起
            return f"{ly:04d}-{lm:02d}"
    return f"{fy:04d}-{fm:02d}"                        # 較早/無日期 → 從 floor 起


def main():
    ap = argparse.ArgumentParser(description="批次：依市場別自動分流下載（上市→TWSE、上櫃→TPEx）")
    ap.add_argument("--xlsx", default="code.xlsx", help="輸入 Excel（預設 code.xlsx）")
    ap.add_argument("--col", default="股票代碼", help="代碼欄位名（預設 股票代碼）")
    ap.add_argument("--market-col", default="市場別", help="市場別欄位名（預設 市場別）")
    ap.add_argument("--date-col", default="上市櫃日期", help="上市櫃日期欄位名（預設 上市櫃日期）")
    ap.add_argument("--out", default=r"H:\data", help=r"下載輸出根目錄（預設 H:\data）")
    ap.add_argument("--start", default="2010-01", help="上市最早回溯月份 YYYY-MM（floor，預設 2010-01）")
    ap.add_argument("--start-otc", default="1994-01", help="上櫃最早回溯月份 YYYY-MM（floor，預設 1994-01）")
    ap.add_argument("--end", default="2026-07", help="結束月份 YYYY-MM（預設本月）")
    ap.add_argument("--delay", type=float, default=4.0, help="每請求間隔秒數")
    ap.add_argument("--limit", type=int, default=0, help="只跑前 N 檔（0=全部；手動測試用）")
    args = ap.parse_args()

    floor_listed = parse_ym(args.start)        # 上市 floor
    floor_otc = parse_ym(args.start_otc)        # 上櫃 floor
    rows, has_date = read_rows(args.xlsx, args.col, args.market_col, args.date_col)
    if args.limit > 0:
        rows = rows[:args.limit]
    total = len(rows)
    listed = sum(1 for _, mk, _ in rows if mk == "上市")
    otc = sum(1 for _, mk, _ in rows if mk == "上櫃")
    print(f"從 {args.xlsx} 讀到 {total} 檔（上市 {listed}、上櫃 {otc}、其他 {total - listed - otc}）→ {args.out}")
    print(f"起抓 floor：上市 {args.start}、上櫃 {args.start_otc}；每檔取 max(上市櫃日期, 該市場 floor)"
          + ("" if has_date else "（無日期欄→全部用 floor）"))

    done = skipped = 0
    for i, (code, market, list_date) in enumerate(rows, 1):
        script = DOWNLOADERS.get(market)
        if not script:                                # 興櫃等不支援 → 跳過
            print(f"[{i}/{total}] {code}（市場別「{market}」不支援，跳過）")
            skipped += 1
            continue
        floor = floor_otc if market == "上櫃" else floor_listed
        start = effective_start(list_date, floor)
        tag = "TWSE" if market == "上市" else "TPEx"
        print(f"\n########## [{i}/{total}] {code}（{market} / {tag}）start={start}（上市櫃 {list_date or '—'}）##########")
        cmd = [sys.executable, script, code,
               "--out", args.out, "--start", start, "--delay", str(args.delay)]
        if args.end:
            cmd += ["--end", args.end]
        subprocess.run(cmd)                           # 一檔一個子程序；某檔失敗不中斷整批
        done += 1

    print(f"\n批次完成：處理 {done} 檔、跳過(市場別不支援) {skipped} 檔。")


if __name__ == "__main__":
    main()
