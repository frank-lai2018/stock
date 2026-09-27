r"""build_etf_flow.py — 主動式 ETF 每日進出：相鄰兩個持股日相減、扣掉「全面等比例」的增減 → etf_flow，並印最新一日的異動。

主動調整 ＝ 本日股數 − 前日股數 × k。k 是當天持股的「共同比例」：最多檔持股一起變動的那個比例
  （每檔 本日股數÷前日股數，取落在 ±0.5 個百分點內最多檔的那一群的中位數；群聚不到 25% 且不到 5 檔就當 1）。
  例：00981A 2026-09-24 有變動的 30 檔全部減少、共同比例 −1.7%（當天淨贖回 1.66%，經理人全面等比例賣）；
      扣掉之後，真正主動賣的是日月光投控、信驊、台積電、緯穎，旺矽等 11 檔只是跟著等比例賣出。
為什麼不直接用單位數比例：實測經理人多半「不會」在申購贖回當天等比例買賣——錢先進出現金、之後才分批處理
  （00981A 9/16、9/17 單位數各 +0.8%，持股幾乎沒動）。用單位數去扣，會把「只加碼一檔」誤判成隨申贖。
  單位數比例仍存在 etf_snapshot.flow_k_units 對照。
佔位股（權重 < 0.01%，或只有 1 張且權重 < 0.05%；例如統一、復華、富邦在很多檔各留 1 張）一律當 0 股：
  從 1 張買到 5,000 張算「新建倉」。
門檻：|主動調整| ≥ max(前日部位 1%, 共同比例應增減量的 30%, 1 張) 才算加碼／減碼；
      沒過門檻的歸為 flow（整張執行造成的零頭，或單純跟著全面等比例增減）。
公司行動（不是交易）：先把前日股數換算到本日的股本基礎，再算增減；只剩零頭的標為 corp。
  倍數用官方表（corp_action.share_ratio，fetch_corp_actions.py 每天抓）：配股 1＋無償配股率、分割／面額變更換股率、
  減資每千股換發股數÷1000。例：緯穎 2026-09-02 除權配股每千股配 1,982.8 股 → ×2.9828（股價 7,800 → 2,615）；
  早期版本用價格斷點猜倍數，只認 2／4／5／10 倍，這天被誤判成 3 家投信共識加碼（00981A 一檔就 +112 億）。
  入帳時點各家不同：股數比較接近「前日×倍數」而不是「沒變」，就當這天入帳；配股要等新股撥入的（KY 股常見），
  除權後 75 天內股數剛好放大該倍數（±0.3%）的那天才算。判斷前先扣掉當天的共同比例（全面等比例賣 2% 的日子，
  配股 1% 也分得出來）。
  附近沒有官方事件、原始收盤價卻超出漲跌幅（< 0.85 或 > 1.18 倍）而且 ETF 股數大致反向變動的，
  才退回舊方法：倍數用價格推回、對齊常見倍數，並印出提醒（通常是官方表還沒抓到）。

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
from math import log, prod
from statistics import median

DUST_WEIGHT = 0.01        # 權重 < 0.01%（顯示為 0.00%）視為佔位股
DUST_LOT_WEIGHT = 0.05    # …只有 1 張、權重 < 0.05% 也是（富邦、野村、中信的 1 張佔位股有 0.01～0.02%）
MIN_REL = 0.01            # 主動調整至少是前日部位的 1%
FLOW_TOL = 0.3            # …且至少是申購贖回應增減量的 30%
MIN_SHARES = 1000         # …且至少 1 張
RECENT = 15               # 平常重算最近幾個持股日

ACTION_NAME = {"new": "新建倉", "exit": "出清", "add": "加碼", "cut": "減碼",
               "add_rel": "相對加碼", "cut_rel": "相對減碼", "flow": "隨申贖", "corp": "分割/配股/減資"}
NICE = (1.5, 2, 2.5, 3, 4, 5, 8, 10, 20)          # 常見的分割／面額變更倍數（只在沒有官方事件時用）
BREAK_LO, BREAK_HI = 0.85, 1.18                    # 單日收盤價超出漲跌幅（±10%）→ 只可能是公司行動
LATE_DAYS = 75            # 配股晚入帳：除權後幾天內，股數剛好放大該倍數也算
LATE_TOL = 0.003          # …倍數要對到 ±0.3%
NEAR_DAYS = 5             # 價格斷點前後幾天內有官方事件 → 以官方為準，不用價格推


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
    """佔位股：權重 < 0.01%，或只有 1 張且權重 < 0.05%。小基金 1 張高價股（00981A 剛上市時信驊 1 張占 0.16%）仍算部位。"""
    w = float(weight) if weight is not None else None
    if w is not None and w < DUST_WEIGHT:
        return True
    return shares <= 1000 and (w is None or w < DUST_LOT_WEIGHT)


def price_breaks(closes):
    """closes：{code: [(date, close)]}（依日期）。回傳 {code: [(date, g)]}：單日原始收盤價變動超出漲跌幅的日子。"""
    out = defaultdict(list)
    for code, xs in closes.items():
        for (_, c0), (d1, c1) in zip(xs, xs[1:]):
            if c0 and c1 and not BREAK_LO <= c1 / c0 <= BREAK_HI:
                out[code].append((d1, c1 / c0))
    return out


def snap_factor(f):
    """價格推回的倍數對齊到常見倍數（×3 的 2.99 → 3）；對不上（例如減資）就原樣回傳。"""
    for m in NICE:
        for x in (m, 1 / m):
            if abs(f / x - 1) <= 0.12:
                return x
    return f


def load_events(cur, since):
    """官方公司行動（corp_action）→ {股號: [(事件日, 股數倍數 m)]}；純現金股利（m＝1）不列。
    m＝持有人股數「事件後÷事件前」（share_ratio）：配股 1＋無償配股率、分割／面額變更換股率、減資換發股數÷1000。
    share_ratio 空白（上市詳細資料還沒查到）：配股退回 FinMind 股票股利，再不行用價格比例（含現金股利時會高估，
    詳細資料補到後重算就好）；其他用價格比例對齊常見倍數。"""
    cur.execute("SELECT stock_id, action_date, kind, ratio, share_ratio FROM corp_action WHERE action_date >= %s",
                (since,))
    rows = cur.fetchall()
    cur.execute("SELECT stock_id, ex_stock_date, 1 + stock_dividend / 10 FROM dividend "
                "WHERE stock_dividend > 0 AND ex_stock_date >= %s", (since,))
    finmind = {(sid, ex): float(r) for sid, ex, r in cur.fetchall()}
    events = defaultdict(list)
    for sid, d, kind, ratio, share in rows:
        if share is not None:
            m = float(share)
        elif kind == "div":
            m = finmind.get((sid, d)) or 1 / float(ratio)
        else:
            m = snap_factor(1 / float(ratio))
        if abs(m - 1) > 1e-6:
            events[sid].append((d, m))
    return events


def in_interval(code, p, c, events, breaks):
    return any(p < d <= c for d, _ in events.get(code, ())) or any(p < d <= c for d, _ in breaks.get(code, ()))


def corp_factor(code, p, c, q, events, breaks, credited, notes=None):
    """(p, c] 之間公司行動造成的股數倍數；沒有（或這檔 ETF 還沒入帳）回 None。q＝本日÷前日股數（已扣共同比例）。
    1. 官方事件在區間內：股數比較接近「前日×倍數」而不是「沒變」→ 這天入帳，用官方倍數（同區間多個事件相乘）。
    2. 晚入帳：LATE_DAYS 天內的官方事件、前面還沒入帳，股數剛好放大該倍數（±LATE_TOL）。
    3. 附近沒有官方事件、原始收盤價卻有斷點、股數大致反向變動：倍數用價格推回（舊方法），並記一筆提醒。
    credited：已入帳的 (股號, 事件日)，同一個事件不會算兩次。"""
    evs = events.get(code, ())
    inside = [(d, m) for d, m in evs if p < d <= c]
    if inside:
        m = prod(x for _, x in inside)
        if abs(log(q / m)) < abs(log(q)):
            credited.update((code, d) for d, _ in inside)
            return m
        return None
    for d, m in evs:
        if d <= c and (c - d).days <= LATE_DAYS and (code, d) not in credited and abs(q / m - 1) <= LATE_TOL:
            credited.add((code, d))
            return m
    g = prod(x for d, x in breaks.get(code, ()) if p < d <= c)
    if g != 1.0 and abs(q - 1) > 0.2 and abs(q * g - 1) < 0.25:      # 股數大致反向跟著價格變
        near = timedelta(days=NEAR_DAYS)
        if not any(p - near <= d <= c + near for d, _ in evs):
            f = snap_factor(1 / g)
            if notes is not None:
                notes.append(f"{code} {p}~{c}：收盤價 ×{g:.3f}，官方表沒有事件 → 用價格推回 ×{f:g}")
            return f
    return None


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


def compute_fund(etf_id, snaps, hold, close, events, breaks, notes=None):
    """snaps：[(as_of, units, nav_total)] 依日期排序；hold：{as_of: {code: (shares, weight, amount)}}。
    events：load_events()；breaks：price_breaks()；notes：退回價格推倍數時的提醒（list，會被 append）。
    回傳 (flow 列, snapshot 更新)。etf_flow 的 shares_prev／shares 存原始股數，d_shares 已扣公司行動（＝實際買賣）。"""
    rows, updates = [], []
    credited = set()                                      # 已入帳的 (股號, 事件日)
    for (p, u0, _), (c, u1, nav1) in zip(snaps, snaps[1:]):
        h0, h1 = hold.get(p), hold.get(c)
        if h0 is None or h1 is None:
            continue
        eff0 = {k: (0 if is_dust(s, w) else s) for k, (s, w, _) in h0.items()}
        eff1 = {k: (0 if is_dust(s, w) else s) for k, (s, w, _) in h1.items()}
        both = [x for x, v in eff0.items() if v > 0 and eff1.get(x, 0) > 0]
        # 先用區間內沒有公司行動的持股估共同比例，判斷配股入帳時扣掉（全面等比例賣 2% 的日子，配股 1% 也分得出來）
        k0 = common_factor([eff1[x] / eff0[x] for x in both if not in_interval(x, p, c, events, breaks)])
        fac = {}                                          # 公司行動倍數：前日股數 × fac＝本日股本基礎
        for x in both:
            f = corp_factor(x, p, c, eff1[x] / eff0[x] / k0, events, breaks, credited, notes)
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
    events = load_events(cur, d_min - timedelta(days=LATE_DAYS + 5))     # 晚入帳要往前看 LATE_DAYS 天

    all_rows, all_updates, notes = [], [], []
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
        breaks = price_breaks(closes)                    # 只給「官方表沒有事件」時的退路用
        dset = set(dates)
        close = {(sid, d): px for sid, xs in closes.items() for d, px in xs if d in dset}
        fund_notes = []
        rows, updates = compute_fund(eid, snaps, hold, close, events, breaks, fund_notes)
        notes += [f"{eid} {n}" for n in fund_notes]
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
    if notes:
        print(f"⚠️ {len(notes)} 處公司行動不在官方表（fetch_corp_actions 沒抓到？），改用價格推倍數：")
        for n in notes[:10]:
            print("   " + n)
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
