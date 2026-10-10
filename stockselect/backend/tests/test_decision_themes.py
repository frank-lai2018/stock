import unittest
from copy import deepcopy
from unittest.mock import Mock, patch

from app import decision_center, decision_themes


def candidate(sid, rs=90):
    strategy = {"key": "breakout", "score": 80, "status": "priority", "stop": 92, "target": None,
                "notes": [], "pattern_prior": None}
    return decision_center._candidate(
        {"stock_id": sid, "name": sid, "industry": "半導體", "close": 100, "raw_close": 100,
         "amt20": 100_000_000, "rs_rating": rs, "trend_template": True}, [strategy], 100)


class DecisionThemesTest(unittest.TestCase):
    def setUp(self):
        self.themes = [{"code": "memory", "name": "記憶體", "layer": 3},
                       {"code": "ccl", "name": "銅箔基板", "layer": 2}]
        self.scan = {"as_of": "2026-10-08", "scanned": 900,
                     "items": [candidate("OTHER", 99), candidate("MEMORY", 95), candidate("BOTH", 90)]}

    def filtered(self, memberships):
        with patch.object(decision_themes.db, "query", side_effect=[self.themes, memberships]):
            return decision_themes.filter_scan(self.scan, ("ccl", "memory"))

    def test_empty_selection_preserves_global_scan_without_membership_queries(self):
        with patch.object(decision_themes.db, "query") as query:
            self.assertIs(decision_themes.filter_scan(self.scan, ()), self.scan)
        query.assert_not_called()

    def test_multi_selection_is_union_with_unique_stocks_and_matching_labels(self):
        scoped = self.filtered([{"code": "memory", "stock_id": "MEMORY"},
                                {"code": "memory", "stock_id": "BOTH"},
                                {"code": "ccl", "stock_id": "BOTH"}])
        self.assertEqual([it["stock_id"] for it in scoped["items"]], ["MEMORY", "BOTH"])
        self.assertEqual(scoped["theme_filter"]["member_count"], 2)
        self.assertEqual({t["code"] for t in scoped["items"][1]["themes"]}, {"memory", "ccl"})

    def test_filter_precedes_ranking_and_limit_so_sector_candidate_can_be_selected(self):
        scoped = self.filtered([{"code": "memory", "stock_id": "MEMORY"}])
        result = decision_center.build_decision_response(
            scoped, [], {"items": [], "stock_ids": set(), "industry_counts": {}},
            market={"above": True}, lot_size=1, max_new_positions=1, limit=1)
        self.assertEqual(result["items"][0]["stock_id"], "MEMORY")
        self.assertTrue(result["items"][0]["selected"])

    def test_scoped_decision_keeps_risk_of_holdings_outside_selected_theme(self):
        scoped = self.filtered([{"code": "memory", "stock_id": "MEMORY"}])
        result = decision_center.build_decision_response(
            scoped, [], {"items": [{"stock_id": "OTHER"}], "stock_ids": {"OTHER"},
                         "industry_counts": {"航運": 1}, "risk_amount": 40_000},
            market={"above": True}, capital=1_000_000, max_total_risk_pct=4, lot_size=1)
        self.assertFalse(result["items"][0]["selected"])
        self.assertEqual(result["summary"]["remaining_risk_budget"], 0)

    def test_filter_does_not_mutate_shared_scan_or_nested_strategy_data(self):
        before = deepcopy(self.scan)
        scoped = self.filtered([{"code": "memory", "stock_id": "MEMORY"}])
        scoped["items"][0]["strategies"][0]["notes"].append("filtered view")
        self.assertEqual(self.scan, before)

    def test_empty_theme_does_not_fall_back_to_all_market_candidates(self):
        scoped = self.filtered([])
        self.assertEqual(scoped["items"], [])
        self.assertEqual(scoped["theme_filter"]["member_count"], 0)

    def test_unknown_or_inactive_theme_is_rejected_instead_of_silently_broadening_scope(self):
        with patch.object(decision_themes.db, "query", return_value=[]):
            with self.assertRaisesRegex(ValueError, "不存在或停用"):
                decision_themes.filter_scan(self.scan, ("missing",))

    def test_codes_are_canonical_and_bounded(self):
        self.assertEqual(decision_themes.parse_codes(" memory, ccl, memory, ,"), ("ccl", "memory"))
        self.assertEqual(decision_themes.parse_codes(None), ())
        with self.assertRaises(ValueError):
            decision_themes.parse_codes(",".join(str(i) for i in range(decision_themes.MAX_THEMES + 1)))

    def test_filtered_selection_never_overwrites_global_history(self):
        recorder = Mock(return_value=7)
        scoped = self.filtered([{"code": "memory", "stock_id": "MEMORY"}])
        self.assertEqual(decision_themes.record_candidates(scoped, {}, "momentum", "trend_template", recorder), 0)
        recorder.assert_not_called()
        self.assertEqual(decision_themes.record_candidates(self.scan, {}, "momentum", "trend_template", recorder), 7)
        recorder.assert_called_once_with(self.scan, {}, "momentum", "trend_template")


if __name__ == "__main__":
    unittest.main()
