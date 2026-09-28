import unittest
import json
from unittest.mock import patch

from app import decision_center


def candidate(stock_id, industry, score=80, state="ready", consensus=1):
    strategies = [{
        "key": "breakout", "label": "型態突破", "score": score,
        "status": "priority" if state == "ready" else "watch",
        "signal_state": "triggered", "pattern": "double_bottom",
        "pattern_name": "W底", "entry": 100, "stop": 95, "target": 115,
        "risk_pct": 5, "rr": 3, "blockers": [], "notes": [], "parts": {},
        "pattern_prior": None,
    }]
    if consensus > 1:
        strategies.append({
            "key": "price_action", "label": "裸 K", "score": score - 2,
            "status": "priority", "signal_state": "triggered", "pattern": "pin_bull",
            "pattern_name": "多方 Pin Bar", "entry": 100, "stop": 96, "target": 112,
            "risk_pct": 4, "rr": 3, "blockers": [], "notes": [], "parts": {},
            "pattern_prior": None,
        })
    return {
        "stock_id": stock_id, "name": stock_id, "industry": industry, "close": 100,
        "amt20": 100_000_000, "rs_rating": 90, "state": state,
        "consensus_count": consensus, "base_score": score,
        "consensus_bonus": 6 if consensus > 1 else 0,
        "calibration_adjustment": 0, "decision_score": score + (6 if consensus > 1 else 0),
        "lead_strategy": "breakout", "strategies": strategies,
    }


class DecisionCenterTest(unittest.TestCase):
    def test_score_bucket(self):
        self.assertEqual(decision_center.score_bucket(75), "70-79")
        self.assertEqual(decision_center.score_bucket(100), "90-100")

    def test_position_sizing_uses_stop_risk(self):
        item = candidate("2330", "半導體")
        plan = decision_center._position_plan(
            item, item["strategies"][0], capital=1_000_000,
            risk_per_trade_pct=1, max_position_pct=30, lot_size=1000)
        self.assertTrue(plan["valid"])
        self.assertEqual(plan["suggested_shares"], 2000)  # 1萬元風險 / 每股5元
        self.assertEqual(plan["risk_amount"], 10000)

    def test_existing_industry_limit_blocks_new_position(self):
        scan = {"as_of": "2026-09-24", "scanned": 2,
                "items": [candidate("A", "半導體", 90), candidate("B", "航運", 80)]}
        holdings = {"items": [{"stock_id": "OLD"}], "stock_ids": {"OLD"},
                    "industry_counts": {"半導體": 1}}
        result = decision_center.build_decision_response(
            scan, [], holdings, capital=1_000_000, risk_per_trade_pct=1,
            max_new_positions=2, max_industry_positions=1)
        by_id = {x["stock_id"]: x for x in result["items"]}
        self.assertFalse(by_id["A"]["selected"])
        self.assertIn("產業上限", by_id["A"]["selection_reason"])
        self.assertTrue(by_id["B"]["selected"])

    def test_calibration_only_adjusts_after_thirty_samples(self):
        scan = {"as_of": "2026-09-24", "scanned": 1,
                "items": [candidate("A", "半導體", 80)]}
        base = {
            "strategy": "breakout", "score_bucket": "80-89", "target_hit_rate": 60,
            "target_hit_ci_low": 45, "target_hit_ci_high": 73, "positive_rate": 60,
            "avg_r": 0.5, "avg_days": 10, "confidence": "low",
        }
        low = decision_center.build_decision_response(
            scan, [{**base, "n": 29}], {"items": [], "stock_ids": set(), "industry_counts": {}},
            capital=1_000_000)
        high = decision_center.build_decision_response(
            scan, [{**base, "n": 30}], {"items": [], "stock_ids": set(), "industry_counts": {}},
            capital=1_000_000)
        self.assertEqual(low["items"][0]["calibration_adjustment"], 0)
        self.assertEqual(high["items"][0]["calibration_adjustment"], 2)

    def test_record_candidates_keeps_rich_decision_snapshot(self):
        scan = {"as_of": "2026-09-24", "scanned": 1,
                "items": [candidate("2330", "半導體", 88)]}
        response = decision_center.build_decision_response(
            scan, [], {"items": [], "stock_ids": set(), "industry_counts": {}},
            capital=1_000_000, max_new_positions=1)
        with patch.object(decision_center, "ensure_tables"), \
             patch.object(decision_center.db, "query", side_effect=[[{"n": 0}], [{"n": 1}]]), \
             patch.object(decision_center.db, "execute_many") as execute_many:
            recorded = decision_center.record_candidates(scan, response)
        self.assertEqual(recorded, 1)
        values = execute_many.call_args.args[1]
        self.assertEqual(len(values), 1)
        self.assertTrue(values[0][17])  # is_selected
        snapshot = json.loads(values[0][-1])
        self.assertTrue(snapshot["selected"])
        self.assertEqual(snapshot["position_plan"]["entry"], 100)


if __name__ == "__main__":
    unittest.main()
