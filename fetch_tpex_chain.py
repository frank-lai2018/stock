r"""fetch_tpex_chain.py — 抓櫃買中心「產業價值鏈資訊平台」的 鏈→節點→子節點→公司，灌進 theme / stock_theme（族群 L2）。

用途：族群分類的 L2 層。官方維護、涵蓋上市櫃＋興櫃，約 45 條產業鏈、300+ 節點，
      比 stock.industry（L1，一檔一類、半導體業一類就 200 檔）細得多，例如「半導體 > IC設計 > 光通訊IC」。
來源：https://ic.tpex.org.tw/introduce.php?ic=<鏈代碼>（每條鏈一頁，節點公司名單內嵌在頁面裡）
存放：theme（layer=2，code 形如 tpex:D000 / tpex:D000:D100 / tpex:D000:D120）
      stock_theme（source='tpex'、status='confirmed'，掛在最細的那層：有子節點就掛子節點）
時效：同一 (股票, 節點) 持續出現就沿用原本那筆開啟列；這次沒出現的補 valid_to=今天（成分剔除）；
      新出現的以 valid_from=今天新增 → 回測可還原當時成分，不會用到未來資訊。
      只處理「這次有成功抓到」的鏈，抓失敗的鏈不會被誤判成全部剔除。
頻率：建議每月一次（平台更新不頻繁，也對網站友善）。
前置：先跑 schema_theme.sql 建表。

用法：
  python fetch_tpex_chain.py                     # 全部鏈，寫入 DB（讀 DATABASE_URL 或 --dsn）
  python fetch_tpex_chain.py --chains D000,I000  # 只抓半導體、通信網路
  python fetch_tpex_chain.py --dry-run           # 只抓取＋解析、印統計，不寫 DB
"""
import argparse
import html
import os
import re
import sys
import time
import urllib.request
from datetime import date

BASE = "https://ic.tpex.org.tw"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36")
KEEP_GROUPS = ("本國上市公司", "本國上櫃公司", "外國上市公司", "外國上櫃公司")   # 有台股代碼、我們 DB 會有價的分組

RE_CHAIN_LINK = re.compile(r"introduce\.php\?ic=([A-Z0-9]{4})")
RE_TITLE = re.compile(r"<h3>(.*?)產業鏈簡介</h3>", re.S)
RE_NODE_HEAD = re.compile(r'<div id="companyList_(\w+)" title="([^"]*)"')
RE_SUB = re.compile(r'<div id="sc_link_(\w+)" class="subchain[^"]*"><span>[^<]*</span>(.*?)</div>', re.S)
RE_TOKEN = re.compile(r'<b>([^<(]+)\(\d+家\)</b>'
                      r'|company_basic\.php\?stk_code=(\w+)"[^>]*?title="([^"]*)"')


def clean_dsn(dsn):
    """容錯＋防呆：剝掉誤貼進值裡的旗標／引號，並先擋掉明顯不合法的連線字串。（同 nightly.py）"""
    dsn = (dsn or "").strip().strip('"').strip("'").strip()
    if dsn.startswith("--dsn"):                      # 誤把旗標本身貼進值裡
        dsn = dsn[5:].lstrip().lstrip("=").lstrip()
    if dsn and not (dsn.startswith(("postgresql://", "postgres://")) or "=" in dsn):
        raise SystemExit(f"連線字串格式不對：{dsn!r}；"
                         "應為 postgresql://user:pw@host:port/db（或 key=value 形式）")
    return dsn


