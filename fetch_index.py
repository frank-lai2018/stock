r"""fetch_index.py — 抓大盤指數（FinMind TaiwanStockTotalReturnIndex）存 CSV。

用途：算個股相對強弱(RS)、大盤多空濾網、beta 等需要大盤基準。
來源：FinMind TaiwanStockTotalReturnIndex（發行量加權股價「報酬」指數，含息；非價格指數）
存放：<輸出根目錄>\<代號>.csv（預設 H:\data\Index）

Token：同 fetch_fundamentals，建議帶 FinMind token。
需求：pip install pandas
用法：
  python fetch_index.py                       # 抓 TAIEX（加權報酬指數）→ H:\data\Index\TAIEX.csv
  python fetch_index.py --ids TAIEX --start 2010-01-01
"""
import argparse
import os
from datetime import date

import fetch_fundamentals as ff                       # 重用 fetch()


def main():
    ap = argparse.ArgumentParser(description="抓大盤指數（FinMind 報酬指數）存 CSV")
    ap.add_argument("--ids", default="TAIEX", help="指數代號，逗號分隔（預設 TAIEX 加權報酬指數）")
    ap.add_argument("--start", default="2010-01-01", help="起始日 YYYY-MM-DD（預設 2010-01-01）")
    ap.add_argument("--end", default=None, help="結束日 YYYY-MM-DD（預設今天）")
    ap.add_argument("--out", default=r"H:\data\Index", help=r"輸出根目錄（預設 H:\data\Index）")
    ap.add_argument("--token", default=os.environ.get("FINMIND_TOKEN", ""), help="FinMind token")
    ap.add_argument("--delay", type=float, default=2.0, help="每請求間隔秒數")
    args = ap.parse_args()

    end = args.end or date.today().isoformat()
    os.makedirs(args.out, exist_ok=True)
    if not args.token:
        print("⚠️ 未提供 FinMind token；免 token 額度很低。")
    for iid in [x.strip() for x in args.ids.split(",") if x.strip()]:
        df = ff.fetch("TaiwanStockTotalReturnIndex", iid, args.start, end, args.token)
        if not df.empty:
            path = os.path.join(args.out, f"{iid}.csv")
            df.sort_values("date").to_csv(path, index=False, encoding="utf-8-sig")
            print(f"  [存] {iid} {len(df)} 列 → {path}")
        else:
            print(f"  [無] {iid} 無資料")
    print("完成。")


if __name__ == "__main__":
    main()
