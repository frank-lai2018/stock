r"""fetch_news_rss.py — 用 Google News RSS 抓「自選股清單」的個股新聞 → 入庫 news 表。

完全不透過 FinMind、免 token、免額度。RSS 格式穩定：每檔以中文名查詢，取近期新聞。
去重在程式層（依 url，同一檔已存在的 url 跳過）→ 可重複執行、定期累積，不會灌重複。

流程：讀代碼清單 → 從 stock 表查中文名 → 查 Google News RSS「<名稱> 股票」→ 解析近 --days 天
      → 濾掉 news 表已有的 url → INSERT 進 news（stock_id/title/content/source/url/published_at）。

前置：news 表已建（schema.sql）。連線：--dsn 或環境變數 DATABASE_URL。
用法：
  python fetch_news_rss.py 2330 2317 --dsn "postgresql://frank:pwd@localhost:5432/twstock"
  python fetch_news_rss.py --codes-file watchlist.txt --days 60 --dsn "..."
  python fetch_news_rss.py 2330 --dry-run --dsn "..."          # 不寫入，只印抓到幾則
"""
import argparse
import os
import re
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
RSS = "https://news.google.com/rss/search?q={q}&hl=zh-TW&gl=TW&ceid=TW:zh-Hant"
_TAG = re.compile(r"<[^>]+>")


def fetch_rss(name, suffix):
    q = urllib.parse.quote(f"{name} {suffix}".strip())
    req = urllib.request.Request(RSS.format(q=q), headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=40) as r:      # news.google.com 憑證正常，用標準 TLS
        return r.read()


def parse_items(raw, since):
    """RSS bytes → [(title, content, source, url, published_at)]；只留 published_at >= since。"""
    out = []
    root = ET.fromstring(raw)
    for it in root.findall(".//item"):
        title = (it.findtext("title") or "").strip()
        link = (it.findtext("link") or "").strip()
        if not title or not link:
            continue
        pub = it.findtext("pubDate")
        try:
            ts = parsedate_to_datetime(pub) if pub else None
        except (TypeError, ValueError):
            ts = None
        if ts is not None:
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            if ts < since:
                continue
        src_el = it.find("{*}source")
        source = (src_el.text.strip() if src_el is not None and src_el.text else "Google News")
        desc = _TAG.sub("", it.findtext("description") or "").strip()
        out.append((title, desc or title, source, link, ts))
    return out


def main():
    ap = argparse.ArgumentParser(description="Google News RSS 個股新聞 → news 表（不碰 FinMind）")
    ap.add_argument("codes", nargs="*", help="股票代碼（可多個）")
    ap.add_argument("--codes-file", default="", help="從檔案讀代碼（每行一個或逗號分隔）")
    ap.add_argument("--dsn", default=os.environ.get("DATABASE_URL", ""))
    ap.add_argument("--days", type=int, default=90, help="只收近 N 天的新聞（預設 90）")
    ap.add_argument("--suffix", default="股票", help="查詢關鍵字後綴（降雜訊；預設「股票」）")
    ap.add_argument("--delay", type=float, default=2.0, help="每檔間隔秒（禮貌；預設 2）")
    ap.add_argument("--dry-run", action="store_true", help="不寫 DB，只印抓到幾則")
    args = ap.parse_args()

    if args.codes_file:
        txt = open(args.codes_file, encoding="utf-8").read()
        codes = [c.strip() for c in txt.replace(",", "\n").splitlines() if c.strip()]
    else:
        codes = [c.strip() for c in args.codes if c.strip()]
    if not codes:
        raise SystemExit("需給代碼（位置參數）或 --codes-file")
    if not args.dsn:
        raise SystemExit("需要 --dsn 或環境變數 DATABASE_URL")

    import psycopg2
    from psycopg2.extras import execute_values
    conn = psycopg2.connect(args.dsn)
    cur = conn.cursor()
    cur.execute("SELECT stock_id, name FROM stock WHERE stock_id = ANY(%s)", (codes,))
    names = dict(cur.fetchall())
    missing = [c for c in codes if c not in names]
    if missing:
        print(f"⚠️ 這些代碼不在 stock 表、略過：{missing}")

    since = datetime.now(timezone.utc) - timedelta(days=args.days)
    print(f"=== fetch_news_rss｜{len(names)} 檔｜近 {args.days} 天"
          + ("（dry-run）" if args.dry_run else "") + " ===")

    total_new = 0
    for code, name in names.items():
        try:
            items = parse_items(fetch_rss(name, args.suffix), since)
        except Exception as e:
            print(f"  {code} {name}: ✗ {type(e).__name__} {str(e)[:80]}")
            continue
        # 去重：跳過該檔已存在的 url
        cur.execute("SELECT url FROM news WHERE stock_id=%s AND url = ANY(%s)",
                    (code, [i[3] for i in items] or [""]))
        seen = {r[0] for r in cur.fetchall()}
        rows, batch_seen = [], set()
        for title, content, source, url, ts in items:
            if url in seen or url in batch_seen:
                continue
            batch_seen.add(url)
            rows.append((code, title, content, source, url, ts))
        print(f"  {code} {name}: 抓到 {len(items)} 則、新增 {len(rows)} 則")
        if rows and not args.dry_run:
            execute_values(cur,
                "INSERT INTO news (stock_id,title,content,source,url,published_at) VALUES %s", rows)
            conn.commit()
        total_new += len(rows)
        time.sleep(args.delay)

    cur.close(); conn.close()
    print(f"=== {'dry-run 未寫入' if args.dry_run else '完成'}｜合計新增 {total_new} 則 ===")


if __name__ == "__main__":
    main()
