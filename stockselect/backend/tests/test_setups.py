"""支撐轉強、W 底測右底：用兩份個案文件的真實日 K（未還原價，張／百萬元）重現文件的數字。"""
import unittest
from datetime import date

from app import execution, support_reclaim as sr, w_bottom as wb

# 6672 騰輝電子-KY：9/17 放量黑 K 回測 285 支撐、9/18 紅 K 收復黑 K 半身
RECLAIM = [
    ("08-13", 269.5, 272.5, 260, 264.5, 4550, 1211), ("08-14", 264.5, 284.5, 258.5, 270, 6294, 1715),
    ("08-17", 271, 288, 266, 283, 5150, 1423), ("08-18", 283, 295, 277.5, 288, 8355, 2401),
    ("08-19", 280, 293, 280, 284, 7856, 2243), ("08-20", 289, 292, 280, 287.5, 5170, 1478),
    ("08-21", 285, 292, 264, 267.5, 6973, 1942), ("08-24", 267, 271, 258, 259, 2894, 760),
    ("08-25", 257, 272, 255, 270.5, 2557, 676), ("08-26", 272, 281.5, 270, 276.5, 3222, 893),
    ("08-27", 279, 285.5, 275, 281.5, 3523, 990), ("08-28", 283.5, 294.5, 282, 289.5, 4031, 1167),
    ("08-31", 285, 291.5, 273, 275, 3571, 1003), ("09-01", 276.5, 282.5, 274, 278.5, 2152, 601),
    ("09-02", 275.5, 283, 271, 272, 1965, 543), ("09-03", 274, 276, 261.5, 263, 1884, 505),
    ("09-04", 289, 289, 278, 289, 5927, 1707), ("09-07", 293, 309.5, 287, 299.5, 14526, 4300),
    ("09-08", 300.5, 317.5, 290, 294, 7324, 2187), ("09-09", 299.5, 314, 293.5, 299.5, 9019, 2747),
    ("09-10", 296, 306, 294, 299, 3575, 1074), ("09-11", 291.5, 303, 291, 292, 3675, 1085),
    ("09-14", 288.5, 297.5, 285, 294.5, 2549, 745), ("09-15", 294.5, 303.5, 289.5, 291, 4204, 1246),
    ("09-16", 295.5, 306, 290, 304, 5010, 1497), ("09-17", 306, 315, 285.5, 293.5, 10176, 3033),
    ("09-18", 299.5, 309.5, 297, 302, 6002, 1813), ("09-21", 306.5, 316, 304, 310, 4860, 1510),
    ("09-22", 315, 315, 300, 302, 4972, 1525), ("09-23", 302, 310, 298, 307, 3276, 997),
    ("09-24", 315, 337.5, 315, 337.5, 15309, 5098),
]
# 2492 華新科：9/14 左底 291、9/21 頸線 326、9/30 縮量測右底 298.5
W_BOTTOM = [
    ("08-31", 308, 316.5, 282.5, 295, 58056, 17294), ("09-01", 295.5, 317, 294, 302.5, 35972, 10995),
    ("09-02", 296, 302.5, 291, 293, 17530, 5197), ("09-03", 295.5, 296, 270, 271.5, 22304, 6290),
    ("09-04", 282.5, 298.5, 282.5, 298.5, 27375, 8065), ("09-07", 310, 328, 309.5, 323, 47400, 15194),
    ("09-08", 313, 320, 309.5, 318.5, 25418, 7992), ("09-09", 319, 323, 312, 319.5, 26073, 8271),
    ("09-10", 316.5, 344, 303, 334, 42694, 13699), ("09-11", 320.5, 340, 310, 311.5, 57397, 18658),
    ("09-14", 303, 309.5, 291, 305.5, 28422, 8526), ("09-15", 305.5, 315.5, 300, 300, 21610, 6629),
    ("09-16", 304, 307.5, 295.5, 307.5, 15103, 4566), ("09-17", 309, 314.5, 295, 297, 21642, 6563),
    ("09-18", 305, 311, 300, 309.5, 15512, 4756), ("09-21", 311.5, 326, 307.5, 308.5, 22904, 7219),
    ("09-22", 318.5, 324, 310.5, 311, 21974, 6936), ("09-23", 314.5, 322.5, 304, 306, 19018, 5916),
    ("09-24", 305, 315.5, 302, 311.5, 15596, 4852), ("09-29", 313, 321, 299, 302, 31201, 9595),
    ("09-30", 306, 309, 298.5, 299.5, 18146, 5460), ("10-01", 302.5, 329, 302.5, 329, 51670, 16462),
    ("10-02", 335, 361.5, 329, 361.5, 55788, 19545),
]


def bars(rows, until=None, **change):
    out = []
    for d, o, h, l, c, lots, millions in rows:
        b = {"trade_date": date(2026, int(d[:2]), int(d[3:])), "open": o, "high": h, "low": l, "close": c,
             "raw_open": o, "raw_high": h, "raw_low": l, "raw_close": c,
             "volume": lots * 1000, "amount": millions * 1_000_000}
        b.update(change.get(d, {}))
        out.append(b)
        if d == until:
            break
    return out


class TickTest(unittest.TestCase):
    def test_tick_table(self):
        self.assertEqual([execution.tick_up(p) for p in (309.5, 309, 99.9, 9.99, 49.95, 1000)],
                         [310, 309.5, 100, 10, 50, 1005])
        self.assertEqual([execution.tick_down(p) for p in (298.5, 100, 10, 500, 1000)],
                         [298, 99.9, 9.99, 499.5, 999])
        self.assertEqual(execution.to_tick(309.4999), 309.5)


