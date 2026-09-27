r"""fetch_corp_actions.py — 抓上市／上櫃官方的「除權息、減資、面額變更、ETF 分割／反分割」參考價，給還原價用。

為什麼要它：build_adjusted_price.py 原本只靠 FinMind 股利政策（5~8 月週日才重抓）與減資（一年兩次），
  季配息、ETF 配息、旺季以外的除權息、臨時減資都要晚好幾個月才進來，分割／面額變更更是完全沒有
  → 還原價在這些日子斷掉（例：0050 一拆四、國巨面額 10→2.5、緯穎 115/09/02 大比例配股）。
  官方表直接給「前收盤價」與「參考價」，全市場一次查一段日期，每天只要 9 個請求。

來源（皆為官網 JSON，可查日期區間）：
  上市 GET  https://www.twse.com.tw/rwd/zh/<路徑>?startDate=YYYYMMDD&endDate=YYYYMMDD&response=json
        除權除息計算結果表 exRight/TWT49U（92/05/05 起）、減資恢復買賣參考價 reducation/TWTAUU（100/01/01 起）、
        變更面額恢復買賣參考價 change/TWTB8U、ETF 分割(反分割)恢復買賣參考價 split/TWTCAU
  上櫃 POST https://www.tpex.org.tw/www/zh-tw/<路徑>（表單 startDate=YYYY/MM/DD&endDate=YYYY/MM/DD&response=json）
        除權除息計算結果表 bulletin/exDailyQ（96/12/17 起）、減資恢復買賣參考價 bulletin/revivt（102/01 起）、
        變更面額 bulletin/pvChgRslt、ETF 分割 bulletin/etfSplitRslt、ETF 反分割 bulletin/etfRvsRslt

還原比例 r（乘在事件日「之前」的價格上，同 build_adjusted_price）：
  除權息：r = (前收盤 − 權值息值) / 前收盤。官方公式「權值+息值 = 除權息前收盤價 − 除權息參考價」，
          是 6 位小數的精確值；「除權息參考價」欄只到 2 位而且是截斷，直接拿來除會有誤差。
          含現金增資的除權也照這個還原（連認購權價值）：開盤基準雖用「減除股利參考價」，但 2007~2026 櫃買個案
          實際收盤多半跌到理論除權價附近（例外如銘旺科 113/07/02 仍漲停）。舊版 FinMind 公式沒算現增。
  其他：  r = 恢復買賣參考價 / 停止買賣前收盤價
  官方除權息表「不含除息併案辦理退還股款減資或分割減資」，那些在減資表，兩表不會重複計算。

持有人股數倍數 share_ratio（事件後÷事件前；build_etf_flow.py 用來換算主動 ETF 的持股；純現金股利＝1）：
  除權      1＋無償配股率。上櫃表直接有「每仟股無償配股」；上市表沒有，逐筆查詳細資料 exRight/TWT49UDetail
            （例：緯穎 115/09/02 每千股配 1,982.8 股 → ×2.9828，不是剛好 ×3）。現金增資要自己認購，不算。
  減資      每千股換發股數÷1000。上櫃表的「詳細資料」欄就有；上市逐筆查 reducation/TWTAVUDetail。
  面額變更  換股率（上櫃詳細資料欄）；上市用 前收÷參考價 對齊簡單分數（國巨 10→2.5 元 ×4）。
  ETF 分割  前收÷參考價 對齊簡單分數（0050 一拆四 ×4、00663L ×7、反分割 ×1/6）。
  上市要逐筆查的只補 DETAIL_SINCE（2025-01-01，主動 ETF 上市前）以後的事件，平常每天只有幾筆；更早的留空。

輸出（--out，預設 H:\data\CorpActions）：
  corp_actions.csv  全部事件：stock_id,date,kind(div/capred/par/split),market,name,prev_close,ref_price,value,ratio,
                    share_ratio,note,source
                    每抓一段就「整段取代」該來源那段日期的資料（官方更正、取消也會反映）
  coverage.json     各來源已完整抓到的日期區間；build_adjusted_price 據此判斷「官方表範圍內只信官方，
                    FinMind 股利只補官方還沒抓到的最近幾天」
  DB 表 corp_action（有 --dsn 時；schema_corp_action.sql，每次自動建表，同樣整段取代）

用法（daily_update.py 每天會在重算還原價之前自動呼叫，一般不用手動跑）：
  python fetch_corp_actions.py --dsn ...            # 每日：從各來源上次抓到的日期往回 10 天抓到今天；沒抓過的來源自動回補
  python fetch_corp_actions.py --backfill --dsn ... # 全部來源從最早可查日重抓到今天（約 40 個請求、2~3 分鐘）
  python fetch_corp_actions.py --start 2026-09-01 --end 2026-09-30
  python fetch_corp_actions.py --dry-run            # 只抓、印筆數，不寫檔也不寫 DB
"""
import argparse
import csv
import json
import os
import re
import sys
import time
from datetime import date, datetime, timedelta

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
SCHEMA = os.path.join(HERE, "schema_corp_action.sql")
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/128.0 Safari/537.36"}
TWSE_URL = "https://www.twse.com.tw/rwd/zh/{path}"
TPEX_URL = "https://www.tpex.org.tw/www/zh-tw/{path}"
CSV_NAME, COV_NAME = "corp_actions.csv", "coverage.json"
FIELDS = ["stock_id", "date", "kind", "market", "name", "prev_close", "ref_price", "value", "ratio", "share_ratio",
          "note", "source"]
