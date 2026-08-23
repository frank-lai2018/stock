r"""update_fundamentals_opendata.py — 從 TWSE/櫃買 OpenAPI 抓「當期全市場」綜合損益表 + 資產負債表，
直接 upsert 進 fundamentals_quarterly（選股寬表）。

路線 B（同 update_chips.py 的思路）：約 10 個請求就能更新全市場當期財報，
取代 FinMind 逐檔（~2,300 檔 × 2 資料集 ≈ 4,600 請求，撞 300 次/hr 限流要跑十幾小時）。

兩個關鍵轉換（已用台積電 2026Q2 對帳確認，勿隨意更動）：
  1) 單位：opendata 金額為「仟元」，DB 為「元」→ ×1000。EPS（元/股）不換算。
  2) 累計 → 單季：MOPS 綜合損益表是「年初至本季累計」，DB 存單季。
     單季 = 本季累計 − 同年度前面各季單季和（前面各季由 DB 取），Q1 免減。
     資產負債表是「時點數」（存量），不需相減。

只更新 opendata 有給的欄位；operating_cash_flow 沒有對應來源，
upsert 用 COALESCE 保留 DB 既有值（現金流仍由 FinMind 的 cashflow 工作補）。

只灌「已存在於 stock 表」的代號。連線：--dsn 或環境變數 DATABASE_URL。
用法：
  python update_fundamentals_opendata.py --dsn "postgresql://frank:pwd@localhost:5432/twstock"
  python update_fundamentals_opendata.py --dry-run           # 不寫 DB，印抓到的期別/檔數與台積電對帳
  python update_fundamentals_opendata.py --check 2330,2317   # 抓完印這幾檔的換算結果供人工核對
"""
import argparse
import json
import os
import ssl
import urllib.request
from datetime import date

import load_to_db as L          # 重用 num / bigint / fin_available

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE          # 沿用專案既有做法（證交所憑證瑕疵）

# 產業別分檔：一般業(ci) 之外，金控/銀行/保險/證券 科目結構不同，各自一個資料集。
# 有些變體當期可能空表（回 1 列空值）→ 自動略過，不算失敗。
TWSE_SUFFIX = ["ci", "fh", "bd", "ins", "basi"]
TPEX_SUFFIX = ["ci", "fh", "bd", "ins", "basi"]
TWSE_URL = "https://openapi.twse.com.tw/v1/opendata/t187ap{n:02d}_L_{sfx}"      # 06=損益、07=資負
TPEX_URL = "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap{n:02d}_O_{sfx}"

# 中文欄位 → 寬表欄位。同義欄位給多個候選，取第一個有值的。
INCOME_MAP = {                                  # 流量（需累計轉單季）
    "revenue":          ["營業收入"],
    "gross_profit":     ["營業毛利（毛損）淨額", "營業毛利（毛損）"],
    "operating_income": ["營業利益（損失）"],
    "pretax_income":    ["稅前淨利（淨損）"],
    "net_income":       ["本期淨利（淨損）", "淨利（淨損）歸屬於母公司業主"],
    "eps":              ["基本每股盈餘（元）"],
}
BALANCE_MAP = {                                 # 存量（時點數，不相減）
    "total_assets":      ["資產總計"],
    "total_liabilities": ["負債總計"],
    "total_equity":      ["權益總計", "歸屬於母公司業主之權益合計"],
}
FLOW_COLS = ["revenue", "gross_profit", "operating_income", "pretax_income", "net_income", "eps"]
MONEY_COLS = set(FLOW_COLS + list(BALANCE_MAP)) - {"eps"}     # 需 ×1000（仟元→元）的欄位


def fetch_json(url, tries=3, timeout=60):
    """抓 JSON；opendata 偶爾會 IncompleteRead，重試幾次。失敗回 None（不中斷整批）。"""
    last = None
    for _ in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "accept": "application/json"})
            raw = urllib.request.urlopen(req, timeout=timeout, context=CTX).read()
            return json.loads(raw.decode("utf-8", "ignore"))
        except Exception as e:                  # noqa: BLE001 — 任何失敗都重試/略過
            last = e
    print(f"    [略過] {url.rsplit('/', 1)[-1]}：{type(last).__name__} {str(last)[:60]}")
    return None


