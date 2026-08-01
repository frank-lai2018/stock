r"""大盤信用交易彙總 → market_margin 表（上市 MI_MARGN 彙總段 + 上櫃 balance summary）。

market_margin(trade_date, market, margin_amt_k, margin_lots, short_lots, maint_ratio)
  market        'TWSE'（上市）/ 'TPEx'（上櫃）
  margin_amt_k  融資餘額（千元，今日餘額）—— 交易所彙總段原始值，精準
  margin_lots   融資餘額（張，今日）
  short_lots    融券餘額（張，今日）
  maint_ratio   整體融資維持率(%)＝Σ(融資張數×收盤×1000)/融資金額 ×100（自算約值；
                受本庫代號過濾影響，通常比第三方站略低約 1pp）

來源：TWSE MI_MARGN CSV 開頭彙總段；TPEx balance JSON 的 tables[0].summary。
不另外抓網路——update_chips 已下載的原始 bytes 直接解析；歷史用 backfill_market_margin.py 從快照回補。
"""
import csv
import io


def _n(s):
    try:
        return float(str(s).replace(",", "").replace('"', "").strip() or 0)
    except ValueError:
        return 0.0


def parse_twse(raw_bytes):
    """TWSE MI_MARGN → (margin_amt_k, margin_lots, short_lots)；解析不到回 None。
    彙總段欄序：[項目, 買進, 賣出, 現金(券)償還, 前日餘額, 今日餘額]（今日餘額=第 5 欄）。"""
    rows = list(csv.reader(io.StringIO(raw_bytes.decode("big5", "ignore"))))
    amt_k = lots = short_lots = None
    for c in rows[:8]:                       # 彙總段在個股段（"代號"欄名列）之前
        if len(c) < 6:
            continue
        label = (c[0] or "").replace('"', "")
        today = _n(c[5])
        if "融資金額" in label:
            amt_k = today
        elif "融資" in label and "單位" in label:
            lots = today
        elif "融券" in label and "單位" in label:
            short_lots = today
    if amt_k is None:
        return None
    return int(amt_k), int(lots or 0), int(short_lots or 0)


def parse_tpex(j):
    """TPEx balance JSON → (margin_amt_k, margin_lots, short_lots)；解析不到回 None。
    summary 欄序：[ , 項目, 前日餘額, 買進, 賣出, 現金償還, 今日餘額, ...(融券段今日=idx14)]。"""
    tables = j.get("tables") if isinstance(j, dict) else None
    if not tables:
        return None
    summ = tables[0].get("summary") or []
    amt_k = lots = short_lots = None
    for r in summ:
        if len(r) < 7:
            continue
        label = str(r[1])
        if "融資金" in label:                    # 上櫃彙總標籤為「融資金(仟元)」(無「額」字)
            amt_k = _n(r[6])                      # idx6=融資今日餘額
        elif "合計" in label:
            lots = _n(r[6])                       # idx6=融資餘額(張)
            short_lots = _n(r[14]) if len(r) > 14 else None   # idx14=融券餘額(張)
    if amt_k is None:
        return None
    return int(amt_k), int(lots or 0), int(short_lots or 0)


def ensure_table(cur):
    cur.execute(
        "CREATE TABLE IF NOT EXISTS market_margin ("
        " trade_date DATE NOT NULL,"
        " market VARCHAR(8) NOT NULL,"
        " margin_amt_k BIGINT,"           # 融資餘額（千元，今日）
        " margin_lots BIGINT,"            # 融資餘額（張）
        " short_lots BIGINT,"             # 融券餘額（張）
        " maint_ratio NUMERIC,"           # 整體融資維持率(%)（自算）
        " PRIMARY KEY (trade_date, market))")


def maint_ratios(cur, d):
    """該日各市場整體融資維持率(%)＝Σ(融資張數×收盤×1000)/融資金額 ×100。
    需該日 price_daily 已入庫；回傳 {market: {'mv_k':.., 'ratio_num':..}}（mv_k=擔保品市值千元）。"""
    cur.execute(
        "SELECT CASE WHEN s.market LIKE %(twse)s THEN 'TWSE' "
        "            WHEN s.market LIKE %(tpex)s THEN 'TPEx' END AS mk, "
        "       sum(m.margin_balance * p.close * 1000.0) AS mv "
        "FROM margin_trading m JOIN stock s USING(stock_id) "
        "JOIN price_daily p ON p.stock_id = m.stock_id AND p.trade_date = m.trade_date "
        "WHERE m.trade_date = %(d)s GROUP BY mk",
        {"d": d, "twse": "上市%", "tpex": "上櫃%"})
    out = {}
    for mk, mv in cur.fetchall():
        if mk and mv:
            out[mk] = float(mv) / 1000.0        # 元 → 千元
    return out


def upsert(cur, d, market, amt_k, lots, short_lots, mv_k):
    ratio = round(mv_k / amt_k * 100, 2) if (amt_k and mv_k) else None
    cur.execute(
        "INSERT INTO market_margin (trade_date, market, margin_amt_k, margin_lots, short_lots, maint_ratio) "
        "VALUES (%(d)s, %(mk)s, %(amt)s, %(lots)s, %(short)s, %(ratio)s) "
        "ON CONFLICT (trade_date, market) DO UPDATE SET "
        " margin_amt_k=EXCLUDED.margin_amt_k, margin_lots=EXCLUDED.margin_lots, "
        " short_lots=EXCLUDED.short_lots, maint_ratio=EXCLUDED.maint_ratio",
        {"d": d, "mk": market, "amt": amt_k, "lots": lots, "short": short_lots, "ratio": ratio})
