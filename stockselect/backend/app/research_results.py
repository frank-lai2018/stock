"""帶程式版本的離線研究結果；規則變更後前端不沿用舊績效文字。"""
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from . import db, decision_center, portfolio_sim

ROOT = Path(__file__).resolve().parents[1]
RESULT_PATH = ROOT / "research" / "decision_backtest_v3.json"


def fingerprint():
    digest = hashlib.sha256()
    for path in [ROOT / "app" / f for f in (
            "decision_center.py", "execution.py", "consolidation.py", "weekly_breakout.py", "portfolio_sim.py", "research_results.py",
            "price_action.py", "swings.py", "breakout_rank.py")
            ] + [ROOT / "backtest_decision_center.py", ROOT.parent / "sql" / "mv_stock_snapshot.sql"]:
        digest.update(path.name.encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def load_report():
    if not RESULT_PATH.exists():
        return {"status": "unvalidated", "message": "新版尚無離線研究结果", "variants": []}
    try:
        result = json.loads(RESULT_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"status": "unavailable", "message": "研究結果檔無法讀取", "variants": []}
    if result.get("fingerprint") != fingerprint():
        return {"status": "outdated", "message": "規則已改變，需重跑同版本回測", "variants": []}
    return result


def metrics(curve, initial):
    import numpy as np
    equity = np.asarray([r["equity"] for r in curve], dtype=float)
    if not len(equity):
        return {"total_return": None, "max_drawdown": None, "cagr": None, "sharpe": None}
    returns = np.diff(np.r_[initial, equity]) / np.r_[initial, equity][:-1]
    sd = returns.std(ddof=1) if len(returns) > 1 else 0
    peak = np.maximum.accumulate(np.r_[initial, equity])[1:]
    return {"total_return": float(equity[-1] / initial - 1),
            "max_drawdown": float((equity / peak - 1).min()),
            "cagr": float((equity[-1] / initial) ** (252 / len(equity)) - 1),
            "sharpe": float(returns.mean() / sd * np.sqrt(252)) if sd else None}


def build_report(D, by_date, combos, picks, capital, lot_size):
    import backtest_decision_center as backtest
    bars = backtest.price_bars(D["px"])
    variants = []
    split = sorted(by_date)[len(by_date) // 2]
    for mode, gate in combos:
        name = backtest.variant_name(mode, gate)
        portfolio = portfolio_sim.run(D["dates"], by_date, bars, D["market"], mode,
                                      gate or decision_center.DEFAULT_GATE, capital=capital, lot_size=lot_size)
        curve, trades = portfolio["curve"], portfolio["trades"]
        group = picks[picks["variant"] == name] if len(picks) else picks
        executed = group[group["ret"].notna()] if len(group) else group
        completed = len(executed)
        event = {"selected": len(group), "executed": completed,
                 "skipped": int((group["outcome"] == "skipped").sum()) if len(group) else 0,
                 "mean_net_return": float(executed["ret"].mean()) if completed else None,
                 "win_rate": float((executed["ret"] > 0).mean()) if completed else None,
                 "top10_60d_rate": float(executed["top10_60d"].mean()) if completed else None,
                 "up30_60d_rate": float(executed["up30_60d"].mean()) if completed else None}
        outcome = metrics(curve, capital)
        outcome.update(trades=len(trades), skipped=len(portfolio["skipped"]), open_positions=portfolio["open_positions"],
                       mean_exposure=sum(r["exposure"] for r in curve) / len(curve) if curve else 0,
                       turnover=sum(t["notional"] for t in trades) * 2 / capital,
                       lot_size=lot_size, accounting=portfolio["accounting"])
        first = [r for r in curve if r["date"] <= split]
        second = [r for r in curve if r["date"] > split]
        periods = [{"name": "前段", "end": split, **metrics(first, capital)},
                   {"name": "後段", "start_after": split,
                    **metrics(second, first[-1]["equity"] if first else capital)}]
        variants.append({"mode": mode, "gate": gate, "name": name, "model_version": decision_center.MODES[mode],
                         "event": event, "portfolio": outcome, "curve": curve, "periods": periods})
        print(f"組合 {name}：交易 {len(trades)}、總報酬 {outcome['total_return']:.2%}、"
              f"最大回撤 {outcome['max_drawdown']:.2%}、平均曝險 {outcome['mean_exposure']:.1%}", flush=True)
    benchmark_rows = db.query(
        "SELECT trade_date,adj_open AS open,adj_close AS close FROM price_daily "
        "WHERE stock_id='0050' AND trade_date>=%(start)s ORDER BY trade_date", {"start": min(by_date)})
    benchmark = []
    if benchmark_rows:
        # 0050 與策略同在第一個訊號日之後進場；買賣費用及 ETF 稅與股票不同。
        entry_day = next(d for d in D["dates"] if d > min(by_date))
        rows = [r for r in benchmark_rows if r["trade_date"] >= entry_day]
        if rows and rows[0]["trade_date"] == entry_day:
            entry = float(rows[0]["open"]) * 1.001
            benchmark = [{"date": r["trade_date"], "equity": capital *
                          (float(r["close"]) * 0.999 / entry - 0.00385)} for r in rows]
    signal_end = max(by_date)
    population = D["forward60"].where(D["feats"]["scan"]).loc[sorted(by_date)]
    valid = int(population.notna().sum().sum())
    return {"status": "in_sample", "message": "同版本歷史研究；尚未證明樣本外有效",
            "fingerprint": fingerprint(), "computed_at": datetime.now(timezone.utc).isoformat(),
            "signal_start": min(by_date), "signal_end": signal_end, "data_end": max(D["dates"]),
            "capital": capital, "opportunity": {"top10_60d_rate": 0.1,
                "up30_60d_rate": float((population >= .3).sum().sum() / valid) if valid else None},
            "benchmark": {"name": "0050（還原價、扣 ETF 成本與滑價）",
                                               **metrics(benchmark, capital), "curve": benchmark},
            "variants": variants, "validation": {"status": "in_sample", "forward_split_date": split,
            "limitations": ["目前主檔存在存活者偏差", "財報公告日為近似值、未保存歷史修訂版本",
                            "產業分類使用目前主檔", "日 K 無法精確重建漲跌停委託佇列",
                            "股利採還原價再投資口徑，非券商現金股利帳",
                            "這段歷史已參與規則設計，分段報告不能當作真正樣本外證據"]}}


def save_report(result, path):
    target = Path(path).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_suffix(target.suffix + ".tmp")
    temp.write_text(json.dumps(result, ensure_ascii=False, default=str, allow_nan=False, indent=2), encoding="utf-8")
    temp.replace(target)
    print(f"研究結果 → {target}", flush=True)