def pick(row, names):
    """依候選欄位名取第一個有值的（各資料集欄名不完全一致）。"""
    for n in names:
        if n in row and str(row[n]).strip() not in ("", "nan", "None"):
            return row[n]
    return None


def period_of(roc_year, season):
    """民國年 + 季別 → 季底日 YYYY-MM-DD。"""
    try:
        y = int(str(roc_year).strip()) + 1911
        q = int(str(season).strip())
    except (TypeError, ValueError):
        return None, None
    end = {1: (3, 31), 2: (6, 30), 3: (9, 30), 4: (12, 31)}.get(q)
    return (date(y, *end).isoformat(), q) if end else (None, None)


def code_of(row):
    return str(pick(row, ["公司代號", "SecuritiesCompanyCode"]) or "").strip()


def collect(kind, mapping):
    """抓某類報表（6=損益 / 7=資負）的上市+上櫃全部產業別，回傳 {(code, period): {col: 原始值}} 與季別。"""
    out, seasons = {}, {}
    for url_tpl, sfxs, mkt in ((TWSE_URL, TWSE_SUFFIX, "上市"), (TPEX_URL, TPEX_SUFFIX, "上櫃")):
        for sfx in sfxs:
            rows = fetch_json(url_tpl.format(n=kind, sfx=sfx))
            if not rows:
                continue
            n = 0
            for r in rows:
                code = code_of(r)
                if not code:
                    continue
                period, q = period_of(pick(r, ["年度", "Year"]), pick(r, ["季別", "Season"]))
                if not period:
                    continue
                vals = {c: L.num(pick(r, names)) for c, names in mapping.items()}
                if all(v is None for v in vals.values()):        # 空表（金融業當期未出）→ 跳過
                    continue
                out[(code, period)] = vals
                seasons[period] = q
                n += 1
            if n:
                print(f"    {mkt} t187ap{kind:02d}_{sfx}：{n} 檔")
    return out, seasons


def scale(vals):
    """仟元 → 元（EPS 是元/股，不換算）。"""
    return {c: (v * 1000 if (v is not None and c in MONEY_COLS) else v) for c, v in (vals or {}).items()}


def to_single_quarter(cur, prior_sum, q):
    """累計 → 單季：Q1 直接用；Q2~Q4 減同年度前面各季單季和。前期缺料回 None（該檔略過）。"""
    if q == 1:
        return dict(cur)
    if prior_sum is None:
        return None
    out = {}
    for c, v in cur.items():
        p = prior_sum.get(c)
        out[c] = None if (v is None or p is None) else round(v - p, 4)
    return out


def prior_sums(cur, codes, period, q):
    """由 DB 取同年度、期別在本季之前的『單季』值加總（供累計相減）。"""
    if q == 1:
        return {}
    y = period[:4]
    cur.execute(
        "SELECT stock_id, sum(revenue) rev, sum(gross_profit) gp, sum(operating_income) oi, "
        "  sum(pretax_income) pti, sum(net_income) ni, sum(eps) eps "
        "FROM fundamentals_quarterly "
        "WHERE stock_id = ANY(%(ids)s) AND period_date >= %(y0)s AND period_date < %(p)s "
        "GROUP BY stock_id, (SELECT 1) HAVING count(*) = %(need)s",
        {"ids": codes, "y0": f"{y}-01-01", "p": period, "need": q - 1})
    keys = ["revenue", "gross_profit", "operating_income", "pretax_income", "net_income", "eps"]
    return {r[0]: dict(zip(keys, [float(v) if v is not None else None for v in r[1:]]))
            for r in cur.fetchall()}


def merge_upsert(cur, rows):
    """upsert fundamentals_quarterly；用 COALESCE 保留 DB 既有值（如 FinMind 來的現金流）。"""
    from psycopg2.extras import execute_values
    cols = ["stock_id", "period_date", "available_date", "revenue", "gross_profit", "operating_income",
            "pretax_income", "net_income", "eps", "total_assets", "total_equity", "total_liabilities",
            "gross_margin", "op_margin", "net_margin", "roe", "debt_ratio"]
    sets = ", ".join(f"{c}=COALESCE(EXCLUDED.{c}, fundamentals_quarterly.{c})" for c in cols[2:])
    sql = (f"INSERT INTO fundamentals_quarterly ({','.join(cols)}) VALUES %s "
           f"ON CONFLICT (stock_id, period_date) DO UPDATE SET {sets}")
    execute_values(cur, sql, rows, page_size=1000)
    return len(rows)


