import unittest
from datetime import date, timedelta

from app import execution


START = date(2026, 1, 1)


def bar(i, o=100, h=102, l=98, c=100, scale=1, amount=100_000_000):
    return {"trade_date": START + timedelta(days=i), "raw_open": o, "raw_high": h,
            "raw_low": l, "raw_close": c, "open": o * scale, "high": h * scale,
            "low": l * scale, "close": c * scale, "volume": 1000000, "amount": amount}


def signal(**kwargs):
    return {"observed_date": START, "entry": 100, "stop_pct": 0.08, "horizon": 20,
            "cost_pct": 0.006, "slippage": 0, **kwargs}


class ExecutionTest(unittest.TestCase):
    def test_next_open_and_actual_fill_stop(self):
        result = execution.simulate([bar(0, l=80), bar(1, o=105, h=107, l=104, c=106)], signal())
        self.assertEqual(result["entry_date"], START + timedelta(days=1))
        self.assertEqual(result["actual_entry_raw"], 105)
        self.assertAlmostEqual(result["actual_stop"], 96.6)
        self.assertEqual(result["status"], "pending")

    def test_gap_stop_cannot_fill_at_untraded_stop(self):
        result = execution.simulate([bar(0), bar(1), bar(2, o=85, h=87, l=83, c=86)], signal())
        self.assertEqual(result["exit_price_raw"], 85)
        self.assertAlmostEqual(result["net_return"], -0.156)

    def test_dividend_rebuild_keeps_same_price_coordinate(self):
        result = execution.simulate([bar(0, scale=.9), bar(1, scale=.9),
                                     bar(2, o=90, h=91, l=89, c=90)], signal(horizon=2))
        self.assertEqual(result["status"], "timeout")
        self.assertAlmostEqual(result["net_return"], -.006)

    def test_split_does_not_create_loss(self):
        result = execution.simulate([bar(0, scale=.5), bar(1, scale=.5),
                                     bar(2, o=50, h=51, l=49, c=50)], signal(horizon=2))
        self.assertEqual(result["status"], "timeout")
        self.assertAlmostEqual(result["net_return"], -.006)

    def test_locked_limit_entry_is_skipped(self):
        result = execution.simulate([bar(0), bar(1, o=110, h=110, l=110, c=110)], signal())
        self.assertEqual(result["status"], "skipped")

    def test_locked_down_exit_is_deferred(self):
        result = execution.simulate([bar(0), bar(1), bar(2, o=90, h=90, l=90, c=90),
                                     bar(3, o=85, h=87, l=83, c=86)], signal())
        self.assertEqual(result["exit_date"], START + timedelta(days=3))
        self.assertEqual(result["exit_price_raw"], 85)

    def test_missing_market_next_day_does_not_shift_entry(self):
        calendar = [START + timedelta(days=i) for i in range(4)]
        result = execution.simulate([bar(0), bar(2), bar(3)], signal(), calendar)
        self.assertEqual(result["status"], "skipped")

    def test_entry_cap_includes_slippage(self):
        result = execution.simulate([bar(0), bar(1, o=103)], signal(max_entry=103, slippage=.001))
        self.assertEqual(result["status"], "skipped")

    def test_market_calendar_counts_suspension_for_expiry(self):
        calendar = [START + timedelta(days=i) for i in range(5)]
        result = execution.simulate([bar(0), bar(1), bar(3), bar(4)], signal(horizon=2), calendar)
        self.assertEqual(result["status"], "timeout")
        self.assertEqual(result["exit_date"], START + timedelta(days=3))
        self.assertEqual(result["exit_timing"], "open")

    def test_ma_exit_uses_next_open_and_no_future_bar(self):
        bars = [bar(i) for i in range(-55, 1)] + [bar(1, o=100, h=101, l=98, c=99),
                                                  bar(2, o=97, h=99, l=96, c=98)]
        pending = execution.simulate(bars, signal(exit_ma=50, horizon=60), as_of=START + timedelta(days=1))
        self.assertEqual(pending["status"], "pending")
        result = execution.simulate(bars, signal(exit_ma=50, horizon=60))
        self.assertEqual(result["status"], "trend")
        self.assertEqual(result["exit_price_raw"], 97)

    def test_ma_cross_waits_until_price_has_been_above_ma(self):
        # 觀察日收 100 在 10 日線下；第 2 天收 112 站上，第 3 天收 105 跌破，第 4 天開盤出
        bars = [bar(i, o=110, h=111, l=109, c=110) for i in range(-10, 0)] + [
            bar(0, o=101, h=102, l=99, c=100), bar(1, o=100, h=102, l=99, c=101), bar(2, o=102, h=113, l=101, c=112),
            bar(3, o=111, h=112, l=104, c=105), bar(4, o=104, h=106, l=103, c=105)]
        spec = dict(stop_pct=None, stop=90, exit_ma=10)
        crossed = execution.simulate(bars, signal(exit_ma_cross=True, **spec))
        self.assertEqual((crossed["status"], crossed["exit_date"], crossed["exit_price_raw"]),
                         ("trend", START + timedelta(days=4), 104))
        level = execution.simulate(bars, signal(**spec))           # 原本的寫法：收在線下隔天就出
        self.assertEqual((level["exit_date"], level["exit_price_raw"]), (START + timedelta(days=2), 102))

    def test_same_bar_target_and_stop_uses_loss(self):
        result = execution.simulate([bar(0), bar(1, h=120, l=90)],
                                     signal(stop_pct=None, stop=92, target=115))
        self.assertEqual(result["status"], "loss")

    def test_no_next_day_is_waiting(self):
        self.assertEqual(execution.simulate([bar(0)], signal())["execution_state"], "waiting")


