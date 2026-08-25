r"""nison_chart.py — 把 price_daily 的日線轉成磚形圖(Renko)／三線反轉(Three-Line Break)。

轉換邏輯在 app/nison_charts.py（純函式）；這支只負責讀 DB、選參數、印結果。
一律使用**還原價** adj_close，除權息才不會被當成跳空。

用法（在 stockselect/backend 下，需可讀 .env 的 DATABASE_URL）：
  python nison_chart.py 2330                          # ATR 磚高，印兩種圖的摘要
  python nison_chart.py 2330 --show 15                # 另印最後 15 筆明細
  python nison_chart.py 2330 --brick-mode pct --pct 3 # 改用 3% 磚高
  python nison_chart.py 2330 --brick-mode abs --brick 20
  python nison_chart.py 2330 --lines 2                # 二線反轉（更敏感）
  python nison_chart.py 2330 --fwd 20                 # 加做翻轉後 20 日報酬的粗略檢查
  python nison_chart.py --sweep 2330                  # 掃不同磚高/線數，看壓縮率與訊號數

--fwd 只是「這個濾波器有沒有方向性」的體檢，**不是回測**：沒扣成本、沒停損、
沒防前視以外的控制，也沒有相對大盤調整。要正式評估請走 backtest_patterns.py 那套。
"""
import argparse
import statistics

from app import db, nison_charts as nc

BRICK_MODES = ("atr", "pct", "abs")


def bars_of(sid, max_bars):
    """由舊到新的還原價日線（欄位名對齊 app.patterns / backtest_patterns 的慣例）。"""
    return db.query(
        "SELECT trade_date, adj_open AS open, adj_high AS high, adj_low AS low,"
        "       adj_close AS close, volume "
        "FROM (SELECT trade_date, adj_open, adj_high, adj_low, adj_close, volume,"
        "        row_number() OVER (ORDER BY trade_date DESC) AS rn "
        "      FROM price_daily WHERE stock_id=%(id)s AND adj_close IS NOT NULL) z "
        "WHERE rn <= %(n)s ORDER BY trade_date",
        {"id": sid, "n": max_bars})


def pick_brick(bars, args):
    if args.brick_mode == "abs":
        if not args.brick:
            raise SystemExit("--brick-mode abs 需要同時給 --brick 金額")
        return args.brick, f"固定 {args.brick:g} 元"
    if args.brick_mode == "pct":
        b = nc.pct_brick(bars, args.pct)
        return b, f"{args.pct:g}% × 最後收盤 = {b:.2f} 元"
    b = nc.atr_brick(bars, args.atr_n, args.atr_k)
    if b is None:
        raise SystemExit(f"資料不足 {args.atr_n + 1} 根，算不出 ATR；改用 --brick-mode pct")
    return b, f"{args.atr_k:g} × ATR({args.atr_n}) = {b:.2f} 元"


def fwd_returns(bars, sig, horizon):
    """翻轉點之後 horizon 個交易日的報酬，依訊號方向調正（順預期方向獲利為正）。"""
    cs = [float(b["close"]) for b in bars]
    rs = []
    for s in sig:
        i, j = s["index"], s["index"] + horizon
        if j < len(cs) and cs[i]:
            rs.append(s["dir"] * (cs[j] / cs[i] - 1))
    return rs


def describe(name, bars, series, sig, args):
    comp = nc.compression(bars, series)
    print(f"\n--- {name} ---")
    print(f"  原始 {comp['bars']} 根 → {comp['units']} 筆"
          f"（壓縮到 {comp['ratio']:.1%}）｜方向翻轉 {len(sig)} 次")
    if series:
        last = series[-1]
        print(f"  目前方向：{'多' if last['dir'] == 1 else '空'}"
              f"（最後一筆 {last['trade_date']}）")
    if args.fwd:
        rs = fwd_returns(bars, sig, args.fwd)
        if len(rs) >= 5:
            win = sum(1 for r in rs if r > 0) / len(rs)
            print(f"  [體檢] 翻轉後 {args.fwd} 日：n={len(rs)}　勝率 {win:.1%}　"
                  f"平均 {statistics.mean(rs):+.2%}　中位 {statistics.median(rs):+.2%}")
        else:
            print(f"  [體檢] 翻轉次數 {len(rs)} 太少（<5），不做統計")
    if args.show:
        print(f"  最後 {args.show} 筆：")
        for x in series[-args.show:]:
            arrow = "▲" if x["dir"] == 1 else "▼"
            lo = x.get("bottom", min(x.get("open", 0), x.get("close", 0)))
            hi = x.get("top", max(x.get("open", 0), x.get("close", 0)))
            print(f"    {x['trade_date']}  {arrow}  {lo:8.2f} ~ {hi:8.2f}")


