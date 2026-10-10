import unittest
from datetime import date

from app import entry_conditions as ec


def pa(signal_date="2026-10-01", score=70, status="waiting"):
    return {"key": "price_action", "label": "裸 K", "status": status, "score": score,
            "entry": 100.0, "stop": 96.0, "signal_date": signal_date, "pattern_name": "多頭吞噬"}


def weekly(status="waiting", stage="pullback", blockers=("離壓力線 +8.0%，等拉回 100.00～105.00 再轉強",)):
    # 9/28 教師節休市，突破週從週二 9/29 開始
    return {"key": "weekly", "label": "週線突破（實驗）", "status": status, "stage": stage,
            "pivot": 100.0, "max_entry": 105.0, "stop": 97.0, "blockers": list(blockers),
            "weekly": {"week_start": date(2026, 9, 29), "week_end": date(2026, 10, 2)}}


CAL = [date(2026, 9, 29), date(2026, 9, 30), date(2026, 10, 1), date(2026, 10, 2)]


def buy_stop(key="reclaim", gap=None):
    return {"key": key, "label": "支撐轉強（實驗）", "status": "priority", "entry_type": "buy_stop",
            "trigger": 310.0, "trigger_raw": 310.0, "max_gap_pct": gap, "signal_date": "2026-10-02"}


def breakout(status="priority"):
    # 停損／目標是還原價座標
    return {"key": "breakout", "label": "型態突破", "status": status, "score": 78.2, "pattern_name": "V型反轉",
            "signal_date": "2026-10-02", "entry": 150.0, "stop": 135.0, "target": 180.0}


class EntryConditionsTest(unittest.TestCase):
    def conditions(self, *strategies, as_of="2026-10-02", gate=None, trend_template=True, mode=None, **item):
        response = {"as_of": as_of, "gate": gate, "mode": mode, "items": [
            {"stock_id": "A", "trend_template": trend_template, "strategies": list(strategies), **item}]}
        return ec.attach(response, lookback=5, expiry=5, calendar=CAL)["items"][0]["entry_conditions"]

    def test_gate_requirement_is_spelled_out(self):
        self.assertIn("另需趨勢模板成立", self.conditions(pa(), gate="trend_template", trend_template=False)[0]["lines"][-1])
        self.assertNotIn("另需", self.conditions(pa(), gate="trend_template")[0]["lines"][-1])
        self.assertIn("觸發了也不會入選", self.conditions(pa(), gate="breakout")[0]["lines"][-1])

    def test_price_action_waiting_counts_days_left(self):
        c = self.conditions(pa())[0]                                   # 10/1 訊號、10/2 是第 1 天
        self.assertEqual((c["trigger_low"], c["invalid_below"], c["deadline"]), (100.0, 96.0, "剩 3 個交易日"))
        self.assertEqual(c["score_after"], 77.0)
        self.assertIn("盤中突破 100.00", c["lines"][0])
        self.assertNotIn("未達", c["lines"][-1])

    def test_price_action_last_day_and_low_score(self):
        c = self.conditions(pa(signal_date="2026-09-26", score=66))[0]  # 已過 4 個交易日：lookback 5 的最後一天
        self.assertEqual(c["deadline"], "已到期限")
        self.assertIn("未達 75", c["lines"][-1])

    def test_weekly_waiting_deadline_is_thursday_of_fourth_week(self):
        c = self.conditions(weekly())[0]
        self.assertEqual(c["deadline"], "2026-10-29 前")
        self.assertEqual((c["trigger_low"], c["trigger_high"], c["invalid_below"]), (100.0, 105.0, 97.0))
        self.assertIn("目前：離壓力線 +8.0%", c["lines"][-1])

    def test_buy_stop_ready_signal_lists_next_day_trigger(self):
        c = self.conditions(buy_stop())[0]
        self.assertEqual((c["strategy"], c["trigger_low"], c["trigger_high"], c["deadline"]),
                         ("reclaim", 310.0, None, "只限下一個交易日"))
        self.assertIn("盤中漲到 310.00（10/2 最高價上一檔）才買", c["lines"][0])
        self.assertIn("第 20 個交易日收盤出場", c["lines"][-1])
        w = self.conditions(buy_stop("w_bottom", gap=0.02))[0]
        self.assertAlmostEqual(w["trigger_high"], 316.2)
        self.assertIn("開盤就高於 316.20（觸發價 +2%）就放棄", w["lines"][1])

    def test_breakout_ready_buys_next_open(self):
        c = self.conditions(breakout(), gate="breakout", mode="momentum", raw_close=100.0, price_factor=1.5)[0]
        self.assertEqual((c["strategy"], c["trigger_low"], c["trigger_high"], c["deadline"]),
                         ("breakout", None, None, "下一個交易日開盤"))
        self.assertEqual(c["lines"][0], "下一個交易日開盤買（10/2 V型反轉收盤帶量突破）；開盤價不設上限")
        self.assertEqual(c["lines"][1], "買進後停損＝成交價下 8%（若以收盤價 100.00 成交，約 92.00），第 20 個交易日收盤出場")
        held = self.conditions(breakout(), gate="breakout", mode="trend_hold", raw_close=100.0)[0]
        self.assertIn("收盤跌破 50 日線隔日開盤出、最長 60 個交易日", held["lines"][1])

    def test_breakout_classic_uses_pattern_stop_and_target(self):
        c = self.conditions(breakout(), mode="classic", raw_close=100.0, price_factor=1.5)[0]
        self.assertEqual(c["lines"][1], "停損 90.00（頸線下 1 ATR）、目標 120.00（量測滿足價）")

    def test_breakout_gate_requirement_and_watch(self):
        c = self.conditions(breakout(), gate="trend_template", trend_template=False, mode="momentum", raw_close=100.0)[0]
        self.assertIn("另需趨勢模板成立", c["lines"][-1])
        self.assertEqual(len(self.conditions(breakout(), gate="breakout", mode="momentum", raw_close=100.0)[0]["lines"]), 2)
        self.assertEqual(self.conditions(breakout(status="watch"), gate="breakout", mode="momentum", raw_close=100.0), [])

    def test_only_waiting_signals_and_weekly_watch(self):
        conds = self.conditions(pa(status="priority"),
                                weekly(status="watch", stage="near", blockers=["接近週線壓力 100.00，還差 2.0%"]))
        self.assertEqual([c["strategy"] for c in conds], ["weekly"])
        self.assertIsNone(conds[0]["invalid_below"])
        self.assertIn("週收盤站上 100.00", conds[0]["lines"][0])


if __name__ == "__main__":
    unittest.main()
