import unittest
from copy import deepcopy
from unittest.mock import Mock

from app import decision_center, decision_combinations as combinations, decision_themes


EMPTY_HOLDINGS = {"items": [], "stock_ids": set(), "industry_counts": {}}


def strategy(key, status="priority", **extra):
    return {"key": key, "label": key, "score": 80 if status == "priority" else 70,
            "status": status, "stop": 92, "target": None, "notes": [], "blockers": [],
            "pattern_prior": None, **extra}


def weekly(status="priority", stage="entry", age=0, **extra):
    return strategy("weekly", status, stage=stage, weekly={"weeks_since_breakout": age}, **{"max_entry": 105, **extra})


def candidate(sid, strategies, trend=True, rs=90, **extra):
    return {**decision_center._candidate(
        {"stock_id": sid, "name": sid, "industry": "半導體", "close": 100, "raw_close": 100,
         "amt20": 100_000_000, "rs_rating": rs, "trend_template": trend}, strategies, 100), **extra}


def scan(*items):
    return {"as_of": "2026-10-08", "scanned": 900, "items": list(items)}


def build(scoped, gate="weekly", **kwargs):
    response = decision_center.build_decision_response(
        scoped, [], kwargs.pop("holdings", EMPTY_HOLDINGS), gate=gate, market=kwargs.pop("market", {"above": True}),
        lot_size=1, **kwargs)
    return combinations.attach(response, scoped)


