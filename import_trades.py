r"""import_trades.py — 把券商「成交明細」CSV 匯入 trade_log（持股診斷用）。

自動辨識兩種券商格式（依日期欄是「一個」還是「兩個日期」判斷）：

  格式 A（新，如 202607.csv）— 日期欄為單一成交日：
    0 成交日期  1 代號  2 名稱  3 交易種類(整股)  4 買/賣  5 交易類別(現股/當沖…)
    6 數量(股)  7 單價  8 價金  9 手續費  10 交易稅  … 19 損益  20 交割日  21 幣別

  格式 B（舊，如 202008.csv）— 日期欄為「成交日 交割日」兩日期、代號名稱合併、無損益：
    0 成交日 交割日  1 集中/櫃檯  2 幣別  3 買/賣  4 「代號 名稱」  5 股數  6 單價
    7 成交金額  8 手續費  9 代徵交易稅  … 12 客戶淨收(付)  （無交易類別/損益欄）
    → 皆為整股現股交割，trade_type 一律填「現股」、pnl 留空。

對應 trade_log：stock_id、action=買→buy/賣→sell、trade_date=成交日、
                shares、price=單價、fee=手續費、tax=交易稅、trade_type、pnl。

連線：環境變數 DATABASE_URL 或 --dsn。
用法：
  python import_trades.py 202008.csv --dry-run            # 先驗證解析（不寫 DB）
  python import_trades.py 202008.csv --dsn "postgresql://frank:pwd@localhost:5432/twstock"
  python import_trades.py 202607.csv --clear              # 先清空 trade_log 再匯入（乾淨重灌）
"""
import argparse
import csv
import io
import os
import re
from datetime import date

DATE_RE = re.compile(r"^\d{4}/\d{1,2}/\d{1,2}$")                       # 單一日期（格式 A）
# 成交日 交割日（格式 B）：兩日期分隔符各月不一（有的用空白「日 日」，有的用斜線「日 / 日」）
DATE2_RE = re.compile(r"^(\d{4}/\d{1,2}/\d{1,2})[\s/]+\d{4}/\d{1,2}/\d{1,2}$")


def num(s):
    s = str(s).replace(",", "").replace('"', "").strip()
    if s in ("", "-", "--"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _iso(dstr):
    y, m, d = dstr.split("/")
    return f"{int(y):04d}-{int(m):02d}-{int(d):02d}"


def _act(s):
    return "buy" if "買" in s else ("sell" if "賣" in s else None)


def _parse_a(c):
    """格式 A：單一成交日、代號/名稱分欄、有交易類別與損益。"""
    if len(c) < 11:
        return None
    action = _act(c[4])
    shares, price = num(c[6]), num(c[7])
    if action is None or not shares or price is None:
        return None
    pnl = num(c[19]) if (action == "sell" and len(c) > 19) else None   # 損益(賣出才有)
    return (c[1].strip(), action, _iso(c[0].strip()), shares, price,
            num(c[9]), num(c[10]), c[5].strip() or None, pnl)


def _parse_b(c, dstr):
    """格式 B：成交日+交割日同欄、代號名稱合併、無交易類別/損益。"""
    if len(c) < 9:
        return None
    action = _act(c[3])
    sec = c[4].strip().split()          # "2887 台新金" → ["2887", "台新金"]
    code = sec[0] if sec else ""
    shares, price = num(c[5]), num(c[6])
    if action is None or not shares or price is None or not code:
        return None
    return (code, action, _iso(dstr), shares, price,
            num(c[8]), num(c[9]), "現股", None)   # 舊格式皆整股現股；無損益


def _read_text(path, encoding):
    """讀檔文字。encoding='auto' 先試 UTF-8（含 BOM），失敗才退回 Big5/CP950。
    （券商月報有的給 UTF-8、有的給 Big5，故自動偵測。）"""
    if encoding and encoding != "auto":
        with open(path, encoding=encoding, errors="ignore", newline="") as f:
            return f.read(), encoding
    for enc in ("utf-8-sig", "big5", "cp950"):
        try:
            with open(path, encoding=enc, newline="") as f:   # strict：錯就換下一個
                return f.read(), enc
        except UnicodeDecodeError:
            continue
    with open(path, encoding="big5", errors="ignore", newline="") as f:
        return f.read(), "big5(ignore)"


def parse(path, encoding):
    rows, skipped = [], 0
    text, used = _read_text(path, encoding)
    parse.encoding_used = used
    for c in csv.reader(io.StringIO(text)):
        if not c:
            skipped += 1
            continue
        c0 = c[0].strip()
        m2 = DATE2_RE.match(c0)
        r = _parse_b(c, m2.group(1)) if m2 else (_parse_a(c) if DATE_RE.match(c0) else None)
        if r is None:                # 表頭/合計/空列/解析失敗
            skipped += 1
            continue
        rows.append(r)
    return rows, skipped


def main():
    ap = argparse.ArgumentParser(description="券商成交明細 CSV → trade_log")
    ap.add_argument("file", help="券商成交明細 CSV（Big5）")
    ap.add_argument("--dsn", default=os.environ.get("DATABASE_URL", ""), help="PostgreSQL 連線字串")
    ap.add_argument("--encoding", default="auto",
                    help="CSV 編碼（預設 auto：先試 UTF-8，失敗退回 Big5/CP950）")
    ap.add_argument("--clear", action="store_true", help="匯入前先清空 trade_log（乾淨重灌，避免重複）")
    ap.add_argument("--dry-run", action="store_true", help="只解析印統計，不寫 DB")
    args = ap.parse_args()

    rows, skipped = parse(args.file, args.encoding)
    if not rows:
        raise SystemExit("未解析到任何交易列（檢查檔案/編碼）。")
    buys = sum(1 for r in rows if r[1] == "buy")
    dmin, dmax = min(r[2] for r in rows), max(r[2] for r in rows)
    codes = sorted({r[0] for r in rows})
    print(f"編碼 {getattr(parse, 'encoding_used', '?')}｜解析 {len(rows)} 筆"
          f"（買 {buys}、賣 {len(rows) - buys}）｜日期 {dmin} ~ {dmax}"
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
        " fee NUMERIC, tax NUMERIC, trade_type VARCHAR(20), pnl NUMERIC, note VARCHAR(200),"
        " created_at TIMESTAMP DEFAULT now())")
    cur.execute("ALTER TABLE trade_log ADD COLUMN IF NOT EXISTS trade_type VARCHAR(20)")  # 舊表補欄
    cur.execute("ALTER TABLE trade_log ADD COLUMN IF NOT EXISTS pnl NUMERIC")
    if args.clear:
        cur.execute("DELETE FROM trade_log")
        print(f"已清空 trade_log（{cur.rowcount} 筆）")
    execute_values(cur,
                   "INSERT INTO trade_log (stock_id, action, trade_date, shares, price, fee, tax, trade_type, pnl) VALUES %s",
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
