r"""morning_us_hot.py — 早上看「昨晚美股熱門族群」與相關台股。不連資料庫，其他電腦也能跑。

雙擊「昨晚美股熱門族群.bat」執行。只用 Python 標準函式庫，價格全部即時從 Yahoo Finance 抓：
  美股  us_theme_defs.py 列的各題材籃子（約 80 檔）昨晚（最新一個已收盤交易日）的漲跌，依扣掉 SPY 後的漲幅排序
  台股  前幾名熱門題材的相關台股，最新一個交易日收盤的漲跌
相關台股名單與跟隨度來自資料庫的快照 us_tw_theme_snapshot.json（跟著專案走，執行時不連資料庫）。
在連得到資料庫的電腦上改了族群成分、或重跑 analyze_us_tw_themes.py 之後，用 --export 更新快照：
  python morning_us_hot.py --export        # 只有這個模式會連資料庫（DATABASE_URL，沒設就讀 stockselect/backend/.env）
其他電腦：把整個 us_stock 資料夾複製過去就能跑（Python 3.9 以上，不用裝套件）。

用法（在 us_stock 資料夾裡）：
  python morning_us_hot.py                          # 熱門題材前 5 名、每個題材列 12 檔台股
  python morning_us_hot.py --top 8 --max-stocks 20
  python morning_us_hot.py --no-color > 今天.txt     # 存檔時關掉顏色
"""
import argparse
import json
import os
import sys
import time
import unicodedata
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

from fetch_us_prices import clean_dsn, fetch_chart, parse_chart
from us_theme_defs import BENCHMARK, INDICATORS, US_THEMES

HERE = os.path.dirname(os.path.abspath(__file__))
SNAPSHOT = os.path.join(HERE, "us_tw_theme_snapshot.json")
ROLE_ORDER = {"核心": 0, "受惠": 1, "沾邊": 2}
SUFFIX = {"上市": ".TW", "上市臺灣創新板": ".TW", "上櫃": ".TWO"}     # Yahoo 台股代號
WEEKDAY = "一二三四五六日"
SHORT = {"SPY": "S&P 500", "QQQ": "Nasdaq", "^SOX": "費半", "TSM": "台積電ADR"}   # 標題列的指標
LINE = 110                    # Windows 主控台預設 120 欄，每行控制在這以內才不會折行
RED, GREEN, YELLOW, GRAY, BOLD, RESET = "\033[31m", "\033[32m", "\033[33m", "\033[90m", "\033[1m", "\033[0m"
FOLLOW_COLOR = {"明顯跟隨": (RED, BOLD), "有一點": (YELLOW,), "幾乎不跟": (GRAY,)}
COLOR = True


# ───────────────────────── 顯示 ─────────────────────────
def enable_ansi():
    """Windows 主控台要先打開 VT 模式才吃 ANSI 顏色；輸出被導到檔案時不上色。"""
    if not sys.stdout.isatty():
        return False
    if os.name != "nt":
        return True
    try:
        import ctypes
        k = ctypes.windll.kernel32
        h = k.GetStdHandle(-11)
        mode = ctypes.c_uint32()
        return bool(k.GetConsoleMode(h, ctypes.byref(mode)) and k.SetConsoleMode(h, mode.value | 0x0004))
    except Exception:
        return False


def paint(s, *codes):
    return f"{''.join(codes)}{s}{RESET}" if COLOR and codes else s


def width(s):
    """主控台顯示寬度：中文字佔兩格。"""
    return sum(2 if unicodedata.east_asian_width(ch) in "WF" else 1 for ch in str(s))


def clip(s, w):
    """截到顯示寬度 w 以內，截掉的用「…」表示。"""
    s = str(s)
    if width(s) > w:
        while s and width(s) > w - 1:
            s = s[:-1]
        s += "…"
    return s


def pad(s, w, right=False):
    """補空白到顯示寬度 w（太長先截斷），欄位才對得齊。"""
    s = clip(s, w)
    gap = " " * max(0, w - width(s))
    return gap + s if right else s + gap


def tone(v):
    return RED if v > 0 else GREEN if v < 0 else ""


def pct_text(v, d=1):
    return "—" if v is None else f"{v * 100:+.{d}f}%"


def pct(v, w=8, bold=False):
    """表格用：靠右、上色（台股慣例紅漲綠跌）的百分比欄位。"""
    text = pad(pct_text(v), w, right=True)
    if v is None:
        return text
    return paint(text, *[c for c in (tone(v), BOLD if bold else "") if c])


