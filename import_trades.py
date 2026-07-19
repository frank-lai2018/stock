r"""import_trades.py — 把券商「成交明細」CSV 匯入 trade_log（持股診斷用）。

來源 CSV（Big5，券商下載）欄位順序：
  0 成交日期  1 代號  2 名稱  3 交易種類(整股)  4 買/賣  5 交易類別(現股/現股當沖…)
  6 數量(股)  7 單價  8 價金  9 手續費  10 交易稅  … 19 損益  20 交割日  21 幣別
對應 trade_log：stock_id=代號、action=買→buy/賣→sell、trade_date=成交日期、
                shares=數量、price=單價、fee=手續費、tax=交易稅、trade_type=交易類別(現股/當沖…)。

連線：環境變數 DATABASE_URL 或 --dsn。
用法：
  python import_trades.py 202607.csv --dry-run            # 先驗證解析（不寫 DB）
  python import_trades.py 202607.csv --dsn "postgresql://frank:pwd@localhost:5432/twstock"
  python import_trades.py 202607.csv --clear              # 先清空 trade_log 再匯入（乾淨重灌）
"""
import argparse
import csv
import os
import re
from datetime import date

DATE_RE = re.compile(r"^\d{4}/\d{1,2}/\d{1,2}$")


def num(s):
    s = str(s).replace(",", "").replace('"', "").strip()
    if s in ("", "-", "--"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def parse(path, encoding):
    rows, skipped = [], 0
    with open(path, encoding=encoding, errors="ignore", newline="") as f:
        for c in csv.reader(f):
            if len(c) < 11 or not DATE_RE.match(c[0].strip()):   # 跳過表頭/合計/空列
                skipped += 1
                continue
            y, m, d = c[0].strip().split("/")
            td = f"{int(y):04d}-{int(m):02d}-{int(d):02d}"
            act_raw = c[4].strip()
            action = "buy" if "買" in act_raw else ("sell" if "賣" in act_raw else None)
            shares, price = num(c[6]), num(c[7])
            if action is None or not shares or price is None:
                skipped += 1
                continue
            rows.append((c[1].strip(), action, td, shares, price,
                         num(c[9]), num(c[10]), c[5].strip() or None))   # 末欄=交易類別 trade_type
    return rows, skipped


def main():
    ap = argparse.ArgumentParser(description="券商成交明細 CSV → trade_log")
    ap.add_argument("file", help="券商成交明細 CSV（Big5）")
    ap.add_argument("--dsn", default=os.environ.get("DATABASE_URL", ""), help="PostgreSQL 連線字串")
    ap.add_argument("--encoding", default="big5", help="CSV 編碼（預設 big5）")
    ap.add_argument("--clear", action="store_true", help="匯入前先清空 trade_log（乾淨重灌，避免重複）")
    ap.add_argument("--dry-run", action="store_true", help="只解析印統計，不寫 DB")
    args = ap.parse_args()

    rows, skipped = parse(args.file, args.encoding)
    if not rows:
        raise SystemExit("未解析到任何交易列（檢查檔案/編碼）。")
    buys = sum(1 for r in rows if r[1] == "buy")
    dmin, dmax = min(r[2] for r in rows), max(r[2] for r in rows)
    codes = sorted({r[0] for r in rows})
    print(f"解析 {len(rows)} 筆（買 {buys}、賣 {len(rows) - buys}）｜日期 {dmin} ~ {dmax}"
          f"｜{len(codes)} 檔｜略過 {skipped} 列")
    for r in rows[:5]:
        print(f"  {r[2]} {r[1]:4} {r[0]:7} {r[3]:>8.0f}股 @{r[4]}  費{r[5]} 稅{r[6]}  {r[7] or ''}")
    if len(rows) > 5:
        print(f"  …（共 {len(rows)} 筆）")

    if args.dry_run:
        print("（dry-run：未寫 DB）")
        return
    if not args.dsn:
        raise SystemExit("需要 --dsn 或環境變數 DATABASE_URL")
    import psycopg2
    from psycopg2.extras import execute_values
    conn = psycopg2.connect(args.dsn)
    cur = conn.cursor()
    cur.execute(
        "CREATE TABLE IF NOT EXISTS trade_log ("
        " id SERIAL PRIMARY KEY, stock_id VARCHAR(16) NOT NULL, action VARCHAR(4) NOT NULL,"
        " trade_date DATE NOT NULL, shares NUMERIC NOT NULL, price NUMERIC NOT NULL,"
        " fee NUMERIC, tax NUMERIC, trade_type VARCHAR(20), note VARCHAR(200),"
        " created_at TIMESTAMP DEFAULT now())")
    cur.execute("ALTER TABLE trade_log ADD COLUMN IF NOT EXISTS trade_type VARCHAR(20)")  # 舊表補欄
    if args.clear:
        cur.execute("DELETE FROM trade_log")
        print(f"已清空 trade_log（{cur.rowcount} 筆）")
    execute_values(cur,
                   "INSERT INTO trade_log (stock_id, action, trade_date, shares, price, fee, tax, trade_type) VALUES %s",
                   rows)
    conn.commit()
    print(f"匯入 trade_log：{len(rows)} 筆")
    # 提示：不在 stock 主檔的代碼（診斷會缺快照）
    cur.execute("SELECT stock_id FROM stock")
    known = {r[0] for r in cur.fetchall()}
    miss = [c for c in codes if c not in known]
    if miss:
        print(f"⚠️ 有 {len(miss)} 檔不在 stock 主檔（診斷會缺快照）：{', '.join(miss)}")
    cur.close(); conn.close()


if __name__ == "__main__":
    main()