def http_get(url, retries=2):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    for i in range(retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.read().decode("utf-8", "ignore")
        except Exception as e:
            if i == retries:
                raise
            print(f"    重試 {url}：{e}")
            time.sleep(2 * (i + 1))


def list_chains():
    return sorted(set(RE_CHAIN_LINK.findall(http_get(f"{BASE}/index.php"))))


def _companies(fragment):
    """依出現順序解析「市場分組標籤」與公司連結 → [(stock_id, name, group)]，只留 KEEP_GROUPS。"""
    out, group = [], None
    for m in RE_TOKEN.finditer(fragment):
        if m.group(1):
            group = m.group(1).strip()
        elif group in KEEP_GROUPS:
            out.append((m.group(2), html.unescape(m.group(3)).strip(), group))
    return out


def parse_chain(page):
    """回傳 (鏈名稱, [節點])；節點 = {code, name, subs:[{code, name, companies}], companies}。"""
    t = RE_TITLE.search(page)
    chain_name = html.unescape(t.group(1)).strip() if t else "?"
    nodes, seen = [], set()
    for block in re.split(r'(?=<div id="companyList_)', page)[1:]:
        block = block.split("</noscript>")[0]            # 最後一個節點後面接簡介文字，裡面也有 stk_code，要切掉
        head = RE_NODE_HEAD.match(block)
        if not head or head.group(1) in seen:            # 頁面把每個節點渲染兩次（JS 版＋noscript 版），只取第一份
            continue
        seen.add(head.group(1))
        node = {"code": head.group(1), "name": html.unescape(head.group(2)).strip(), "subs": [], "companies": []}
        pieces = block.split('<table id="sc_company_')
        if len(pieces) > 1:                              # 有子節點
            names = {}
            for code, raw in RE_SUB.findall(pieces[0]):
                txt = html.unescape(re.sub(r"<[^>]+>", "", raw)).replace("\xa0", " ").strip()
                names[code] = re.sub(r"\s*\(\d+家\)\s*$", "", txt).strip()
            for p in pieces[1:]:
                sub_code = p[:p.index('"')]
                node["subs"].append({"code": sub_code, "name": names.get(sub_code, sub_code),
                                     "companies": _companies(p)})
        else:
            node["companies"] = _companies(block)
        nodes.append(node)
    return chain_name, nodes


def build_rows(chain_code, chain_name, nodes):
    """攤平成 theme 列與 membership 列（membership 掛在最細那層）。"""
    themes = [(f"tpex:{chain_code}", chain_name, None)]
    members = []
    for n in nodes:
        ncode = f"tpex:{chain_code}:{n['code']}"
        themes.append((ncode, n["name"], f"tpex:{chain_code}"))
        for sid, sname, grp in n["companies"]:
            members.append((sid, ncode, grp, f"櫃買產業鏈：{chain_name} > {n['name']}"))
        for s in n["subs"]:
            scode = f"tpex:{chain_code}:{s['code']}"
            themes.append((scode, f"{n['name']} > {s['name']}", ncode))
            for sid, sname, grp in s["companies"]:
                members.append((sid, scode, grp, f"櫃買產業鏈：{chain_name} > {n['name']} > {s['name']}"))
    return themes, members


def load(cur, themes, members, chain_codes, today):
    from psycopg2.extras import execute_values

    themes = list({t[0]: t for t in themes}.values())   # 同一 code 只留一筆，免 ON CONFLICT 同句更新兩次
    execute_values(cur, """
        INSERT INTO theme (code, name, layer, parent_code) VALUES %s
        ON CONFLICT (code) DO UPDATE SET name=EXCLUDED.name, parent_code=EXCLUDED.parent_code,
                                         is_active=true, updated_at=now()""",
                   [(c, n, 2, p) for c, n, p in themes], template="(%s,%s,%s,%s)")
    # 本次抓到的鏈裡，已經不存在的節點 → 停用
    seen = [c for c, _, _ in themes]
    for cc in chain_codes:
        cur.execute("UPDATE theme SET is_active=false, updated_at=now() "
                    "WHERE layer=2 AND code LIKE %s AND NOT (code = ANY(%s)) AND is_active",
                    (f"tpex:{cc}:%", seen))

    cur.execute("SELECT code, theme_id FROM theme WHERE layer=2")
    tid = dict(cur.fetchall())
    cur.execute("SELECT stock_id FROM stock")
    known = {r[0] for r in cur.fetchall()}

    rows, skipped = {}, 0
    for sid, code, grp, ev in members:
        if sid not in known:
            skipped += 1
            continue
        rows[(sid, tid[code])] = (sid, tid[code], "confirmed", "tpex", grp, 1.0, ev, today)
    rows = list(rows.values())

    # 1) 新增／更新開啟列
    execute_values(cur, """
        INSERT INTO stock_theme (stock_id, theme_id, status, source, role, confidence, evidence, valid_from)
        VALUES %s
        ON CONFLICT (stock_id, theme_id) WHERE valid_to IS NULL
        DO UPDATE SET role=EXCLUDED.role, evidence=EXCLUDED.evidence, updated_at=now()""", rows)

    # 2) 這次抓到的鏈底下、但本次沒出現的開啟列 → 剔除（今天才加的直接刪，避免同日重跑撞 PK）
    cur.execute("CREATE TEMP TABLE _seen (stock_id VARCHAR(10), theme_id INT) ON COMMIT DROP")
    execute_values(cur, "INSERT INTO _seen VALUES %s", [(r[0], r[1]) for r in rows])
    scope = [tid[c] for c in seen if c in tid]
    cur.execute("""
        DELETE FROM stock_theme st
         WHERE st.source='tpex' AND st.valid_to IS NULL AND st.valid_from=%s AND st.theme_id = ANY(%s)
           AND NOT EXISTS (SELECT 1 FROM _seen s WHERE s.stock_id=st.stock_id AND s.theme_id=st.theme_id)""",
                (today, scope))
    deleted = cur.rowcount
    cur.execute("""
        UPDATE stock_theme st SET valid_to=%s, updated_at=now()
         WHERE st.source='tpex' AND st.valid_to IS NULL AND st.theme_id = ANY(%s)
           AND NOT EXISTS (SELECT 1 FROM _seen s WHERE s.stock_id=st.stock_id AND s.theme_id=st.theme_id)""",
                (today, scope))
    return len(rows), skipped, cur.rowcount + deleted


def main():
    ap = argparse.ArgumentParser(description="抓櫃買產業價值鏈 → theme / stock_theme（族群 L2）")
    ap.add_argument("--dsn", default=os.environ.get("DATABASE_URL", ""), help="PostgreSQL 連線字串")
    ap.add_argument("--chains", default="", help="只抓指定鏈代碼（逗號分隔，如 D000,I000）；預設全部")
    ap.add_argument("--delay", type=float, default=0.6, help="每條鏈請求間隔秒數（預設 0.6）")
    ap.add_argument("--dry-run", action="store_true", help="只抓取解析、印統計，不寫 DB")
    args = ap.parse_args()
    args.dsn = clean_dsn(args.dsn)
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except (AttributeError, ValueError):
        pass
    if not args.dry_run and not args.dsn:
        raise SystemExit("需要 --dsn 或環境變數 DATABASE_URL（或改用 --dry-run）")

    chains = [c.strip() for c in args.chains.split(",") if c.strip()] or list_chains()
    print(f"=== 櫃買產業價值鏈：{len(chains)} 條鏈 ===")
    all_themes, all_members, ok = [], [], []
    for cc in chains:
        try:
            name, nodes = parse_chain(http_get(f"{BASE}/introduce.php?ic={cc}"))
        except Exception as e:
            print(f"  ⚠️ {cc} 抓取失敗，本次略過（不會剔除它既有的成分）：{e}")
            continue
        themes, members = build_rows(cc, name, nodes)
        n_sub = sum(len(n["subs"]) for n in nodes)
        print(f"  {cc} {name:<14} 節點 {len(nodes):>3}｜子節點 {n_sub:>3}｜上市櫃公司連結 {len(members):>4}")
        all_themes += themes
        all_members += members
        ok.append(cc)
        time.sleep(args.delay)

    print(f"合計：族群節點 {len(all_themes)}、個股×節點 {len(all_members)}、"
          f"不重複個股 {len({m[0] for m in all_members})}")
    if args.dry_run:
        print("(--dry-run：未寫入 DB)")
        return

    import psycopg2
    today = date.today()
    conn = psycopg2.connect(args.dsn)
    try:
        with conn, conn.cursor() as cur:
            n, skipped, closed = load(cur, all_themes, all_members, ok, today)
        print(f"入庫完成：有效成分 {n} 筆｜不在 stock 表（興櫃/已下市）略過 {skipped} 筆｜本次剔除 {closed} 筆")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
