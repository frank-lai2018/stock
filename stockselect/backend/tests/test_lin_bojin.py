from copy import deepcopy
from datetime import date, timedelta
from unittest import TestCase
from unittest.mock import patch

from app import decision_center as dc, decision_combinations as combos, entry_conditions, execution, lin_bojin as lin


def base(gap=False):
    # 真實偵測器可辨識的雙重底：50、65 日為兩底，57 日反彈高點形成 100 元頸線。
    anchors = [(0, 94), (20, 96), (40, 93), (50, 82), (57, 99), (65, 83), (79, 99)]
    rows = []
    for i in range(80):
        left, right = next((a, b) for a, b in zip(anchors, anchors[1:]) if a[0] <= i <= b[0])
        close = left[1] + (right[1] - left[1]) * (i - left[0]) / (right[0] - left[0])
        rows.append(bar(i, close - .4, close + 1, close - 1, close))
    rows.append(bar(80, 101.5 if gap else 99, 103.5 if gap else 104,
                    101 if gap else 98, 103, volume=2_000_000))
    return rows


def bar(i, opened, high, low, close, volume=1_000_000):
    return {"trade_date": date(2026, 1, 1) + timedelta(days=i), "open": opened, "high": high,
            "low": low, "close": close, "raw_close": close, "volume": volume, "amount": volume * close}


def retest():
    return base() + [bar(81, 102, 102.5, 100, 101), bar(82, 101, 101.3, 99.8, 100.8),
                     bar(83, 100.8, 103.5, 100.5, 103, volume=1_400_000)]


EMPTY = {"items": [], "stock_ids": set(), "industry_counts": {}}


def scan(strategy, factor=1):
    return {"as_of": "2026-03-25", "scanned": 1, "data_health": {"healthy": True}, "items": [
        dc._candidate({"stock_id": "TEST", "name": "測試", "industry": "電子", "close": 103 * factor,
                       "raw_close": 103, "price_factor": factor, "rs_rating": 90,
                       "amt20": 100_000_000, "trend_template": False}, [strategy], 103 * factor)]}


