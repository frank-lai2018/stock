import unittest
from datetime import date, timedelta

from app import decision_center, weekly_breakout as wb

MONDAY = date(2024, 1, 1)                    # 2024-01-01 是週一


def bar(d, close, high=None, low=None, open_=None):
    return {"trade_date": d, "open": open_ or close, "high": high or close * 1.01,
            "low": low or close * 0.99, "close": close, "volume": 1000}


def weekly_path(closes, start=MONDAY):
    """每週一到週五各一根，收盤都等於當週收盤（週收盤＝closes[i]）。"""
    return [bar(start + timedelta(weeks=w, days=d), c) for w, c in enumerate(closes) for d in range(5)]


def base_then(last):
    """40 週從 50 漲到 100（第 40 週收 100＝壓力線），之後 14 週在 92～98 整理，最後一週收 last。"""
    rise = [50 + 50 * i / 40 for i in range(40)]
    base = [96, 94, 92, 93, 95, 97, 96, 94, 93, 95, 96, 97, 98, 97]
    return rise + [100] + base + [last]


def day_after(bars, n):
    return bars[-1]["trade_date"] + timedelta(days=n)


class WeeklyBarsTest(unittest.TestCase):
    def test_week_is_complete_only_on_friday_or_after(self):
        bars = weekly_path([10, 11])
        weeks, current = wb.weekly_bars(bars)
        self.assertEqual((len(weeks), current), (2, None))           # 最後一天是週五
        thursday = bars[:-1]
        weeks, current = wb.weekly_bars(thursday)
        self.assertEqual(len(weeks), 1)
        self.assertEqual(current["days"], 4)                        # 週五放假，週四還不算收完
        weeks, current = wb.weekly_bars(thursday, as_of=day_after(thursday, 4))
        self.assertEqual((len(weeks), current), (2, None))          # 已進入下一週，就算收完


class WeeklyBreakoutTest(unittest.TestCase):
    def test_breakout_week_close_in_buy_zone_is_entry(self):
        r = wb.analyze(weekly_path(base_then(103)))
        self.assertEqual((r["status"], r["stage"]), ("priority", "entry"))
        self.assertEqual(r["pivot"], 100)
        self.assertAlmostEqual(r["max_entry"], 105)
        self.assertEqual(r["weekly"]["base_weeks"], 15)
        self.assertLess(r["stop"], 100)                              # 壓力線 − 1 ATR

    def test_extended_breakout_waits_for_pullback(self):
        r = wb.analyze(weekly_path(base_then(112)))
        self.assertEqual((r["status"], r["stage"]), ("waiting", "pullback"))
        self.assertIn("等拉回 100.00～105.00", r["blockers"][0])

    def test_pullback_then_turn_up_is_entry(self):
        bars = weekly_path(base_then(112))
        mon = day_after(bars, 3)
        bars += [bar(mon, 108, 109, 107), bar(mon + timedelta(days=1), 102.5, 104, 101),
                 bar(mon + timedelta(days=2), 104.5, 105, 102)]      # 週三收盤 > 週二高點
        r = wb.analyze(bars)
        self.assertEqual((r["status"], r["stage"]), ("priority", "entry"))
        r = wb.analyze(bars[:-1])                                   # 週二還在跌，等轉強
        self.assertEqual(r["status"], "waiting")
        self.assertIn("等收盤高於前一日高點", r["blockers"][0])

    def test_close_below_pivot_minus_atr_fails(self):
        bars = weekly_path(base_then(103))
        bars.append(bar(day_after(bars, 3), 96))
        r = wb.analyze(bars)
        self.assertEqual((r["status"], r["stage"]), ("skip", "failed"))

    def test_near_pivot_is_watch_and_mid_week_break_waits_for_close(self):
        bars = weekly_path(base_then(97))
        r = wb.analyze(bars)
        self.assertEqual((r["status"], r["stage"]), ("watch", "near"))
        mon = day_after(bars, 3)
        r = wb.analyze(bars + [bar(mon, 99), bar(mon + timedelta(days=1), 101)])
        self.assertEqual((r["status"], r["stage"]), ("watch", "pending_week"))

    def test_no_base_or_no_trend_is_skip(self):
        r = wb.analyze(weekly_path([50 + i for i in range(60)]))     # 一路創新高
        self.assertEqual(r["status"], "skip")
        self.assertTrue(any("一路創新高" in x for x in r["blockers"]))
        r = wb.analyze(weekly_path([100] * 56 + [96] * 4))           # 30 週線走平、收盤在線下
        self.assertEqual(r["status"], "skip")
        self.assertTrue(any("週線趨勢未成立" in x for x in r["blockers"]))

    def test_not_enough_weeks(self):
        self.assertIsNone(wb.analyze(weekly_path([10] * 40)))


class WeeklyGateTest(unittest.TestCase):
    def scan(self):
        from test_decision_center import candidate
        weekly = candidate("W", "電子", 80)
        weekly["strategies"] = [{**weekly["strategies"][0], "key": "weekly", "target": None, "max_entry": 105}]
        return {"as_of": "2026-10-02", "scanned": 2, "items": [candidate("B", "航運", 85, trend_template=True), weekly]}

    def test_weekly_gate_uses_only_weekly_strategy(self):
        empty = {"items": [], "stock_ids": set(), "industry_counts": {}}
        result = decision_center.build_decision_response(
            self.scan(), [], empty, mode="momentum", gate="weekly", market={"above": True})
        self.assertEqual([(x["stock_id"], x["selected"]) for x in result["items"]], [("W", True)])
        self.assertEqual(result["items"][0]["position_plan"]["max_entry"], 105)
        other = decision_center.build_decision_response(
            self.scan(), [], empty, mode="momentum", gate="trend_template", market={"above": True})
        self.assertEqual([x["stock_id"] for x in other["items"]], ["B"])

    def test_execution_spec_keeps_chase_limit(self):
        item = self.scan()["items"][1]
        spec = decision_center.execution_spec(item, item["strategies"][0], "momentum")
        self.assertEqual(spec["max_entry"], 105)
        self.assertTrue(decision_center.strategy_used("weekly", "weekly"))
        self.assertFalse(decision_center.strategy_used("weekly", "trend_template"))
        self.assertFalse(decision_center.strategy_used("weekly", "weekly", momentum=False))


if __name__ == "__main__":
    unittest.main()
