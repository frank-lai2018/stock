r"""load_to_db.py — 讀 H:\data 的 CSV，正規化 + 算 available_date → 批次 upsert 進 PostgreSQL。

對應 schema.sql / 資料庫設計.md。可重跑（ON CONFLICT DO UPDATE）。

前置：
  pip install psycopg2-binary pandas openpyxl
  先建好 database 與 schema.sql（見 schema.sql 檔頭）。
連線（擇一）：
  set DATABASE_URL=postgresql://postgres:密碼@localhost:5432/twstock   （Windows: set；或 --dsn 帶入）

用法：
  python load_to_db.py --dry-run                     # 不連 DB，只驗證讀檔/轉換（印各表列數+樣本）
  python load_to_db.py --dsn "postgresql://postgres:pwd@localhost:5432/twstock"
  python load_to_db.py --codes 2330,5483             # 只灌指定幾檔（測試）
  python load_to_db.py --tables stock,price,revenue  # 只灌指定表
  python load_to_db.py --since 2026-07-11            # 增量：只 upsert 日期>=該日的資料列（每日同步用）

資料來源假設：
  股價 → <root>\<股號>\<股號>_adj.csv（需先跑 build_adjusted_price.py 產生還原價）
  其餘 → <root>\Fundamentals\<股號>\<股號>_*.csv、<root>\Index\*.csv
"""
import argparse
import glob
import math
import os
from datetime import date, timedelta

import pandas as pd

# ---------- 小工具 ----------

def num(x):
    try:
        s = str(x).replace(",", "").strip()
        if s in ("", "nan", "None", "--"):
            return None
        v = float(s)
        return v if math.isfinite(v) else None   # inf/nan（如營收基期為0算出的成長率）→ None
    except Exception:
        return None

def bigint(x):
    v = num(x)
    return None if v is None else int(round(v))

def iso(x):
    s = str(x).strip()[:10].replace("/", "-")        # 容忍 1962/02/09 與 2023-01-03
    return s if len(s) == 10 and s[4] == "-" else None

def rev_available(month_first):
    """月營收可得日 = 次月 10 日。"""
    d = date.fromisoformat(month_first)
    nm = date(d.year + (d.month == 12), 1 if d.month == 12 else d.month + 1, 1)
    return nm.replace(day=10).isoformat()

def rev_month(year, month):
    """由 FinMind revenue_year / revenue_month 欄組真實營收月份首日 YYYY-MM-01。
       （注意：FinMind 的 date 欄是「公告月」，比真實營收月多一個月，故不可拿 date 當月份。）"""
    try:
        y, m = int(str(year).strip()), int(str(month).strip())
        if y > 1900 and 1 <= m <= 12:
            return f"{y:04d}-{m:02d}-01"
    except (ValueError, TypeError):
        pass
    return None

def fin_available(period_date):
    """財報可得日（公告慣例）：Q1→5/15、Q2→8/14、Q3→11/14、年報→次年 3/31。"""
    d = date.fromisoformat(period_date)
    return {3: date(d.year, 5, 15), 6: date(d.year, 8, 14),
            9: date(d.year, 11, 14), 12: date(d.year + 1, 3, 31)}.get(d.month, d).isoformat()


# ---------- 各表轉換（回傳 rows）----------

def load_stock(xlsx):
    df = pd.read_excel(xlsx, dtype=str).fillna("")
    rows = []
    for _, r in df.iterrows():
        code = r["股票代碼"].strip()
        if not code:
            continue
        rows.append((code, r.get("中文名稱", "").strip(), r.get("市場別", "").strip() or None,
                     iso(r.get("上市櫃日期", "")), r.get("產業別", "").strip() or None))
    return rows

def load_prices(folder, code, market):
    """讀 <股號>_adj.csv；上櫃成交量張→股（×1000）對齊正規化。"""
    path = os.path.join(folder, f"{code}_adj.csv")
    if not os.path.exists(path):
        return []
    df = pd.read_csv(path, dtype=str)
    mult = 1000 if market == "上櫃" else 1
    rows = []
    for _, r in df.iterrows():
        d = iso(r["date"])
        if not d:
            continue
        vol = bigint(r.get("volume"))
        amt = bigint(r.get("amount"))
        rows.append((code, d, num(r.get("open")), num(r.get("high")), num(r.get("low")), num(r.get("close")),
                     None if vol is None else vol * mult,             # 成交量：上櫃 張→股 ×1000
                     None if amt is None else amt * mult, None,       # 成交金額：上櫃 千元→元 ×1000；trades: _adj 無→NULL
                     num(r.get("adj_open")), num(r.get("adj_high")), num(r.get("adj_low")), num(r.get("adj_close")),
                     num(r.get("cumfactor"))))
    return rows

