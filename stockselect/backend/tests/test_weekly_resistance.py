import contextlib
import io
import json
import tempfile
from copy import deepcopy
from datetime import date, timedelta
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

import capture_daily_decisions as capture
import notify_daily_decision as cli
from app import decision_center as dc, decision_digest as dg, entry_conditions, execution
from app import lin_bojin as lin, line_notify as ln, weekly_resistance as wr
from test_lin_bojin import EMPTY
from test_line_notify import AUTH


def weekly_rows(touches=(4, 12, 24), *, opened=93.5, high=94.5, low=93, close=94, volume=1_000_000):
    start, rows = date(2025, 1, 6), []
    for week in range(32):
        for weekday in range(5):
            price = 80 + week * .4 + weekday * .02
            rows.append({"trade_date": start + timedelta(weeks=week, days=weekday),
                         "open": price - .1, "high": 100 if week in touches and weekday == 2 else price + .6,
                         "low": price - .5, "close": price, "raw_close": price,
                         "volume": 1_000_000, "amount": price * 1_000_000})
    rows.append({"trade_date": start + timedelta(weeks=32), "open": opened, "high": high,
                 "low": low, "close": close, "raw_close": close, "volume": volume,
                 "amount": volume * close})
    return rows


def source(rows, *, eps=1, rs=90, sid="WEEK", amt20=100_000_000):
    strategy = lin.analyze(rows, rs)
    return {"as_of": str(rows[-1]["trade_date"]), "scanned": 1, "data_health": {"healthy": True},
            "items": [dc._candidate({"stock_id": sid, "name": sid, "industry": "電子", "eps": eps,
                                     "rs_rating": rs, "amt20": amt20, "close": rows[-1]["close"],
                                     "raw_close": rows[-1]["raw_close"],
                                     "price_factor": rows[-1]["close"] / rows[-1]["raw_close"]},
                                    [strategy], rows[-1]["close"])]}


