r"""backfill_market_margin.py — 從本機每日快照回補「大盤信用交易彙總」到 market_margin。

不連網路：直接讀 update_chips 已存的原始快照
  <chips-root>\<YYYY-MM-DD>\TWSE_MI_MARGN.csv   （上市，必要）
  <chips-root>\<YYYY-MM-DD>\TPEX_margin.json    （上櫃，可選）
解析彙總段（融資金額/融資張數/融券張數），並用該日 price_daily 收盤自算整體融資維持率，
upsert 進 market_margin（trade_date, market）。維持率需該日股價已入庫，否則存 NULL。

用法：
  python backfill_market_margin.py --dsn "postgresql://frank:pwd@localhost:5432/twstock"
  python backfill_market_margin.py --dsn ... --start 2026-07-01 --end 2026-07-28
"""
import argparse
import json
import os
from datetime import date

import psycopg2

import market_margin as mm


def main():
    ap = argparse.ArgumentParser(description="回補大盤信用交易彙總 market_margin（讀本機快照）")
    ap.add_argument("--dsn", default=os.environ.get("DATABASE_URL", ""), help="PostgreSQL 連線字串")
    ap.add_argument("--chips-root", default=r"H:\data\Chips", help=r"每日快照根目錄（預設 H:\data\Chips）")
    ap.add_argument("--start", help="起日（含）YYYY-MM-DD；不給＝掃全部快照")
    ap.add_argument("--end", help="迄日（含）YYYY-MM-DD")
    args = ap.parse_args()
    if not args.dsn:
        raise SystemExit("需要 --dsn 或環境變數 DATABASE_URL")

    dates = sorted(d for d in os.listdir(args.chips_root)
                   if os.path.isdir(os.path.join(args.chips_root, d)) and len(d) == 10 and d[4] == "-")
    if args.start:
        dates = [d for d in dates if d >= args.start]
    if args.end:
        dates = [d for d in dates if d <= args.end]

    conn = psycopg2.connect(args.dsn)
    cur = conn.cursor()
    mm.ensure_table(cur)
    conn.commit()

    ok = skip = 0
    for d in dates:
        folder = os.path.join(args.chips_root, d)
        tw_path = os.path.join(folder, "TWSE_MI_MARGN.csv")
        if not os.path.isfile(tw_path):
            skip += 1
            continue
        tw = mm.parse_twse(open(tw_path, "rb").read())
        tp = None
        tp_path = os.path.join(folder, "TPEX_margin.json")
        if os.path.isfile(tp_path):
            try:
                tp = mm.parse_tpex(json.load(open(tp_path, "r", encoding="utf-8")))
            except Exception:
                tp = None
        if not tw:
            print(f"  {d}  ✗ 上市彙總解析失敗，略過")
            skip += 1
            continue

        mv = mm.maint_ratios(cur, d)                 # {market: 擔保品市值(千元)}；無股價則空
        mm.upsert(cur, d, "TWSE", tw[0], tw[1], tw[2], mv.get("TWSE"))
        if tp:
            mm.upsert(cur, d, "TPEx", tp[0], tp[1], tp[2], mv.get("TPEx"))
        conn.commit()
        ok += 1
        tot_amt = tw[0] + (tp[0] if tp else 0)
        ratio = round(mv["TWSE"] / tw[0] * 100, 2) if mv.get("TWSE") else "—"
        print(f"  {d}  融資合計 {tot_amt / 1e5:,.2f} 億（上市 {tw[0] / 1e5:,.2f}）上市維持率 {ratio}%")

    cur.close()
    conn.close()
    print(f"=== 完成：寫入 {ok} 日、略過 {skip} 日 ===")


if __name__ == "__main__":
    main()