CODE_RE = re.compile(r"^\d{4,6}[A-Z]?$")
KIND_NAME = {"div": "除權息", "capred": "減資", "par": "面額變更", "split": "ETF分割"}
RECHECK_DAYS = 10            # 每日模式往回重抓幾天（官方偶爾更正、補登）
DETAIL_SINCE = date(2025, 1, 1)   # 上市配股／減資的換股率要逐筆查詳細資料：只補這天以後的
DETAIL_MAX = 400             # 每次最多查幾筆詳細資料（第一次補約 270 筆；平常每天幾筆）
# 上市逐筆詳細資料：來源 → (路徑, 日期參數名, 欄名關鍵字, 換算成 share_ratio)
DETAIL_API = {
    "TWT49U": ("exRight/TWT49UDetail", "T1", "無償配股", lambda v: 1 + v / 1000),       # 每千股無償配股
    "TWTAUU": ("reducation/TWTAVUDetail", "FILE_DATE", "換發新股", lambda v: v / 1000),   # 每壹仟股換發新股票
}

# 來源：(代號, 市場, kind, 路徑, 最早可查日, 回補時每次查幾年)
SOURCES = [
    ("TWT49U",       "上市", "div",    "exRight/TWT49U",        "2003-05-05", 3),
    ("TWTAUU",       "上市", "capred", "reducation/TWTAUU",     "2011-01-01", 8),
    ("TWTB8U",       "上市", "par",    "change/TWTB8U",         "2011-01-01", 8),
    ("TWTCAU",       "上市", "split",  "split/TWTCAU",          "2011-01-01", 8),
    ("exDailyQ",     "上櫃", "div",    "bulletin/exDailyQ",     "2007-12-17", 3),
    ("revivt",       "上櫃", "capred", "bulletin/revivt",       "2013-01-01", 8),
    ("pvChgRslt",    "上櫃", "par",    "bulletin/pvChgRslt",    "2011-01-01", 8),
    ("etfSplitRslt", "上櫃", "split",  "bulletin/etfSplitRslt", "2011-01-01", 8),
    ("etfRvsRslt",   "上櫃", "split",  "bulletin/etfRvsRslt",   "2011-01-01", 8),
]
DIV_SOURCES = [s[0] for s in SOURCES if s[2] == "div"]


def clean_dsn(dsn):
    """容錯＋防呆：剝掉誤貼進值裡的旗標／引號，並先擋掉明顯不合法的連線字串。（同 nightly.py）"""
    dsn = (dsn or "").strip().strip('"').strip("'").strip()
    if dsn.startswith("--dsn"):
        dsn = dsn[5:].lstrip().lstrip("=").lstrip()
    if dsn and not (dsn.startswith(("postgresql://", "postgres://")) or "=" in dsn):
        raise SystemExit(f"連線字串格式不對：{dsn!r}；應為 postgresql://user:pw@host:port/db（或 key=value 形式）")
    return dsn


