r"""update_chips.py — 從證交所/櫃買「當日全市場」端點抓 法人買賣超 / 融資融券 / PER，
直接 upsert 進 PostgreSQL（inst_trades / margin_trading / valuation_daily）。

路線 B：一天只需 6 個請求（上市3 + 上櫃3）即可更新全市場，取代 FinMind 逐檔（~8000 請求）。
不經 CSV、直接入庫；沿用 load_to_db 的 TABLES / upsert / num / bigint，單位與現有資料一致
（法人=股、融資=張、PER=倍，均免換算，已與 DB 現值核對）。

只灌「已存在於 stock 表」的代號（其餘如部分 ETF 無基本資料 → 跳過，避免外鍵錯誤）。

連線：設環境變數 DATABASE_URL 或帶 --dsn。
用法：
  python update_chips.py --date 2026-07-06 --dsn "postgresql://frank:pwd@localhost:5432/twstock"
  python update_chips.py --start 2026-07-06 --end 2026-07-09 --dsn ...   # 回補一段區間（非交易日自動略過）
  python update_chips.py --date 2026-07-06 --dry-run                     # 不寫 DB，只印各表抓到幾列
"""
import argparse
import csv
import io
import json
import ssl
import time
import urllib.request
from datetime import date, timedelta

import load_to_db as L   # 重用 TABLES / upsert / num / bigint

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE          # 證交所憑證瑕疵，沿用專案既有做法

import re
CODE_RE = re.compile(r"^\d{4,6}[A-Z]?$")

TWSE_T86 = "https://www.twse.com.tw/rwd/zh/fund/T86?date={ymd}&selectType=ALLBUT0999&response=csv"
TWSE_MARGN = "https://www.twse.com.tw/rwd/zh/marginTrading/MI_MARGN?date={ymd}&selectType=ALL&response=csv"
TWSE_BWIBBU = "https://www.twse.com.tw/rwd/zh/afterTrading/BWIBBU_d?date={ymd}&selectType=ALL&response=csv"
TPEX_INST = "https://www.tpex.org.tw/www/zh-tw/insti/dailyTrade?type=Daily&sect=EW&date={slash}&response=json"
TPEX_MARGN = "https://www.tpex.org.tw/www/zh-tw/margin/balance?date={slash}&response=json"
# 櫃買 PER：by-date www 端點已全數 404；改用 OpenAPI（但只回「最新一天」）
TPEX_PER_OPENAPI = "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_peratio_analysis"


def http_get(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=45, context=CTX) as r:
        return r.read()


def http_json(url):
    return json.loads(http_get(url).decode("utf-8", "ignore"))


def tpex_data(j):
    """TPEx 回傳 → tables[0].data（list of list）；無則空 list。"""
    tables = j.get("tables") if isinstance(j, dict) else None
    if tables:
        return tables[0].get("data") or []
    return j.get("data") or [] if isinstance(j, dict) else []


def clean_code(s):
    return str(s).strip().lstrip("=").strip().strip('"').strip()


def twse_rows(raw, encoding="big5"):
    """Big5 CSV → list[cells]（csv 已處理引號）。"""
    return list(csv.reader(io.StringIO(raw.decode(encoding, "ignore"))))


# ---------- 各資料集解析（回傳符合 load_to_db.TABLES 欄位順序的 rows）----------

def parse_inst_twse(raw, d):
    """T86：foreign_net=4, foreign_dealer_net=7, trust_net=10, dealer_self_net=14, dealer_hedge_net=17。"""
    out = []
    for c in twse_rows(raw):
        if len(c) < 19:
            continue
        code = clean_code(c[0])
        if not CODE_RE.match(code):
            continue
        out.append((code, d, L.bigint(c[4]), L.bigint(c[7]), L.bigint(c[10]), L.bigint(c[14]), L.bigint(c[17])))
    return out


def parse_inst_tpex(data, d):
    """TPEx 法人：foreign_net=4, foreign_dealer_net=7, trust_net=13, dealer_self_net=16, dealer_hedge_net=19。"""
    out = []
    for r in data:
        if len(r) < 20:
            continue
        code = clean_code(r[0])
        if not CODE_RE.match(code):
            continue
        out.append((code, d, L.bigint(r[4]), L.bigint(r[7]), L.bigint(r[13]), L.bigint(r[16]), L.bigint(r[19])))
    return out


