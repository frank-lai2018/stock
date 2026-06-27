"""FinMind 抓 2330 還原日K + 月營收 + 三大法人 → PostgreSQL。
對應 skill_gap_checklist #1（Python）。最小可跑，先求正確（還原價 / 公告日）。

用法：python fetch_finmind.py --stock 2330 --start 2024-01-01
"""
import argparse
import os
import pathlib

import pandas as pd
from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from FinMind.data import DataLoader

load_dotenv()
DB_URL = os.environ["DATABASE_URL"]
TOKEN = os.environ["FINMIND_TOKEN"]
engine = create_engine(DB_URL)


def apply_schema():
    """套用 schema.sql（冪等，重跑無妨）。"""
    sql = pathlib.Path(__file__).with_name("schema.sql").read_text(encoding="utf-8")
    with engine.begin() as conn:
        for stmt in (s.strip() for s in sql.split(";")):
            if stmt:
                conn.execute(text(stmt))


def replace_rows(df: pd.DataFrame, table: str, stock_id: str):
    """同一檔先刪再插：重跑不會 PK 衝突（之後可改成 upsert）。"""
    if df is None or df.empty:
        print(f"  [skip] {table} 無資料")
        return
    with engine.begin() as conn:
        conn.execute(text(f"DELETE FROM {table} WHERE stock_id = :s"), {"s": stock_id})
    df.to_sql(table, engine, if_exists="append", index=False)
    print(f"  [ok]  {table}: {len(df)} 列")


def fetch_prices(api, stock_id, start):
    # 地基①：用「還原股價」算報酬/技術指標才不會錯
    df = api.taiwan_stock_daily_adj(stock_id=stock_id, start_date=start)
    if df.empty:
        return df
    df = df.rename(columns={"max": "high", "min": "low", "Trading_Volume": "volume"})
    return df[["stock_id", "date", "open", "high", "low", "close", "volume"]]


def fetch_revenue(api, stock_id, start):
    df = api.taiwan_stock_month_revenue(stock_id=stock_id, start_date=start)
    if df.empty:
        return df
    df = df.rename(columns={"revenue_month": "rev_m"})  # FinMind 的 revenue_month 是「月份(int)」
    df["revenue_month"] = pd.to_datetime(
        dict(year=df["revenue_year"], month=df["rev_m"], day=1)
    )
    # 地基②：公告日近似＝次月 10 日（認真回測請換實際公告日，否則前視偏差）
    df["announce_date"] = df["revenue_month"] + pd.offsets.MonthBegin(1) + pd.Timedelta(days=9)
    return df[["stock_id", "revenue_month", "revenue", "announce_date"]]


def fetch_institutional(api, stock_id, start):
    df = api.taiwan_stock_institutional_investors(stock_id=stock_id, start_date=start)
    if df.empty:
        return df
    df["net"] = df["buy"] - df["sell"]            # 買賣超(股)
    df = df.rename(columns={"name": "investor"})
    return df[["stock_id", "date", "investor", "net"]]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stock", default="2330")
    ap.add_argument("--start", default="2024-01-01")
    args = ap.parse_args()

    apply_schema()
    api = DataLoader()
    api.login_by_token(api_token=TOKEN)

    print(f"抓取 {args.stock}（{args.start} 起）…")
    replace_rows(fetch_prices(api, args.stock, args.start), "price_daily", args.stock)
    replace_rows(fetch_revenue(api, args.stock, args.start), "monthly_revenue", args.stock)
    replace_rows(fetch_institutional(api, args.stock, args.start), "institutional", args.stock)
    print("完成。檢查：psql \"$DATABASE_URL\" -c 'SELECT count(*) FROM price_daily;'")


if __name__ == "__main__":
    main()
