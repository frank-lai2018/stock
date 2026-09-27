import unittest
from datetime import date, timedelta

from app import price_action


def bar(i, o, h, l, c):
    return {"trade_date": date(2026, 1, 1) + timedelta(days=i),
            "open": o, "high": h, "low": l, "close": c}


class PriceActionTest(unittest.TestCase):
    def test_growth_streak_stops_at_first_non_growth_period(self):
        self.assertEqual(price_action.growth_streak([130, 120, 100, 105, 90]), 2)
        self.assertEqual(price_action.growth_streak([130, None, 100]), 0)
        self.assertEqual(price_action.growth_streak([100]), 0)

    def test_inside_bar_and_nr7(self):
        bars = [bar(i, 100, 105 + i * .1, 95 - i * .1, 101) for i in range(6)]
        bars.append(bar(6, 100, 102, 98, 101))
        keys = {x["key"] for x in price_action.detect_setups(bars)}
        self.assertIn("inside_bar", keys)
        self.assertIn("nr7", keys)

    def test_signal_waits_then_triggers_on_later_bar(self):
        bars = [bar(i, 100, 102, 98, 100 + (i % 3) * .2) for i in range(30)]
        bars.append(bar(30, 101, 102, 97, 101.8))
        waiting = price_action.evaluate_signal(bars, 30, "bull", expiry=5)
        self.assertEqual(waiting["status"], "waiting")
        bars.append(bar(31, 102.5, 104, 101, 103))
        triggered = price_action.evaluate_signal(bars, 30, "bull", expiry=5)
        self.assertEqual(triggered["status"], "triggered")
        self.assertEqual(triggered["fill"], 102.5)  # 跳空高於觸發價，以開盤估計成交

    def test_stop_before_trigger_is_invalid(self):
        bars = [bar(i, 100, 102, 98, 100) for i in range(30)]
        bars.append(bar(30, 100, 103, 97, 102))
        bars.append(bar(31, 101, 102, 96, 97))
        result = price_action.evaluate_signal(bars, 30, "bull", expiry=5)
        self.assertEqual(result["status"], "invalid")


if __name__ == "__main__":
    unittest.main()
