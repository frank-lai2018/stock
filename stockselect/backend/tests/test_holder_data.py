import sys
import unittest
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from app import holder_data as h, decision_combinations as c

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
import update_holderdist as ingest


def raw(**extra):
    return {"stock_id": "A", "data_date": date(2026, 10, 2), "level_count": 15, "valid_count": 15,
            "graded_shares": 1_000_020, "graded_holders": 1000, "official_shares": 1_000_010,
            "official_holders": 1000, "adjustment_shares": 10,
            "big400_shares": 600_000, "big1000_shares": 300_000,
            "big400_pct": 60, "big1000_pct": 30, "retail_pct": 10, **extra}


def history(n=9):
    return [h.normalize(raw(data_date=date(2026, 10, 2) - timedelta(days=7 * (n - i - 1)),
                            big1000_pct=30 + i, big400_pct=60 + i, retail_pct=10 - i,
                            graded_holders=1000 - i, official_holders=1000 - i)) for i in range(n)]


class HolderDataTest(unittest.TestCase):
    def test_official_total_keeps_adjustment_and_lot_precision_without_double_count(self):
        row = h.normalize(raw())
        self.assertAlmostEqual(row["total_lots"], 1000.01)
        self.assertEqual(row["holders_total"], 1000)
        self.assertAlmostEqual(row["avg_lots_per_holder"], 1.00001)
        self.assertEqual(row["big400_lots"], 600)
        self.assertEqual(row["big1000_lots"], 300)
        self.assertTrue(row["quality_ok"])
        self.assertEqual(row["shares_difference"], 0)

    def test_old_period_without_official_total_is_explicitly_graded_sum(self):
        row = h.normalize(raw(official_shares=None, official_holders=None, adjustment_shares=None))
        self.assertEqual(row["total_lots"], 1000.02)
        self.assertEqual(row["holders_total"], 1000)
        self.assertIn("分級加總", row["total_source"])
        self.assertTrue(row["quality_ok"])

    def test_missing_invalid_grades_and_inconsistent_totals_fail_closed(self):
        for extra in ({"level_count": 14}, {"valid_count": 14}, {"official_shares": 1_000_020},
                      {"official_holders": 1001}, {"official_shares": 0}):
            with self.subTest(extra=extra):
                self.assertFalse(h.normalize(raw(**extra))["quality_ok"])

    def test_changes_compare_n_plus_one_observations_and_use_percentage_points(self):
        result = h.summary(history())
        self.assertEqual([r["big1000_pct"] for r in result["changes"]], [1, 4, 8])
        self.assertEqual(result["changes"][1]["holders_total"], -4)
        self.assertEqual(result["big1000_up_periods"], 8)

    def test_missing_history_and_gap_produce_reasons_not_zero_or_cross_gap(self):
        result = h.summary(history(4))
        self.assertIsNone(result["changes"][1]["big1000_pct"])
        self.assertIn("至少 5", result["changes"][1]["reason"])
        rows = history(5)
        rows[2]["data_date"] = "2026-09-10"
        self.assertIn("缺週", h.summary(rows)["changes"][1]["reason"])
        self.assertFalse(h.concentration(rows, "2026-10-08")["passed"])

    def test_concentration_is_net_increase_not_four_consecutive_increases(self):
        rows = history(5)
        rows[2]["big1000_pct"] = 36
        self.assertTrue(h.concentration(rows, "2026-10-08")["passed"])
        self.assertEqual(h.summary(rows)["big1000_up_periods"], 1)

    def test_flat_thousand_or_decreasing_400_fails(self):
        for key, value in (("big1000_pct", 30), ("big400_pct", 59)):
            rows = history(5)
            rows[-1][key] = value
            self.assertFalse(h.concentration(rows, "2026-10-08")["passed"])

    def test_invalid_values_missing_periods_future_and_stale_fail(self):
        self.assertFalse(h.concentration([], "2026-10-08")["passed"])
        self.assertFalse(h.concentration(history(4), "2026-10-08")["passed"])
        for cutoff in ("2026-10-01", "2026-10-17"):
            self.assertFalse(h.concentration(history(5), cutoff)["passed"])
        for value in (None, float("nan"), float("inf")):
            rows = history(5)
            rows[-1]["big1000_pct"] = value
            self.assertFalse(h.concentration(rows, "2026-10-08")["passed"])
        rows = history(5)
        rows[2]["quality_ok"] = False
        self.assertFalse(h.concentration(rows, "2026-10-08")["passed"])

    @patch.object(h.db, "query")
    def test_query_bounds_grades_and_known_versions_and_uses_single_price_join(self, query):
        query.return_value = [raw()]
        known = datetime(2026, 10, 9, tzinfo=timezone.utc)
        result = h.series(["A"], as_of="2026-10-08", known_at=known, with_prices=True)
        self.assertEqual(len(result["A"]), 1)
        query.assert_called_once()
        sql, args = query.call_args.args
        self.assertIn("level BETWEEN 12 AND 15", sql)
        self.assertIn("level BETWEEN 1 AND 15", sql)
        self.assertIn("values_seen_at", sql)
        self.assertIn("p.trade_date<=z.data_date", sql)
        self.assertEqual(args["known"], known)

    @patch.object(h, "series")
    def test_attach_scan_uses_explicit_knowledge_cutoff_and_preserves_cache(self, series):
        series.return_value = {"A": history(5)}
        scan = {"as_of": "2026-10-08", "items": [{"stock_id": "A"}]}
        before = deepcopy(scan)
        cutoff = datetime(2026, 10, 9, tzinfo=timezone.utc)
        result = h.attach_scan(scan, cutoff)
        self.assertEqual(scan, before)
        self.assertTrue(result["items"][0]["holder_concentration"]["passed"])
        self.assertEqual(series.call_args.kwargs["known_at"], cutoff)

    def test_combination_exposes_reason_and_does_not_invent_price_cap(self):
        probe = {"holder_concentration": h.concentration(history(5), "2026-10-08")}
        check = c._check(probe, "holder_concentration", 100)
        self.assertTrue(check["passed"])
        self.assertIn("百分點", check["reason"])
        self.assertIsNone(check["max_entry"])
        self.assertFalse(c._check({}, "holder_concentration", 100)["passed"])

    def test_ingestion_keeps_all_17_rows_strips_codes_and_validates_date(self):
        payload = '資料日期,證券代號,持股分級,人數,股數,比例\n' + ''.join(
            f'20261008,A  ,{n},1,{n * 1000},1.00\n' for n in range(1, 19))
        rows, dates = ingest.parse(payload.encode('utf-8-sig'), {"A"})
        self.assertEqual(len(rows), 17)
        self.assertEqual(rows[-1][2], 17)
        self.assertEqual(dates, {"2026-10-08"})
        self.assertIsNone(ingest.ymd_to_iso("20260230"))
        self.assertFalse(ingest.parse(payload.encode(), {"B"})[0])

    def test_snapshot_hash_is_order_independent_and_changes_when_revised(self):
        rows = [("A", "2026-10-08", 1, 10, 100, 1.0), ("A", "2026-10-08", 2, 20, 200, 2.0)]
        seen = datetime.now(timezone.utc)
        first = ingest.observations(rows, seen)
        self.assertEqual(first, ingest.observations(list(reversed(rows)), seen))
        changed = ingest.observations([rows[0], ("A", "2026-10-08", 2, 20, 201, 2.0)], seen)
        self.assertNotEqual(first[0][-1], changed[0][-1])


if __name__ == "__main__":
    unittest.main()
