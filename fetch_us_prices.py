r"""fetch_us_prices.py — 抓美股日線（Yahoo Finance）→ us_price_daily。代號清單在 us_theme_defs.py。

來源：Yahoo Finance chart API（query1.finance.yahoo.com/v8/finance/chart），免金鑰、一檔一請求、約 80 檔。
      非官方服務，偶爾會被擋（HTTP 429）；失敗的代號會列出來，隔晚重跑會自己補上。
調整：adj_close 含分割與股息。Yahoo 遇到除息、分割會回頭改整段歷史，所以每次拿抓到的資料比對庫裡
      重疊的日期，對不上（或庫裡還沒有這檔）就整檔用 --full-range 重抓、整段換掉，同一檔永遠是同一套基準。
未收盤：台灣晚上 9 點半後美股開盤，這時執行會抓到盤中的 K 棒；最後一根還沒收盤就丟掉，只存已收盤的。
前置：schema_us.sql。

用法：
  python fetch_us_prices.py                      # 每晚：抓近 1 個月，對不上的整檔重抓
  python fetch_us_prices.py --range 5y           # 第一次：回補 5 年（整檔換掉）
  python fetch_us_prices.py --symbols NVDA,^SOX  # 只抓指定代號
"""
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

from us_theme_defs import all_symbols

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/126.0 Safari/537.36")
HOSTS = ("query1.finance.yahoo.com", "query2.finance.yahoo.com")
URL = ("https://{host}/v8/finance/chart/{sym}?range={rng}&interval=1d"
       "&events=div%7Csplit&includeAdjustedClose=true")
ADJ_TOLERANCE = 1e-4          # 重疊日 adj_close 相對差超過這個就當作 Yahoo 改過歷史（除息約 0.1% 以上）


def clean_dsn(dsn):
    """容錯＋防呆：剝掉誤貼進值裡的旗標／引號，並先擋掉明顯不合法的連線字串。（同 nightly.py）"""
    dsn = (dsn or "").strip().strip('"').strip("'").strip()
    if dsn.startswith("--dsn"):                      # 誤把旗標本身貼進值裡
        dsn = dsn[5:].lstrip().lstrip("=").lstrip()
    if dsn and not (dsn.startswith(("postgresql://", "postgres://")) or "=" in dsn):
        raise SystemExit(f"連線字串格式不對：{dsn!r}；"
                         "應為 postgresql://user:pw@host:port/db（或 key=value 形式）")
    return dsn


class NoData(Exception):
    """代號不存在或已下市：重試也沒用。"""


def fetch_chart(sym, rng, retries=3):
    """回傳 chart result（dict）；兩個主機輪流試，429／5xx／斷線退避重試。"""
    last = None
    for attempt in range(retries):
        for host in HOSTS:
            url = URL.format(host=host, sym=urllib.parse.quote(sym, safe=""), rng=rng)
            try:
                req = urllib.request.Request(url, headers={"User-Agent": UA})
                with urllib.request.urlopen(req, timeout=30) as r:
                    j = json.loads(r.read().decode("utf-8"))
            except urllib.error.HTTPError as e:
                if e.code == 404:
                    raise NoData("HTTP 404（代號不存在？）") from e
                last = e
                continue
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
                last = e
                continue
            err = (j.get("chart") or {}).get("error")
            if err:
                raise NoData(f"{err.get('code')}: {err.get('description')}")
            return j["chart"]["result"][0]
        time.sleep(2 + attempt * 3)
    raise last


def parse_chart(result, now_ts=None):
    """chart result → [(trade_date, open, high, low, close, adj_close, volume)]，丟掉還沒收盤的最後一根。"""
    meta = result.get("meta") or {}
    ts = result.get("timestamp") or []
    quote = ((result.get("indicators") or {}).get("quote") or [{}])[0]
    adj = ((result.get("indicators") or {}).get("adjclose") or [{}])[0].get("adjclose") or quote.get("close") or []
    if not ts:
        return []
    now_ts = time.time() if now_ts is None else now_ts
    period = (meta.get("currentTradingPeriod") or {}).get("regular") or {}
    if period and now_ts < period.get("end", 0) and ts[-1] >= period.get("start", 0):
        ts = ts[:-1]                                       # 盤中：當天這根還會變
    offset = int(meta.get("gmtoffset") or 0)               # K 棒時間是開盤時刻，換成交易所當地日期
    at = lambda key, i: (quote.get(key) or [])[i] if i < len(quote.get(key) or []) else None  # noqa: E731
    rows = {}
    for i, t in enumerate(ts):
        close, a = at("close", i), (adj[i] if i < len(adj) else None)
        if close is None or a is None:
            continue
        d = datetime.fromtimestamp(t + offset, tz=timezone.utc).date()
        vol = at("volume", i)
        rows[d] = (d, at("open", i), at("high", i), at("low", i), close, a, int(vol) if vol is not None else None)
    return [rows[d] for d in sorted(rows)]


