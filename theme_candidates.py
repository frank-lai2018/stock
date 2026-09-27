r"""theme_candidates.py — 族群 L3（市場題材）：同步題材與起手名單，產生候選清單 CSV 給你確認；--apply 寫回確認結果。

用途：CPO、台積電概念、記憶體這類「市場敘事」會跨好幾個產業鏈節點，無法全自動分類。
      做法是 起手名單（theme_defs.py）＋ 兩種訊號找候選 ＋ 人工確認：
        1) L2 節點：題材關鍵字命中的櫃買產業鏈節點，其成分股列為候選（例：CPO → 光通訊設備）
        2) 股價相關：近 60 日日報酬與「起手名單等權籃子」的相關係數（市場實際把它當同一族群在炒）
      候選不會自動進 DB，要你在 CSV 標 Y 才納入；標 N 會記成 rejected，之後不再推薦。
存放：theme（layer=3）、stock_theme（seed=起手未複核 / confirmed=已確認 / rejected=已否決）
      你已確認或否決過的，重跑不會被覆蓋。
前置：schema_theme.sql、fetch_tpex_chain.py（L2 節點）先跑過。

用法：
  python theme_candidates.py                              # 同步題材與起手名單 → 產生 族群候選_YYYYMMDD.csv
  python theme_candidates.py --themes cpo,memory          # 只處理指定題材
  python theme_candidates.py --apply 族群候選_20260927.csv  # 把 CSV「採用」欄的 Y/N 寫回 DB
"""
import argparse
import csv
import os
import sys
from datetime import date, timedelta

import pandas as pd

from theme_defs import THEMES

SEED_NOTE = "起手名單（Claude 依公開市場資訊提供，待複核）"
CSV_COLS = ["題材代碼", "題材", "代碼", "名稱", "官方產業", "目前狀態", "來源", "角色",
            "相關係數", "分數", "證據", "採用(Y/N)"]


def clean_dsn(dsn):
    """容錯＋防呆：剝掉誤貼進值裡的旗標／引號，並先擋掉明顯不合法的連線字串。（同 nightly.py）"""
    dsn = (dsn or "").strip().strip('"').strip("'").strip()
    if dsn.startswith("--dsn"):                      # 誤把旗標本身貼進值裡
        dsn = dsn[5:].lstrip().lstrip("=").lstrip()
    if dsn and not (dsn.startswith(("postgresql://", "postgres://")) or "=" in dsn):
        raise SystemExit(f"連線字串格式不對：{dsn!r}；"
                         "應為 postgresql://user:pw@host:port/db（或 key=value 形式）")
    return dsn


def sync_themes(cur, defs, today):
    """theme_defs.py → theme（layer=3）＋起手名單（只補沒有開啟列的，不覆蓋你的確認／否決）。"""
    from psycopg2.extras import execute_values
    execute_values(cur, """
        INSERT INTO theme (code, name, layer, keywords, note) VALUES %s
        ON CONFLICT (code) DO UPDATE SET name=EXCLUDED.name, keywords=EXCLUDED.keywords,
                                         note=EXCLUDED.note, is_active=true, updated_at=now()""",
                   [(t["code"], t["name"], 3, t["keywords"], t.get("note")) for t in defs],
                   template="(%s,%s,%s,%s,%s)")
    cur.execute("SELECT code, theme_id FROM theme WHERE layer=3")
    tid = dict(cur.fetchall())
    cur.execute("SELECT stock_id FROM stock")
    known = {r[0] for r in cur.fetchall()}
    rows, missing = [], []
    for t in defs:
        for sid, role in t["seeds"]:
            if sid in known:
                rows.append((sid, tid[t["code"]], "seed", "seed", role, 0.8, SEED_NOTE, today))
            else:
                missing.append(f"{t['code']}:{sid}")
    if rows:
        execute_values(cur, """
            INSERT INTO stock_theme (stock_id, theme_id, status, source, role, confidence, evidence, valid_from)
            VALUES %s ON CONFLICT (stock_id, theme_id) WHERE valid_to IS NULL DO NOTHING""", rows)
    if missing:
        print(f"  ⚠️ 起手名單有代碼不在 stock 表，已略過：{', '.join(missing)}")
    return tid


