"""交易復盤：把 FIFO 配對出的已實現交易做橫切分析。

回答的是「我到底靠什麼賺錢、又都怎麼賠錢」：勝率／賺賠比／獲利因子／期望值、
賺賠各抱多久、以及分產業／持有天數／進場情境（動能・均線・本益比）的績效。

point-in-time：進場情境一律用「買進日（含）之前」的行情與估值重算，不用今天的
mv 快照，否則等於拿未來資訊自我安慰。報酬/均線用還原價（adj_close），
最大有利/不利波動（MFE/MAE）用未還原的高低價，才對得上你實際的成交價。
"""
import bisect

HOLD_BUCKETS = [(0, 1, "當沖/隔日"), (2, 5, "2~5 日"), (6, 20, "6~20 日"),
                (21, 60, "21~60 日"), (61, 120, "61~120 日"), (121, 10 ** 6, "120 日以上")]
MOM_BUCKETS = [(-10 ** 6, -0.10, "跌 10% 以上"), (-0.10, 0.0, "跌 0~10%"), (0.0, 0.10, "漲 0~10%"),
               (0.10, 0.30, "漲 10~30%"), (0.30, 10 ** 6, "漲 30% 以上")]
PER_BUCKETS = [(0, 10, "10 倍以下"), (10, 15, "10~15"), (15, 20, "15~20"),
               (20, 30, "20~30"), (30, 10 ** 6, "30 倍以上")]


def _at(dates, d):
    """回傳「日期 ≤ d」的最後一個索引；查無回 -1。"""
    return bisect.bisect_right(dates, d) - 1


def enrich(closed, px, val, meta):
    """替每筆已實現交易補上進場情境與持有期間的波動。closed 需含 stock_id。"""
    out = []
    for c in closed:
        sid = c["stock_id"]
        t = dict(c)
        m = meta.get(sid) or {}
        t["name"] = m.get("name") or sid
        t["industry"] = m.get("industry")
        # 先給預設值：查無行情/估值的股票也要有這些欄位，前端與分箱才不會缺鍵
        t.update({"entry_ret_3m": None, "entry_above_ma60": None, "entry_dist_52w_high": None,
                  "entry_per": None, "mfe": None, "mae": None})
        p = px.get(sid)
        bd, sd = c["buy_date"], c["sell_date"]
        if p:
            i, j = _at(p["d"], bd), _at(p["d"], sd)
            a = p["a"]
            if i >= 0:
                # 進場當下：近 3 月動能、是否站上季線、距 52 週高（皆用還原價）
                if i >= 63 and a[i - 63]:
                    t["entry_ret_3m"] = a[i] / a[i - 63] - 1
                if i >= 59:
                    t["entry_above_ma60"] = a[i] > sum(a[i - 59:i + 1]) / 60
                if i >= 251:
                    hi52 = max(a[i - 251:i + 1])
                    t["entry_dist_52w_high"] = (a[i] / hi52 - 1) if hi52 else None
            # 持有期間最大有利／不利波動（相對買進價；同日進出則無區間）
            bp = c["buy_price"]
            if bp and 0 <= i < j:
                t["mfe"] = max(p["h"][i + 1:j + 1]) / bp - 1
                t["mae"] = min(p["l"][i + 1:j + 1]) / bp - 1
        v = val.get(sid)
        if v:
            k = _at(v["d"], bd)
            per = v["per"][k] if k >= 0 else None
            t["entry_per"] = float(per) if per and float(per) > 0 else None
        out.append(t)
    return out


def _agg(rows):
    """一組交易的績效：筆數／勝率／平均報酬／平均持有天數／總損益。"""
    n = len(rows)
    if not n:
        return None
    wins = [r for r in rows if r["pnl"] > 0]
    rets = [r["ret_pct"] for r in rows if r["ret_pct"] is not None]
    return {
        "n": n,
        "win_rate": round(len(wins) / n * 100, 1),
        "avg_ret": round(sum(rets) / len(rets) * 100, 2) if rets else None,
        "avg_days": round(sum(r["days"] for r in rows) / n, 1),
        "pnl": round(sum(r["pnl"] for r in rows)),
    }


def _bucket_by(rows, key, buckets):
    """依數值欄位分箱（含 None 一組），保持箱子順序。"""
    out = []
    for lo, hi, label in buckets:
        sel = [r for r in rows if r.get(key) is not None and lo <= r[key] < hi]
        a = _agg(sel)
        if a:
            out.append({"label": label, **a})
    unknown = _agg([r for r in rows if r.get(key) is None])
    if unknown:
        out.append({"label": "無資料", **unknown})
    return out


