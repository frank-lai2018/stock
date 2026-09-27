r"""build_etf_flow.py — 主動式 ETF 每日進出：相鄰兩個持股日相減、扣掉「全面等比例」的增減 → etf_flow，並印最新一日的異動。

主動調整 ＝ 本日股數 − 前日股數 × k。k 是當天持股的「共同比例」：最多檔持股一起變動的那個比例
  （每檔 本日股數÷前日股數，取落在 ±0.5 個百分點內最多檔的那一群的中位數；群聚不到 25% 且不到 5 檔就當 1）。
  例：00981A 2026-09-24 有變動的 30 檔全部減少、共同比例 −1.7%（當天淨贖回 1.66%，經理人全面等比例賣）；
      扣掉之後，真正主動賣的是日月光投控、信驊、台積電、緯穎，旺矽等 11 檔只是跟著等比例賣出。
為什麼不直接用單位數比例：實測經理人多半「不會」在申購贖回當天等比例買賣——錢先進出現金、之後才分批處理
  （00981A 9/16、9/17 單位數各 +0.8%，持股幾乎沒動）。用單位數去扣，會把「只加碼一檔」誤判成隨申贖。
  單位數比例仍存在 etf_snapshot.flow_k_units 對照。
佔位股（權重 < 0.01%，例如統一、復華在很多檔各留 1 張）一律當 0 股：從 1 張買到 5,000 張算「新建倉」。
門檻：|主動調整| ≥ max(前日部位 1%, 共同比例應增減量的 30%, 1 張) 才算加碼／減碼；
      沒過門檻的歸為 flow（整張執行造成的零頭，或單純跟著全面等比例增減）。
公司行動（不是交易）：先把前日股數換算到本日的股本基礎，再算增減；只剩零頭的標為 corp。
  分割／面額變更／減資  兩個持股日之間，原始收盤價單日變動超出漲跌幅（< 0.85 或 > 1.18 倍），且 ETF 股數
                        大致反向變動 → 倍數用價格推回（對齊 1.5／2／2.5／3／4／5／10… 等常見倍數）。
                        例：緯穎 2026-09-02 一拆三，股價 7,800 → 2,610、各 ETF 股數約 ×3；
                        早期版本只認 2／4／5／10 倍，這天被誤判成 3 家投信共識加碼（00981A 一檔就 +112 億）。
  配股                  除權日落在區間內 → ×（1＋股票股利/10）；KY 股晚入帳的，除權後 75 天內股數剛好放大該倍數也算。
  減資（沒有價格斷點）  恢復買賣日落在區間內。

用法：
  python build_etf_flow.py              # 每檔重算最近 15 個持股日 → etf_flow，印最新一日異動
  python build_etf_flow.py --all        # 全部重算（回補歷史後用）
  python build_etf_flow.py --report-only
前置：schema_etf_holding.sql、fetch_active_etf.py（fetch 跑完會自動呼叫本程式的 run()）。
"""
import argparse
import os
import sys
from collections import defaultdict
from datetime import timedelta
from statistics import median

DUST_WEIGHT = 0.01        # 權重 < 0.01%（顯示為 0.00%）視為佔位股
MIN_REL = 0.01            # 主動調整至少是前日部位的 1%
FLOW_TOL = 0.3            # …且至少是申購贖回應增減量的 30%
MIN_SHARES = 1000         # …且至少 1 張
RECENT = 15               # 平常重算最近幾個持股日

ACTION_NAME = {"new": "新建倉", "exit": "出清", "add": "加碼", "cut": "減碼",
               "add_rel": "相對加碼", "cut_rel": "相對減碼", "flow": "隨申贖", "corp": "分割/配股/減資"}
NICE = (1.5, 2, 2.5, 3, 4, 5, 8, 10, 20)          # 常見的分割／面額變更倍數
BREAK_LO, BREAK_HI = 0.85, 1.18                    # 單日收盤價超出漲跌幅（±10%）→ 只可能是公司行動