def load_l2_paths(cur):
    """L2 節點的完整路徑（鏈 > 節點 > 子節點）與成分股。"""
    cur.execute("SELECT code, name, parent_code FROM theme WHERE layer=2 AND is_active")
    nodes = {c: (n, p) for c, n, p in cur.fetchall()}

    def path(code):
        name, parent = nodes[code]
        if parent is None:
            return name
        chain = parent.split(":")[0] + ":" + parent.split(":")[1]   # tpex:D000
        return f"{nodes[chain][0]} > {name}" if chain in nodes else name

    cur.execute("""SELECT t.code, st.stock_id FROM stock_theme st JOIN theme t USING (theme_id)
                    WHERE t.layer=2 AND t.is_active AND st.valid_to IS NULL AND st.status='confirmed'""")
    members = {}
    for code, sid in cur.fetchall():
        members.setdefault(code, set()).add(sid)
    return {c: path(c) for c in nodes if nodes[c][1] is not None}, members


def load_returns(cur, days=60):
    """近 days 個交易日的日報酬（寬表）與 in_universe（交易天數足、20 日均成交額 ≥ 500 萬）。"""
    cur.execute("SELECT max(trade_date) FROM price_daily")
    last = cur.fetchone()[0]
    cur.execute("""SELECT p.stock_id, p.trade_date, p.adj_close, p.amount
                     FROM price_daily p JOIN stock s USING (stock_id)
                    WHERE s.security_type='stock' AND p.trade_date >= %s""",
                (last - timedelta(days=int(days * 1.7) + 10),))
    df = pd.DataFrame(cur.fetchall(), columns=["stock_id", "trade_date", "adj_close", "amount"])
    px = df.pivot(index="trade_date", columns="stock_id", values="adj_close").astype(float).sort_index()
    amt = df.pivot(index="trade_date", columns="stock_id", values="amount").astype(float).sort_index()
    ret = px.pct_change(fill_method=None).iloc[-days:]
    universe = set(ret.columns[(ret.notna().sum() >= days - 5) & (amt.iloc[-20:].mean() >= 5_000_000)])
    return ret, universe, last


def candidates_for(theme, tid, ret, universe, l2_paths, l2_members, open_rows, min_corr, top):
    """回傳 [(stock_id, source, corr, score, evidence)]，已排除起手／已確認／已否決的。"""
    kws = [k.lower() for k in theme["keywords"]]
    node_hits = {}
    for code, p in l2_paths.items():
        if kws and any(k in p.lower() for k in kws):
            for sid in l2_members.get(code, ()):
                node_hits.setdefault(sid, []).append(p)

    members = [sid for (sid, t), st in open_rows.items() if t == tid and st in ("seed", "confirmed")]
    basket_cols = [s for s in members if s in ret.columns and ret[s].notna().sum() >= 40]
    corr = pd.Series(dtype=float)
    if len(basket_cols) >= 3:
        basket = ret[basket_cols].mean(axis=1)
        pool = [s for s in universe if s not in basket_cols]
        corr = ret[pool].corrwith(basket).dropna()

    taken = {sid for (sid, t) in open_rows if t == tid}          # 起手／確認／否決過的都不再推薦
    out = {}
    for sid, paths in node_hits.items():
        if sid in taken:
            continue
        c = float(corr.get(sid, float("nan")))
        ev = "櫃買節點：" + "；".join(sorted(set(paths))[:2])
        if c == c:                                              # 不是 NaN
            ev += f"｜近60日與起手籃子相關 {c:.2f}"
        out[sid] = ("node", c, 0.4 + 0.6 * max(c if c == c else 0.0, 0.0), ev)
    for sid, c in corr[corr >= min_corr].sort_values(ascending=False).items():
        if sid in taken:
            continue
        if sid in out:
            out[sid] = ("node+corr",) + out[sid][1:]
        else:
            out[sid] = ("corr", float(c), 0.6 * float(c), f"近60日與起手籃子相關 {c:.2f}")
    ranked = sorted(out.items(), key=lambda kv: -kv[1][2])[:top]
    return [(sid, src, c, s, ev) for sid, (src, c, s, ev) in ranked]


