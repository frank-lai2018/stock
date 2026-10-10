r"""update_holderdist.py — 抓 TDCC 集保戶股權分散表（by-date 全市場）→ upsert 進 shareholding_dist。

來源：TDCC opendata（免 token）https://opendata.tdcc.com.tw/getOD.ashx?id=1-5
      回傳「最新一週」全市場、每檔 17 級（1~15 分級、16 差異、17 合計）。
      持股分級：1=1-999股 … 15=1,000,001股以上。千張大戶=15；400張大戶=12~15。

只回最新一週 → 每週跑一次即可累積歷史（每晚跑也行，idempotent）。既存官方快照可用 --import-root 回補；未知歷史發布時間不倒填。
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
import hashlib
from datetime import datetime, timezone
from pathlib import Path

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
    if len(s) != 8 or not s.isdigit():
        return None
    try:
        return datetime.strptime(s, "%Y%m%d").date().isoformat()
    except ValueError:
        return None


def parse(raw, valid):
    """TDCC CSV → 分級、差異、合計；保留股數整數與官方比例。"""
    rows, dates = [], set()
    rd = csv.reader(io.StringIO(raw.decode("utf-8-sig")))
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
        if not (1 <= level <= 17):
            continue
        d = ymd_to_iso(c[0])
        if not d:
            continue
        rows.append((code, d, level, L.bigint(c[3]), L.bigint(c[4]), L.num(c[5])))
        dates.add(d)
    return rows, dates


def observations(rows, seen_at):
    groups = {}
    for code, d, level, holders, shares, pct in rows:
        groups.setdefault((code, d), []).append((level, holders, shares, pct))
    return [(code, d, seen_at, seen_at, seen_at,
             hashlib.sha256(repr(sorted(values)).encode()).hexdigest())
            for (code, d), values in groups.items()]


def ensure_observations(cur):
    cur.execute("""CREATE TABLE IF NOT EXISTS shareholding_observation (
        stock_id VARCHAR(10) NOT NULL, data_date DATE NOT NULL,
        first_seen_at TIMESTAMPTZ NOT NULL, last_seen_at TIMESTAMPTZ NOT NULL,
        values_seen_at TIMESTAMPTZ NOT NULL, published_at TIMESTAMPTZ,
        content_hash TEXT, PRIMARY KEY(stock_id,data_date))""")
    # 僅確認現在存在；不假造歷史公告日。之後收到修訂時更新 values_seen_at。
    cur.execute("""INSERT INTO shareholding_observation
        (stock_id,data_date,first_seen_at,last_seen_at,values_seen_at)
        SELECT DISTINCT stock_id,data_date,now(),now(),now() FROM shareholding_dist
        ON CONFLICT(stock_id,data_date) DO NOTHING""")


def save_observations(cur, rows, seen_at):
    from psycopg2.extras import execute_values
    execute_values(cur, """INSERT INTO shareholding_observation
      (stock_id,data_date,first_seen_at,last_seen_at,values_seen_at,content_hash) VALUES %s
      ON CONFLICT(stock_id,data_date) DO UPDATE SET
        last_seen_at=excluded.last_seen_at,
        values_seen_at=CASE WHEN shareholding_observation.content_hash IS DISTINCT FROM excluded.content_hash
          THEN excluded.values_seen_at ELSE shareholding_observation.values_seen_at END,
        content_hash=excluded.content_hash""", observations(rows, seen_at))


def main():
    ap = argparse.ArgumentParser(description="TDCC 集保股權分散 → shareholding_dist")
    ap.add_argument("--dsn", default=os.environ.get("DATABASE_URL", ""))
    ap.add_argument("--raw-root", default="", help="另存原始 CSV 快照根目錄（預設不存）")
    ap.add_argument("--import-root", default="", help="從既存 tdcc_holderdist_*.csv 回補（不連網；可得時間記為本次取得時間）")
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

    raw_files = sorted(Path(args.import_root).glob("tdcc_holderdist_*.csv")) if args.import_root else []
    if args.import_root and not raw_files:
        raise SystemExit("--import-root 找不到 tdcc_holderdist_*.csv")
    raw = fetch() if not args.import_root else None
    seen_at = datetime.now(timezone.utc)
    rows, dates = [], set()
    for payload in ([raw] if raw is not None else [p.read_bytes() for p in raw_files]):
        parsed, ds = parse(payload, valid)
        rows.extend(parsed); dates.update(ds)
    # 多個快照有重複日期時，最後檔案的版本優先。
    rows = list({r[:3]: r for r in rows}.values())
    print(f"=== update_holderdist｜資料日期 {sorted(dates)}｜{len(rows)} 列"
          + ("（dry-run）" if args.dry_run else "") + " ===")

    if args.raw_root and dates and raw is not None:
        os.makedirs(args.raw_root, exist_ok=True)
        fn = f"tdcc_holderdist_{sorted(dates)[-1].replace('-', '')}.csv"
        with open(os.path.join(args.raw_root, fn), "wb") as f:
            f.write(raw)
        print(f"  快照 → {os.path.join(args.raw_root, fn)}")

    if not args.dry_run and rows:
        ensure_observations(cur)
        L.upsert(cur, "shareholding_dist", rows)
        save_observations(cur, rows, seen_at)
        conn.commit(); cur.close(); conn.close()
        print(f"  已入庫 {len(rows)} 列（{len({r[0] for r in rows})} 檔；含差異與合計），已記錄可得時間")
    print("=== 完成 ===")


if __name__ == "__main__":
    main()