def pct_inline(v, d=1):
    return paint(pct_text(v, d), tone(v)) if v is not None and tone(v) else pct_text(v, d)


def follow_cell(f, w=18):
    if not f:
        return pad("—", w)
    label = f["label"] + ("・看費半" if f.get("sox_only") else "")
    return paint(pad(label, w), *FOLLOW_COLOR.get(f["label"], ()))


# ───────────────────────── 抓價 ─────────────────────────
def closes(symbol, rng="2mo"):
    """[(日期, 還原收盤)]；還沒收盤的最後一根會被丟掉，抓不到回 []。"""
    try:
        return [(r[0], float(r[5])) for r in parse_chart(fetch_chart(symbol, rng, retries=2))]
    except Exception:
        return []


def tw_closes(stock):
    """台股：上市 .TW、上櫃 .TWO，抓不到再試另一個。"""
    first = SUFFIX.get(stock.get("market"), ".TW")
    for suffix in (first, ".TWO" if first == ".TW" else ".TW"):
        rows = closes(stock["id"] + suffix)
        if rows:
            return rows
    return []


def fetch_many(fn, items, workers=8):
    with ThreadPoolExecutor(workers) as ex:
        return list(ex.map(fn, items))


def returns(series, last_date=None):
    """最新收盤與 1／5／20 日報酬；給 last_date 時，最新一筆必須是那天（沒抓到當天的不算）。"""
    if not series or (last_date and series[-1][0] != last_date):
        return None
    c = [v for _, v in series]
    r = lambda k: c[-1] / c[-1 - k] - 1 if len(c) > k and c[-1 - k] else None     # noqa: E731
    return {"date": series[-1][0], "close": c[-1], "ret_1d": r(1), "ret_5d": r(5), "ret_20d": r(20)}


def mean(values):
    v = [x for x in values if x is not None]
    return sum(v) / len(v) if v else None


# ───────────────────────── 快照（只有 --export 連資料庫）─────────────────────────
def dsn_from_env_file():
    try:
        with open(os.path.join(os.path.dirname(HERE), "stockselect", "backend", ".env"), encoding="utf-8") as f:
            for line in f:
                if line.startswith("DATABASE_URL="):
                    return line.split("=", 1)[1].strip()
    except OSError:
        pass
    return ""


def export(dsn):
    import psycopg2
    conn = psycopg2.connect(dsn)
    try:
        with conn, conn.cursor() as cur:
            cur.execute("""SELECT t.code, t.name, st.stock_id, s.name, s.market, st.role
                             FROM stock_theme st JOIN theme t USING (theme_id) JOIN stock s USING (stock_id)
                            WHERE t.layer = 3 AND t.is_active AND st.valid_to IS NULL
                              AND st.status IN ('confirmed', 'seed')
                            ORDER BY t.code, st.stock_id""")
            themes = {}
            for code, name, sid, sname, market, role in cur.fetchall():
                t = themes.setdefault(code, {"name": name, "follow": None, "tw": []})
                t["tw"].append({"id": sid, "name": sname, "market": market, "role": role})
            period = None
            cur.execute("SELECT to_regclass('public.us_theme_follow')")
            if cur.fetchone()[0]:
                cur.execute("""SELECT t.code, f.label, f.corr_ex, f.t_partial_sox, f.up_next, f.down_next, f.up_win,
                                      f.period_from, f.period_to
                                 FROM us_theme_follow f JOIN theme t USING (theme_id)""")
                fv = lambda v: None if v is None else float(v)                    # noqa: E731
                for code, label, corr, t_sox, up, down, win, p0, p1 in cur.fetchall():
                    if code in themes:
                        themes[code]["follow"] = {
                            "label": label, "corr_ex": fv(corr), "up_next": fv(up), "down_next": fv(down),
                            "up_win": fv(win),
                            # 扣掉費半後，這籃美股沒有多出資訊
                            "sox_only": label != "幾乎不跟" and t_sox is not None and float(t_sox) < 2}
                        period = [p0.isoformat(), p1.isoformat()]
    finally:
        conn.close()
    snap = {"generated_at": datetime.now().isoformat(timespec="seconds"), "follow_period": period, "themes": themes}
    with open(SNAPSHOT, "w", encoding="utf-8") as f:
        json.dump(snap, f, ensure_ascii=False, indent=1)
    n = sum(len(t["tw"]) for t in themes.values())
    print(f"快照 → {SNAPSHOT}：{len(themes)} 個題材、{n} 筆台股成分、跟隨度期間 {period}")


