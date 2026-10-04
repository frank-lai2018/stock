import copy
import unittest

from app import decision_center as dc, portfolio_sim
from test_decision_center import candidate, EMPTY_HOLDINGS
from test_execution import START, bar


class PortfolioTest(unittest.TestCase):
    def test_repeated_signals_do_not_duplicate_held_stock(self):
        bars = {"A": [bar(i) for i in range(30)]}
        signals = {bar(i)["trade_date"]: [candidate("A", "X", trend_template=True)] for i in range(4)}
        markets = {d: {"above": True} for d in signals}
        result = portfolio_sim.run([b["trade_date"] for b in bars["A"]], signals, bars, markets,
                                   "momentum", "trend_template", lot_size=1)
        self.assertEqual(len(result["trades"]), 1)
        self.assertTrue(all(r["cash"] >= 0 for r in result["curve"]))

    def test_total_positions_and_risk_apply_across_days(self):
        bars = {sid: [bar(i) for i in range(30)] for sid in ("A", "B")}
        signals = {START: [candidate("A", "X", trend_template=True)],
                   bar(1)["trade_date"]: [candidate("B", "Y", trend_template=True)]}
        result = portfolio_sim.run([b["trade_date"] for b in bars["A"]], signals, bars,
                                   {d: {"above": True} for d in signals}, "momentum", "trend_template",
                                   max_total_positions=1, max_total_risk_pct=.5)
        self.assertEqual(len(result["trades"]), 1)
        self.assertTrue(all(r["positions"] <= 1 for r in result["curve"]))
        self.assertLessEqual(result["trades"][0]["notional"] * .08, 5000)

    def test_consolidation_is_not_used_by_control_gate(self):
        item = candidate("A", "X", trend_template=True)
        item["strategies"] = [{**item["strategies"][0], "key": "consolidation", "target": None, "max_entry": 103}]
        scan = {"as_of": START.isoformat(), "items": [item], "scanned": 1}
        baseline = dc.build_decision_response(scan, [], EMPTY_HOLDINGS, market={"above": True})
        experiment = dc.build_decision_response(scan, [], EMPTY_HOLDINGS, market={"above": True}, gate="consolidation")
        self.assertEqual(baseline["items"], [])  # 對照組用不到整理突破，只有它的股票不列為候選
        self.assertTrue(experiment["items"][0]["selected"])

    def test_missing_market_and_stale_snapshot_block(self):
        item = candidate("A", "X", trend_template=True)
        scan = {"items": [item], "scanned": 1}
        self.assertEqual(dc.build_decision_response(scan, [], EMPTY_HOLDINGS, market={"above": None})["summary"]["selected"], 0)
        scan["data_health"] = {"healthy": False}
        self.assertEqual(dc.build_decision_response(scan, [], EMPTY_HOLDINGS, market={"above": True})["summary"]["selected"], 0)

    def test_today_close_is_not_used_to_size_open_order(self):
        bars = {sid: [bar(i) for i in range(30)] for sid in ("A", "B")}
        signals = {START: [candidate("A", "X", trend_template=True), candidate("B", "Y", trend_template=True)]}
        dates = [b["trade_date"] for b in bars["A"]]
        r1 = portfolio_sim.run(dates, signals, bars, {START: {"above": True}}, "momentum", "trend_template")
        changed = copy.deepcopy(bars)
        changed["A"][1].update(close=108, raw_close=108, high=109, raw_high=109)
        r2 = portfolio_sim.run(dates, signals, changed, {START: {"above": True}}, "momentum", "trend_template")
        a = next(t for t in r1["trades"] if t["stock_id"] == "B")
        b = next(t for t in r2["trades"] if t["stock_id"] == "B")
        self.assertEqual(a["shares"], b["shares"])

    def test_later_prices_cannot_change_earlier_portfolio_curve(self):
        bars = {"A": [bar(i) for i in range(30)]}
        signals = {START: [candidate("A", "X", trend_template=True)]}
        dates = [b["trade_date"] for b in bars["A"]]
        markets = {START: {"above": True}}
        first = portfolio_sim.run(dates, signals, bars, markets, "momentum", "trend_template")
        changed = copy.deepcopy(bars)
        for row in changed['A'][10:]:
            row.update(open=85, high=86, low=84, close=85,
                       raw_open=85, raw_high=86, raw_low=84, raw_close=85)
        second = portfolio_sim.run(dates, signals, changed, markets, "momentum", "trend_template")
        self.assertEqual(first['curve'][:10], second['curve'][:10])
        self.assertNotEqual(first['curve'][-1]['equity'], second['curve'][-1]['equity'])


if __name__ == "__main__":
    unittest.main()