def _group_by(rows, key, min_n=1, top=None):
    """依類別欄位分組（如產業／交易類別），依筆數排序。"""
    by = {}
    for r in rows:
        by.setdefault(r.get(key) or "未分類", []).append(r)
    out = [{"label": k, **_agg(v)} for k, v in by.items() if len(v) >= min_n]
    out.sort(key=lambda x: -x["n"])
    return out[:top] if top else out


def _streak(rows, win):
    """依賣出日排序後的最長連勝／連敗。"""
    best = cur = 0
    for r in sorted(rows, key=lambda x: x["sell_date"]):
        hit = (r["pnl"] > 0) if win else (r["pnl"] <= 0)
        cur = cur + 1 if hit else 0
        best = max(best, cur)
    return best


def summarize(rows):
    """整體績效指標：勝率、賺賠比、獲利因子、期望值，以及賺賠各抱多久。"""
    n = len(rows)
    if not n:
        return None
    wins = [r for r in rows if r["pnl"] > 0]
    losses = [r for r in rows if r["pnl"] <= 0]
    wret = [r["ret_pct"] for r in wins if r["ret_pct"] is not None]
    lret = [r["ret_pct"] for r in losses if r["ret_pct"] is not None]
    gross_win = sum(r["pnl"] for r in wins)
    gross_loss = abs(sum(r["pnl"] for r in losses))
    avg_win = (sum(wret) / len(wret)) if wret else None
    avg_loss = (sum(lret) / len(lret)) if lret else None
    rets = [r["ret_pct"] for r in rows if r["ret_pct"] is not None]
    # 賠錢的曾經最多賺過多少、賺錢的中途最多回檔多少（有沒有「賺的變賠的」）
    lose_mfe = [r["mfe"] for r in losses if r.get("mfe") is not None]
    win_mae = [r["mae"] for r in wins if r.get("mae") is not None]
    return {
        "n": n, "wins": len(wins), "losses": len(losses),
        "win_rate": round(len(wins) / n * 100, 1),
        "total_pnl": round(sum(r["pnl"] for r in rows)),
        "gross_win": round(gross_win), "gross_loss": round(gross_loss),
        "avg_win_pct": round(avg_win * 100, 2) if avg_win is not None else None,
        "avg_loss_pct": round(avg_loss * 100, 2) if avg_loss is not None else None,
        # 賺賠比＝平均獲利% ÷ 平均虧損%；獲利因子＝總獲利 ÷ 總虧損（>1 才是賺錢系統）
        "payoff": round(avg_win / abs(avg_loss), 2) if (avg_win and avg_loss) else None,
        "profit_factor": round(gross_win / gross_loss, 2) if gross_loss else None,
        "expectancy_pct": round(sum(rets) / len(rets) * 100, 2) if rets else None,
        "avg_days_win": round(sum(r["days"] for r in wins) / len(wins), 1) if wins else None,
        "avg_days_loss": round(sum(r["days"] for r in losses) / len(losses), 1) if losses else None,
        "best": max(rows, key=lambda r: r["pnl"]),
        "worst": min(rows, key=lambda r: r["pnl"]),
        "max_consec_win": _streak(rows, True),
        "max_consec_loss": _streak(rows, False),
        "lose_avg_mfe": round(sum(lose_mfe) / len(lose_mfe) * 100, 2) if lose_mfe else None,
        "win_avg_mae": round(sum(win_mae) / len(win_mae) * 100, 2) if win_mae else None,
        "turned_loser": sum(1 for r in losses if (r.get("mfe") or 0) >= 0.10),   # 曾賺 10% 以上卻收黑
    }


def cuts(rows):
    """各種切法的績效比較（前端切頁籤用）。"""
    ma60 = []
    for flag, label in ((True, "進場時站上季線"), (False, "進場時在季線下")):
        a = _agg([r for r in rows if r.get("entry_above_ma60") is flag])
        if a:
            ma60.append({"label": label, **a})
    return {
        "hold": _bucket_by(rows, "days", HOLD_BUCKETS),
        "momentum": _bucket_by(rows, "entry_ret_3m", MOM_BUCKETS),
        "per": _bucket_by(rows, "entry_per", PER_BUCKETS),
        "industry": _group_by(rows, "industry", min_n=3),
        "trade_type": _group_by(rows, "trade_type"),
        "ma60": ma60,
    }


def by_period(rows):
    """年度 / 月度已實現損益（依賣出日），含累計曲線。"""
    ym, yr = {}, {}
    for r in rows:
        m, y = r["sell_date"][:7], r["sell_date"][:4]
        ym[m] = ym.get(m, 0) + r["pnl"]
        yr.setdefault(y, []).append(r)
    months, cum = [], 0
    for m in sorted(ym):
        cum += ym[m]
        months.append({"month": m, "pnl": round(ym[m]), "cum": round(cum)})
    years = [{"year": y, **_agg(v)} for y, v in sorted(yr.items())]
    return {"months": months, "years": years}
