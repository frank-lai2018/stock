"""比較RS、營收排序及營收篩選；相同訊號／成交／資金模型，不挑最好參數。

在backend執行 python backtest_growth_quality.py --workers 4
月營收只在推估可得日後一天使用；無公告／修訂版本，屬假設日期的歷史研究。
同時重算既有基準報告，維持同版本比較；不包含籌碼、今日族群或額外技術交集。
"""
import argparse
from datetime import date, datetime, timezone
import pickle
import sys

import backtest_decision_center as baseline
from app import db, decision_center as dc, growth_quality as growth, growth_research, portfolio_sim, research_results

VARIANTS = [
    {"code": "rs", "label": "RS基準", "ranking": "rs", "filter": False},
    {"code": "growth_rank", "label": "營收品質排序", "ranking": "growth_quality", "filter": False},
    {"code": "growth_filter_rs", "label": "營收篩選＋RS排序", "ranking": "rs", "filter": True},
    {"code": "growth_filter_rank", "label": "營收篩選＋品質排序", "ranking": "growth_quality", "filter": True},
]


def enrich(by_date, start):
    rows = db.query("SELECT stock_id,revenue_month,revenue,available_date FROM monthly_revenue "
                    "WHERE revenue_month>=%(start)s ORDER BY stock_id,revenue_month",
                    {"start": growth.shift_month(start.replace(day=1), -19)})
    grouped = {}
    for row in rows:
        grouped.setdefault(row["stock_id"], []).append(row)
    stats = {"candidates": 0, "available": 0, "passed": 0}
    out = {}
    for d, items in by_date.items():
        enriched = []
        for source in items:
            feature = growth.analyze(grouped.get(source["stock_id"], []), d)
            stats["candidates"] += 1
            stats["available"] += int(feature["available"])
            stats["passed"] += int(feature["passed"])
            enriched.append({**source, "growth_quality": feature})
        out[d] = enriched
    return out, stats


def scoped(by_date, filtered):
    # 保留空白訊號日，使各組起迄、資金曲線與0050一致。
    return {d: [x for x in rows if not filtered or x["growth_quality"]["passed"]]
            for d, rows in by_date.items()}


