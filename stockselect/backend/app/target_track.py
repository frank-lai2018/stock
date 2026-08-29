r"""target_track.py — 突破後的「量測滿足價」達成追蹤（純函式，無 DB）。

自選股加入時，snapshot.breakout 已存了型態偵測當下的 neckline / target / breakout_date /
breakout_close（見 swings.py，target = 突破價 + 型態高度）。這裡回答三件事：

  1. 曾經到過目標價嗎？（用最高/最低價判定，不是收盤——盤中摸到就算）
  2. 到達花了幾個交易日？
  3. 現在距離目標還差多少？走完幾成？

**為什麼用「比例」而不是直接比價**：還原價是回頭調整的，之後每次除權息都會把歷史
adj_* 整條重新縮放。三個月前存的 target 是當時那條 adj 序列上的數字，跟今天的 adj
不同基準，直接比會錯。所以改成：
    target_ratio = target / breakout_close          （同一基準相除 → 不受再調整影響）
    今日基準的目標 = 突破日的 adj_close × target_ratio
這樣不論中間除權息幾次都對得起來。

bars：突破日（含）之後、由舊到新的 list[dict]，需含 trade_date/high/low/close（用 adj_*）。
"""

__all__ = ["track", "summarize"]


def _f(x):
    return None if x is None else float(x)


def track(breakout, bars):
    """算單一筆的追蹤結果；資料不足回 None。

    breakout：snapshot["breakout"]，需有 dir / target / breakout_close（breakout_date 由呼叫端用來切 bars）。
    回傳 dict：
      dir            bull / bear
      target         換算到「今日 adj 基準」的目標價
      hit            是否曾經到過目標
      hit_date       第一次到達的日期（未達為 None）
      days_to_hit    突破日算起第幾個交易日到達（突破當日=0；未達為 None）
      days_elapsed   突破後已經過幾個交易日
      last_close     最新收盤
      progress       走完幾成（1.0=剛好到目標；可 >1 或為負）
      gap_pct        還差幾 %（相對現價；已達標為 0 或負）
      best           最有利價（多=期間最高、空=期間最低）
      best_progress  最有利價走完幾成（曾經最接近的程度）
      worst_pct      最不利回檔（相對突破價，負值；判斷是否早該停損）
    """
    if not breakout or not bars:
        return None
    target0, base0 = _f(breakout.get("target")), _f(breakout.get("breakout_close"))
    if not target0 or not base0 or base0 <= 0:
        return None
    bull = breakout.get("dir") != "bear"

    c0 = _f(bars[0]["close"])                       # 突破日在「今日 adj 基準」下的收盤
    if not c0 or c0 <= 0:
        return None
    target = c0 * (target0 / base0)                 # 比例換算，避開還原價重新縮放的坑
    span = target - c0
    if span == 0:
        return None

    hit_i = None
    best = c0
    worst = c0
    for i, b in enumerate(bars):
        hi, lo = _f(b["high"]), _f(b["low"])
        if hi is None or lo is None:
            continue
        if bull:
            best = max(best, hi)
            worst = min(worst, lo)
            reached = hi >= target
        else:
            best = min(best, lo)
            worst = max(worst, hi)
            reached = lo <= target
        if reached and hit_i is None:
            hit_i = i

    last = _f(bars[-1]["close"])
    # progress 用同一個 span 正規化，多空共用（span 在空方為負，相除後仍是「走完幾成」）
    progress = (last - c0) / span
    gap = (target - last) / last if last else None
    return {
        "dir": "bull" if bull else "bear",
        "target": round(target, 2),
        "breakout_close_adj": round(c0, 2),
        "hit": hit_i is not None,
        "hit_date": bars[hit_i]["trade_date"] if hit_i is not None else None,
        "days_to_hit": hit_i,
        "days_elapsed": len(bars) - 1,
        "last_close": round(last, 2) if last else None,
        "progress": round(progress, 4),
        "gap_pct": round(gap if bull else -gap, 4) if gap is not None else None,
        "best": round(best, 2),
        "best_progress": round((best - c0) / span, 4),
        "worst_pct": round(((worst - c0) / c0) * (1 if bull else -1), 4),
    }


def summarize(tracks):
    """一批追蹤結果的統計：達標率、達標天數中位數、未達標者的平均進度。

    這是**你自己資料的實證**，比任何書上的 measure-rule 命中率都貼近你的用法。
    注意樣本偏差：自選股是你挑過的，不是隨機樣本，別拿來當全市場結論。
    """
    ts = [t for t in tracks if t]
    if not ts:
        return None
    hits = [t for t in ts if t["hit"]]
    days = sorted(t["days_to_hit"] for t in hits)
    miss = [t for t in ts if not t["hit"]]
    med = None
    if days:
        m = len(days)
        med = days[m // 2] if m % 2 else (days[m // 2 - 1] + days[m // 2]) / 2
    return {
        "n": len(ts),
        "hit": len(hits),
        "hit_rate": round(len(hits) / len(ts), 4),
        "days_to_hit_median": med,
        "days_to_hit_min": days[0] if days else None,
        "days_to_hit_max": days[-1] if days else None,
        "miss_avg_progress": round(sum(t["progress"] for t in miss) / len(miss), 4) if miss else None,
        "avg_days_elapsed": round(sum(t["days_elapsed"] for t in ts) / len(ts), 1),
    }
