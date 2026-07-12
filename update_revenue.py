r"""update_revenue.py — 用官方 OpenAPI 抓「全體上市/上櫃月營收」直接 upsert 進 monthly_revenue。

注意：月營收各公司申報時間不一（雖規定次月 10 號前，仍有延後者），openapi 只含「查詢當下已申報」
      的公司，故建議每月 11~15 號間跑，並可間隔幾天再跑一次補上晚申報者（upsert 可重複執行）。

來源（皆回「最新一個月」全市場）：
  上市一般業  https://openapi.twse.com.tw/v1/opendata/t187ap05_L
  上市金融業  https://openapi.twse.com.tw/v1/opendata/t187ap05_P
  上櫃        https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap05_O

月份用「資料年月」（真實營收月，非公告月）；當月營收單位為仟元 → ×1000 對齊 DB 的元。
只灌存在於 stock 表的代號（興櫃等非上市櫃 → 過濾）。沿用 load_to_db 的 upsert/num/bigint/rev_available。

連線：--dsn 或環境變數 DATABASE_URL。
用法：
  python update_revenue.py --dsn "postgresql://frank:pwd@localhost:5432/twstock"
  python update_revenue.py --dry-run          # 不寫 DB，只印抓到的月份與筆數
  python update_revenue.py --raw-root "H:\data\Revenue"   # 另存原始 openapi 快照（預設不存）
"""
import argparse
import json
import os
import ssl
import urllib.request

import load_to_db as L

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE

SOURCES = [
    ("上市一般", "https://openapi.twse.com.tw/v1/opendata/t187ap05_L"),
    ("上市金融", "https://openapi.twse.com.tw/v1/opendata/t187ap05_P"),
    ("上櫃",     "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap05_O"),
]

F_YM = "資料年月"
F_CODE = "公司代號"
F_REV = "營業收入-當月營收"
F_MOM = "營業收入-上月比較增減(%)"
F_YOY = "營業收入-去年同月增減(%)"


def http_get(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=45, context=CTX) as r:
        return r.read()


def roc_ym_to_month(ym):
    """民國年月 '11506' → '2026-06-01'。"""
    s = str(ym).strip()
    if len(s) == 5 and s.isdigit():
        return f"{int(s[:3]) + 1911:04d}-{int(s[3:]):02d}-01"
    if len(s) == 6 and s.isdigit():                 # 容忍 6 位民國年月 '115006'? 少見；保底
        return f"{int(s[:3]) + 1911:04d}-{int(s[4:]):02d}-01"
    return None


def parse(rows, valid):
    """openapi 列 → monthly_revenue 欄序 rows；過濾非法代號。回傳 (rows, 月份集合)。"""
    out, months = [], set()
    for r in rows:
        code = str(r.get(F_CODE, "")).strip()
        if valid and code not in valid:
            continue
        mon = roc_ym_to_month(r.get(F_YM))
        if not mon:
            continue
        rev = L.bigint(r.get(F_REV))
        out.append((code, mon,
                    None if rev is None else rev * 1000,   # 仟元 → 元
                    L.num(r.get(F_MOM)), L.num(r.get(F_YOY)),
                    L.rev_available(mon)))
        months.add(mon)
    return out, months


def main():
    ap = argparse.ArgumentParser(description="OpenAPI 全市場月營收 → monthly_revenue")
    ap.add_argument("--dsn", default=os.environ.get("DATABASE_URL", ""))
    ap.add_argument("--raw-root", default="", help="另存原始 openapi 快照根目錄（預設不存）")
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
    elif args.dsn:                                   # dry-run 若給 dsn 也拿合法代號來過濾
        try:
            import psycopg2
            c = psycopg2.connect(args.dsn); cc = c.cursor()
            cc.execute("SELECT stock_id FROM stock"); valid = {r[0] for r in cc.fetchall()}
            cc.close(); c.close()
        except Exception:
            pass

    total = 0
    all_months = set()
    for name, url in SOURCES:
        try:
            raw = http_get(url)
            arr = json.loads(raw.decode("utf-8", "ignore"))
        except Exception as e:
            print(f"  [{name}] ✗ 取得失敗：{str(e)[:100]}")
            continue
        if not isinstance(arr, list):
            print(f"  [{name}] ✗ 非預期格式")
            continue
        if args.raw_root:
            os.makedirs(args.raw_root, exist_ok=True)
            fn = url.rstrip("/").split("/")[-1] + ".json"
            with open(os.path.join(args.raw_root, fn), "wb") as f:
                f.write(raw)
        rows, months = parse(arr, valid)
        all_months |= months
        total += len(rows)
        print(f"  [{name}] 原始 {len(arr)} 筆 → 入庫 {len(rows)} 筆｜月份 {sorted(months)}")
        if not args.dry_run and rows:
            L.upsert(cur, "monthly_revenue", rows)
    if not args.dry_run:
        conn.commit(); cur.close(); conn.close()

    print(f"=== {'dry-run 未寫入' if args.dry_run else '完成'}｜合計 {total} 筆｜涵蓋月份 {sorted(all_months)} ===")


if __name__ == "__main__":
    main()