def load_index(root):
    rows = []
    for path in glob.glob(os.path.join(root, "Index", "*.csv")):
        df = pd.read_csv(path, dtype=str)
        iid = os.path.splitext(os.path.basename(path))[0]
        for _, r in df.iterrows():
            d = iso(r["date"])
            if d:
                rows.append((iid, d, num(r.get("price") or r.get("close"))))
    return rows

def load_revenue(fdir, code):
    path = os.path.join(fdir, f"{code}_revenue.csv")
    if not os.path.exists(path):
        return []
    df = pd.read_csv(path, dtype=str)
    rows = []
    for _, r in df.iterrows():
        mon = rev_month(r.get("revenue_year"), r.get("revenue_month")) or iso(r.get("date"))
        if not mon:
            continue
        rows.append((code, mon, bigint(r.get("revenue")), num(r.get("月增率(%)")), num(r.get("年增率(%)")),
                     rev_available(mon)))
    return rows

FIN_FILES = {"income": "_financials.csv", "balance": "_balance.csv", "cashflow": "_cashflow.csv"}
SKIP_ITEMS = {"date", "stock_id", "毛利率(%)", "營業利益率(%)", "-", ""}   # 非科目/衍生欄

def load_financials(fdir, code):
    """三張財報寬轉長 → financial_statement；同時回傳 per-period 科目 dict 供彙整。"""
    fs_rows, items_by_period = [], {}
    for stmt, suffix in FIN_FILES.items():
        path = os.path.join(fdir, f"{code}{suffix}")
        if not os.path.exists(path):
            continue
        df = pd.read_csv(path, dtype=str)
        for _, r in df.iterrows():
            d = iso(r.get("date"))
            if not d:
                continue
            av = fin_available(d)
            for col in df.columns:
                if col.strip() in SKIP_ITEMS:
                    continue
                v = num(r[col])
                if v is None:
                    continue
                fs_rows.append((code, d, stmt, col, v, av))
                items_by_period.setdefault(d, {})[col] = v
    return fs_rows, items_by_period

def _first(d, keys):
    for k in keys:
        if k in d and d[k] is not None:
            return d[k]
    return None

def build_fundamentals_q(code, items_by_period):
    """由財報科目彙整選股寬表。"""
    rows = []
    for d, it in sorted(items_by_period.items()):
        rev = it.get("Revenue"); gp = it.get("GrossProfit"); oi = it.get("OperatingIncome")
        ni = _first(it, ["IncomeAfterTaxes", "NetIncome", "TotalConsolidatedProfitForThePeriod",
                         "ProfitLossAttributableToOwnersOfParent"])
        ta = _first(it, ["TotalAssets"]); li = _first(it, ["Liabilities", "TotalLiabilities"])
        eq = _first(it, ["Equity", "TotalEquity", "EquityAttributableToOwnersOfParent"])
        ocf = _first(it, ["CashFlowsProvidedFromOperatingActivities", "NetCashProvidedByOperatingActivities"])
        pct = lambda a, b: round(a / b * 100, 2) if (a is not None and b) else None
        rows.append((code, d, fin_available(d),
                     bigint(rev), bigint(gp), bigint(oi), bigint(it.get("PreTaxIncome")), bigint(ni),
                     it.get("EPS"),
                     bigint(ta), bigint(eq), bigint(li), bigint(ocf),
                     pct(gp, rev), pct(oi, rev), pct(ni, rev),
                     pct(ni, eq), pct(li, ta)))
    return rows

def load_dividend(fdir, code):
    path = os.path.join(fdir, f"{code}_dividend.csv")
    if not os.path.exists(path):
        return []
    df = pd.read_csv(path, dtype=str)
    best = {}   # announce_date -> row；同一公告日多筆時取股利總額較大者（濾掉全0空列，避免主鍵重複）
    for _, r in df.iterrows():
        av = iso(r.get("AnnouncementDate"))
        if not av:
            continue
        cash = (num(r.get("CashEarningsDistribution")) or 0) + (num(r.get("CashStatutorySurplus")) or 0)
        stk = (num(r.get("StockEarningsDistribution")) or 0) + (num(r.get("StockStatutorySurplus")) or 0)
        row = (code, av, r.get("year"), cash, stk,
               iso(r.get("CashExDividendTradingDate")), iso(r.get("StockExDividendTradingDate")),
               iso(r.get("CashDividendPaymentDate")))
        prev = best.get(av)
        if prev is None or (cash + stk) > (prev[3] + prev[4]):
            best[av] = row
    return list(best.values())