def buy_stop(**kwargs):
    return signal(**{"stop_pct": None, "entry_type": "buy_stop", "trigger": 103, "stop": 97, **kwargs})


class BuyStopTest(unittest.TestCase):
    def test_fills_at_trigger_when_open_is_below(self):
        result = execution.simulate([bar(0), bar(1, o=100, h=106, l=99, c=105)], buy_stop())
        self.assertEqual((result["actual_entry"], result["actual_entry_raw"], result["actual_stop"]), (103, 103, 97))
        self.assertEqual(result["execution_version"], execution.BUY_STOP_VERSION)

    def test_gap_above_trigger_fills_at_open(self):
        result = execution.simulate([bar(0), bar(1, o=104, h=106, l=103.5, c=105)], buy_stop())
        self.assertEqual(result["actual_entry"], 104)

    def test_not_reached_expires(self):
        result = execution.simulate([bar(0), bar(1, h=102.9), bar(2, h=110, c=108)], buy_stop())
        self.assertEqual(result["status"], "skipped")
        self.assertIn("沒有漲到觸發價", result["reason"])

    def test_gap_cap_and_risk_cap(self):
        gapped = execution.simulate([bar(0), bar(1, o=105.1, h=106, l=105, c=105)], buy_stop(max_gap_pct=0.02))
        self.assertIn("放棄", gapped["reason"])
        risky = execution.simulate([bar(0), bar(1, o=100, h=106, l=99, c=105)], buy_stop(stop=96, max_risk_pct=0.06))
        self.assertIn("超過上限 6%", risky["reason"])

    def test_same_day_stop_counts_as_loss_after_entry(self):
        # 開盤 96 已在停損下，盤中漲過 103 再跌回：當作 103 買進、97 停損
        result = execution.simulate([bar(0), bar(1, o=96, h=104, l=95, c=100)], buy_stop())
        self.assertEqual((result["status"], result["exit_price"], result["exit_timing"]), ("loss", 97, "intraday"))
        self.assertAlmostEqual(result["net_return"], 97 / 103 - 1 - 0.006)

    def test_locked_limit_up_cannot_fill(self):
        result = execution.simulate([bar(0), bar(1, o=110, h=110, l=110, c=110)], buy_stop())
        self.assertEqual(result["status"], "skipped")

    def test_nearest_order_keeps_position_when_low_came_first(self):
        # 開盤 96 離最低 95 較近：當作先跌到 95、再漲過 103 買進，收 100 沒跌回停損 97
        bars = [bar(0), bar(1, o=96, h=104, l=95, c=100), bar(2, o=100, h=101, l=98, c=100)]
        kept = execution.simulate(bars, buy_stop(intraday_order="nearest"))
        self.assertEqual((kept["status"], kept["actual_entry"]), ("pending", 103))
        # 開盤 100 離最高 104 較近：當作先漲過 103 買進、再跌到 95，停損
        early_high = [bar(0), bar(1, o=100, h=104, l=95, c=100)]
        self.assertEqual(execution.simulate(early_high, buy_stop(intraday_order="nearest"))["status"], "loss")

    def test_dividend_on_entry_day_keeps_observed_coordinate(self):
        # 進場日除息（原始價 ×0.9），還原後同一座標：最高 106 仍觸發 103
        result = execution.simulate([bar(0, scale=.9), bar(1, o=90, h=95.4, l=89.1, c=94.5)], buy_stop())
        self.assertAlmostEqual(result["actual_entry"], 103)
        self.assertAlmostEqual(result["actual_entry_raw"], 103 * .9)


if __name__ == "__main__":
    unittest.main()