def parse_margin_twse(raw, d):
    """MI_MARGN 個股段：margin_balance=6, short_balance=12, margin_buy=2, margin_sell=3, short_sell=9, short_buy=8。"""
    out, started = [], False
    for c in twse_rows(raw):
        if not c:
            continue
        head = clean_code(c[0])
        if head == "代號":                     # 個股彙總段的欄名列 → 之後才是個股
            started = True
            continue
        if not started or len(c) < 13:
            continue
        code = clean_code(c[0])
        if not CODE_RE.match(code):
            continue
        out.append((code, d, L.bigint(c[6]), L.bigint(c[12]), L.bigint(c[2]), L.bigint(c[3]), L.bigint(c[9]), L.bigint(c[8])))
    return out


def parse_margin_tpex(data, d):
    """TPEx 融資融券：margin_balance=6, short_balance=14, margin_buy=3, margin_sell=4, short_sell=11, short_buy=12。"""
    out = []
    for r in data:
        if len(r) < 15:
            continue
        code = clean_code(r[0])
        if not CODE_RE.match(code):
            continue
        out.append((code, d, L.bigint(r[6]), L.bigint(r[14]), L.bigint(r[3]), L.bigint(r[4]), L.bigint(r[11]), L.bigint(r[12])))
    return out


def parse_per_twse(raw, d):
    """BWIBBU_d：dividend_yield=3, per=5, pbr=6（'-' → None）。"""
    out = []
    for c in twse_rows(raw):
        if len(c) < 7:
            continue
        code = clean_code(c[0])
        if not CODE_RE.match(code):
            continue
        out.append((code, d, L.num(c[5]), L.num(c[6]), L.num(c[3])))
    return out


def _iso_from_any(s):
    """把各種日期字串正規化成 YYYY-MM-DD：民國 1150709 / 西元 20260709 / 2026-07-09 / 2026/07/09。"""
    s = str(s).strip().replace("/", "-")
    if s.isdigit() and len(s) == 7:                      # 民國緊湊 1150709
        return f"{int(s[:3]) + 1911}-{s[3:5]}-{s[5:7]}"
    if s.isdigit() and len(s) == 8:                      # 西元緊湊 20260709
        return f"{s[:4]}-{s[4:6]}-{s[6:8]}"
    return s[:10]


def fetch_tpex_per(d):
    """OpenAPI 抓上櫃本益比（只回最新日）；只取 Date==d 的列（回補舊日期會自動不符→空，避免寫錯資料）。
       回傳 (原始 bytes, rows, api_date)；失敗回 (None, [], None)。"""
    try:
        raw = http_get(TPEX_PER_OPENAPI)
        arr = json.loads(raw.decode("utf-8", "ignore"))
    except Exception:
        return None, [], None
    if not isinstance(arr, list) or not arr:
        return None, [], None
    api_date = _iso_from_any(arr[0].get("Date", ""))
    rows = []
    for r in arr:
        if _iso_from_any(r.get("Date", "")) != d:        # 只接受與要求日期相符者
            continue
        code = clean_code(r.get("SecuritiesCompanyCode", ""))
        if not CODE_RE.match(code):
            continue
        rows.append((code, d, L.num(r.get("PriceEarningRatio")),
                     L.num(r.get("PriceBookRatio")), L.num(r.get("YieldRatio"))))
    return raw, rows, api_date


def save_raw(raw_root, d, name, data_bytes):
    """把原始回傳存成 <raw_root>\\<日期>\\<name>（每日全市場快照，備份/稽核用）。"""
    if not raw_root:
        return
    import os
    folder = os.path.join(raw_root, d)
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, name), "wb") as f:
        f.write(data_bytes)


# ---------- 單日主流程 ----------

