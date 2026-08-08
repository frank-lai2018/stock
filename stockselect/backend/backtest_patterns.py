r"""backtest_patterns.py — 型態/突破訊號歷史回測（多核平行 + 相對大盤超額）。

對母體內個股、逐日用「與實盤相同的近 150 根視窗」偵測各型態突破事件，記錄突破後 N 日：
  報酬（個股）、超額報酬（個股 − 同期加權指數）。統計每型態：事件數、勝率、平均報酬、平均超額。
方向調整：底部/多方 → 漲為勝；頭部/空方 → 跌為勝（avg_ret / avg_excess 正＝順預期方向獲利）。
成本：每筆已扣來回交易成本(--cost，預設 0.6%)；並套停損(--stop，預設 8%)：
  盤中最低/最高觸價即以 −stop 出場，較貼近實務可執行的績效。
結果 upsert 進 pattern_backtest，供前端「型態回測」頁顯示。

用法（在 stockselect/backend 下，需可讀 .env 的 DATABASE_URL）：
  python backtest_patterns.py                       # 全母體、自動多核、8% 停損、0.6% 成本
  python backtest_patterns.py --limit 500           # 前 500 檔（依成交額）
  python backtest_patterns.py --stop 0.1 --cost 0.004
  python backtest_patterns.py --stop 0              # 關閉停損（只扣成本）
  python backtest_patterns.py --horizons 5,10,20,60
"""
import argparse
import multiprocessing as mp
import os
import statistics
from datetime import datetime

from app import db, swings

WIN = 150            # 與實盤掃描相同的視窗長度
WARMUP = 60          # 前 60 根不夠形成型態，跳過
COOLDOWN = 10        # 同股同型態 10 交易日內只算一次事件

# 子行程 globals（由 _init 設定，避免每檔重複 pickle 大物件）
_MKT = {}
_CFG = {}


def ensure_table():
    db.execute(
        "CREATE TABLE IF NOT EXISTS pattern_backtest ("
        " pattern VARCHAR(24) NOT NULL, horizon INT NOT NULL,"
        " n INT, win_rate NUMERIC, avg_ret NUMERIC, median_ret NUMERIC,"
        " avg_excess NUMERIC, win_excess NUMERIC, computed_at TIMESTAMP,"
        " PRIMARY KEY (pattern, horizon))")
    for col in ("avg_excess NUMERIC", "win_excess NUMERIC"):        # 舊表補欄
        db.execute(f"ALTER TABLE pattern_backtest ADD COLUMN IF NOT EXISTS {col}")


def universe(limit, min_amt, sec):
    cond = ["in_universe = true", "amt20 >= %(a)s"]
    p = {"a": min_amt}
    if sec in ("stock", "etf"):
        cond.append("security_type = %(s)s"); p["s"] = sec
    sql = f"SELECT stock_id FROM mv_stock_snapshot WHERE {' AND '.join(cond)} ORDER BY amt20 DESC"
    if limit:
        sql += f" LIMIT {int(limit)}"
    return [r["stock_id"] for r in db.query(sql, p)]


def bars_of(sid, max_bars):
    return db.query(
        "SELECT trade_date, adj_high AS high, adj_low AS low, adj_close AS close, volume "
        "FROM (SELECT trade_date, adj_high, adj_low, adj_close, volume, "
        "  row_number() OVER (ORDER BY trade_date DESC) AS rn "
        "  FROM price_daily WHERE stock_id=%(id)s) z WHERE rn <= %(n)s ORDER BY trade_date",
        {"id": sid, "n": max_bars})


def _init(mkt, cfg):
    global _MKT, _CFG
    _MKT, _CFG = mkt, cfg


def _scan(sid):
    """單檔：回傳 {pattern: [(dir, {h:淨報酬}, {h:淨超額 or None}), ...]}。子行程執行。

    每筆已『方向調整 + 扣來回成本 + 套停損』：
      方向調整：底部/多方漲為正、頭部/空方跌為正（正＝順預期方向獲利）。
      停損：多單盤中最低跌破 base×(1−stop) 以 −stop 出場；空單盤中最高漲破 base×(1+stop) 以 −stop 出場。
      成本：來回交易成本(手續費+證交稅) cost，直接自報酬扣除。
    """
    horizons, hmax, max_bars = _CFG["horizons"], _CFG["hmax"], _CFG["max_bars"]
    stop, cost = _CFG["stop"], _CFG["cost"]
    bars = bars_of(sid, max_bars)
    n = len(bars)
    out = {}
    if n < WARMUP + hmax + 5:
        return out
    closes = [float(b["close"]) for b in bars]
    highs = [float(b["high"]) for b in bars]
    lows = [float(b["low"]) for b in bars]
    dates = [b["trade_date"] for b in bars]
    last_hit = {}
    for t in range(WARMUP, n - hmax):
        window = bars[max(0, t - WIN + 1):t + 1]
        base = closes[t]
        if base <= 0:
            continue
        for k in swings.ALL:
            if t - last_hit.get(k, -10 ** 9) < COOLDOWN:
                continue
            bk = swings.ALL[k](window, recent=1)          # 只在當天(t)剛突破才算
            if not bk:
                continue
            last_hit[k] = t
            d = bk.get("dir", "bull")
            sign = -1 if d == "bear" else 1               # 方向調整係數
            m0 = _MKT.get(dates[t])
            ks = None                                     # 首次觸發停損的第幾根（None=未停損）
            if stop and stop > 0:
                slv = base * (1 - stop) if sign > 0 else base * (1 + stop)
                for kk in range(1, hmax + 1):
                    hit = lows[t + kk] <= slv if sign > 0 else highs[t + kk] >= slv
                    if hit:
                        ks = kk
                        break
            rets, exc = {}, {}
            for h in horizons:
                if ks is not None and ks <= h:            # 期間內先停損出場
                    ex, r_raw = ks, (-stop if sign > 0 else stop)
                else:                                     # 抱到持有期滿以收盤出場
                    ex, r_raw = h, closes[t + h] / base - 1
                rets[h] = sign * r_raw - cost             # 方向調整後淨報酬（已扣成本）
                mh = _MKT.get(dates[t + ex])
                exc[h] = (sign * r_raw - sign * (mh / m0 - 1) - cost) if (m0 and mh) else None
            out.setdefault(k, []).append((d, rets, exc))
    return out


