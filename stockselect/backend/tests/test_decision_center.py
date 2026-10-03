import unittest
import json
from unittest.mock import patch

from app import decision_center


EMPTY_HOLDINGS = {"items": [], "stock_ids": set(), "industry_counts": {}}


def candidate(stock_id, industry, score=80, state="ready", consensus=1, trend_template=False):
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
        "trend_template": trend_template,
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
        plan = decision_center._position_plan(
            100, 95, 115, capital=1_000_000,
            risk_per_trade_pct=1, max_position_pct=30, lot_size=1000)
        self.assertTrue(plan["valid"])
        self.assertEqual(plan["suggested_shares"], 2000)  # 1萬元風險 / 每股5元
        self.assertEqual(plan["risk_amount"], 10000)
        self.assertEqual(plan["rr"], 3)

    def test_position_plan_without_target(self):
        plan = decision_center._position_plan(
            100, 92, None, capital=1_000_000, risk_per_trade_pct=0.8,
            max_position_pct=25, lot_size=1, exit_rule="第 20 個交易日收盤出場")
        self.assertTrue(plan["valid"])
        self.assertEqual(plan["suggested_shares"], 1000)  # 8000 元風險 / 每股 8 元
        self.assertIsNone(plan["target"])
        self.assertIsNone(plan["rr"])
        self.assertEqual(plan["exit_rule"], "第 20 個交易日收盤出場")

    def test_existing_industry_limit_blocks_new_position(self):
        scan = {"as_of": "2026-09-24", "scanned": 2,
                "items": [candidate("A", "半導體", 90), candidate("B", "航運", 80)]}
        holdings = {"items": [{"stock_id": "OLD"}], "stock_ids": {"OLD"},
                    "industry_counts": {"半導體": 1}}
        result = decision_center.build_decision_response(
            scan, [], holdings, capital=1_000_000, risk_per_trade_pct=1,
            max_new_positions=2, max_industry_positions=1, mode="classic")
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
            scan, [{**base, "n": 29}], EMPTY_HOLDINGS, capital=1_000_000, mode="classic")
        high = decision_center.build_decision_response(
            scan, [{**base, "n": 30}], EMPTY_HOLDINGS, capital=1_000_000, mode="classic")
        self.assertEqual(low["items"][0]["calibration_adjustment"], 0)
        self.assertEqual(high["items"][0]["calibration_adjustment"], 2)
        # 動能模式以 RS 排序，校準不調整分數
        mom = decision_center.build_decision_response(
            scan, [{**base, "n": 30}], EMPTY_HOLDINGS, capital=1_000_000, mode="momentum")
        self.assertEqual(mom["items"][0]["calibration_adjustment"], 0)

    def test_record_candidates_keeps_rich_decision_snapshot(self):
        scan = {"as_of": "2026-09-24", "scanned": 1,
                "items": [candidate("2330", "半導體", 88)]}
        response = decision_center.build_decision_response(
            scan, [], EMPTY_HOLDINGS, capital=1_000_000, max_new_positions=1, mode="classic")
        with patch.object(decision_center, "ensure_tables"), \
             patch.object(decision_center.db, "query", side_effect=[[{"n": 0}], [{"n": 1}]]), \
             patch.object(decision_center.db, "execute_many") as execute_many:
            recorded = decision_center.record_candidates(scan, response, "classic")
        self.assertEqual(recorded, 1)
        values = execute_many.call_args.args[1]
        self.assertEqual(len(values), 1)
        self.assertEqual(values[0][3], decision_center.MODEL_VERSION)
        self.assertEqual((values[0][9], values[0][10]), (95, 115))  # 原始規則用策略自己的停損／目標
        self.assertTrue(values[0][17])  # is_selected
        snapshot = json.loads(values[0][-1])
        self.assertTrue(snapshot["selected"])
        self.assertEqual(snapshot["position_plan"]["entry"], 100)


def momentum_scan():
    """A：型態突破可執行、無趨勢模板；B：趨勢模板＋裸 K 可執行、突破只到觀察級；C：趨勢模板＋型態突破可執行。"""
    a = candidate("A", "航運", 82)
    b = candidate("B", "電子", 84, consensus=2, trend_template=True)
    b["strategies"][0]["status"] = "watch"
    b["strategies"][0]["blockers"] = ["突破量比未達 1.5"]
    c = candidate("C", "半導體", 80, trend_template=True)
    a["rs_rating"], b["rs_rating"], c["rs_rating"] = 95, 90, 85
    return {"as_of": "2026-10-02", "scanned": 3, "items": [a, b, c]}