def generate(conn, defs, min_corr, top, out_path):
    today = date.today()
    with conn, conn.cursor() as cur:
        tid = sync_themes(cur, defs, today)
        l2_paths, l2_members = load_l2_paths(cur)
        ret, universe, last = load_returns(cur)
        cur.execute("SELECT stock_id, name, industry FROM stock")
        info = {s: (n, i) for s, n, i in cur.fetchall()}
        cur.execute("""SELECT stock_id, theme_id, status, source, role, evidence FROM stock_theme
                        WHERE valid_to IS NULL AND theme_id = ANY(%s)""", (list(tid.values()),))
        existing = cur.fetchall()
    open_rows = {(s, t): st for s, t, st, *_ in existing}

    print(f"股價相關性：近 60 個交易日（至 {last}），可比較母體 {len(universe)} 檔")
    lines = []
    for t in defs:
        t_id = tid[t["code"]]
        mine = [e for e in existing if e[1] == t_id]
        for s, _, st, src, role, ev in sorted(mine, key=lambda e: (e[2] != "confirmed", e[0])):
            n, ind = info.get(s, ("?", "?"))
            lines.append([t["code"], t["name"], s, n, ind, st, src, role or "", "", "", ev or "",
                          "N" if st == "rejected" else "Y"])
        cands = candidates_for(t, t_id, ret, universe, l2_paths, l2_members, open_rows, min_corr, top)
        for s, src, c, score, ev in cands:
            n, ind = info.get(s, ("?", "?"))
            lines.append([t["code"], t["name"], s, n, ind, "（候選）", src, "",
                          f"{c:.2f}" if c == c else "", f"{score:.2f}", ev, ""])
        preview = "、".join(f"{info.get(s, ('?',))[0]}({c:.2f})" if c == c else info.get(s, ('?',))[0]
                           for s, _, c, _, _ in cands[:6])
        print(f"  {t['name']:<22} 目前成員 {sum(1 for e in mine if e[2] in ('seed','confirmed')):>2} 檔｜"
              f"候選 {len(cands):>2} 檔｜前幾名：{preview}")

    with open(out_path, "w", newline="", encoding="utf-8-sig") as f:       # utf-8-sig：Excel 直接開不會亂碼
        w = csv.writer(f)
        w.writerow(CSV_COLS)
        w.writerows(lines)
    print(f"\n候選清單已輸出：{out_path}")
    print("  → 用 Excel 開，「採用(Y/N)」欄：Y=納入／確認，N=否決，空白=先不處理；起手名單預設 Y，覺得不對就改 N。")
    print(f"  → 改完執行：python theme_candidates.py --apply {os.path.basename(out_path)}")