def sweep(sid, bars):
    """掃參數：看不同磚高/線數下的壓縮率與訊號數，用來挑合理範圍。"""
    print(f"\n=== 參數掃描 {sid}（{len(bars)} 根）===")
    print(f"{'磚高(ATR倍數)':>14} {'磚高(元)':>10} {'磚數':>6} {'壓縮率':>8} {'翻轉':>6}")
    for k in (0.5, 0.75, 1.0, 1.5, 2.0, 3.0):
        b = nc.atr_brick(bars, 14, k)
        if not b:
            continue
        br = nc.renko(bars, b)
        print(f"{k:>14.2f} {b:>10.2f} {len(br):>6} "
              f"{nc.compression(bars, br)['ratio']:>7.1%} {len(nc.flips(br)):>6}")
    print(f"\n{'線數':>14} {'線數量':>10} {'壓縮率':>8} {'翻轉':>6}")
    for n in (2, 3, 4, 5):
        tl = nc.three_line_break(bars, lines=n)
        print(f"{n:>14} {len(tl):>10} {nc.compression(bars, tl)['ratio']:>7.1%} "
              f"{len(nc.flips(tl)):>6}")


def main():
    ap = argparse.ArgumentParser(description="price_daily 日線 → 磚形圖 / 三線反轉")
    ap.add_argument("stock_id", help="股票代號，如 2330")
    ap.add_argument("--bars", type=int, default=750, help="取最近幾根日線（預設 750 ≈ 3 年）")
    ap.add_argument("--brick-mode", choices=BRICK_MODES, default="atr", help="磚高決定方式")
    ap.add_argument("--atr-n", type=int, default=14, help="ATR 期數（brick-mode=atr）")
    ap.add_argument("--atr-k", type=float, default=1.0, help="ATR 倍數（brick-mode=atr）")
    ap.add_argument("--pct", type=float, default=3.0, help="磚高百分比（brick-mode=pct）")
    ap.add_argument("--brick", type=float, default=0.0, help="磚高金額（brick-mode=abs）")
    ap.add_argument("--lines", type=int, default=3, help="幾線反轉（預設 3）")
    ap.add_argument("--show", type=int, default=0, help="另印最後 N 筆明細")
    ap.add_argument("--fwd", type=int, default=0, help="做「翻轉後 N 日報酬」的粗略體檢")
    ap.add_argument("--sweep", action="store_true", help="掃參數，不印單一結果")
    args = ap.parse_args()

    bars = bars_of(args.stock_id, args.bars)
    if len(bars) < 30:
        raise SystemExit(f"{args.stock_id} 只取到 {len(bars)} 根還原價日線，太少（先跑 build_adjusted_price.py？）")
    print(f"=== {args.stock_id}｜{bars[0]['trade_date']} ~ {bars[-1]['trade_date']}"
          f"（{len(bars)} 根，還原價）===")

    if args.sweep:
        sweep(args.stock_id, bars)
        return

    brick, how = pick_brick(bars, args)
    print(f"磚高：{how}")
    br = nc.renko(bars, brick)
    describe("磚形圖 Renko", bars, br, nc.flips(br), args)
    tl = nc.three_line_break(bars, lines=args.lines)
    describe(f"{args.lines} 線反轉 Three-Line Break", bars, tl, nc.flips(tl), args)


if __name__ == "__main__":
    main()