def load_capreduction(fdir, code):
    path = os.path.join(fdir, f"{code}_capreduction.csv")
    if not os.path.exists(path):
        return []
    df = pd.read_csv(path, dtype=str)
    return [(code, iso(r["date"]), num(r.get("ClosingPriceonTheLastTradingDay")),
             num(r.get("PostReductionReferencePrice")), r.get("ReasonforCapitalReduction"))
            for _, r in df.iterrows() if iso(r.get("date"))]

def load_inst(fdir, code):
    path = os.path.join(fdir, f"{code}_institutional.csv")
    if not os.path.exists(path):
        return []
    df = pd.read_csv(path, dtype=str)
    g = lambda r, c: bigint(r.get(c))
    return [(code, iso(r["date"]), g(r, "Foreign_Investor_net"), g(r, "Foreign_Dealer_Self_net"),
             g(r, "Investment_Trust_net"), g(r, "Dealer_self_net"), g(r, "Dealer_Hedging_net"))
            for _, r in df.iterrows() if iso(r.get("date"))]

def load_margin(fdir, code):
    path = os.path.join(fdir, f"{code}_margin.csv")
    if not os.path.exists(path):
        return []
    df = pd.read_csv(path, dtype=str)
    g = lambda r, c: bigint(r.get(c))
    return [(code, iso(r["date"]), g(r, "MarginPurchaseTodayBalance"), g(r, "ShortSaleTodayBalance"),
             g(r, "MarginPurchaseBuy"), g(r, "MarginPurchaseSell"), g(r, "ShortSaleSell"), g(r, "ShortSaleBuy"))
            for _, r in df.iterrows() if iso(r.get("date"))]

def load_shareholding(fdir, code):
    path = os.path.join(fdir, f"{code}_shareholding.csv")
    if not os.path.exists(path):
        return []
    df = pd.read_csv(path, dtype=str)
    return [(code, iso(r["date"]), num(r.get("ForeignInvestmentSharesRatio")),
             bigint(r.get("ForeignInvestmentShares")), bigint(r.get("NumberOfSharesIssued")))
            for _, r in df.iterrows() if iso(r.get("date"))]

def load_per(fdir, code):
    path = os.path.join(fdir, f"{code}_per.csv")
    if not os.path.exists(path):
        return []
    df = pd.read_csv(path, dtype=str)
    return [(code, iso(r["date"]), num(r.get("PER")), num(r.get("PBR")), num(r.get("dividend_yield")))
            for _, r in df.iterrows() if iso(r.get("date"))]


# 表定義：name -> (欄位, 主鍵)
TABLES = {
    "stock": (["stock_id", "name", "market", "list_date", "industry"], ["stock_id"]),
    "price_daily": (["stock_id", "trade_date", "open", "high", "low", "close", "volume", "amount", "trades",
                     "adj_open", "adj_high", "adj_low", "adj_close", "adj_factor"], ["stock_id", "trade_date"]),
    "market_index": (["index_id", "trade_date", "close"], ["index_id", "trade_date"]),
    "monthly_revenue": (["stock_id", "revenue_month", "revenue", "mom_pct", "yoy_pct", "available_date"], ["stock_id", "revenue_month"]),
    "financial_statement": (["stock_id", "period_date", "statement", "item", "value", "available_date"], ["stock_id", "period_date", "statement", "item"]),
    "fundamentals_quarterly": (["stock_id", "period_date", "available_date", "revenue", "gross_profit", "operating_income",
                                 "pretax_income", "net_income", "eps", "total_assets", "total_equity", "total_liabilities",
                                 "operating_cash_flow", "gross_margin", "op_margin", "net_margin", "roe", "debt_ratio"], ["stock_id", "period_date"]),
    "dividend": (["stock_id", "announce_date", "year_label", "cash_dividend", "stock_dividend",
                  "ex_cash_date", "ex_stock_date", "cash_pay_date"], ["stock_id", "announce_date"]),
    "capital_reduction": (["stock_id", "resume_date", "pre_close", "post_ref_price", "reason"], ["stock_id", "resume_date"]),
    "inst_trades": (["stock_id", "trade_date", "foreign_net", "foreign_dealer_net", "trust_net", "dealer_self_net", "dealer_hedge_net"], ["stock_id", "trade_date"]),
    "margin_trading": (["stock_id", "trade_date", "margin_balance", "short_balance", "margin_buy", "margin_sell", "short_sell", "short_buy"], ["stock_id", "trade_date"]),
    "shareholding": (["stock_id", "trade_date", "foreign_ratio", "foreign_shares", "shares_issued"], ["stock_id", "trade_date"]),
    "valuation_daily": (["stock_id", "trade_date", "per", "pbr", "dividend_yield"], ["stock_id", "trade_date"]),
}