# ---------- 抓取 ----------

def _json(sess, method, url, **kw):
    """帶重試的請求；官網偶爾逾時或回 HTML 維護頁。"""
    for i in range(4):
        try:
            r = sess.request(method, url, headers=UA, timeout=60, **kw)
            r.raise_for_status()
            return r.json()
        except (requests.RequestException, ValueError):
            if i == 3:
                raise
            time.sleep(5 * (i + 1))


def fetch_range(sess, market, path, s, e):
    """回傳 (欄名, 資料列)。查無資料回空；其他錯誤（格式、維護）丟例外，該段就不算抓到。"""
    if market == "上市":
        j = _json(sess, "GET", TWSE_URL.format(path=path),
                  params={"startDate": f"{s:%Y%m%d}", "endDate": f"{e:%Y%m%d}", "response": "json"})
        stat = str(j.get("stat") or "")
        if stat.upper() != "OK":
            if "沒有符合" in stat or "查無" in stat:
                return [], []
            raise RuntimeError(stat[:80])
        return j.get("fields") or [], j.get("data") or []
    j = _json(sess, "POST", TPEX_URL.format(path=path),
              data={"startDate": f"{s:%Y/%m/%d}", "endDate": f"{e:%Y/%m/%d}", "response": "json"})
    if str(j.get("stat") or "").lower() != "ok":
        raise RuntimeError(str(j.get("stat"))[:80])
    t = (j.get("tables") or [{}])[0]
    return t.get("fields") or [], t.get("data") or []


# ---------- 解析 ----------

def roc_date(s):
    """'115年09月01日'、'115/09/01'、'96/12/17'、'1130205'（民國 yyyMMdd）→ date；不是日期回 None。"""
    parts = re.findall(r"\d+", str(s))
    if len(parts) == 1 and len(parts[0]) in (6, 7):
        p = parts[0]
        parts = [p[:-4], p[-4:-2], p[-2:]]
    if len(parts) != 3:
        return None
    y, m, d = map(int, parts)
    try:
        return date(y + 1911 if y < 1911 else y, m, d)
    except ValueError:
        return None


def num(s):
    try:
        return float(str(s).replace(",", "").strip())
    except ValueError:
        return None


def first_num(s):
    """'1,982.8 股'、'950.00000000&nbsp股' → 1982.8、950.0；沒有數字回 None。"""
    m = re.search(r"-?\d[\d,]*(?:\.\d+)?", str(s))
    return float(m.group(0).replace(",", "")) if m else None


def snap_ratio(x):
    """換股率對齊簡單分數（4、7、2.5、1/6…）：參考價有四捨五入，前收÷參考價不會剛好是整數；差 1% 以上就原樣回傳。"""
    from fractions import Fraction
    f = Fraction(x).limit_denominator(4) if x >= 1 else 1 / Fraction(1 / x).limit_denominator(4)
    return float(f) if f and abs(float(f) / x - 1) < 0.01 else x


def html_num(det, label):
    """櫃買「詳細資料」欄是一小段 HTML：<th>每壹仟股換發新股票:</th><td>550.00000000&nbsp股</td>。"""
    m = re.search(re.escape(label) + r"[:：]?\s*</th>\s*<td>\s*([-\d.,]+)", det or "")
    return first_num(m.group(1)) if m else None


def _col(fields, *keys, exclude=()):
    for i, f in enumerate(fields):
        if any(k in f for k in keys) and not any(x in f for x in exclude):
            return i
    return None


