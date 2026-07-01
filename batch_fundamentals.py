r"""batch_fundamentals.py — 讀 code.xlsx 的股票代碼，批次抓基本面（月營收 + 綜合損益表）存 CSV。

直接重用 fetch_fundamentals.py 的邏輯，逐檔抓 → <輸出根目錄>\<股號>\（預設 H:\data\Fundamentals）
特性：FinMind token、禮貌限流、可續抓（已抓過的檔預設跳過；--refresh 可強制重抓）。
       基本面不需分上市/上櫃，FinMind 同一組 API 兩種都吃，所以只看「股票代碼」欄。

Token：FinMind 免費註冊拿 token（https://finmindtrade.com）。批次很多檔，務必帶 token，否則會被限流擋。
       設環境變數 FINMIND_TOKEN，或用 --token。

需求：pip install pandas openpyxl
用法：
  python batch_fundamentals.py                                  # 讀 code.xlsx，全部 → H:\data\Fundamentals
  python batch_fundamentals.py --xlsx 台股股票代碼NEW.xlsx --limit 5   # 先小測
  python batch_fundamentals.py --token xxxxx --start 2010-01-01
  python batch_fundamentals.py --refresh                        # 不跳過、重抓更新已存在的
"""
import argparse
import os
from datetime import date

import pandas as pd

import fetch_fundamentals as ff                       # 重用單檔抓取邏輯


def read_codes(xlsx, code_col):
    """讀 Excel 的代碼欄 → 去重、保留順序的代碼清單。"""
    df = pd.read_excel(xlsx, dtype=str)               # dtype=str：保留 0050 前導零
    if code_col not in df.columns:
        raise SystemExit(f"找不到代碼欄「{code_col}」；Excel 現有欄位：{list(df.columns)}")
    seen, codes = set(), []
    for v in df[code_col]:
        c = str(v).strip()
        if c and c.lower() != "nan" and c not in seen:
            seen.add(c)
            codes.append(c)
    return codes


def main():
    ap = argparse.ArgumentParser(description="批次抓台股基本面（月營收 + 綜合損益表）存 CSV")
    ap.add_argument("--xlsx", default="code.xlsx", help="輸入 Excel（預設 code.xlsx）")
    ap.add_argument("--col", default="股票代碼", help="代碼欄位名（預設 股票代碼）")
    ap.add_argument("--out", default=r"H:\data\Fundamentals", help=r"輸出根目錄（預設 H:\data\Fundamentals）")
    ap.add_argument("--start", default="2010-01-01", help="起始日 YYYY-MM-DD（預設 2010-01-01）")
    ap.add_argument("--end", default=None, help="結束日 YYYY-MM-DD（預設今天）")
    ap.add_argument("--token", default=os.environ.get("FINMIND_TOKEN", ""),
                    help="FinMind token（或設環境變數 FINMIND_TOKEN）")
    ap.add_argument("--delay", type=float, default=2.0, help="每請求間隔秒數（預設 2）")
    ap.add_argument("--limit", type=int, default=0, help="只跑前 N 檔（0=全部；手動測試用）")
    ap.add_argument("--refresh", action="store_true", help="不跳過已存在的，全部重抓（更新用）")
    args = ap.parse_args()

    end = args.end or date.today().isoformat()
    codes = read_codes(args.xlsx, args.col)
    if args.limit > 0:
        codes = codes[:args.limit]
    total = len(codes)
    if not args.token:
        print("⚠️ 未提供 FinMind token；批次很多檔幾乎一定會被限流擋。建議 --token 或設 FINMIND_TOKEN。")
    print(f"從 {args.xlsx} 讀到 {total} 檔｜範圍 {args.start} ~ {end} → {args.out}")

    done = skipped = 0
    for i, code in enumerate(codes, 1):
        rev_path = os.path.join(args.out, code, f"{code}_revenue.csv")
        if not args.refresh and os.path.exists(rev_path):    # 可續抓：已抓過就跳過
            skipped += 1
            print(f"[{i}/{total}] {code} 已存在，跳過")
            continue
        print(f"\n########## [{i}/{total}] {code} ##########")
        ff.download_one(code, args.start, end, args.out, args.token, args.delay)
        done += 1

    print(f"\n批次完成：處理 {done} 檔、跳過(已存在) {skipped} 檔。")


if __name__ == "__main__":
    main()