class MomentumGateTest(unittest.TestCase):
    def build(self, gate, market=None, **kwargs):
        result = decision_center.build_decision_response(
            momentum_scan(), [], EMPTY_HOLDINGS, capital=1_000_000, max_new_positions=5,
            mode="momentum", gate=gate, market=market or {"above": True}, **kwargs)
        return result, {x["stock_id"]: x for x in result["items"]}

    def test_gate_selects_by_condition(self):
        expected = {"trend_template": {"B", "C"}, "breakout": {"A", "C"}}
        for gate, ids in expected.items():
            result, by_id = self.build(gate)
            self.assertEqual({sid for sid, x in by_id.items() if x["selected"]}, ids, gate)
            self.assertEqual(result["gate"], gate)
            self.assertEqual(result["summary"]["ready_momentum"], len(ids))
        _, by_id = self.build("breakout")
        self.assertIn("型態突破只到觀察級", by_id["B"]["selection_reason"])
        _, by_id = self.build("trend_template")
        self.assertIn("趨勢模板未成立", by_id["A"]["selection_reason"])

    def test_total_counts_candidates_beyond_limit(self):
        result, _ = self.build("trend_template", limit=1)
        self.assertEqual((result["count"], result["total"]), (1, 3))

    def test_unknown_gate_falls_back_to_default(self):
        for gate in ("nope", "both"):
            result, _ = self.build(gate)
            self.assertEqual(result["gate"], decision_center.DEFAULT_GATE)

    def test_rank_by_rs_and_fixed_stop_without_target(self):
        result, by_id = self.build("breakout")
        self.assertEqual([x["stock_id"] for x in result["items"]][:2], ["A", "C"])  # RS 95 > 85
        plan = by_id["A"]["position_plan"]
        self.assertEqual(plan["stop"], 92)
        self.assertIsNone(plan["target"])
        self.assertEqual(plan["exit_rule"], "第 20 個交易日收盤出場")

    def test_market_below_ma_blocks_new_positions(self):
        result, by_id = self.build("breakout", market={"above": False})
        self.assertTrue(result["market_blocked"])
        self.assertEqual(result["summary"]["selected"], 0)
        self.assertIn("60 日線", by_id["A"]["selection_reason"])

    def record(self, gate, limit=200):
        scan = momentum_scan()
        response = decision_center.build_decision_response(
            scan, [], EMPTY_HOLDINGS, capital=1_000_000, mode="momentum", gate=gate,
            market={"above": True}, limit=limit)
        with patch.object(decision_center, "ensure_tables"), \
             patch.object(decision_center.db, "query", side_effect=[[{"n": 0}], [{"n": 4}]]), \
             patch.object(decision_center.db, "execute_many") as execute_many:
            decision_center.record_candidates(scan, response, "momentum", gate)
        return {(v[1], v[0]): v for v in execute_many.call_args.args[1]}

    def test_record_primary_gate_fills_main_columns(self):
        rows = self.record("trend_template")
        row = rows[("C", "breakout")]
        self.assertEqual(row[3], decision_center.MOMENTUM_VERSION)
        self.assertEqual((row[9], row[10]), (92, None))  # 進場價下 8%、不設目標
        self.assertTrue(row[17])
        self.assertIsNone(row[21])
        self.assertTrue(json.loads(row[22])["selected"])

    def test_record_other_gate_only_writes_gate_selection(self):
        rows = self.record("breakout")
        row = rows[("A", "breakout")]
        self.assertEqual(row[3], decision_center.MOMENTUM_VERSION)  # 與預設條件共用同一筆訊號
        self.assertEqual(row[17:21], (None, None, None, None))
        self.assertIsNone(row[22])
        selection = json.loads(row[21])["breakout"]
        self.assertTrue(selection["selected"])
        self.assertGreater(selection["shares"], 0)
        self.assertFalse(json.loads(rows[("B", "price_action")][21])["breakout"]["selected"])

    def test_record_other_gate_skips_rows_beyond_limit(self):
        # 畫面只列前 limit 檔；其餘沒有入選結果，不能先寫成空值擋住之後完整的紀錄
        rows = self.record("breakout", limit=1)
        self.assertIsNotNone(rows[("A", "breakout")][21])
        self.assertIsNone(rows[("C", "breakout")][21])


if __name__ == "__main__":
    unittest.main()