class WeeklyResistanceTest(TestCase):
    def test_repeated_completed_week_highs_are_used_instead_of_week_closes(self):
        bands = wr.bands(weekly_rows())
        band = next(b for b in bands if b["high"] == 100)
        self.assertEqual(band["touch_count"], 3)
        self.assertEqual([t["week"] for t in band["touches"]], ["2025-02-03", "2025-03-31", "2025-06-23"])
        self.assertEqual((band["low"], band["high"]), (100, 100))

    def test_one_touch_nearby_touches_short_history_and_wide_price_band_are_not_enough(self):
        cases = [weekly_rows((4,)), weekly_rows((22, 24)), weekly_rows()[:110]]
        wide = weekly_rows((4, 12))
        wide[12 * 5 + 2]["high"] = 103
        cases.append(wide)
        for rows in cases:
            with self.subTest(length=len(rows)):
                self.assertFalse(any(b["high"] == 100 for b in wr.bands(rows)))

    def test_open_week_and_newly_unconfirmed_pivot_cannot_raise_pressure(self):
        rows = weekly_rows()
        rows[-1]["high"] = 150
        self.assertEqual([b["high"] for b in wr.bands(rows)], [100])
        # 最新已完成週高點缺少另一個已完成右鄰週，不算新增測試。
        rows = weekly_rows((4,))
        rows[31 * 5 + 2]["high"] = 100
        self.assertFalse(wr.bands(rows))
        rows = weekly_rows()
        friday = deepcopy(rows[-1])
        friday.update(trade_date=friday["trade_date"] + timedelta(days=4), high=150)
        self.assertEqual([b["high"] for b in wr.bands(rows + [friday])], [100])

    def test_band_upper_bound_is_a_retained_touch_after_spacing_deduplication(self):
        rows = weekly_rows((4, 6, 24))
        rows[4 * 5 + 2]["high"] = 98
        rows[24 * 5 + 2]["high"] = 99
        band = next(b for b in wr.bands(rows) if b["high"] == 100)
        self.assertEqual(max(t["price"] for t in band["touches"]), band["high"])
        self.assertEqual(band["touch_count"], 2)

    def test_previously_broken_weekly_resistance_is_not_a_new_pressure_band(self):
        rows = weekly_rows()
        rows[28 * 5 + 4].update(open=99, high=102, low=98, close=101, raw_close=101)
        self.assertFalse(any(b["high"] == 100 for b in wr.bands(rows)))

    def test_near_and_volume_challenge_are_observations_with_zero_selected_positions(self):
        for rows, kind in [(weekly_rows(), "near"),
                           (weekly_rows(opened=95, high=101, low=94.5, close=99, volume=2_000_000), "challenge")]:
            with self.subTest(kind=kind):
                scanned = source(rows)
                result = dg.build_response(scanned, EMPTY, {"above": True}, gate=lin.KEY)
                self.assertEqual(result["summary"]["selected"], 0)
                self.assertEqual(result["summary"]["ready"], 0)
                self.assertEqual(result["lin_bojin_observation_count"], 1)
                self.assertEqual(result["items"][0]["position_plan"]["suggested_shares"], 0)
                self.assertFalse(result["items"][0]["position_plan"]["valid"])
                observed = result["items"][0]["lin_bojin"]["observation"]
                self.assertEqual(observed["type"], kind)
                text = dg.render_digest({"response": result, "holding_checks": []})
                self.assertIn("通過買點條件：0檔", text)
                self.assertIn("提前觀察：1檔｜尚未確認買點", text)
                self.assertIn("WEEK WEEK", text)
                self.assertNotIn("入選試算：", text)
                self.assertIn("不是買進訊號", text)

    def test_observation_limits_do_not_accept_low_rs_falling_ma_or_overextended_price(self):
        rows = weekly_rows()
        self.assertIsNone(wr.observation(rows, 69, wr.bands(rows)))
        self.assertIsNone(wr.observation(rows, None, wr.bands(rows)))
        for close in (91.9, 105.1):
            data = weekly_rows(opened=close - .2, high=close + .3, low=close - .4, close=close)
            self.assertIsNone(wr.observation(data, 90, wr.bands(data)))
        down = weekly_rows()
        for i in range(len(down) - 25, len(down)):
            p = 99 - (i - (len(down) - 25)) * .1
            down[i].update(open=p - .1, high=p + .2, low=p - .2, close=p, raw_close=p)
        self.assertIsNone(wr.observation(down, 90, wr.bands(down)))

    def test_observation_eps_and_liquidity_required_and_confirmed_stocks_not_duplicated(self):
        for eps in (0, -1, None, float("nan"), float("inf")):
            with self.subTest(eps=eps):
                result = dg.build_response(source(weekly_rows(), eps=eps), EMPTY, {"above": True}, gate=lin.KEY)
                self.assertEqual(lin.observation_items(result), [])
                self.assertNotIn("WEEK WEEK", dg.render_digest({"response": result, "holding_checks": []}))
        result = dg.build_response(source(weekly_rows(), amt20=19_999_999), EMPTY, {"above": True}, gate=lin.KEY)
        self.assertEqual(lin.observation_items(result), [])
        result = dg.build_response(source(weekly_rows()), EMPTY, {"above": True}, gate=lin.KEY)
        result["items"][0]["state"] = "ready"
        self.assertEqual(lin.observation_items(result), [])

    def test_weekly_pressure_buy_point_keeps_all_daily_confirmation_and_risk_limits(self):
        rows = weekly_rows(opened=99, high=103.5, low=98.5, close=103, volume=2_000_000)
        setup = lin.analyze(rows, 90)
        self.assertEqual((setup["status"], setup["stage"], setup["pattern"]), ("priority", "first", wr.PATTERN))
        self.assertEqual(setup["lin_bojin"]["pressure_band"]["high"], 100)
        self.assertEqual(setup["max_entry"], 105)
        result = dg.build_response(source(rows), EMPTY, {"above": True}, gate=lin.KEY)
        self.assertEqual(result["summary"]["selected"], 1)
        self.assertEqual((result["settings"]["horizon"], result["settings"]["lot_size"]), (60, 1))
        spec = dc.execution_spec(result["items"][0], setup, "trend_hold")
        self.assertEqual(spec["max_entry"], 105)
        self.assertEqual(spec["stop_pct"], .08)
        for changes in [dict(open=102.8), dict(high=105), dict(close=106, high=107), dict(volume=1_000_000)]:
            data = deepcopy(rows)
            data[-1].update(changes)
            if data[-1]["close"] != data[-1]["raw_close"]:
                data[-1]["raw_close"] = data[-1]["close"]
            with self.subTest(changes=changes):
                trial = lin.analyze(data, 90)
                self.assertTrue(trial is None or trial["status"] != "priority")

    def test_frozen_weekly_anchor_and_corporate_action_coordinate_conversion(self):
        rows = weekly_rows(opened=99, high=103.5, low=98.5, close=103, volume=2_000_000)
        initial = lin.analyze(rows, 90)
        following = deepcopy(rows[-1])
        following.update(trade_date=following["trade_date"] + timedelta(days=1), open=102, high=103, low=100, close=101, raw_close=101, volume=1_000_000)
        later = lin.analyze(rows + [following], 90)
        self.assertEqual(later["lin_bojin"]["pressure_band"], initial["lin_bojin"]["pressure_band"])
        scaled = deepcopy(rows)
        for row in scaled:
            for key in ("open", "high", "low", "close"):
                row[key] *= 2
        scaled_setup = lin.analyze(scaled, 90)
        self.assertEqual(scaled_setup["lin_bojin"]["pressure_band"]["high"], 100)
        self.assertEqual(scaled_setup["lin_bojin"]["neckline"], 100)
        self.assertEqual(scaled_setup["max_entry"], 210)
        scaled_observation = deepcopy(weekly_rows())
        for row in scaled_observation:
            for key in ("open", "high", "low", "close"):
                row[key] *= 2
        self.assertEqual(lin.analyze(scaled_observation, 90)["lin_bojin"]["observation"]["pressure_band"]["high"], 100)

    def test_prefix_replay_is_unchanged_by_future_bars_and_input_is_not_mutated(self):
        before = weekly_rows()
        original = deepcopy(before)
        expected = lin.analyze(before, 90)
        future = deepcopy(before[-1])
        future.update(trade_date=future["trade_date"] + timedelta(days=1), open=125, high=131, low=124, close=130, raw_close=130)
        complete = before + [future]
        self.assertEqual(lin.analyze(complete[:-1], 90), expected)
        self.assertEqual(before, original)

    def test_same_day_second_signal_does_not_relabel_a_failed_breakout_as_near_pressure(self):
        rows = weekly_rows(opened=99, high=103.5, low=98.5, close=103, volume=2_000_000)
        following = deepcopy(rows[-1])
        following.update(trade_date=following["trade_date"] + timedelta(days=1), open=98, high=99, low=95, close=96, raw_close=96)
        self.assertIsNone(wr.observation(rows + [following], 90, wr.bands(rows + [following])))

    def test_observation_does_not_use_trade_entry_instructions_or_block_confirmed_stock_allocation(self):
        watch = source(weekly_rows())
        ready = source(weekly_rows(opened=99, high=103.5, low=98.5, close=103, volume=2_000_000), sid="BUY")
        scanned = {**watch, "scanned": 2, "items": watch["items"] + ready["items"]}
        result = dg.build_response(scanned, EMPTY, {"above": True}, gate=lin.KEY)
        self.assertEqual([x["stock_id"] for x in result["items"] if x["selected"]], ["BUY"])
        watch_item = next(x for x in result["items"] if x["stock_id"] == "WEEK")
        self.assertFalse(watch_item["selected"])
        condition = watch_item["entry_conditions"][0]
        self.assertEqual(condition["deadline"], "等待確認")
        self.assertTrue(any("尚未確認買點、不配置部位" in line for line in condition["lines"]))

    def test_one_snapshot_carries_observation_without_additional_line_message(self):
        scanned = source(weekly_rows())
        scanned["as_of"] = str(dg.today())
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "lin.json"
            with patch.object(dc, "scan_market", return_value=scanned), \
                 patch.object(dc, "settle_pending", return_value=0), \
                 patch.object(dc, "current_holdings", return_value=EMPTY), \
                 patch.object(dc, "market_regime", return_value={"above": True, "ma_days": 60}), \
                 patch.object(dc, "calibration_rows", return_value=[]), \
                 patch.object(dc, "record_candidates", return_value=0), \
                 patch.object(dg, "load_holding_checks", return_value=[]), \
                 patch.object(dg, "enrich_selected"), contextlib.redirect_stdout(io.StringIO()):
                capture.main(["--lin-line-snapshot", str(path)])
            snapshot = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(snapshot["response"]["lin_bojin_observation_count"], 1)
            with patch.object(ln, "credentials", return_value=AUTH), \
                 patch.object(ln, "send_once", return_value={"message": "accepted"}) as send, \
                 contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(cli.main(["--strategy", lin.KEY, "--snapshot", str(path)]), 0)
            send.assert_called_once()
            self.assertIn("提前觀察：1檔", send.call_args.args[1])