def consistent(cur, sym, rows):
    """庫裡重疊日期的 adj_close 是否和這次抓到的一致（Yahoo 沒有回頭改歷史）。庫裡沒有重疊時回傳 None。"""
    cur.execute("SELECT trade_date, adj_close FROM us_price_daily WHERE symbol=%s AND trade_date >= %s",
                (sym, rows[0][0]))
    old = {d: float(v) for d, v in cur.fetchall() if v is not None}
    pairs = [(old[r[0]], float(r[5])) for r in rows if r[0] in old]
    if not pairs:
        return None
    return all(abs(new / prev - 1) <= ADJ_TOLERANCE for prev, new in pairs if prev)


def write(cur, sym, rows, replace=False):
    from psycopg2.extras import execute_values
    if replace:
        cur.execute("DELETE FROM us_price_daily WHERE symbol=%s", (sym,))
    execute_values(cur, """INSERT INTO us_price_daily (symbol, trade_date, open, high, low, close, adj_close, volume)
                           VALUES %s ON CONFLICT (symbol, trade_date) DO UPDATE SET open=EXCLUDED.open,
                           high=EXCLUDED.high, low=EXCLUDED.low, close=EXCLUDED.close,
                           adj_close=EXCLUDED.adj_close, volume=EXCLUDED.volume""",
                   [(sym, *r) for r in rows], page_size=2000)


def main():
    ap = argparse.ArgumentParser(description="抓美股日線（Yahoo Finance）→ us_price_daily")
    ap.add_argument("--dsn", default=os.environ.get("DATABASE_URL", ""), help="PostgreSQL 連線字串")
    ap.add_argument("--range", default="1mo", help="每檔抓多長（Yahoo range：1mo、6mo、1y、5y…；預設 1mo）")
    ap.add_argument("--full-range", default="5y", help="整檔重抓時的長度（預設 5y）")
    ap.add_argument("--symbols", default="", help="只抓這些代號（逗號分隔）；預設 us_theme_defs.py 全部")
    ap.add_argument("--delay", type=float, default=0.3, help="每檔間隔秒（預設 0.3）")
    args = ap.parse_args()
    args.dsn = clean_dsn(args.dsn)
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
    except (AttributeError, ValueError):
        pass
    if not args.dsn:
        raise SystemExit("需要 --dsn 或環境變數 DATABASE_URL")

    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()] or all_symbols()
    full = args.range == args.full_range
    import psycopg2
    conn = psycopg2.connect(args.dsn)
    added, refetched, failed = 0, [], []
    try:
        cur = conn.cursor()
        cur.execute("SELECT to_regclass('public.us_price_daily')")
        if cur.fetchone()[0] is None:
            raise SystemExit("還沒有 us_price_daily：先跑 schema_us.sql")
        for sym in symbols:
            try:
                rows = parse_chart(fetch_chart(sym, args.range))
                if not rows:
                    raise ValueError("沒有已收盤的資料")
                same = None if full else consistent(cur, sym, rows)
                if full:
                    write(cur, sym, rows, replace=True)
                elif same is True:
                    write(cur, sym, rows)
                else:                                      # 庫裡沒有、或 Yahoo 改過歷史 → 整檔重抓
                    rows = parse_chart(fetch_chart(sym, args.full_range))
                    write(cur, sym, rows, replace=True)
                    refetched.append(sym if same is None else f"{sym}（調整過）")
                conn.commit()
                added += len(rows)
            except Exception as e:                         # 單檔失敗不影響其他檔
                conn.rollback()
                failed.append(f"{sym}：{str(e)[:80]}")
            time.sleep(args.delay)
        cur.execute("SELECT max(trade_date) FROM us_price_daily WHERE symbol='SPY'")
        last = cur.fetchone()[0]
    finally:
        conn.close()

    print(f"美股日線：{len(symbols) - len(failed)}/{len(symbols)} 檔成功，寫入 {added} 列｜SPY 最新 {last}")
    if refetched:
        print(f"  整檔重抓 {len(refetched)} 檔：{'、'.join(refetched)}")
    if failed:
        print(f"  失敗 {len(failed)} 檔：")
        for f in failed:
            print(f"    {f}")
    if len(failed) > len(symbols) / 2:                     # 大量失敗多半是被 Yahoo 擋，讓 nightly 標成失敗
        sys.exit(1)


if __name__ == "__main__":
    main()