def process_date(cur, conn, d, valid, dry, raw_root=None):
    """抓某交易日全市場三資料集 → 存原始快照 → 過濾合法代號 → upsert。
       回傳 dict{table: 列數} 或 None(非交易日)。"""
    y, m, dd = (int(x) for x in d.split("-"))
    ymd = f"{y}{m:02d}{dd:02d}"
    slash = f"{y}/{m:02d}/{dd:02d}"

    r_t86 = http_get(TWSE_T86.format(ymd=ymd))
    inst = parse_inst_twse(r_t86, d)
    if not inst:                                        # T86 空 → 該日非交易日/端點無資料（不存快照）
        return None
    save_raw(raw_root, d, "TWSE_T86.csv", r_t86)

    r_ti = http_get(TPEX_INST.format(slash=slash))
    save_raw(raw_root, d, "TPEX_inst.json", r_ti)
    inst += parse_inst_tpex(tpex_data(json.loads(r_ti.decode("utf-8", "ignore"))), d)

    r_tm = http_get(TWSE_MARGN.format(ymd=ymd))
    save_raw(raw_root, d, "TWSE_MI_MARGN.csv", r_tm)
    margin = parse_margin_twse(r_tm, d)
    r_pm = http_get(TPEX_MARGN.format(slash=slash))
    save_raw(raw_root, d, "TPEX_margin.json", r_pm)
    margin += parse_margin_tpex(tpex_data(json.loads(r_pm.decode("utf-8", "ignore"))), d)

    r_bw = http_get(TWSE_BWIBBU.format(ymd=ymd))
    save_raw(raw_root, d, "TWSE_BWIBBU.csv", r_bw)
    val = parse_per_twse(r_bw, d)
    per_raw, otc_per, api_date = fetch_tpex_per(d)
    if otc_per:
        save_raw(raw_root, d, "TPEX_per_openapi.json", per_raw)
        val += otc_per
    elif api_date and api_date != d:
        print(f"    （上櫃 PER：OpenAPI 最新為 {api_date}，≠ {d} → 本日上櫃 PER 略過；上市 PER 已抓）")
    else:
        print("    ⚠️ 上櫃 PER OpenAPI 取得失敗 → 本日上櫃 PER 略過（上市 PER 已抓）")

    result = {}
    for table, rows in [("inst_trades", inst), ("margin_trading", margin), ("valuation_daily", val)]:
        if valid:                                       # 外鍵過濾：只留 stock 表內代號（valid 空=不過濾，dry-run 用）
            rows = [r for r in rows if r[0] in valid]
        result[table] = len(rows)
        if not dry and rows:
            L.upsert(cur, table, rows)
    if not dry:
        conn.commit()
    return result


def daterange(start, end):
    s = date.fromisoformat(start)
    e = date.fromisoformat(end)
    while s <= e:
        yield s.isoformat()
        s += timedelta(days=1)


def main():
    import os
    ap = argparse.ArgumentParser(description="by-date 抓全市場法人/融資/PER 入庫")
    ap.add_argument("--date", help="單一交易日 YYYY-MM-DD")
    ap.add_argument("--start", help="區間起（含）YYYY-MM-DD")
    ap.add_argument("--end", help="區間迄（含）YYYY-MM-DD")
    ap.add_argument("--dsn", default=os.environ.get("DATABASE_URL", ""), help="PostgreSQL 連線字串")
    ap.add_argument("--raw-root", default=r"H:\data\Chips", help=r"每日全市場原始快照存放根目錄（預設 H:\data\Chips）")
    ap.add_argument("--no-raw", action="store_true", help="不存原始快照 CSV")
    ap.add_argument("--dry-run", action="store_true", help="不寫 DB，只印各表抓到幾列（仍會存快照，除非 --no-raw）")
    args = ap.parse_args()
    raw_root = None if args.no_raw else args.raw_root

    if args.date:
        dates = [args.date]
    elif args.start and args.end:
        dates = list(daterange(args.start, args.end))
    else:
        raise SystemExit("需 --date 或 --start/--end")
    for d in dates:
        date.fromisoformat(d)                           # 驗證格式

    conn = cur = None
    valid = None
    if not args.dry_run:
        import psycopg2
        if not args.dsn:
            raise SystemExit("需要 --dsn 或環境變數 DATABASE_URL")
        conn = psycopg2.connect(args.dsn)
        cur = conn.cursor()
        cur.execute("SELECT stock_id FROM stock")
        valid = {r[0] for r in cur.fetchall()}
    else:
        valid = _valid_from_dsn(args.dsn)               # dry-run 也盡量過濾（拿得到就拿）

    print(f"=== update_chips：{len(dates)} 個日期"
          + ("（dry-run）" if args.dry_run else f"，合法代號 {len(valid)} 檔")
          + (f"｜快照→{raw_root}" if raw_root else "｜不存快照") + " ===")
    for d in dates:
        try:
            res = process_date(cur, conn, d, valid or set(), args.dry_run, raw_root)
        except Exception as e:
            print(f"  {d}  ✗ 失敗：{e}")
            continue
        if res is None:
            print(f"  {d}  —（非交易日/無資料，略過）")
        else:
            print(f"  {d}  法人 {res['inst_trades']:>5}｜融資 {res['margin_trading']:>5}｜PER {res['valuation_daily']:>5}"
                  + ("（dry-run 未寫入）" if args.dry_run else " ✓"))
        time.sleep(1)                                    # 對端點客氣一點

    if cur:
        cur.close(); conn.close()
    print("=== 完成 ===")


def _valid_from_dsn(dsn):
    """dry-run 時若給了 dsn 就抓合法代號，否則回空集（不過濾）。"""
    if not dsn:
        return set()
    try:
        import psycopg2
        c = psycopg2.connect(dsn); cur = c.cursor()
        cur.execute("SELECT stock_id FROM stock")
        v = {r[0] for r in cur.fetchall()}
        cur.close(); c.close()
        return v
    except Exception:
        return set()


if __name__ == "__main__":
    main()