def upsert(cur, table, rows):
    from psycopg2.extras import execute_values
    cols, pk = TABLES[table]
    if not rows:
        return 0
    pk_idx = [cols.index(c) for c in pk]                 # 同批去重：同主鍵只留最後一筆，
    rows = list({tuple(r[i] for i in pk_idx): r for r in rows}.values())  # 免 ON CONFLICT 同句更新兩次
    updates = [c for c in cols if c not in pk]
    setc = ", ".join(f"{c}=EXCLUDED.{c}" for c in updates)
    sql = (f"INSERT INTO {table} ({','.join(cols)}) VALUES %s ON CONFLICT ({','.join(pk)}) "
           + (f"DO UPDATE SET {setc}" if updates else "DO NOTHING"))
    execute_values(cur, sql, rows, page_size=1000)
    return len(rows)


def main():
    ap = argparse.ArgumentParser(description="載入 H:\\data 的 CSV 進 PostgreSQL")
    ap.add_argument("--dsn", default=os.environ.get("DATABASE_URL", ""), help="PostgreSQL 連線字串")
    ap.add_argument("--root", default=r"H:\data", help="資料根目錄")
    ap.add_argument("--xlsx", default="台股股票代碼NEW.xlsx", help="代碼 Excel")
    ap.add_argument("--codes", default="", help="只灌指定代碼（逗號分隔）")
    ap.add_argument("--tables", default="all", help="只灌指定表（逗號分隔）；all=全部")
    ap.add_argument("--since", default="", help="增量：只 upsert 日期>=此日(YYYY-MM-DD)的資料列；維度表 stock 不受限")
    ap.add_argument("--dry-run", action="store_true", help="不連 DB，只驗證讀檔/轉換")
    args = ap.parse_args()

    if args.since:
        try:
            date.fromisoformat(args.since)
        except ValueError:
            raise SystemExit("--since 需為 YYYY-MM-DD 格式")

    want = set(TABLES) if args.tables == "all" else set(args.tables.split(","))
    conn = cur = None
    if not args.dry_run:
        import psycopg2
        if not args.dsn:
            raise SystemExit("需要 --dsn 或環境變數 DATABASE_URL")
        conn = psycopg2.connect(args.dsn); cur = conn.cursor()

    totals = {}
    def sink(table, rows):
        if table not in want or not rows:
            return
        rows = [r for r in rows if r[0]]                 # 去掉空代碼
        if args.since and table != "stock":              # 增量：時序表只留日期(第2欄)>=since；維度表 stock 不限
            rows = [r for r in rows if r[1] and str(r[1]) >= args.since]
        if not rows:                                     # 過濾後可能為空 → 略過（免 upsert 空集 / 樣本索引越界）
            return
        if args.dry_run:
            if table not in totals:
                print(f"  [{table}] 樣本: {rows[0]}")
        else:
            upsert(cur, table, rows)
        totals[table] = totals.get(table, 0) + len(rows)

    # 1) 維度先灌（外鍵依賴）
    stock_rows = load_stock(args.xlsx)
    market = {r[0]: r[2] for r in stock_rows}
    sink("stock", stock_rows)
    if not args.dry_run and "stock" in want:
        conn.commit()

    # 2) 大盤
    sink("market_index", load_index(args.root))

    # 3) 逐檔
    codes = [c.strip() for c in args.codes.split(",") if c.strip()] or sorted(market)
    froot = os.path.join(args.root, "Fundamentals")
    for i, code in enumerate(codes, 1):
        sdir = os.path.join(args.root, code)
        fdir = os.path.join(froot, code)
        sink("price_daily", load_prices(sdir, code, market.get(code)))
        sink("monthly_revenue", load_revenue(fdir, code))
        fs_rows, items = load_financials(fdir, code)
        sink("financial_statement", fs_rows)
        sink("fundamentals_quarterly", build_fundamentals_q(code, items))
        sink("dividend", load_dividend(fdir, code))
        sink("capital_reduction", load_capreduction(fdir, code))
        sink("inst_trades", load_inst(fdir, code))
        sink("margin_trading", load_margin(fdir, code))
        sink("shareholding", load_shareholding(fdir, code))
        sink("valuation_daily", load_per(fdir, code))
        if not args.dry_run and i % 50 == 0:
            conn.commit()
            print(f"  ...已處理 {i}/{len(codes)} 檔")

    if not args.dry_run:
        conn.commit(); cur.close(); conn.close()

    print("\n=== 完成" + ("（dry-run，未寫入）" if args.dry_run else "") + " ===")
    for t in TABLES:
        if t in totals:
            print(f"  {t:24} {totals[t]:>10,} 列")


if __name__ == "__main__":
    main()