def clean_dsn(dsn):
    """容錯＋防呆：剝掉誤貼進值裡的旗標／引號，並先擋掉明顯不合法的連線字串。（同 nightly.py）"""
    dsn = (dsn or "").strip().strip('"').strip("'").strip()
    if dsn.startswith("--dsn"):                      # 誤把旗標本身貼進值裡
        dsn = dsn[5:].lstrip().lstrip("=").lstrip()
    if dsn and not (dsn.startswith(("postgresql://", "postgres://")) or "=" in dsn):
        raise SystemExit(f"連線字串格式不對：{dsn!r}；"
                         "應為 postgresql://user:pw@host:port/db（或 key=value 形式）")
    return dsn


def common_factor(ratios, tol=0.005, min_share=0.25, min_n=5):
    """當天持股的共同比例：最多檔落在同一比例附近（視窗寬 2×tol）的那一群的中位數。
    全面等比例賣 1.7% → ≈0.983；多數沒動 → 1。群聚太小（< min_share 且 < min_n 檔）回 1。"""
    rs = sorted(ratios)
    if len(rs) < min_n:
        return 1.0
    lo = best_lo = best_hi = 0
    for hi in range(len(rs)):
        while rs[hi] - rs[lo] > 2 * tol:
            lo += 1
        if hi - lo > best_hi - best_lo:
            best_lo, best_hi = lo, hi
    cluster = rs[best_lo:best_hi + 1]
    if len(cluster) < min_n and len(cluster) < min_share * len(rs):
        return 1.0
    k = median(cluster)
    return 1.0 if abs(k - 1) < 0.001 else k


def is_dust(shares, weight):
    if weight is not None:
        return float(weight) < DUST_WEIGHT
    return shares <= 1000


def price_breaks(closes):
    """closes：{code: [(date, close)]}（依日期）。回傳 {code: [(date, g)]}：單日原始收盤價變動超出漲跌幅的日子。"""
    out = defaultdict(list)
    for code, xs in closes.items():
        for (_, c0), (d1, c1) in zip(xs, xs[1:]):
            if c0 and c1 and not BREAK_LO <= c1 / c0 <= BREAK_HI:
                out[code].append((d1, c1 / c0))
    return out


def snap_factor(f):
    """價格推回的倍數對齊到常見倍數（一拆三的 2.99 → 3）；對不上（例如減資）就原樣回傳。"""
    for m in NICE:
        for x in (m, 1 / m):
            if abs(f / x - 1) <= 0.12:
                return x
    return f


def corp_factor(code, p, c, q, breaks, divs):
    """(p, c] 之間公司行動造成的股數倍數；沒有回 None。q＝本日股數 ÷ 前日股數（原始）。"""
    g = 1.0
    for d, x in breaks.get(code, ()):
        if p < d <= c:
            g *= x
    if g != 1.0 and abs(q - 1) > 0.2 and abs(q * g - 1) < 0.25:      # 股數大致反向跟著價格變
        return snap_factor(1 / g)
    for ex, ratio in divs.get(code, ()):
        if p < ex <= c and abs(q / ratio - 1) < 0.25:                 # 除權日入帳的配股
            return ratio
    return None


def late_corp(code, p, c, q, divs, caps):
    """沒有價格斷點、也不在除權日的公司行動：晚入帳的配股（KY 股）、沒造成跳空的減資。"""
    for ex, ratio in divs.get(code, ()):
        if ex <= c and (c - ex).days <= 75 and abs(q - ratio) <= 0.003:
            return True
    return any(p < rd <= c for rd in caps.get(code, ()))


def classify(e0, e1, d, active, flow):
    if e0 == 0 and e1 > 0:
        return "new"
    if e0 > 0 and e1 == 0:
        return "exit"
    if abs(active) >= max(MIN_REL * e0, FLOW_TOL * abs(flow), MIN_SHARES):
        if active > 0:
            return "add" if d > 0 else "add_rel"
        return "cut" if d < 0 else "cut_rel"
    return "flow"


