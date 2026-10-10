import json
from copy import deepcopy
from datetime import date
from math import exp
from tempfile import TemporaryDirectory
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from app import growth_quality as g, growth_research, decision_center as dc, decision_combinations as combos, decision_themes, portfolio_sim
from test_decision_combinations import candidate, weekly, scan, EMPTY_HOLDINGS
from test_execution import START, bar


AS_OF = date(2026, 10, 8)


def history():
    latest = date(2026, 8, 1)
    rows = []
    for i in range(18):
        m = g.shift_month(latest, i - 17)
        base = 100_000 * (1 + (i % 12) / 10)
        revenue = base if i < 12 else rows[i - 12]["revenue"] * 1.1 * exp(.07 * (i - 12))
        rows.append({"stock_id": "A", "revenue_month": m, "revenue": revenue,
                     "available_date": g.shift_month(m, 1).replace(day=10)})
    return rows


def build(source, ranking="rs", **kw):
    return dc.build_decision_response(source, [], EMPTY_HOLDINGS, mode="momentum", gate="weekly",
                                     market={"above": True}, ranking=ranking, **kw)


class GrowthQualityTest(unittest.TestCase):
    def test_same_function_reports_units_and_independent_bounded_score(self):
        out = g.analyze(history(), AS_OF)
        self.assertTrue(out["passed"])
        self.assertAlmostEqual(out["excess_slope_pct"], (exp(.07) - 1) * 100, places=2)
        self.assertEqual(out["linearity_pct"], 100)
        self.assertAlmostEqual(out["acceleration_pp"], out["yoy_3m"] - out["previous_yoy_3m"], places=2)
        self.assertLessEqual(out["score"], 100)
        self.assertEqual(out["usable_from"], "2026-09-11")
        changed = history()
        for r in changed:
            r.update(yoy_pct=-9999, rs_rating=99)
        self.assertEqual(g.analyze(changed, AS_OF)["score"], out["score"])

    def test_seasonal_monthly_growth_does_not_become_yoy_acceleration(self):
        rows = history()
        for i in range(12, 18):
            rows[i]["revenue"] = rows[i - 12]["revenue"] * 1.2
        out = g.analyze(rows, AS_OF)
        self.assertFalse(out["passed"])
        self.assertEqual(out["acceleration_pp"], 0)
        self.assertEqual(out["linearity_pct"], 0)

    def test_estimated_available_date_is_not_used_on_that_date(self):
        rows = history()
        self.assertNotEqual(g.analyze(rows, date(2026, 9, 10)).get("revenue_month"), "2026-08-01")
        self.assertTrue(g.analyze(rows, date(2026, 9, 11))["passed"])

    def test_future_and_missing_availability_never_leak(self):
        rows = history()
        before = g.analyze(rows, AS_OF)
        rows.append({"revenue_month": date(2026, 9, 1), "available_date": date(2026, 10, 10), "revenue": 10**20})
        self.assertEqual(g.analyze(rows, AS_OF), before)
        for availability in (None, "invalid", date(2026, 8, 1)):
            bad = history()
            bad[-1]["available_date"] = availability
            self.assertFalse(g.analyze(bad, AS_OF)["available"])

    def test_missing_month_cannot_be_silently_skipped(self):
        rows = history()
        del rows[3]
        self.assertIsNone(g.analyze(rows, AS_OF)["score"])
        self.assertIn("缺月", g.analyze(rows, AS_OF)["reason"])

    def test_invalid_or_stale_revenue_has_no_score(self):
        for invalid in (None, 0, -1, float("nan"), float("inf")):
            rows = history()
            rows[2]["revenue"] = invalid
            self.assertIsNone(g.analyze(rows, AS_OF)["score"])
        self.assertIsNone(g.analyze(history(), date(2026, 11, 20))["score"])
        self.assertFalse(g.analyze(history(), None)["passed"])

    def test_revenue_deceleration_does_not_pass_even_if_level_is_high(self):
        rows = history()
        for i in range(12, 18):
            rows[i]["revenue"] = rows[i - 12]["revenue"] * (3 - .2 * (i - 12))
        out = g.analyze(rows, AS_OF)
        self.assertGreater(out["yoy_3m"], 10)
        self.assertLess(out["acceleration_pp"], 0)
        self.assertFalse(out["passed"])

    def test_extreme_low_base_is_flagged_and_score_clipped(self):
        rows = history()
        rows[5]["revenue"] = 1
        out = g.analyze(rows, AS_OF)
        self.assertTrue(out["warnings"])
        self.assertLessEqual(out["score"], 100)
        json.dumps(out, allow_nan=False)

    def test_batch_query_bounds_dates_and_months_and_keeps_source_unchanged(self):
        source = scan(candidate("A", [weekly()]))
        original = deepcopy(source)
        with patch.object(g.db, "query", return_value=history()) as query:
            out = g.attach_scan(source)
        self.assertEqual(query.call_count, 1)
        sql, params = query.call_args.args
        self.assertIn("available_date<", sql)
        self.assertIn("rn<=18", sql)
        self.assertEqual(params["as_of"], AS_OF)
        self.assertTrue(out["items"][0]["growth_quality"]["passed"])
        self.assertEqual(source, original)

    def test_quality_filter_before_allocation_and_missing_fails(self):
        sources = scan(candidate("FAIL", [weekly()], rs=99, growth_quality={"passed": False}),
                       candidate("PASS", [weekly()], rs=80, growth_quality={"passed": True, "reason": "成長通過"}),
                       candidate("MISSING", [weekly()], rs=95))
        scoped = combos.filter_scan(sources, ("growth_quality",), gate="weekly")
        out = build(scoped, max_new_positions=1, limit=1)
        self.assertEqual(out["items"][0]["stock_id"], "PASS")
        self.assertEqual(scoped["combination_filter"]["excluded_count"], 2)

    def test_new_ranking_changes_allocation_without_promoting_waiting_signal(self):
        sources = scan(candidate("RS", [weekly()], rs=99, growth_quality={"score": 10}),
                       candidate("GROWTH", [weekly()], rs=80, growth_quality={"score": 80}),
                       candidate("WAIT", [weekly("waiting")], rs=99, growth_quality={"score": 100}))
        self.assertEqual(build(sources, max_new_positions=1)["items"][0]["stock_id"], "RS")
        out = build(sources, "growth_quality", max_new_positions=1, limit=1)
        self.assertEqual(out["items"][0]["stock_id"], "GROWTH")
        self.assertTrue(out["items"][0]["selected"])

    def test_quality_sort_keeps_unknown_last_and_still_respects_market(self):
        sources = scan(candidate("UNKNOWN", [weekly()], rs=99),
                       candidate("ZERO", [weekly()], rs=80, growth_quality={"score": 0}))
        self.assertEqual(build(sources, "growth_quality")["items"][0]["stock_id"], "ZERO")
        out = dc.build_decision_response(sources, [], EMPTY_HOLDINGS, mode="momentum", gate="weekly",
                                        market={"above": False}, ranking="growth_quality")
        self.assertFalse(any(i["selected"] for i in out["items"]))
        for mode, ranking in (("classic", "growth_quality"), ("momentum", "unknown")):
            with self.assertRaises(ValueError):
                dc.build_decision_response(sources, [], EMPTY_HOLDINGS, mode=mode, ranking=ranking)

    def test_scoped_ranking_never_writes_baseline_or_borrows_calibration(self):
        source = {**scan(candidate("A", [weekly()])), "growth_quality_config": {"version": g.VERSION}}
        recorder = Mock()
        self.assertEqual(decision_themes.record_candidates(source, {}, "momentum", "weekly", recorder), 0)
        recorder.assert_not_called()
        out = g.attach(build(source), source, "growth_quality")
        self.assertEqual(out["calibration"], [])
        self.assertIsNone(out["items"][0]["historical_reference"])
        self.assertEqual(out["validation"]["status"], "unvalidated")

    def test_portfolio_uses_the_same_optional_ranking(self):
        def item(sid, rs, score):
            from test_decision_center import candidate as base_candidate
            return {**base_candidate(sid, sid, trend_template=True), "rs_rating": rs, "growth_quality": {"score": score}}
        signals = {START: [item("RS", 99, 10), item("GROWTH", 80, 90)]}
        bars = {sid: [bar(i) for i in range(30)] for sid in ("RS", "GROWTH")}
        dates = [r["trade_date"] for r in bars["RS"]]
        for ranking, chosen in (("rs", "RS"), ("growth_quality", "GROWTH")):
            out = portfolio_sim.run(dates, signals, bars, {START: {"above": True}}, "momentum",
                                    "trend_template", ranking=ranking, max_total_positions=1)
            self.assertEqual(out["trades"][0]["stock_id"], chosen)

    def test_report_rejects_stale_fingerprint(self):
        with TemporaryDirectory() as folder:
            path = Path(folder) / "report.json"
            path.write_text(json.dumps({"fingerprint": "old", "variants": [{}]}), encoding="utf-8")
            with patch.object(growth_research, "RESULT_PATH", path):
                self.assertEqual(growth_research.load_report()["status"], "outdated")
                self.assertEqual(growth_research.load_report()["variants"], [])


if __name__ == "__main__":
    unittest.main()