def read_table(path):
    """讀人工勾選後的清單。Excel 另存時常改掉編碼與分隔符（繁中 Windows 預設 Big5、
    「文字檔」為 Tab 分隔、「Unicode 文字」為 UTF-16），這裡都自動判斷；
    並補回 Excel 吃掉的股票代碼開頭 0。"""
    raw = open(path, "rb").read()
    for enc in ("utf-8-sig", "utf-16", "cp950"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise SystemExit(f"{path}：無法判斷檔案編碼（試過 UTF-8／UTF-16／Big5），請另存為「CSV UTF-8」再試")
    lines = text.splitlines()
    delim = "\t" if lines and "\t" in lines[0] else ","
    rows = []
    for r in csv.DictReader(lines, delimiter=delim):
        r = {(k or "").strip(): (v or "").strip() for k, v in r.items()}
        if r.get("代碼", "").isdigit() and len(r["代碼"]) < 4:
            r["代碼"] = r["代碼"].zfill(4)
        rows.append(r)
    if rows and "採用(Y/N)" not in rows[0]:
        raise SystemExit(f"{path}：找不到「採用(Y/N)」欄，欄位是 {list(rows[0])}")
    print(f"讀入 {path}：{len(rows)} 列（編碼 {enc}、{'Tab' if delim == chr(9) else '逗號'}分隔）")
    return rows


def apply(conn, path):
    today = date.today().isoformat()
    rows = read_table(path)
    yes, no = {"Y", "y", "是", "1"}, {"N", "n", "否", "0"}
    done = {"confirmed": 0, "rejected": 0}
    marks = {}
    with conn, conn.cursor() as cur:
        cur.execute("SELECT code, theme_id FROM theme WHERE layer=3")
        tid = dict(cur.fetchall())
        for r in rows:
            mark = r.get("採用(Y/N)", "")
            if mark not in yes | no or r.get("題材代碼") not in tid:
                continue
            status = "confirmed" if mark in yes else "rejected"
            t_id, sid = tid[r["題材代碼"]], r["代碼"]
            key = (r.get("題材", r["題材代碼"]), status)
            marks[key] = marks.get(key, 0) + 1
            note = f"｜{today} 人工{'確認' if status == 'confirmed' else '否決'}"
            cur.execute("""UPDATE stock_theme SET status=%s, role=COALESCE(NULLIF(%s,''), role),
                                  evidence=COALESCE(evidence,'') || %s, updated_at=now()
                            WHERE stock_id=%s AND theme_id=%s AND valid_to IS NULL AND status<>%s""",
                        (status, r.get("角色", ""), note, sid, t_id, status))
            if cur.rowcount == 0:
                try:
                    score = min(max(float(r.get("分數") or ""), 0.0), 1.0)
                except ValueError:
                    score = None
                cur.execute("""INSERT INTO stock_theme (stock_id, theme_id, status, source, role, confidence, evidence)
                               VALUES (%s,%s,%s,%s,NULLIF(%s,''),%s,%s)
                               ON CONFLICT (stock_id, theme_id) WHERE valid_to IS NULL DO NOTHING""",
                            (sid, t_id, status, r.get("來源") or "manual", r.get("角色", ""),
                             score, (r.get("證據") or "") + note))
            done[status] += cur.rowcount
    for theme in dict.fromkeys(k[0] for k in marks):
        print(f"  {theme:<24} Y {marks.get((theme, 'confirmed'), 0):>3}　N {marks.get((theme, 'rejected'), 0):>3}")
    print(f"已寫回：確認 {done['confirmed']} 筆、否決 {done['rejected']} 筆（狀態沒變的不重複計）")


def main():
    ap = argparse.ArgumentParser(description="族群 L3：同步題材與起手名單、產生候選清單、寫回確認結果")
    ap.add_argument("--dsn", default=os.environ.get("DATABASE_URL", ""), help="PostgreSQL 連線字串")
    ap.add_argument("--themes", default="", help="只處理指定題材代碼（逗號分隔）；預設 theme_defs.py 全部")
    ap.add_argument("--min-corr", type=float, default=0.55, help="股價相關候選門檻（預設 0.55）")
    ap.add_argument("--top", type=int, default=25, help="每個題材最多列幾檔候選（預設 25）")
    ap.add_argument("--out", default="", help="候選 CSV 輸出路徑（預設 族群候選_YYYYMMDD.csv）")
    ap.add_argument("--apply", default="", help="把指定 CSV 的「採用(Y/N)」寫回 DB")
    args = ap.parse_args()
    args.dsn = clean_dsn(args.dsn)
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except (AttributeError, ValueError):
        pass
    if not args.dsn:
        raise SystemExit("需要 --dsn 或環境變數 DATABASE_URL")

    import psycopg2
    conn = psycopg2.connect(args.dsn)
    try:
        if args.apply:
            apply(conn, args.apply)
            return
        want = {c.strip() for c in args.themes.split(",") if c.strip()}
        defs = [t for t in THEMES if not want or t["code"] in want]
        if want - {t["code"] for t in defs}:
            raise SystemExit(f"theme_defs.py 沒有這些題材：{sorted(want - {t['code'] for t in defs})}")
        out = args.out or f"族群候選_{date.today():%Y%m%d}.csv"
        generate(conn, defs, args.min_corr, args.top, out)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