def compute_fund(etf_id, snaps, hold, close, divs, caps, breaks):
    """snaps：[(as_of, units, nav_total)] 依日期排序；hold：{as_of: {code: (shares, weight, amount)}}。
    回傳 (flow 列, snapshot 更新)。etf_flow 的 shares_prev／shares 存原始股數，d_shares 已扣公司行動（＝實際買賣）。"""
    rows, updates = [], []
    for (p, u0, _), (c, u1, nav1) in zip(snaps, snaps[1:]):
        h0, h1 = hold.get(p), hold.get(c)
        if h0 is None or h1 is None:
            continue
        eff0 = {k: (0 if is_dust(s, w) else s) for k, (s, w, _) in h0.items()}
        eff1 = {k: (0 if is_dust(s, w) else s) for k, (s, w, _) in h1.items()}
        fac = {}                                          # 公司行動倍數：前日股數 × fac＝本日股本基礎
        for x, v in eff0.items():
            if v > 0 and eff1.get(x, 0) > 0:
                f = corp_factor(x, p, c, eff1[x] / v, breaks, divs)
                if f:
                    fac[x] = f
        base0 = {x: v * fac.get(x, 1.0) for x, v in eff0.items()}
        k_units = float(u1) / float(u0) if u0 and u1 else None
        k = common_factor([eff1[x] / base0[x] for x in base0 if base0[x] > 0 and eff1.get(x, 0) > 0])
        updates.append((p, k, k_units, etf_id, c))
        for code in set(h0) | set(h1):
            s0, w0, _ = h0.get(code, (0, None, None))
            s1, w1, a1 = h1.get(code, (0, None, None))
            e0, e1 = base0.get(code, 0), eff1.get(code, 0)
            if (e0 == 0 and e1 == 0) or s1 == s0:
                continue
            d = s1 - s0 * fac.get(code, 1.0)              # 實際買賣股數（已扣分割、配股）
            flow = e0 * (k - 1)
            active = e1 - e0 * k
            px = close.get((code, c))
            if px is None and s1:                          # 缺價：用來源給的市值或權重回推
                px = (a1 / s1) if a1 else (float(w1) / 100 * float(nav1) / s1 if w1 and nav1 else None)
            action = classify(e0, e1, d, active, flow)
            if code in fac and action == "flow":
                action = "corp"                            # 只有公司行動、沒有實質買賣
            elif action not in ("new", "exit") and code not in fac and e0 > 0                     and late_corp(code, p, c, e1 / (e0 * k), divs, caps):
                action, active, d = "corp", 0.0, 0.0
            if action == "corp":
                active = 0.0
            rows.append((etf_id, c, code, p, eff0.get(code, 0), e1, round(d), round(active, 1), px,
                         round(d * px) if px and action != "corp" else (0 if action == "corp" else None),
                         round(active * px) if px else None, w0, w1, action))
    return rows, updates


def run(conn, etf_ids=None, full=False, write=True, report=True):
    """重算 etf_flow。fetch_active_etf.py 抓完會呼叫這裡；full=True 全部重算，否則每檔只算最近 RECENT 個持股日。
    write=False：照樣寫進交易、印完報表後 rollback（報表看得到這次的計算結果，DB 不變）。"""
    try:
        with conn.cursor() as cur:
            _run(cur, etf_ids, full, report)
        conn.commit() if write else conn.rollback()
    except Exception:
        conn.rollback()
        raise