class SupportReclaimTest(unittest.TestCase):
    def test_6672_on_0918_matches_document(self):
        r = sr.analyze(bars(RECLAIM, "09-18"))
        p = r["plan"]
        # 決策中心格式：成立就可執行（隔日盤中過 310 才買）；文件停損 297 只寫在說明
        self.assertEqual((r["trigger_raw"], r["stop"], r["status"], r["signal_state"]), (310, 297, "priority", "triggered"))
        self.assertEqual((r["entry_type"], r["score"], r["max_gap_pct"]), ("buy_stop", 80, None))
        self.assertAlmostEqual(p["risk_pct"], 13 / 310)
        self.assertEqual((p["a_mid"], p["support"], p["box_high"], p["box_low"]), (299.75, 285, 317.5, 285))
        self.assertAlmostEqual(p["gain20"], 302 / 267.5 - 1)
        self.assertAlmostEqual(p["test_volume"], 10176 / 3802.6)
        self.assertIn("317.50 是近期壓力", " ".join(r["notes"]))

    def test_only_0918_in_the_window_and_close_confirmation_on_0921(self):
        s = sr.series(bars(RECLAIM))
        hits = [t for t in range(len(s["close"])) if sr.setup(s, t)]
        self.assertEqual([str(s["date"][t]) for t in hits], ["2026-09-18"])
        self.assertTrue(sr.confirmed(s, hits[0]))           # 9/21 低 304 > 297、收 310 > 309.5

    def test_each_condition_is_required(self):
        cases = {"09-18": {"close": 299.5},                  # 沒收回 A 實體中點 299.75
                 "09-17": {"volume": 5_000_000},             # A 沒放量
                 "09-14": {"low": 280}}                      # 支撐變 280，A 低點 285.5 離支撐 +2%
        for d, change in cases.items():
            with self.subTest(d=d):
                self.assertIsNone(sr.analyze(bars(RECLAIM, "09-18", **{d: change})))
        self.assertIsNone(sr.analyze(bars(RECLAIM, "09-18")[-24:]))     # 不足 25 根
        self.assertIsNone(sr.analyze(bars(RECLAIM, "09-18"), {**sr.PARAMS, "min_gain20": 0.15}))

    def test_zero_price_bar_is_missing_not_a_crash(self):
        zero = {"open": 0, "high": 0, "low": 0, "close": 0, "raw_close": 0}
        self.assertIsNone(sr.analyze(bars(RECLAIM, "09-18", **{"09-14": zero})))
        moved = wb.analyze(bars(W_BOTTOM, "09-30", **{"09-14": zero}))      # 缺值那天不當左底，改用 9/17 的 295
        self.assertEqual((str(moved["plan"]["left_date"]), moved["plan"]["left_low"]), ("2026-09-17", 295))

    def test_wide_signal_bar_notes_document_risk_cap(self):
        # 文件規則風險 > 6% 不買；決策中心用動能停損照常列入，只在說明註記
        r = sr.analyze(bars(RECLAIM, "09-18", **{"09-18": {"high": 316, "raw_high": 316}}))
        self.assertEqual((r["trigger_raw"], r["status"], r["plan"]["executable"]), (316.5, "priority", False))
        self.assertIn("超過文件上限 6%", " ".join(r["notes"]))


class WBottomTest(unittest.TestCase):
    def test_2492_on_0930_matches_document(self):
        r = wb.analyze(bars(W_BOTTOM, "09-30"))
        p = r["plan"]
        self.assertEqual((r["trigger_raw"], r["stop"], p["neckline"], p["left_low"]), (309.5, 298, 326, 291))
        self.assertEqual((r["status"], r["max_gap_pct"]), ("priority", 0.02))
        self.assertEqual(str(p["left_date"]), "2026-09-14")
        self.assertAlmostEqual(p["risk_pct"], 11.5 / 309.5)
        self.assertAlmostEqual(p["pullback"], 1 - 299.5 / 334)
        self.assertAlmostEqual(p["volume_ratio"], 0.68, places=2)
        self.assertEqual(str(p["strong_date"]), "2026-09-07")

    def test_version2_confirms_on_1001(self):
        s = wb.series(bars(W_BOTTOM))
        t = 20
        self.assertEqual(str(s["date"][t]), "2026-09-30")
        self.assertTrue(wb.confirmed(s, t, wb.setup(s, t)))   # 329 > 326，量 1.94 倍

    def test_each_condition_is_required(self):
        self.assertIsNone(wb.analyze(bars(W_BOTTOM, "09-30", **{"09-30": {"volume": 22_000_000}})))  # 量沒縮
        for change in ({"right_max": 0.02},        # 右底比左底高 2.6%
                       {"strong_day": 0.10},       # 近 20 日最大單日漲幅 9.9%（9/4）
                       {"min_rebound": 0.13}):     # 頸線只比左底高 12.0%
            with self.subTest(change=change):
                self.assertIsNone(wb.analyze(bars(W_BOTTOM, "09-30"), {**wb.PARAMS, **change}))


class BuyStopFromSetupTest(unittest.TestCase):
    def test_6672_fills_at_310_on_0921(self):
        b = bars(RECLAIM)
        r = sr.analyze(b[:27])
        fill = execution.simulate(b, {"observed_date": r["signal_date"], "entry_type": "buy_stop",
                                      "trigger": r["trigger_raw"], "stop": r["stop"], "max_risk_pct": 0.06,
                                      "exit_ma": 10, "horizon": 20, "slippage": 0})
        self.assertEqual((str(fill["entry_date"]), fill["actual_entry"]), ("2026-09-21", 310))
        self.assertEqual(fill["status"], "pending")          # 9/23 最低 298 沒碰到停損 297


if __name__ == "__main__":
    unittest.main()