def run(args):
    ensure_table()
    horizons = [int(h) for h in args.horizons.split(",") if h.strip()]
    hmax = max(horizons)
    ids = universe(args.limit, args.min_amt, args.security_type)
    mkt = {r["trade_date"]: float(r["close"])
           for r in db.query("SELECT trade_date, close FROM market_index WHERE index_id='TWSE'")}
    workers = args.workers or max(1, min((os.cpu_count() or 4) - 2, 8))
    print(f"回測母體 {len(ids)} 檔｜型態 {len(swings.ALL)} 種｜持有期 {horizons}｜"
          f"停損 {args.stop:.0%}｜來回成本 {args.cost:.2%}｜"
          f"大盤基準點 {len(mkt)}｜{workers} 核平行", flush=True)

    cfg = {"horizons": horizons, "hmax": hmax, "max_bars": args.max_bars,
           "stop": args.stop, "cost": args.cost}
    events = {k: [] for k in swings.ALL}
    done = 0
    if workers == 1:
        _init(mkt, cfg)
        results = (_scan(sid) for sid in ids)
        for res in results:
            for k, evs in res.items():
                events[k].extend(evs)
            done += 1
            if done % 50 == 0:
                print(f"  … {done}/{len(ids)}", flush=True)
    else:
        with mp.Pool(workers, initializer=_init, initargs=(mkt, cfg)) as pool:
            for res in pool.imap_unordered(_scan, ids, chunksize=4):
                for k, evs in res.items():
                    events[k].extend(evs)
                done += 1
                if done % 100 == 0:
                    print(f"  … {done}/{len(ids)}", flush=True)

    stamp = datetime.now()
    print("\n=== 回測結果（方向調整後、已扣成本+停損：正＝順預期方向獲利）===")
    for k in swings.ALL:
        evs = events[k]
        name = swings.PATTERN_NAMES[k]
        for h in horizons:
            if evs:
                rets = [fwd[h] for (_, fwd, _) in evs]                 # 已方向調整+扣成本+停損
                exc = [e[h] for (_, _, e) in evs if e[h] is not None]
                wr = sum(1 for r in rets if r > 0) / len(rets) * 100
                avg = statistics.mean(rets) * 100
                med = statistics.median(rets) * 100
                avg_x = (statistics.mean(exc) * 100) if exc else None
                win_x = (sum(1 for r in exc if r > 0) / len(exc) * 100) if exc else None
            else:
                wr = avg = med = avg_x = win_x = None
            db.execute(
                "INSERT INTO pattern_backtest "
                "(pattern,horizon,n,win_rate,avg_ret,median_ret,avg_excess,win_excess,computed_at) "
                "VALUES (%(k)s,%(h)s,%(n)s,%(wr)s,%(avg)s,%(med)s,%(ax)s,%(wx)s,%(t)s) "
                "ON CONFLICT (pattern,horizon) DO UPDATE SET "
                " n=EXCLUDED.n,win_rate=EXCLUDED.win_rate,avg_ret=EXCLUDED.avg_ret,"
                " median_ret=EXCLUDED.median_ret,avg_excess=EXCLUDED.avg_excess,"
                " win_excess=EXCLUDED.win_excess,computed_at=EXCLUDED.computed_at",
                {"k": k, "h": h, "n": len(evs),
                 "wr": None if wr is None else round(wr, 1),
                 "avg": None if avg is None else round(avg, 2),
                 "med": None if med is None else round(med, 2),
                 "ax": None if avg_x is None else round(avg_x, 2),
                 "wx": None if win_x is None else round(win_x, 1), "t": stamp})
        if evs:
            h0 = horizons[-1]
            r0 = [fwd[h0] for (_, fwd, _) in evs]
            x0 = [e[h0] for (_, _, e) in evs if e[h0] is not None]
            xm = f"超額{statistics.mean(x0) * 100:+.1f}%" if x0 else "超額—"
            print(f"  {name}({k}) n={len(evs)}｜{h0}日 均{statistics.mean(r0) * 100:+.1f}%｜{xm}")
        else:
            print(f"  {name}({k}): 無事件")
    print("\n=== 完成，已寫入 pattern_backtest ===")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="型態突破歷史回測（多核 + 超額報酬）→ pattern_backtest")
    ap.add_argument("--limit", type=int, default=0, help="只取前 N 檔（依成交額，0=全部）")
    ap.add_argument("--min-amt", type=int, default=20000000, help="母體 20 日均額門檻（預設 2 千萬）")
    ap.add_argument("--security-type", default="", help="stock / etf / 空=全部")
    ap.add_argument("--max-bars", type=int, default=1200, help="每檔回看幾根（預設 1200≈5年）")
    ap.add_argument("--horizons", default="5,10,20", help="持有期（交易日，逗號分隔）")
    ap.add_argument("--workers", type=int, default=0, help="平行核心數（0=自動 CPU-2，上限 8）")
    ap.add_argument("--stop", type=float, default=0.08, help="停損比例（預設 0.08=8%%；0=不停損）")
    ap.add_argument("--cost", type=float, default=0.006, help="來回交易成本（預設 0.006=0.6%%；手續費+證交稅）")
    run(ap.parse_args())