class LinBojinTest(TestCase):
    def test_first_breakout_with_real_pattern_detector(self):
        result = lin.analyze(base(), 90)
        self.assertEqual(result["stage"], "first")
        self.assertEqual(result["status"], "priority")
        self.assertEqual(result["lin_bojin"]["neckline"], 100)
        self.assertEqual(result["max_entry"], 105)
        self.assertIsNone(result["pattern_prior"])

    def test_full_gap_and_intraday_gap_fill_are_distinguished(self):
        result = lin.analyze(base(gap=True), 90)
        self.assertEqual(result["stage"], "gap")
        self.assertEqual(result["status"], "priority")
        rows = base(gap=True)
        rows[-1].update(open=100, low=99.5)
        self.assertEqual(lin.analyze(rows, 90)["stage"], "first")

    def test_retest_uses_original_neckline_and_requires_new_strength(self):
        result = lin.analyze(retest(), 90)
        self.assertEqual(result["stage"], "retest")
        self.assertEqual(result["status"], "priority")
        self.assertEqual(result["lin_bojin"]["age_days"], 3)
        self.assertEqual(result["lin_bojin"]["breakout_date"], base()[-1]["trade_date"].isoformat())
        self.assertEqual(result["lin_bojin"]["neckline"], 100)
        waiting = lin.analyze(retest()[:-1], 90)
        self.assertEqual(waiting["status"], "waiting")

    def test_close_below_neckline_failure_discards_old_breakout(self):
        rows = retest()
        rows[82] = bar(82, 98, 99, 96, 97)
        self.assertIsNone(lin.analyze(rows, 90))

    def test_near_neckline_is_waiting_and_cannot_be_selected(self):
        strategy = lin.analyze(base()[:-1], 90)
        self.assertEqual(strategy["stage"], "near")
        self.assertEqual(strategy["status"], "waiting")
        result = dc.build_decision_response(scan(strategy), [], EMPTY, gate=lin.KEY, market={"above": True})
        self.assertFalse(result["items"][0]["selected"])

    def test_overextended_low_rs_and_missing_rs_are_not_executable(self):
        rows = base()
        rows[-1] = bar(80, 99, 109, 98, 108, volume=2_000_000)
        for data, rs in [(rows, 90), (base(), 69), (base(), None), (base(), float("nan"))]:
            with self.subTest(rs=rs):
                result = lin.analyze(data, rs)
                self.assertNotEqual(result["status"], "priority")
                self.assertTrue(result["blockers"])

    def test_thin_body_or_weak_close_cannot_anchor_later_retest(self):
        rows = retest()
        rows[80].update(open=102.7)  # 突破日薄實體，即使有放量也不是確認長紅。
        self.assertIsNone(lin.analyze(rows, 90))

    def test_bad_prices_dates_or_volume_fail_closed(self):
        for key, value in [("close", float("nan")), ("low", -1), ("high", 100),
                           ("volume", None), ("raw_close", 0), ("trade_date", None)]:
            rows = base()
            rows[-1][key] = value
            with self.subTest(key=key):
                self.assertIsNone(lin.analyze(rows, 90))
        rows = base()
        rows[-1]["trade_date"] = rows[-2]["trade_date"]
        self.assertIsNone(lin.analyze(rows, 90))
        self.assertIsNone(lin.analyze(base()[:69], 90))

    def test_replay_never_passes_later_bars_to_earlier_detection(self):
        rows = retest()
        original = deepcopy(rows)
        detected_dates = []
        real = lin._confirmed

        def record(prefix):
            detected_dates.append(prefix[-1]["trade_date"])
            self.assertTrue(all(b["trade_date"] <= prefix[-1]["trade_date"] for b in prefix))
            return real(prefix)

        with patch.object(lin, "_confirmed", side_effect=record):
            lin.analyze(rows, 90)
        self.assertIn(rows[80]["trade_date"], detected_dates)
        self.assertEqual(rows, original)

    def test_adjusted_coordinates_plan_and_execution_cap_agree(self):
        rows = base()
        for row in rows:
            for key in ("open", "high", "low", "close"):
                row[key] *= 2
        strategy = lin.analyze(rows, 90)
        self.assertEqual(strategy["lin_bojin"]["neckline"], 100)
        result = dc.build_decision_response(scan(strategy, 2), [], EMPTY, gate=lin.KEY,
                                           mode="trend_hold", market={"above": True})
        row = result["items"][0]
        self.assertTrue(row["selected"])
        self.assertEqual(row["position_plan"]["max_entry"], 105)
        spec = dc.execution_spec(row, strategy, "trend_hold")
        self.assertEqual(spec["max_entry"], 105)
        self.assertEqual((spec["horizon"], spec["exit_ma"]), (60, 50))
        self.assertEqual(spec["strategy_version"], lin.VERSION)

    def test_other_gates_and_classic_cannot_use_this_experiment(self):
        source = scan(lin.analyze(base(), 90))
        for mode, gate in [("classic", "trend_template"), ("momentum", "breakout"), ("momentum", "trend_template")]:
            result = dc.build_decision_response(source, [], EMPTY, mode=mode, gate=gate, market={"above": True})
            self.assertEqual(result["items"], [])

    def test_market_data_health_and_account_risk_still_apply(self):
        source = scan(lin.analyze(base(), 90))
        for market, holdings, healthy in [({"above": False}, EMPTY, True),
                                          ({"above": True}, EMPTY, False),
                                          ({"above": True}, {**EMPTY, "risk_amount": 40_000}, True)]:
            source["data_health"]["healthy"] = healthy
            result = dc.build_decision_response(source, [], holdings, gate=lin.KEY, market=market)
            self.assertFalse(result["items"][0]["selected"])

    def test_combination_cap_and_no_borrowed_calibration(self):
        source = scan(lin.analyze(base(), 90))
        source["items"][0]["strategies"].append({"key": "breakout", "label": "型態突破", "score": 90,
                                                  "status": "priority", "pattern_prior": None})
        scoped = combos.filter_scan(source, (lin.KEY,), gate="breakout")
        result = dc.build_decision_response(scoped, [], EMPTY, gate="breakout", market={"above": True})
        combos.attach(result, scoped)
        lin.attach(result, scoped)
        self.assertEqual(result["items"][0]["position_plan"]["max_entry"], 105)
        self.assertEqual(result["lin_bojin_config"]["version"], lin.VERSION)
        self.assertIsNone(result["items"][0]["historical_reference"])

    def test_entry_instructions_show_cap_and_waiting_reasons(self):
        source = scan(lin.analyze(base(), 90))
        result = dc.build_decision_response(source, [], EMPTY, gate=lin.KEY, mode="trend_hold", market={"above": True})
        entry_conditions.attach(result, calendar=[])
        condition = result["items"][0]["entry_conditions"][0]
        self.assertEqual(condition["trigger_high"], 105)
        self.assertIn("60", "；".join(condition["lines"]))
        self.assertIn("作廢", "；".join(condition["lines"]))

    def test_next_open_above_neckline_cap_is_skipped(self):
        rows = base()
        strategy = lin.analyze(rows, 90)
        spec = dc.execution_spec(scan(strategy)["items"][0], strategy, "trend_hold")
        spec["observed_date"] = rows[-1]["trade_date"]
        for row in rows:
            row.update(raw_open=row["open"], raw_high=row["high"], raw_low=row["low"])
        following = bar(81, 106, 108, 104, 107)
        following.update(raw_open=106, raw_high=108, raw_low=104)
        result = execution.simulate(rows + [following], spec)
        self.assertEqual(result["status"], "skipped")
        self.assertIn("追價上限", result["reason"])