def parse(src, market, kind, fields, data):
    """欄位依名稱找（上市櫃、各表欄序不同）；找不到關鍵欄就丟例外，避免官網改版後默默寫錯。"""
    i_prev = _col(fields, "前收盤", "最後交易日之收盤")
    i_ref = _col(fields, "參考價", exclude=("減除股利", "除權參考價"))
    i_val = _col(fields, "權值+息值")
    i_exd = _col(fields, "減除股利參考價")
    i_note = _col(fields, "權/息", "減資原因", "分割(反分割)")
    i_stk = _col(fields, "無償配股")                   # 只有上櫃除權息表有：每仟股無償配股
    i_det = _col(fields, "詳細資料")                   # 上市：查詳細資料的參數；上櫃：一小段 HTML
    if data and (i_prev is None or i_ref is None or (kind == "div" and (i_val is None or i_exd is None))):
        raise RuntimeError(f"{src} 欄位對不上（官網改版？）：{fields}")
    out = {}
    for r in data:
        d, code = roc_date(r[0]), str(r[1]).strip()
        if d is None or not CODE_RE.match(code):
            continue
        prev, ref = num(r[i_prev]), num(r[i_ref])
        val = num(r[i_val]) if i_val is not None else None
        note = str(r[i_note]).strip() if i_note is not None else ""
        if not prev or prev <= 0:
            continue
        if kind == "div":
            if val is None:
                continue
            ratio = (prev - val) / prev                 # 權值息值是 6 位小數精確值（含現增的認購權價值）
            exd = num(r[i_exd])
            if exd and ref and abs(exd - ref) >= 0.005:
                note += "・含現增"                     # 開盤基準用減除股利參考價，但實際多半跌到理論除權價
        elif ref and ref > 0:
            ratio = ref / prev
        else:
            continue
        if abs(ratio - 1) < 1e-9:                       # 權值息值為 0（例：ETF 當期不配、只有現增）→ 不影響還原
            continue
        if not 0.01 < ratio < 100:
            print(f"  ⚠️ {src} {code} {d} 比例 {ratio:.4f} 不合理，略過")
            continue
        if kind == "split" and not note:
            note = "分割" if ratio < 1 else "反分割"
        det = str(r[i_det]).strip() if i_det is not None else ""
        if kind == "div":
            if i_stk is not None:
                share = 1 + (first_num(r[i_stk]) or 0) / 1000
            else:
                share = None if "權" in note else 1.0     # 上市：純除息＝1；有配股的要查詳細資料
        elif kind == "capred":
            v = html_num(det, "每壹仟股換發新股票")
            share = v / 1000 if v else None               # 上市查詳細資料
        else:                                             # 面額變更、ETF 分割
            share = html_num(det, "換股率") or (snap_ratio(prev / ref) if ref else None)
        row = {"stock_id": code, "date": d.isoformat(), "kind": kind, "market": market,
               "name": str(r[2]).strip()[:40], "prev_close": prev, "ref_price": ref,
               "value": val, "ratio": round(ratio, 10), "share_ratio": round(share, 10) if share else None,
               "note": note[:40], "source": src}
        if share is None and src in DETAIL_API and det:
            row["_detail"] = det                          # '6669,20260902'、'2371  ,20250611'（不寫進檔案）
        key = (code, row["date"])
        if key in out and abs(out[key]["ratio"] - row["ratio"]) > 1e-9:
            print(f"  ⚠️ {src} {code} {d} 同日兩筆比例不同（{out[key]['ratio']:.6f} / {row['ratio']:.6f}），取後者")
        out[key] = row
    return list(out.values())


def fill_details(sess, rows, limit=DETAIL_MAX):
    """上市有配股的除權息、減資：逐筆查詳細資料補 share_ratio（DETAIL_SINCE 以後、還沒有的）。回傳 (補到, 沒補到)。
    查不到的留空，下次再試（build_etf_flow 會先退回 FinMind 股利或價格比例）。"""
    todo = [r for r in rows if r.get("share_ratio") is None and r.get("_detail")
            and r["date"] >= DETAIL_SINCE.isoformat()]
    done = 0
    for r in todo[:limit]:
        path, date_key, label, conv = DETAIL_API[r["source"]]
        parts = [x.strip() for x in r["_detail"].split(",")]
        try:
            j = _json(sess, "GET", TWSE_URL.format(path=path),
                      params={"STK_NO": parts[0], date_key: parts[1], "response": "json"})
            fields, data = j.get("fields") or [], j.get("data") or []
            i = _col(fields, label)
            v = first_num(data[0][i]) if data and i is not None else None
        except Exception as ex:                           # 單筆失敗不影響其他筆
            print(f"  ✗ {r['source']} {r['stock_id']} {r['date']} 詳細資料失敗：{str(ex)[:80]}")
            v = None
        if v is not None:
            r["share_ratio"] = round(conv(v), 10)
            done += 1
        time.sleep(3.0)                                   # 證交所請求太密會暫時封鎖
    return done, len(todo) - done