class DecisionCombinationsTest(unittest.TestCase):
    def test_holder_filter_precedes_allocation_and_missing_data_does_not_pass(self):
        source = scan(candidate("FAIL", [weekly()], rs=99,
                                holder_concentration={"passed": False, "reason": "千張持有比例未增加"}),
                      candidate("MATCH", [weekly()], rs=80,
                                holder_concentration={"passed": True, "reason": "近四期淨增加"}),
                      candidate("MISSING", [weekly()]))
        scoped = combinations.filter_scan(source, ("holder_concentration",), gate="weekly")
        result = build(scoped, max_new_positions=1, limit=1)
        self.assertEqual(result["items"][0]["stock_id"], "MATCH")
        self.assertTrue(result["items"][0]["selected"])
        self.assertEqual(result["combination_filter"]["excluded_count"], 2)
        self.assertEqual(result["validation"]["status"], "unvalidated")

    def test_holder_confirmation_cannot_turn_waiting_weekly_into_buy(self):
        source = scan(candidate("WAIT", [weekly("waiting", "pullback")],
                                holder_concentration={"passed": True, "reason": "淨增加"}))
        result = build(combinations.filter_scan(source, ("holder_concentration",), gate="weekly"))
        self.assertEqual(result["items"][0]["state"], "waiting")
        self.assertFalse(result["items"][0]["selected"])
        self.assertFalse(result["items"][0]["combination_ready"])

    def test_holder_with_trend_respects_and_or_and_keeps_failed_reasons(self):
        source = scan(candidate("TREND_ONLY", [weekly()],
                                holder_concentration={"passed": False, "reason": "籌碼分散"}))
        codes = ("holder_concentration", "trend_template")
        strict = combinations.filter_scan(source, codes, "all", "weekly")
        loose = combinations.filter_scan(source, codes, "any", "weekly")
        self.assertFalse(strict["items"])
        self.assertEqual(loose["items"][0]["combination_satisfied_by"], ["trend_template"])
        self.assertEqual(loose["items"][0]["combination_checks"][1]["reason"], "籌碼分散")

    def test_empty_conditions_preserve_baseline_and_shared_scan(self):
        source = scan(candidate("A", [weekly()]))
        self.assertIs(combinations.filter_scan(source, ()), source)
        baseline = decision_center.build_decision_response(source, [], EMPTY_HOLDINGS, gate="weekly", market={"above": True})
        self.assertEqual(combinations.attach(deepcopy(baseline), source), baseline)

    def test_codes_validate_normalize_and_reject_unknown_logic_or_classic(self):
        self.assertEqual(combinations.parse_conditions(" weekly_structure,breakout,breakout "), ("breakout", "weekly_structure"))
        for value, logic, mode, gate in [("typo", "all", "momentum", "weekly"),
                                        ("breakout", "typo", "momentum", "weekly"),
                                        ("weekly", "all", "classic", "breakout"),
                                        ("weekly", "all", "momentum", "weekly"),
                                        ("weekly_structure", "all", "momentum", "weekly")]:
            with self.subTest(value=value, logic=logic, mode=mode, gate=gate), self.assertRaises(ValueError):
                combinations.parse_conditions(value, logic, mode, gate)

    def test_all_requires_every_confirmation_and_any_is_union(self):
        source = scan(candidate("BOTH", [weekly(), strategy("consolidation", max_entry=105)]),
                      candidate("TREND", [weekly()]),
                      candidate("NONE", [weekly()], trend=False))
        codes = ("trend_template", "consolidation")
        all_scan = combinations.filter_scan(source, codes, "all", "weekly")
        any_scan = combinations.filter_scan(source, codes, "any", "weekly")
        self.assertEqual([x["stock_id"] for x in all_scan["items"]], ["BOTH"])
        self.assertEqual([x["stock_id"] for x in any_scan["items"]], ["BOTH", "TREND"])
        self.assertEqual(all_scan["combination_filter"]["excluded_count"], 2)

    def test_observation_is_not_an_executable_confirmation(self):
        source = scan(candidate("A", [weekly(), strategy("breakout", "watch")]))
        scoped = combinations.filter_scan(source, ("breakout",), gate="weekly")
        self.assertFalse(scoped["items"])
        checks = scoped["combination_filter"]["excluded_items"][0]["combination_checks"]
        self.assertFalse(checks[1]["passed"])
        self.assertIn("等待／觀察", checks[1]["reason"])

    def test_blocked_priority_is_not_a_confirmation(self):
        source = scan(candidate("A", [weekly(), strategy("breakout", blockers=["量能不足"])]))
        scoped = combinations.filter_scan(source, ("breakout",), gate="weekly")
        self.assertFalse(scoped["items"])

    def test_consolidation_confirmation_still_requires_daily_trend(self):
        source = scan(candidate("A", [weekly(), strategy("consolidation")], trend=False))
        scoped = combinations.filter_scan(source, ("consolidation",), gate="weekly")
        self.assertFalse(scoped["items"])
        self.assertIn("趨勢模板", scoped["combination_filter"]["excluded_items"][0]["combination_checks"][1]["reason"])

    def test_secondary_ready_signal_cannot_bypass_waiting_primary(self):
        source = scan(candidate("A", [weekly("waiting", "pullback"), strategy("breakout")]))
        scoped = combinations.filter_scan(source, ("breakout",), gate="weekly")
        result = build(scoped)
        self.assertEqual(len(result["items"]), 1)
        row = result["items"][0]
        self.assertEqual(row["state"], "waiting")
        self.assertFalse(row["selected"])
        self.assertFalse(row["combination_ready"])
        self.assertEqual([s["key"] for s in row["strategies"]], ["weekly"])

    def test_confirmed_weekly_structure_can_confirm_a_daily_buy_stop(self):
        source = scan(candidate("A", [weekly("waiting", "pullback"),
                                      strategy("reclaim", entry_type="buy_stop", trigger_raw=101)]))
        scoped = combinations.filter_scan(source, ("weekly_structure",), gate="reclaim")
        result = build(scoped, gate="reclaim")
        self.assertTrue(result["items"][0]["selected"])
        self.assertEqual(result["items"][0]["position_plan"]["entry"], 101)
        self.assertEqual(result["items"][0]["position_plan"]["entry_type"], "buy_stop")

    def test_weekly_structure_rejects_unclosed_near_failed_expired_and_missing_age(self):
        for status, stage, age in [("watch", "pending_week", 0), ("watch", "near", 0),
                                   ("skip", "failed", 0), ("waiting", "pullback", 4),
                                   ("waiting", "pullback", None), ("waiting", "pullback", -1)]:
            with self.subTest(status=status, stage=stage, age=age):
                source = scan(candidate("A", [weekly(status, stage, age), strategy("reclaim")]))
                scoped = combinations.filter_scan(source, ("weekly_structure",), gate="reclaim")
                self.assertFalse(scoped["items"])

    def test_weekly_buy_point_is_stricter_than_confirmed_structure(self):
        source = scan(candidate("A", [weekly("waiting", "pullback"), strategy("breakout")]))
        structure = combinations.filter_scan(source, ("weekly_structure",), gate="breakout")
        buy_point = combinations.filter_scan(source, ("weekly",), gate="breakout")
        self.assertEqual(len(structure["items"]), 1)
        self.assertEqual(len(buy_point["items"]), 0)

    def test_filter_precedes_rank_and_limit_and_does_not_consume_cash_for_rejected_stock(self):
        source = scan(candidate("HIGH_RS", [weekly()], trend=False, rs=99),
                      candidate("MATCH", [weekly()], rs=80))
        scoped = combinations.filter_scan(source, ("trend_template",), gate="weekly")
        result = build(scoped, max_new_positions=1, limit=1)
        self.assertEqual(result["items"][0]["stock_id"], "MATCH")
        self.assertTrue(result["items"][0]["selected"])

    def test_holdings_outside_combination_still_exhaust_total_risk(self):
        source = scan(candidate("A", [weekly()]))
        scoped = combinations.filter_scan(source, ("trend_template",), gate="weekly")
        result = build(scoped, holdings={"items": [{"stock_id": "OUTSIDE"}], "stock_ids": {"OUTSIDE"},
                                        "industry_counts": {}, "risk_amount": 40_000})
        self.assertFalse(result["items"][0]["selected"])
        self.assertEqual(result["summary"]["remaining_risk_budget"], 0)

    def test_market_and_data_health_remain_required(self):
        source = scan(candidate("A", [weekly()]))
        scoped = combinations.filter_scan(source, ("trend_template",), gate="weekly")
        self.assertFalse(build(scoped, market={"above": False})["items"][0]["selected"])
        scoped["data_health"] = {"healthy": False}
        self.assertFalse(build(scoped)["items"][0]["selected"])

    def test_global_cache_and_nested_metadata_are_not_mutated(self):
        source = scan(candidate("A", [weekly()]))
        original = deepcopy(source)
        scoped = combinations.filter_scan(source, ("trend_template",), gate="weekly")
        scoped["items"][0]["strategies"][0]["notes"].append("test")
        build(scoped)
        self.assertEqual(source, original)

    def test_confirmation_cannot_add_consensus_points_or_take_over_the_primary_entry(self):
        source = scan(candidate("A", [weekly(), strategy("breakout", score=99)]))
        result = build(combinations.filter_scan(source, ("breakout",), gate="weekly"))
        row = result["items"][0]
        self.assertEqual(row["lead_strategy"], "weekly")
        self.assertEqual(row["consensus_count"], 1)
        self.assertEqual(row["consensus_bonus"], 0)
        self.assertEqual(row["decision_score"], 80)

    def test_secondary_price_cap_uses_raw_prices_and_is_carried_into_the_plan(self):
        source = scan(candidate("A", [weekly(max_entry=210), strategy("breakout")],
                                close=200, raw_close=100, price_factor=2))
        result = build(combinations.filter_scan(source, ("weekly",), gate="breakout"), gate="breakout")
        plan = result["items"][0]["position_plan"]
        self.assertEqual(plan["max_entry"], 105)
        self.assertIn("105.00", plan["entry_rule"])
        self.assertEqual(result["items"][0]["entry_conditions"][-1]["trigger_high"], 105)

    def test_buy_stop_above_secondary_price_cap_is_rejected_before_sizing(self):
        source = scan(candidate("A", [weekly(max_entry=105),
                                      strategy("reclaim", entry_type="buy_stop", trigger_raw=106)]))
        scoped = combinations.filter_scan(source, ("weekly",), gate="reclaim")
        self.assertFalse(scoped["items"])
        self.assertIn("追價上限", scoped["combination_filter"]["excluded_items"][0]["combination_checks"][1]["reason"])

    def test_secondary_cap_includes_slippage_and_missing_cap_fails_closed(self):
        for cap in (100, None, float("nan")):
            with self.subTest(cap=cap):
                source = scan(candidate("A", [weekly(max_entry=cap), strategy("breakout")]))
                scoped = combinations.filter_scan(source, ("weekly",), gate="breakout")
                self.assertFalse(scoped["items"])

    def test_all_uses_stricter_cap_and_any_uses_a_valid_single_confirmation(self):
        source = scan(candidate("A", [weekly(max_entry=106), strategy("consolidation", max_entry=103),
                                      strategy("breakout")]))
        codes = ("weekly", "consolidation")
        all_result = build(combinations.filter_scan(source, codes, "all", "breakout"), gate="breakout")
        any_result = build(combinations.filter_scan(source, codes, "any", "breakout"), gate="breakout")
        self.assertEqual(all_result["items"][0]["position_plan"]["max_entry"], 103)
        self.assertEqual(any_result["items"][0]["position_plan"]["max_entry"], 106)
        self.assertEqual(any_result["items"][0]["combination_satisfied_by"], ["weekly"])

    def test_primary_cap_cannot_be_loosened_by_secondary_confirmation(self):
        source = scan(candidate("A", [weekly(max_entry=103), strategy("consolidation", max_entry=106)]))
        result = build(combinations.filter_scan(source, ("consolidation",), gate="weekly"))
        self.assertEqual(result["items"][0]["position_plan"]["max_entry"], 103)

    def test_baseline_history_is_never_overwritten_even_without_theme_filter(self):
        scoped = combinations.filter_scan(scan(candidate("A", [weekly()])), ("trend_template",), gate="weekly")
        recorder = Mock()
        self.assertEqual(decision_themes.record_candidates(scoped, {}, "momentum", "weekly", recorder), 0)
        recorder.assert_not_called()

    def test_combination_does_not_borrow_single_strategy_calibration(self):
        source = scan(candidate("A", [weekly()]))
        scoped = combinations.filter_scan(source, ("trend_template",), gate="weekly")
        calibration = [{"strategy": "weekly", "score_bucket": "80-89", "n": 100, "avg_r": 1.0}]
        baseline = decision_center.build_decision_response(scoped, calibration, EMPTY_HOLDINGS, gate="weekly", market={"above": True})
        self.assertIsNotNone(baseline["items"][0]["historical_reference"])
        result = combinations.attach(baseline, scoped)
        self.assertEqual(result["calibration"], [])
        self.assertIsNone(result["items"][0]["historical_reference"])
        self.assertIsNone(result["items"][0]["strategies"][0]["score_calibration"])
        self.assertEqual(result["validation"]["status"], "unvalidated")

    def test_exclusion_diagnostics_are_bounded_and_preserve_exact_totals(self):
        source = scan(*(candidate(str(i), [weekly()], trend=False) for i in range(105)))
        config = combinations.filter_scan(source, ("trend_template",), gate="weekly")["combination_filter"]
        self.assertEqual(config["candidate_count"], 105)
        self.assertEqual(config["excluded_count"], 105)
        self.assertEqual(len(config["excluded_items"]), 100)

    def test_theme_scope_survives_and_stocks_without_primary_strategy_are_not_candidates(self):
        source = scan(candidate("WEEKLY", [weekly()]), candidate("OTHER", [strategy("breakout")]))
        source["theme_filter"] = {"codes": ["memory"], "member_count": 34}
        scoped = combinations.filter_scan(source, ("trend_template",), gate="weekly")
        self.assertEqual(scoped["theme_filter"], source["theme_filter"])
        self.assertEqual(scoped["combination_filter"]["candidate_count"], 1)


if __name__ == "__main__":
    unittest.main()