def main():
    ap = argparse.ArgumentParser(description="TWSE/櫃買 OpenAPI 當期財報 → fundamentals_quarterly")
    ap.add_argument("--dsn", default=os.environ.get("DATABASE_URL", ""), help="PostgreSQL 連線字串")
    ap.add_argument("--dry-run", action="store_true", help="不寫 DB，只印抓到的期別與檔數")
    ap.add_argument("--check", default="2330", help="印這幾檔的換算結果供人工核對（逗號分隔）")
    args = ap.parse_args()

    print("=== update_fundamentals_opendata（TWSE/櫃買 當期全市場財報）===")
    print("[1/3] 抓綜合損益表（累計數）…")
    inc, seasons = collect(6, INCOME_MAP)
    print("[2/3] 抓資產負債表（時點數）…")
    bal, _ = collect(7, BALANCE_MAP)
    if not inc and not bal:
        raise SystemExit("兩張表都沒抓到，可能來源改版或網路不通。")
    periods = sorted({p for _, p in list(inc) + list(bal)})
    print(f"    期別：{', '.join(periods)}　損益 {len(inc)} 檔、資負 {len(bal)} 檔")

    if not args.dsn:
        if args.dry_run:
            print("(--dry-run 且無 --dsn：只做抓取，未做累計轉單季/入庫)")
            return
        raise SystemExit("需要 --dsn 或環境變數 DATABASE_URL")

    import psycopg2
    conn = psycopg2.connect(args.dsn)
    cur = conn.cursor()
    cur.execute("SELECT stock_id FROM stock")
    known = {r[0] for r in cur.fetchall()}

    print("[3/3] 累計轉單季 + 入庫…")
    check = {c.strip() for c in args.check.split(",") if c.strip()}
    rows, skipped = [], 0
    for period in periods:
        q = seasons.get(period, 1)
        codes = sorted({c for (c, p) in set(inc) | set(bal) if p == period and c in known})
        priors = prior_sums(cur, codes, period, q)
        for code in codes:
            # 先把仟元換成元，再拿 DB 的前期單季（元）相減，順序不可顛倒
            cum = scale(inc.get((code, period)))
            single = to_single_quarter(cum, priors.get(code), q) if cum else {}
            if cum and single is None:                    # 前期缺料 → 算不出單季，寧可不寫
                skipped += 1
                continue
            v = {**{c: single.get(c) for c in FLOW_COLS}, **scale(bal.get((code, period)) or {})}
            g = lambda c: L.bigint(v.get(c))              # noqa: E731
            rev, gp, oi, ni = g("revenue"), g("gross_profit"), g("operating_income"), g("net_income")
            ta, eq, li = g("total_assets"), g("total_equity"), g("total_liabilities")
            pct = lambda a, b_: round(a / b_ * 100, 2) if (a is not None and b_) else None   # noqa: E731
            rows.append((code, period, L.fin_available(period), rev, gp, oi, g("pretax_income"), ni,
                         (round(v["eps"], 2) if v.get("eps") is not None else None),
                         ta, eq, li, pct(gp, rev), pct(oi, rev), pct(ni, rev), pct(ni, eq), pct(li, ta)))
            if code in check:
                print(f"    [對帳] {code} {period} 單季：營收 {rev:,}　營益 {oi:,}　"
                      f"稅後 {ni:,}　EPS {v.get('eps')}　資產 {ta:,}" if rev else f"    [對帳] {code} 無數值")

    if args.dry_run:
        print(f"(--dry-run) 可寫入 {len(rows)} 列；因前期缺料略過 {skipped} 檔")
        return
    n = merge_upsert(cur, rows)
    conn.commit()
    cur.close(); conn.close()
    print(f"完成：upsert {n} 列（略過 {skipped} 檔：同年度前期缺料算不出單季）")


if __name__ == "__main__":
    main()