def _run(cur, etf_ids, full, report):
    from psycopg2.extras import execute_values
    cur.execute("SELECT etf_id, as_of, units, nav_total FROM etf_snapshot "
                "WHERE %(ids)s::text[] IS NULL OR etf_id = ANY(%(ids)s) ORDER BY etf_id, as_of",
                {"ids": etf_ids})
    by_fund = defaultdict(list)
    for eid, d, u, nav in cur.fetchall():
        by_fund[eid].append((d, u, nav))
    if not by_fund:
        print("etf_snapshot 沒有資料，先跑 fetch_active_etf.py")
        return
    if not full:
        by_fund = {e: s[-(RECENT + 1):] for e, s in by_fund.items()}
    d_min = min(s[0][0] for s in by_fund.values())
    cur.execute("SELECT stock_id, ex_stock_date, 1 + stock_dividend / 10 FROM dividend "
                "WHERE stock_dividend > 0 AND ex_stock_date >= %s", (d_min - timedelta(days=80),))
    divs = defaultdict(list)
    for sid, ex, ratio in cur.fetchall():
        divs[sid].append((ex, float(ratio)))
    cur.execute("SELECT stock_id, resume_date FROM capital_reduction WHERE resume_date >= %s",
                (d_min - timedelta(days=10),))
    caps = defaultdict(list)
    for sid, rd in cur.fetchall():
        caps[sid].append(rd)

    all_rows, all_updates = [], []
    for eid, snaps in by_fund.items():
        dates = [s[0] for s in snaps]
        cur.execute("SELECT as_of, code, shares, weight, amount FROM etf_holding "
                    "WHERE etf_id = %s AND kind = 'stock' AND as_of = ANY(%s)", (eid, dates))
        hold = defaultdict(dict)
        codes = set()
        for d, code, s, w, a in cur.fetchall():
            hold[d][code] = (float(s), w, float(a) if a is not None else None)
            codes.add(code)
        cur.execute("SELECT stock_id, trade_date, close FROM price_daily "
                    "WHERE stock_id = ANY(%s) AND trade_date >= %s ORDER BY stock_id, trade_date",
                    (list(codes), dates[0] - timedelta(days=40)))
        closes = defaultdict(list)
        for sid, d, px in cur.fetchall():
            if px:
                closes[sid].append((d, float(px)))
        breaks = price_breaks(closes)                    # 分割／面額變更／減資造成的價格斷點
        dset = set(dates)
        close = {(sid, d): px for sid, xs in closes.items() for d, px in xs if d in dset}
        rows, updates = compute_fund(eid, snaps, hold, close, divs, caps, breaks)
        all_rows += rows
        all_updates += updates
        if len(snaps) > 1:
            cur.execute("DELETE FROM etf_flow WHERE etf_id = %s AND trade_date = ANY(%s)", (eid, dates[1:]))
    if all_rows:
        execute_values(cur, "INSERT INTO etf_flow (etf_id, trade_date, stock_id, prev_date, shares_prev, shares, "
                            "d_shares, active_shares, close, amount, active_amount, weight_prev, weight, action) "
                            "VALUES %s", all_rows, page_size=5000)
    if all_updates:
        execute_values(cur, "UPDATE etf_snapshot s SET prev_as_of = v.p, flow_k = v.k, flow_k_units = v.ku "
                            "FROM (VALUES %s) AS v(p, k, ku, etf_id, as_of) "
                            "WHERE s.etf_id = v.etf_id AND s.as_of = v.as_of",
                       all_updates, template="(%s::date, %s::numeric, %s::numeric, %s, %s::date)")
    print(f"etf_flow 計算 {len(all_rows)} 列（{len(by_fund)} 檔 ETF、{len(all_updates)} 個持股日）")
    if report:
        print_report(cur)


def _yi(x):
    return f"{x / 1e8:+,.1f} 億" if x is not None else "?"