def event_metrics(D, signals, bars, gate, ranking):
    empty = {"items": [], "stock_ids": set(), "industry_counts": {}}
    returns, top10, up30 = [], [], []
    selected = skipped = 0
    for d, items in sorted(signals.items()):
        response = dc.build_decision_response({"as_of": str(d), "items": items}, [], empty,
                    mode="momentum", gate=gate, market=D["market"].get(d), lot_size=1,
                    ranking=ranking, limit=500)
        for item in response["items"]:
            if not item["selected"]:
                continue
            selected += 1
            strategy = next(s for s in item["strategies"] if s["key"] == item["lead_strategy"])
            spec = dc.execution_spec(item, strategy, "momentum")
            spec["observed_date"] = d
            fill = dc.execution.simulate(bars[item["stock_id"]], spec, D["dates"])
            if fill["net_return"] is None:
                skipped += 1
                continue
            returns.append(fill["net_return"])
            sid = item["stock_id"]
            top10.append(bool(D["winner_rank"].at[d, sid] >= .9))
            up30.append(bool(D["forward60"].at[d, sid] >= .3))
    n = len(returns)
    return {"selected": selected, "executed": n, "skipped": skipped,
            "mean_net_return": sum(returns) / n if n else None,
            "win_rate": sum(r > 0 for r in returns) / n if n else None,
            "top10_60d_rate": sum(top10) / n if n else None,
            "up30_60d_rate": sum(up30) / n if n else None}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--start", default="2024-07-01")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--lot-size", type=int, choices=(1, 1000), default=1)
    ap.add_argument("--capital", type=float, default=1_000_000)
    ap.add_argument("--reuse-candidates", action="store_true", help="僅本機同一次資料研究續跑時使用，不重掃技術訊號")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    start = date.fromisoformat(args.start)
    D = baseline.load_all(start)
    cache = research_results.ROOT / "research" / "local_backup" / "growth_candidates.pkl"
    stamp = (str(start), str(max(D["dates"])), research_results.fingerprint())
    if args.reuse_candidates:
        # 僅讀本程式在受控本機路徑產生的pickle；從不讀外部或使用者提供的檔案。
        cached = pickle.loads(cache.read_bytes())
        if cached["stamp"] != stamp:
            raise SystemExit("候選快取版本／日期不同，請移除--reuse-candidates重跑")
        by_date = cached["by_date"]
    else:
        by_date = baseline.scan_candidates(D, max(1, args.workers))
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_bytes(pickle.dumps({"stamp": stamp, "by_date": by_date}))
    print("重算既有策略基準", flush=True)
    combos = baseline.variants()
    picks = baseline.evaluate(D, by_date, combos)
    base_report = research_results.build_report(D, by_date, combos, picks, args.capital, args.lot_size)
    research_results.save_report(base_report, research_results.RESULT_PATH)
    enriched, coverage = enrich(by_date, start)
    bars = baseline.price_bars(D["px"])
    split = sorted(by_date)[len(by_date) // 2]
    variants = []
    for gate in ("trend_template", "weekly"):
        for variant in VARIANTS:
            name = f"{'趨勢模板' if gate == 'trend_template' else '週線買點'}・{variant['label']}"
            signals = scoped(enriched, variant["filter"])
            simulation = portfolio_sim.run(D["dates"], signals, bars, D["market"], "momentum", gate,
                        capital=args.capital, lot_size=args.lot_size, ranking=variant["ranking"])
            curve = simulation["curve"]
            metrics = research_results.metrics(curve, args.capital)
            metrics.update(trades=len(simulation["trades"]), skipped=len(simulation["skipped"]),
                           open_positions=simulation["open_positions"], lot_size=args.lot_size,
                           mean_exposure=sum(r["exposure"] for r in curve) / len(curve) if curve else 0)
            first = [r for r in curve if r["date"] <= split]
            second = [r for r in curve if r["date"] > split]
            periods = [{"name": "前段", **research_results.metrics(first, args.capital)},
                       {"name": "後段", **research_results.metrics(second, first[-1]["equity"] if first else args.capital)}]
            event = event_metrics(D, signals, bars, gate, variant["ranking"])
            variants.append({**variant, "name": name, "gate": gate, "portfolio": metrics,
                             "event": event, "curve": curve, "periods": periods})
            print(f"{name}：報酬 {metrics['total_return']:.2%}、回撤 {metrics['max_drawdown']:.2%}、交易 {metrics['trades']}", flush=True)
    report = {"status": "in_sample_assumed_dates", "fingerprint": growth_research.fingerprint(),
              "message": "營收品質歷史研究：推估可得日、尚無前瞻驗證；額外族群／籌碼交集不適用此績效。",
              "computed_at": datetime.now(timezone.utc).isoformat(), "version": growth.VERSION,
              "rule": growth.RULE, "timing": growth.TIMING, "capital": args.capital,
              "signal_start": min(by_date), "signal_end": max(by_date), "data_end": max(D["dates"]),
              "coverage": coverage, "benchmark": base_report["benchmark"], "variants": variants,
              "validation": {"split_date": split, "limitations": [
                  "使用目前營收版本；歷史修訂與晚報無法完整還原，不能視為嚴格point-in-time回測",
                  "推估可得日後一天才使用營收；缺日期、缺月、非正值、過期不評分",
                  "未依本次結果最佳化門檻／權重；兩段皆屬歷史研究，不是樣本外證據",
                  "同一訊號可能重複同檔股票；事件勝率與60日捕捉率不是獨立預測機率",
                  "目前主檔存在存活者偏差；日K漲跌停與委託佇列為近似",
                  "20日持有、8%停損、每邊0.1%滑價及0.6%來回成本；0050扣ETF成本",
                  "只比較趨勢模板／週線主買點；未驗證其他買點、族群、籌碼組合與趨勢持有模式",
              ]}}
    research_results.save_report(report, growth_research.RESULT_PATH)


if __name__ == "__main__":
    main()