def load_snapshot():
    try:
        with open(SNAPSHOT, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


# ───────────────────────── 輸出 ─────────────────────────
def us_part(snap):
    """抓美股、算各題材籃子昨晚的漲跌；回傳 (美股日期, 各檔報酬, 依扣 SPY 排序的題材)。"""
    t0 = time.time()
    syms = list(dict.fromkeys([s for s, _ in INDICATORS] + ["TSM"]
                              + [s for t in US_THEMES.values() for s, _ in t["symbols"]]))
    print(f"抓美股 {len(syms)} 檔…", end="", flush=True)
    series = dict(zip(syms, fetch_many(closes, syms)))
    spy = returns(series.get(BENCHMARK))
    if not spy:
        raise SystemExit("\n抓不到 SPY：網路不通，或 Yahoo Finance 暫時擋掉，等幾分鐘再試")
    rets = {s: returns(series[s], spy["date"]) for s in syms}
    missing = [s for s in syms if not rets[s]]
    print(f"完成（{time.time() - t0:.1f} 秒" + (f"，沒抓到：{'、'.join(missing)}" if missing else "") + "）")

    themes = []
    for code, t in US_THEMES.items():
        ok = [(n, rets[s]) for s, n in t["symbols"] if rets.get(s)]
        if not ok:
            continue
        r1 = mean(r["ret_1d"] for _, r in ok)
        themes.append({
            "code": code, "name": (snap or {}).get("themes", {}).get(code, {}).get("name", code),
            "ret_1d": r1, "ex_1d": None if r1 is None or spy["ret_1d"] is None else r1 - spy["ret_1d"],
            "ret_5d": mean(r["ret_5d"] for _, r in ok), "ret_20d": mean(r["ret_20d"] for _, r in ok),
            "members": sorted(ok, key=lambda x: x[1]["ret_1d"] if x[1]["ret_1d"] is not None else -9, reverse=True),
            "follow": ((snap or {}).get("themes", {}).get(code) or {}).get("follow"),
        })
    themes.sort(key=lambda t: t["ex_1d"] if t["ex_1d"] is not None else -9, reverse=True)
    return spy["date"], rets, themes


def print_us(us_date, rets, themes):
    line = "═" * LINE
    tw_morning = us_date + timedelta(days=1)
    print(f"\n{line}\n {paint('昨晚美股熱門族群', BOLD)}　美股 {us_date}（週{WEEKDAY[us_date.weekday()]}）收盤"
          f"＝台灣 {tw_morning.month}/{tw_morning.day} 清晨\n{line}")
    parts = []
    for s, n in SHORT.items():
        r = rets.get(s)
        if r:
            parts.append(f"{n} {pct_inline(r['ret_1d'])}" + paint(f"（20日 {pct_text(r['ret_20d'])}）", GRAY))
    print(" " + "｜".join(parts))

    print(f"\n{paint('【美股各題材昨晚表現】', BOLD)}依扣掉 SPY 後的漲幅排序｜"
          "跟隨度＝近兩年回測，美股這一籃大漲時台股同題材隔天跟不跟")
    print(f" {pad('#', 3)}{pad('題材', 28)}{pad('昨晚', 8, True)}{pad('扣SPY', 8, True)}{pad('5日', 8, True)}"
          f"{pad('20日', 8, True)}  {pad('跟隨度', 18)}昨晚領漲")
    for i, t in enumerate(themes, 1):
        lead = "、".join(f"{n} {pct_text(r['ret_1d'])}" for n, r in t["members"][:2] if r["ret_1d"] is not None)
        print(f" {pad(i, 3)}{pad(t['name'], 28)}{pct(t['ret_1d'])}{pct(t['ex_1d'], bold=True)}{pct(t['ret_5d'])}"
              f"{pct(t['ret_20d'])}  {follow_cell(t['follow'])}{clip(lead, 34)}")
    weak = [t for t in reversed(themes) if t["ex_1d"] is not None and t["ex_1d"] < 0
            and (t["follow"] or {}).get("label") in ("明顯跟隨", "有一點")][:3]
    if weak:
        print(paint(" 要小心（美股昨晚弱、台股會跟）：", YELLOW)
              + "、".join(f"{t['name']} {pct_inline(t['ex_1d'])}" for t in weak))


def print_tw(hot, snap, max_stocks):
    stocks = {}
    for t in hot:
        for s in snap["themes"].get(t["code"], {}).get("tw", []):
            stocks.setdefault(s["id"], s)
    t0 = time.time()
    print(f"\n抓相關台股 {len(stocks)} 檔…", end="", flush=True)
    tw = dict(zip(stocks, (returns(x) for x in fetch_many(tw_closes, list(stocks.values())))))
    dates = Counter(r["date"] for r in tw.values() if r)
    tw_date = dates.most_common(1)[0][0] if dates else None
    print(f"完成（{time.time() - t0:.1f} 秒）")
    print(f"\n{paint('【熱門題材 × 相關台股】', BOLD)}扣 SPY 前 {len(hot)} 名｜台股是 {tw_date or '—'} 收盤｜"
          "核心 → 受惠 → 其他，同角色依 20 日漲幅排序")
    for i, t in enumerate(hot, 1):
        f = t["follow"]
        if not f:
            note = "跟隨度：沒有資料"
        elif f["label"] == "幾乎不跟":
            note = "幾乎不跟：台股這個題材不太跟美股，僅參考"
        else:
            note = (f"{f['label']}：美股這一籃大漲的隔天，台股平均 {pct_text(f['up_next'], 2)}、"
                    f"{round(f['up_win'] * 100)}% 跑贏大盤" + ("（看費半就夠）" if f.get("sox_only") else ""))
        print(f"\n{paint(f'━━ {i}. ' + t['name'], BOLD)}　美股昨晚 {pct_inline(t['ret_1d'])}"
              f"（扣 SPY {pct_text(t['ex_1d'])}）")
        print("   " + paint(note, *FOLLOW_COLOR.get((f or {}).get("label"), ())))
        members = sorted(snap["themes"].get(t["code"], {}).get("tw", []),
                         key=lambda s: (ROLE_ORDER.get(s.get("role"), 3), -((tw.get(s["id"]) or {}).get("ret_20d") or -9)))
        print(f"   {pad('代號', 7)}{pad('名稱', 12)}{pad('角色', 6)}{pad('收盤', 10, True)}{pad('漲跌', 8, True)}"
              f"{pad('5日', 8, True)}{pad('20日', 8, True)}")
        for s in members[:max_stocks]:
            head = f"   {pad(s['id'], 7)}{pad(s['name'], 12)}{pad(s.get('role') or '—', 6)}"
            r = tw.get(s["id"])
            if not r:
                print(head + paint("（抓不到）", GRAY))
                continue
            stale = paint(f"　資料日 {r['date']}", GRAY) if tw_date and r["date"] != tw_date else ""
            close = f"{r['close']:,.2f}"
            print(f"{head}{pad(close, 10, True)}{pct(r['ret_1d'])}{pct(r['ret_5d'])}{pct(r['ret_20d'])}{stale}")
        if len(members) > max_stocks:
            print(paint(f"   …另 {len(members) - max_stocks} 檔（--max-stocks 可調）", GRAY))


def main():
    global COLOR
    ap = argparse.ArgumentParser(description="昨晚美股熱門族群與相關台股（不連資料庫）")
    ap.add_argument("--top", type=int, default=5, help="列出相關台股的熱門題材數（預設 5）")
    ap.add_argument("--max-stocks", type=int, default=12, help="每個題材最多列幾檔台股（預設 12）")
    ap.add_argument("--no-color", action="store_true", help="不上色（輸出存檔用）")
    ap.add_argument("--export", action="store_true", help="從資料庫更新 us_tw_theme_snapshot.json 後結束")
    ap.add_argument("--dsn", default=os.environ.get("DATABASE_URL", ""), help="--export 用的連線字串")
    args = ap.parse_args()
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
    if args.export:
        dsn = clean_dsn(args.dsn or dsn_from_env_file())
        if not dsn:
            raise SystemExit("需要 --dsn、環境變數 DATABASE_URL 或 stockselect/backend/.env")
        export(dsn)
        return
    COLOR = not args.no_color and enable_ansi()

    snap = load_snapshot()
    us_date, rets, themes = us_part(snap)
    print_us(us_date, rets, themes)
    if not snap:
        print(f"\n找不到 {os.path.basename(SNAPSHOT)}，沒辦法列相關台股：在連得到資料庫的電腦跑 "
              "python morning_us_hot.py --export，再把檔案帶過來")
        return
    hot = [t for t in themes if t["ex_1d"] is not None and t["ex_1d"] > 0][:max(0, args.top)]
    if hot:
        print_tw(hot, snap, args.max_stocks)
    period = "～".join(snap.get("follow_period") or ["—"])
    print(paint(f"\n相關台股名單與跟隨度：快照 {snap.get('generated_at', '')[:10]}（跟隨度期間 {period}）\n"
                "改了族群成分後，在連得到資料庫的電腦跑 python morning_us_hot.py --export 更新快照", GRAY))


if __name__ == "__main__":
    main()