def print_report(cur, top=6):
    """最新持股日：各 ETF 的主動買賣前幾名，以及跨投信的共識。"""
    cur.execute("SELECT max(trade_date) FROM etf_flow")
    d = cur.fetchone()[0]
    if d is None:
        return
    cur.execute("SELECT s.etf_id, e.issuer, e.name, s.flow_k, s.flow_k_units, s.prev_as_of "
                "FROM etf_snapshot s JOIN etf_fund e USING (etf_id) WHERE s.as_of = %s ORDER BY s.etf_id", (d,))
    funds = cur.fetchall()
    cur.execute("SELECT f.etf_id, f.stock_id, COALESCE(st.name, h.name, f.stock_id), f.action, f.d_shares, "
                "       f.active_shares, f.active_amount "
                "FROM etf_flow f LEFT JOIN stock st ON st.stock_id = f.stock_id "
                "LEFT JOIN etf_holding h ON h.etf_id = f.etf_id AND h.as_of = f.trade_date AND h.kind = 'stock' "
                "     AND h.code = f.stock_id "
                "WHERE f.trade_date = %s", (d,))
    flows = defaultdict(list)
    for r in cur.fetchall():
        flows[r[0]].append(r)
    print(f"\n=== 主動式 ETF 進出（持股日 {d}，已扣全面等比例增減）===")
    for eid, issuer, name, k, ku, p in funds:
        fs = flows.get(eid, [])
        cnt = defaultdict(int)
        for r in fs:
            cnt[r[3]] += 1
        ku_s = f"單位數 {float(ku) - 1:+.2%}，" if ku else ""
        k_s = f"{ku_s}持股共同變動 {float(k) - 1:+.2%}" if k else "（第一天，沒有可比較的前一日）"
        print(f"\n{eid} {name}（{issuer}）vs {p}：{k_s}｜"
              + "、".join(f"{ACTION_NAME[a]} {cnt[a]}" for a in ("new", "exit", "add", "cut", "add_rel", "cut_rel")
                         if cnt[a]))
        buys = sorted((r for r in fs if r[3] in ("new", "add", "add_rel")), key=lambda r: -(r[6] or 0))[:top]
        sells = sorted((r for r in fs if r[3] in ("exit", "cut", "cut_rel")), key=lambda r: r[6] or 0)[:top]
        for label, rs in (("買", buys), ("賣", sells)):
            if rs:
                print(f"  {label}：" + "、".join(
                    f"{r[2]} {float(r[5]) / 1000:+,.0f}張 {_yi(float(r[6]) if r[6] is not None else None)}"
                    f"[{ACTION_NAME[r[3]]}]" for r in rs))
    # 跨投信共識（新建倉／加碼 vs 出清／減碼，以投信家數計）
    issuer_of = {eid: issuer for eid, issuer, *_ in funds}
    agg = defaultdict(lambda: {"buy": set(), "sell": set(), "amt": 0.0, "name": ""})
    for eid, fs in flows.items():
        for r in fs:
            a = agg[r[1]]
            a["name"] = r[2]
            a["amt"] += float(r[6] or 0)
            if r[3] in ("new", "add"):
                a["buy"].add(issuer_of.get(eid, eid))
            elif r[3] in ("exit", "cut"):
                a["sell"].add(issuer_of.get(eid, eid))
    for side, label in (("buy", "共識買進"), ("sell", "共識賣出")):
        rs = sorted((v for v in agg.values() if len(v[side]) >= 2), key=lambda v: -abs(v["amt"]))
        if rs:
            print(f"\n{label}（≥2 家投信）：" + "、".join(
                f"{v['name']}（{'/'.join(sorted(v[side]))}，{_yi(v['amt'])}）" for v in rs[:10]))


def main():
    ap = argparse.ArgumentParser(description="主動式 ETF 每日進出（扣申購贖回）→ etf_flow")
    ap.add_argument("--dsn", default=os.environ.get("DATABASE_URL", ""), help="PostgreSQL 連線字串")
    ap.add_argument("--etf", default="", help="只算這些 ETF（逗號分隔）")
    ap.add_argument("--all", action="store_true", help="全部持股日重算（預設只算每檔最近 15 個）")
    ap.add_argument("--report-only", action="store_true", help="照樣計算、只印報表，不寫 DB")
    args = ap.parse_args()
    args.dsn = clean_dsn(args.dsn)
    try:
        sys.stdout.reconfigure(line_buffering=True, errors="replace")
    except (AttributeError, ValueError):
        pass
    if not args.dsn:
        raise SystemExit("需要 --dsn 或環境變數 DATABASE_URL")
    import psycopg2
    conn = psycopg2.connect(args.dsn)
    try:
        ids = [s.strip().upper() for s in args.etf.split(",") if s.strip()] or None
        run(conn, etf_ids=ids, full=args.all, write=not args.report_only)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