# ---------- 存檔 ----------

def load_csv(path):
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def save_csv(path, rows):
    rows = sorted(rows, key=lambda r: (r["date"], r["stock_id"], r["kind"], r["source"]))
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        for r in rows:
            w.writerow({k: ("" if r.get(k) is None else r.get(k)) for k in FIELDS})
    os.replace(tmp, path)                               # 寫完才換，避免半截檔被還原價讀到


def load_coverage(out):
    try:
        with open(os.path.join(out, COV_NAME), encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def extend_coverage(cov, src, s, e):
    """只記連續的區間：新抓的段落跟舊區間重疊或相鄰才合併；不相鄰（手動抓一段）就不動。"""
    s, e = s.isoformat(), e.isoformat()
    old = cov.get(src)
    if not old:
        cov[src] = [s, e]
    elif s <= (date.fromisoformat(old[1]) + timedelta(days=1)).isoformat() and e >= old[0]:
        cov[src] = [min(old[0], s), max(old[1], e)]


def write_db(dsn, fetched):
    import psycopg2
    from psycopg2.extras import execute_values
    conn = psycopg2.connect(dsn)
    try:
        cur = conn.cursor()
        with open(SCHEMA, encoding="utf-8") as f:
            cur.execute(f.read())
        n = 0
        for (src, s, e), rows in fetched:
            cur.execute("DELETE FROM corp_action WHERE source = %s AND action_date BETWEEN %s AND %s", (src, s, e))
            if rows:
                execute_values(cur, "INSERT INTO corp_action (stock_id, action_date, kind, source, market, name, "
                               "prev_close, ref_price, value, ratio, share_ratio, note) VALUES %s",
                               [(r["stock_id"], r["date"], r["kind"], r["source"], r["market"], r["name"],
                                 r["prev_close"], r["ref_price"], r["value"], r["ratio"], r.get("share_ratio"),
                                 r["note"] or None)
                                for r in rows], page_size=1000)
                n += len(rows)
        conn.commit()
        return n
    finally:
        conn.close()


# ---------- 主流程 ----------

def plan(cov, today, backfill, start, end):
    """每個來源要抓的 (起, 迄)：手動區間 > 回補 > 每日（上次抓到的日期往回 RECHECK_DAYS 天）。"""
    out = []
    for src, market, kind, path, earliest, years in SOURCES:
        e0 = date.fromisoformat(earliest)
        if start:
            s, e = max(start, e0), end
        elif backfill or src not in cov:
            s, e = e0, today
        else:
            s, e = max(e0, date.fromisoformat(cov[src][1]) - timedelta(days=RECHECK_DAYS)), today
        if s <= e:
            out.append((src, market, kind, path, s, e, years))
    return out


def chunks(s, e, years):
    while s <= e:
        c = min(e, date(s.year + years, s.month, 1) - timedelta(days=1))
        yield s, c
        s = c + timedelta(days=1)


def run(out=r"H:\data\CorpActions", dsn="", backfill=False, start=None, end=None, dry_run=False, today=None):
    """抓官方事件並合併寫入 CSV／coverage.json／DB。回傳 (新抓筆數, 失敗的來源清單)。
    某來源某段失敗：不寫該段、coverage 不前進，下次自動從斷點補。"""
    today = today or date.today()
    end = min(end or today, today)
    os.makedirs(out, exist_ok=True)
    cov = load_coverage(out)
    todo = plan(cov, today, backfill, start, end)
    sess = requests.Session()
    fetched, failed = [], []
    for src, market, kind, path, s, e, years in todo:
        got, ok = 0, True
        for cs, ce in chunks(s, e, years):
            try:
                fields, data = fetch_range(sess, market, path, cs, ce)
                rows = parse(src, market, kind, fields, data)
            except Exception as ex:
                print(f"  ✗ {src}（{market}{KIND_NAME[kind]}）{cs}~{ce} 失敗：{str(ex)[:100]}")
                ok = False
                break                                   # 後面的段落不抓：coverage 必須連續
            fetched.append(((src, cs, ce), rows))
            got += len(rows)
            time.sleep(3.0 if market == "上市" else 1.2)          # 證交所請求太密會暫時封鎖
        print(f"  {'✓' if ok else '✗'} {src:12} {market}{KIND_NAME[kind]:5} {s}~{e}  {got:>6,} 筆")
        if not ok:
            failed.append(src)
    n = sum(len(r) for _, r in fetched)
    if dry_run or not fetched:
        return n, failed

    path = os.path.join(out, CSV_NAME)
    rows = load_csv(path)
    known = {(r["source"], r["stock_id"], r["date"]): float(r["share_ratio"]) for r in rows if r.get("share_ratio")}
    new_rows = [r for _, rs in fetched for r in rs]
    for r in new_rows:                                  # 重抓的段落：之前查過的詳細資料沿用
        if r.get("share_ratio") is None and (r["source"], r["stock_id"], r["date"]) in known:
            r["share_ratio"] = known[(r["source"], r["stock_id"], r["date"])]
    got, left = fill_details(sess, new_rows)
    if got or left:
        print(f"  上市配股／減資詳細資料：補到 {got} 筆" + (f"，還有 {left} 筆沒補到（下次再試）" if left else ""))
    for (src, s, e), new in fetched:                    # 整段取代：先移除舊的，再放新的
        s_iso, e_iso = s.isoformat(), e.isoformat()
        rows = [r for r in rows if not (r["source"] == src and s_iso <= r["date"] <= e_iso)]
        rows += new
        extend_coverage(cov, src, s, e)
    save_csv(path, rows)
    with open(os.path.join(out, COV_NAME), "w", encoding="utf-8") as f:
        json.dump(cov, f, ensure_ascii=False, indent=1)
    if dsn:
        write_db(dsn, fetched)

    # 近 10 天的非除權息事件（減資／面額變更／分割）印出來，nightly log 一眼看得到
    recent = (today - timedelta(days=10)).isoformat()
    for r in sorted((r for _, rs in fetched for r in rs if r["kind"] != "div" and r["date"] >= recent),
                    key=lambda r: r["date"]):
        print(f"    {r['date']} {r['stock_id']} {r['name']} {KIND_NAME[r['kind']]}{('（' + r['note'] + '）') if r['note'] else ''}"
              f" 前收 {r['prev_close']:g} → 參考價 {r['ref_price']:g}（×{float(r['ratio']):.4f}"
              + (f"，股數 ×{r['share_ratio']:g}" if r.get("share_ratio") else "") + "）")
    return n, failed


def main():
    ap = argparse.ArgumentParser(description="抓上市櫃官方除權息／減資／面額變更／ETF 分割參考價（還原價用）")
    ap.add_argument("--out", default=r"H:\data\CorpActions", help=r"輸出資料夾（預設 H:\data\CorpActions）")
    ap.add_argument("--dsn", default=os.environ.get("DATABASE_URL", ""), help="PostgreSQL 連線字串（給了才寫 corp_action 表）")
    ap.add_argument("--backfill", action="store_true", help="全部來源從最早可查日重抓")
    ap.add_argument("--start", default="", help="手動區間起日 YYYY-MM-DD")
    ap.add_argument("--end", default="", help="手動區間迄日 YYYY-MM-DD（預設今天）")
    ap.add_argument("--dry-run", action="store_true", help="只抓、印筆數，不寫檔也不寫 DB")
    args = ap.parse_args()
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except (AttributeError, ValueError):
        pass
    start = date.fromisoformat(args.start) if args.start else None
    end = date.fromisoformat(args.end) if args.end else None
    t0 = time.time()
    n, failed = run(args.out, clean_dsn(args.dsn), args.backfill, start, end, args.dry_run)
    print(f"完成：{n:,} 筆（{time.time() - t0:.0f}s）" + (f"；失敗來源 {failed}（下次自動補）" if failed else ""))
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
