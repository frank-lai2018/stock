r"""fetch_etf_nav.py — 抓 ETF 每日淨值/規模，入庫 etf_daily（折溢價由 App 用市價計算）。

來源：mis.twse.com.tw 揭露淨值 all_etf.txt（單一請求、全 ETF）。
   欄位：a=代碼、b=名稱、c=發行受益權單位數、d=單位變動、e=當日淨值(揭露/估計)、
         f=前一交易日淨值、g=淨值漲跌%、i=日期(YYYYMMDD)。
   規模 AUM = 單位數 × 淨值。折溢價不在此存，App 讀取時用 price_daily 收盤價算。

限制：此源為「當日揭露」，收盤後(建議傍晚)擷取，e≈當日官方淨值；無歷史（只能往後每日累積）。

連線：環境變數 DATABASE_URL 或 --dsn。
用法：
  python fetch_etf_nav.py                     # 抓當日、入庫 + 存快照 H:\data\ETF_NAV\YYYYMMDD.csv
  python fetch_etf_nav.py --dry-run           # 只抓解析、印統計，不寫 DB
  python fetch_etf_nav.py --out "H:\data"     # 快照根目錄
"""
import argparse
import csv
import json
import os
import ssl
import urllib.request
from datetime import date

URL = "https://mis.twse.com.tw/stock/data/all_etf.txt"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"

CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE          # 證交所憑證瑕疵，沿用專案既有做法


def http_get(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=45, context=CTX) as r:
        return r.read().decode("utf-8", errors="ignore")


def _num(v):
    try:
        f = float(str(v).replace(",", "").strip())
        return f
    except (TypeError, ValueError):
        return None


def collect_records(obj):
    """遞迴蒐集所有含 a(代碼)+e(淨值) 的紀錄，容忍 {msgArray:[...]} 或扁平陣列。"""
    out = []
    if isinstance(obj, dict):
        if "a" in obj and ("e" in obj or "f" in obj):
            out.append(obj)
        else:
            for v in obj.values():
                out += collect_records(v)
    elif isinstance(obj, list):
        for v in obj:
            out += collect_records(v)
    return out


def parse(text):
    """回傳 [(stock_id, trade_date, nav, prev_nav, units, aum, nav_chg_pct)]。"""
    recs = collect_records(json.loads(text))
    rows, seen = [], set()
    for r in recs:
        code = str(r.get("a", "")).strip()
        d = str(r.get("i", "")).strip()
        if not code or len(d) != 8 or code in seen:
            continue
        seen.add(code)
        td = f"{d[0:4]}-{d[4:6]}-{d[6:8]}"
        nav = _num(r.get("e"))
        prev = _num(r.get("f"))
        units = _num(r.get("c"))
        chg = _num(r.get("g"))
        aum = (units * nav) if (units is not None and nav is not None) else None
        rows.append((code, td, nav, prev, units, aum, chg))
    return rows


UPSERT = (
    "INSERT INTO etf_daily (stock_id, trade_date, nav, prev_nav, units, aum, nav_chg_pct) "
    "VALUES %s ON CONFLICT (stock_id, trade_date) DO UPDATE SET "
    "nav=EXCLUDED.nav, prev_nav=EXCLUDED.prev_nav, units=EXCLUDED.units, "
    "aum=EXCLUDED.aum, nav_chg_pct=EXCLUDED.nav_chg_pct")


def main():
    ap = argparse.ArgumentParser(description="抓 ETF 每日淨值/規模入庫 etf_daily")
    ap.add_argument("--dsn", default=os.environ.get("DATABASE_URL", ""), help="PostgreSQL 連線字串")
    ap.add_argument("--out", default=r"H:\data", help=r"快照根目錄（存 ETF_NAV\YYYYMMDD.csv）")
    ap.add_argument("--dry-run", action="store_true", help="只解析印統計，不寫 DB")
    args = ap.parse_args()

    rows = parse(http_get(URL))
    if not rows:
        raise SystemExit("未解析到任何 ETF 淨值（來源格式可能變動）。")
    td = rows[0][1]
    print(f"解析 {len(rows)} 檔 ETF（資料日 {td}）")
    sample = rows[:3]
    for s in sample:
        print(f"  {s[0]}  nav={s[2]}  prev={s[3]}  units={s[4]}  aum={s[5]}")

    # 存快照
    if args.out:
        d8 = td.replace("-", "")
        path = os.path.join(args.out, "ETF_NAV", f"{d8}.csv")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(["stock_id", "trade_date", "nav", "prev_nav", "units", "aum", "nav_chg_pct"])
            w.writerows(rows)
        print(f"快照 → {path}")

    if args.dry_run:
        print("（dry-run：不寫 DB）")
        return
    import psycopg2
    if not args.dsn:
        raise SystemExit("需要 --dsn 或環境變數 DATABASE_URL")
    conn = psycopg2.connect(args.dsn)
    cur = conn.cursor()
    cur.execute("SELECT stock_id FROM stock")
    valid = {r[0] for r in cur.fetchall()}
    use = [r for r in rows if r[0] in valid]          # 只入庫 stock 主檔有的（避免外鍵/雜代碼）
    from psycopg2.extras import execute_values
    execute_values(cur, UPSERT, use)
    conn.commit()
    print(f"入庫 etf_daily：{len(use)} 檔（略過 {len(rows) - len(use)} 檔非主檔代碼）")
    cur.close(); conn.close()


if __name__ == "__main__":
    main()
