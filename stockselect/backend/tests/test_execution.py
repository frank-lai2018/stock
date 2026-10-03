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

    def test_same_bar_target_and_stop_uses_loss(self):
        result = execution.simulate([bar(0), bar(1, h=120, l=90)],
                                     signal(stop_pct=None, stop=92, target=115))
        self.assertEqual(result["status"], "loss")

    def test_no_next_day_is_waiting(self):
        self.assertEqual(execution.simulate([bar(0)], signal())["execution_state"], "waiting")


if __name__ == "__main__":
    unittest.main()
