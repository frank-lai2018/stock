r"""batch_fundamentals.py — 讀 code.xlsx 的股票代碼，批次抓 FinMind 各項個股資料集存 CSV。

直接重用 fetch_fundamentals.py 的邏輯，逐檔抓 → <輸出根目錄>\<股號>\（預設 H:\data\Fundamentals）
資料集（--datasets 選擇，預設 all）：月營收/損益表/資產負債表/現金流/股利/三大法人/融資融券/PER/外資持股。
特性：FinMind token、禮貌限流、可續抓（資料集層級：已抓過的 CSV 跳過；--refresh 強制重抓）。
       只看「股票代碼」欄（FinMind 同一組 API 上市/上櫃都吃，不需分市場）。

Token：FinMind 免費註冊拿 token（https://finmindtrade.com）。批次很多檔，務必帶 token，否則會被限流擋。
       設環境變數 FINMIND_TOKEN，或用 --token。

⚠️ 全部資料集 = 每檔 9 次 API 呼叫。1970 檔 × 9 ≈ 17700 次，在 600/hr 額度下要跑很久（~30hr、分多時段）。
   建議先挑重點：例如 --datasets institutional,per,balance,dividend。可中途停、重跑自動續抓。

需求：pip install pandas openpyxl
用法：
  python batch_fundamentals.py --xlsx 台股股票代碼NEW.xlsx --limit 5              # 先小測（全資料集）
  python batch_fundamentals.py --xlsx 台股股票代碼NEW.xlsx --datasets institutional,per
  python batch_fundamentals.py --xlsx 台股股票代碼NEW.xlsx --token xxxxx --delay 7
  python batch_fundamentals.py --xlsx 台股股票代碼NEW.xlsx --refresh              # 重抓更新
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
    ap.add_argument("--datasets", default="all",
                    help="要抓的資料集，逗號分隔；all=全部。可選：" + ",".join(ff.DATASETS))
    ap.add_argument("--out", default=r"H:\data\Fundamentals", help=r"輸出根目錄（預設 H:\data\Fundamentals）")
    ap.add_argument("--start", default="2010-01-01", help="起始日 YYYY-MM-DD（預設 2010-01-01）")
    ap.add_argument("--end", default=None, help="結束日 YYYY-MM-DD（預設今天）")
    ap.add_argument("--token", default=os.environ.get("FINMIND_TOKEN", ""),
                    help="FinMind token（或設環境變數 FINMIND_TOKEN）")
    ap.add_argument("--delay", type=float, default=2.0, help="每請求間隔秒數（預設 2）")
    ap.add_argument("--limit", type=int, default=0, help="只跑前 N 檔（0=全部；手動測試用）")
    ap.add_argument("--refresh", action="store_true", help="不跳過已存在的，全部重抓（更新用）")
    args = ap.parse_args()

    keys = ff.resolve_keys(args.datasets)
    end = args.end or date.today().isoformat()
    codes = read_codes(args.xlsx, args.col)
    if args.limit > 0:
        codes = codes[:args.limit]
    total = len(codes)
    if not args.token:
        print("⚠️ 未提供 FinMind token；批次很多檔幾乎一定會被限流擋。建議 --token 或設 FINMIND_TOKEN。")
    print(f"從 {args.xlsx} 讀到 {total} 檔｜範圍 {args.start} ~ {end}｜資料集 {keys} → {args.out}")

    done = skipped = 0
    for i, code in enumerate(codes, 1):
        # 快速跳過：這檔所有選定資料集的 CSV 都在了（非 refresh）
        all_done = all(os.path.exists(os.path.join(args.out, code, f"{code}_{k}.csv")) for k in keys)
        if not args.refresh and all_done:
            skipped += 1
            print(f"[{i}/{total}] {code} 已完成，跳過")
            continue
        print(f"\n########## [{i}/{total}] {code} ##########")
        ff.download_one(code, keys, args.start, end, args.out, args.token, args.delay, args.refresh)
        done += 1

    print(f"\n批次完成：處理 {done} 檔、跳過(全部已存在) {skipped} 檔。")


if __name__ == "__main__":
    main()
