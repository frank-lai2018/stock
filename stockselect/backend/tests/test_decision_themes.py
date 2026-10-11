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


class DisplayThemesTest(unittest.TestCase):
    def test_market_groups_are_attached_without_selecting_a_theme_filter(self):
        response = {"items": [{"stock_id": sid} for sid in ("3026", "2383", "2337", "5425")]}
        members = [
            {"stock_id": "3026", "code": "passive", "name": "被動元件（MLCC／電阻／電感／石英）", "layer": 3},
            {"stock_id": "3026", "code": "tpex:J000:J600", "name": "電容器", "layer": 2},
            {"stock_id": "2383", "code": "pcb", "name": "AI PCB／CCL／載板", "layer": 3},
            {"stock_id": "2383", "code": "tpex:L000:L630", "name": "銅箔基板", "layer": 2},
            {"stock_id": "2337", "code": "memory", "name": "記憶體（DRAM／NAND／HBM）", "layer": 3},
            {"stock_id": "5425", "code": "power_semi", "name": "功率半導體（MOSFET／二極體）", "layer": 3},
        ]
        with patch.object(decision_themes.db, "query", return_value=members) as query:
            decision_themes.attach_display_themes(response)
        self.assertEqual([[t["name"] for t in row["display_themes"]] for row in response["items"]],
                         [["被動元件"], ["CCL", "PCB"], ["記憶體"], ["功率元件"]])
        self.assertEqual(response["items"][1]["display_themes"][0]["full_name"], "銅箔基板")
        query.assert_called_once()
        self.assertEqual(query.call_args.args[1]["ids"], ["2337", "2383", "3026", "5425"])

    def test_product_fallback_has_short_unique_names_and_missing_members_stay_unclassified(self):
        response = {"items": [{"stock_id": "PRODUCT"}, {"stock_id": "UNKNOWN", "industry": "電子零組件業"}]}
        members = [
            {"stock_id": "PRODUCT", "code": "one", "name": "IC設計 > 記憶體IC（範例）", "layer": 2},
            {"stock_id": "PRODUCT", "code": "two", "name": "記憶體IC", "layer": 2},
            {"stock_id": "PRODUCT", "code": "three", "name": "其他", "layer": 2},
        ]
        with patch.object(decision_themes.db, "query", return_value=members):
            decision_themes.attach_display_themes(response)
        self.assertEqual([t["name"] for t in response["items"][0]["display_themes"]], ["記憶體IC"])
        self.assertEqual(response["items"][1]["display_themes"], [])

    def test_display_does_not_change_selection_risk_classification_or_filter_matches(self):
        scan = {"items": [candidate("3026")], "as_of": "2026-10-08"}
        before_scan = deepcopy(scan)
        response = decision_center.build_decision_response(
            scan, [], {"items": [], "stock_ids": set(), "industry_counts": {}},
            market={"above": True}, lot_size=1)
        response["items"][0]["themes"] = [{"code": "selected", "name": "篩選命中族群"}]
        before = deepcopy(response)
        with patch.object(decision_themes.db, "query", return_value=[
            {"stock_id": "3026", "code": "passive", "name": "被動元件", "layer": 3}
        ]):
            decision_themes.attach_display_themes(response)
        response["items"][0].pop("display_themes")
        self.assertEqual(response, before)
        self.assertEqual(scan, before_scan)

    def test_excluded_candidates_receive_groups_in_same_batch_with_independent_labels(self):
        response = {"items": [{"stock_id": "A"}],
                    "combination_filter": {"excluded_items": [{"stock_id": "A"}, {"stock_id": "B"}]}}
        with patch.object(decision_themes.db, "query", return_value=[
            {"stock_id": "A", "code": "memory", "name": "記憶體", "layer": 3}
        ]) as query:
            decision_themes.attach_display_themes(response)
        self.assertEqual(query.call_args.args[1]["ids"], ["A", "B"])
        excluded = response["combination_filter"]["excluded_items"]
        self.assertEqual(excluded[0]["display_themes"], response["items"][0]["display_themes"])
        excluded[0]["display_themes"][0]["name"] = "changed"
        self.assertEqual(response["items"][0]["display_themes"][0]["name"], "記憶體")
        self.assertEqual(excluded[1]["display_themes"], [])

    def test_empty_response_does_not_query_memberships(self):
        with patch.object(decision_themes.db, "query") as query:
            decision_themes.attach_display_themes({"items": []})
        query.assert_not_called()


if __name__ == "__main__":
    unittest.main()
