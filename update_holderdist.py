r"""update_holderdist.py — 抓 TDCC 集保戶股權分散表（by-date 全市場）→ upsert 進 shareholding_dist。

來源：TDCC opendata（免 token）https://opendata.tdcc.com.tw/getOD.ashx?id=1-5
      回傳「最新一週」全市場、每檔 17 級（本表只存 1~15 級，16 差異/17 合計不存）。
      持股分級：1=1-999股 … 15=1,000,001股以上。千張大戶=15；400張大戶=12~15。

只回最新一週 → 每週跑一次即可累積歷史（每晚跑也行，idempotent）。歷史回補另需 FinMind（見 README）。
只灌存在於 stock 表的代號。沿用 load_to_db 的 upsert/num/bigint。

用法：
  python update_holderdist.py --dsn "postgresql://frank:pwd@localhost:5432/twstock"
  python update_holderdist.py --dry-run                     # 不寫 DB，只印日期與筆數
  python update_holderdist.py --dsn "..." --raw-root "H:\data\Holders"   # 另存原始快照
"""
import argparse
import csv
import io
import os
import ssl
import urllib.request

import load_to_db as L

URL = "https://opendata.tdcc.com.tw/getOD.ashx?id=1-5"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE


def fetch():
    req = urllib.request.Request(URL, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=90, context=CTX) as r:
        return r.read()


def ymd_to_iso(s):
    s = str(s).strip()
    return f"{s[:4]}-{s[4:6]}-{s[6:8]}" if len(s) == 8 and s.isdigit() else None


def parse(raw, valid):
    """TDCC CSV → shareholding_dist 欄序 rows；只留 level 1~15、合法代號。回傳 (rows, 日期集合)。"""
    rows, dates = [], set()
    rd = csv.reader(io.StringIO(raw.decode("utf-8", "ignore")))
    next(rd, None)                                       # 跳過標題
    for c in rd:
        if len(c) < 6:
            continue
        code = c[1].strip()
        if valid and code not in valid:
            continue
        try:
            level = int(c[2].strip())
        except ValueError:
            continue
        if not (1 <= level <= 15):                       # 16 差異/17 合計不存
            continue
        d = ymd_to_iso(c[0])
        if not d:
            continue
        rows.append((code, d, level, L.bigint(c[3]), L.bigint(c[4]), L.num(c[5])))
        dates.add(d)
    return rows, dates


def main():
    ap = argparse.ArgumentParser(description="TDCC 集保股權分散 → shareholding_dist")
    ap.add_argument("--dsn", default=os.environ.get("DATABASE_URL", ""))
    ap.add_argument("--raw-root", default="", help="另存原始 CSV 快照根目錄（預設不存）")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    conn = cur = None
    valid = set()
    if not args.dry_run:
        import psycopg2
        if not args.dsn:
            raise SystemExit("需要 --dsn 或環境變數 DATABASE_URL")
        conn = psycopg2.connect(args.dsn); cur = conn.cursor()
        cur.execute("SELECT stock_id FROM stock")
        valid = {r[0] for r in cur.fetchall()}
    elif args.dsn:
        try:
            import psycopg2
            c = psycopg2.connect(args.dsn); cc = c.cursor()
            cc.execute("SELECT stock_id FROM stock"); valid = {r[0] for r in cc.fetchall()}
            cc.close(); c.close()
        except Exception:
            pass

    raw = fetch()
    rows, dates = parse(raw, valid)
    print(f"=== update_holderdist｜資料日期 {sorted(dates)}｜{len(rows)} 列"
          + ("（dry-run）" if args.dry_run else "") + " ===")

    if args.raw_root and dates:
        os.makedirs(args.raw_root, exist_ok=True)
        fn = f"tdcc_holderdist_{sorted(dates)[-1].replace('-', '')}.csv"
        with open(os.path.join(args.raw_root, fn), "wb") as f:
            f.write(raw)
        print(f"  快照 → {os.path.join(args.raw_root, fn)}")

    if not args.dry_run and rows:
        L.upsert(cur, "shareholding_dist", rows)
        conn.commit(); cur.close(); conn.close()
        print(f"  已入庫 {len(rows)} 列（{len(rows)//15} 檔 × 15 級）")
    print("=== 完成 ===")


if __name__ == "__main__":
    main()
