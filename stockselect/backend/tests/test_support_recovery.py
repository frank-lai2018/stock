from copy import deepcopy
from datetime import date, timedelta
from unittest import TestCase
from unittest.mock import patch

from app import decision_center as dc, decision_combinations, decision_digest as dg, entry_conditions
from app import lin_bojin as lin, support_recovery as recovery
from test_lin_bojin import EMPTY


def rows():
    # 最後十二根採華星光 8/4～8/19 的價量形態；前綴只提供向上的趨勢背景。
    recent = [(425, 447.5, 425, 447.5, 3_178_000), (492, 492, 480, 492, 6_690_000),
              (472.5, 494.5, 472.5, 488, 3_150_000), (494, 498, 473, 488, 2_301_000),
              (517, 536, 508, 536, 9_026_000), (520, 558, 506, 510, 20_184_000),
              (529, 561, 529, 561, 17_058_000), (579, 615, 560, 564, 25_909_000),
              (550, 565, 508, 522, 3_822_000), (540, 545, 521, 545, 1_567_000),
              (550, 550, 515, 528, 1_954_000), (495, 533, 495, 529, 1_266_000)]
    days, stamp = [], date(2026, 8, 19)
    while len(days) < 82:
        if stamp.weekday() < 5:
            days.append(stamp)
        stamp -= timedelta(days=1)
    days.reverse()
    prices = [(240 + i * 3 - 1, 240 + i * 3 + 1, 240 + i * 3 - 2,
               240 + i * 3, 1_000_000) for i in range(70)] + recent
    return [dict(trade_date=d, open=o, high=h, low=l, close=c, raw_close=c,
                 volume=v, amount=v * c) for d, (o, h, l, c, v) in zip(days, prices)]


def scan(data, eps=1.29, rs=92, amt20=100_000_000):
    strategy = lin.analyze(data, rs)
    return {"as_of": str(data[-1]["trade_date"]), "scanned": 1, "data_health": {"healthy": True},
            "items": [dc._candidate(dict(stock_id="4979", name="華星光", industry="光電",
                                         eps=eps, rs_rating=rs, amt20=amt20, close=data[-1]["close"],
                                         raw_close=data[-1]["raw_close"],
                                         price_factor=data[-1]["close"] / data[-1]["raw_close"]),
                                    [strategy], data[-1]["close"])]}


def following(data, *, low=518, close=570):
    row = dict(trade_date=data[-1]["trade_date"] + timedelta(days=1), open=542,
               high=max(570, close), low=low, close=close, raw_close=close,
               volume=1_486_000, amount=807_351_000)
    return data + [row]


class SupportRecoveryTest(TestCase):
    def test_seed_uses_known_support_and_upper_shadow_without_requiring_neckline_breakout(self):
        data = rows()
        before = deepcopy(data)
        observed = recovery.observation(data, 92)
        self.assertEqual(observed["type"], "support_recovery")
        self.assertEqual((observed["support"], observed["signal_high"], observed["signal_low"]), (508, 533, 495))
        self.assertEqual(observed["test_date"], "2026-08-13")
        self.assertEqual(observed["signal_date"], "2026-08-19")
        self.assertEqual(observed["undercut_pct"], 2.56)
        self.assertFalse(observed["turned"])
        self.assertEqual(data, before)

    def test_low_rs_missing_trend_no_upper_shadow_and_invalid_recovery_are_rejected(self):
        self.assertIsNone(recovery.observation(rows(), 69))
        self.assertIsNone(recovery.observation(rows(), None))
        for change in (dict(low=488), dict(close=500), dict(volume=20_000_000),
                       dict(open=526), dict(high=562)):
            data = rows()
            data[-1].update(change)
            with self.subTest(change=change):
                self.assertIsNone(recovery.observation(data, 92))
        data = rows()
        for b in data[:-1]:
            b["high"] = max(b["open"], b["close"]) + .01
        self.assertIsNone(recovery.observation(data, 92))
        down = rows()
        for i, b in enumerate(down[-25:-5]):
            b["close"] = 900 - i
        self.assertIsNone(recovery.observation(down, 92))

    def test_followup_freezes_support_and_reports_excessive_structural_risk(self):
        data = rows()
        initial = recovery.observation(data, 92)
        observed = recovery.observation(following(data), 92)
        self.assertEqual(observed["support"], initial["support"])
        self.assertEqual(observed["signal_date"], initial["signal_date"])
        self.assertEqual(observed["age_days"], 1)
        self.assertTrue(observed["turned"])
        self.assertTrue(observed["exceeds_risk_reference"])
        self.assertAlmostEqual(observed["risk_reference_max"], 538.04)
        self.assertIn("暫不追價", observed["confirmation"])
        self.assertIsNone(recovery.observation(following(data, low=495), 92))
        self.assertIsNone(recovery.observation(following(data, low=496, close=508), 92))

    def test_followup_expires_and_future_data_cannot_change_original_observation(self):
        original = rows()
        expected = recovery.observation(original, 92)
        future = original
        for _ in range(4):
            future = following(future)
        self.assertIsNone(recovery.observation(future, 92))
        self.assertEqual(recovery.observation(future[:len(original)], 92), expected)

    def test_adjusted_prices_are_converted_to_raw_observation_coordinates(self):
        data = rows()
        for b in data:
            for key in ("open", "high", "low", "close"):
                b[key] *= 2
        observed = lin.analyze(data, 92)["lin_bojin"]["observation"]
        self.assertEqual((observed["support"], observed["test_high"], observed["signal_low"]), (508, 615, 495))

    def test_digest_and_decision_table_have_observation_without_buy_plan_or_allocation(self):
        result = dg.build_response(scan(rows()), EMPTY, {"above": True}, gate=lin.KEY)
        item = result["items"][0]
        self.assertEqual(result["lin_bojin_observation_count"], 1)
        self.assertFalse(item["selected"])
        self.assertFalse(item["position_plan"]["valid"])
        self.assertEqual(item["position_plan"]["suggested_shares"], 0)
        self.assertEqual(result["summary"]["ready"], 0)
        text = dg.render_digest({"response": result, "holding_checks": []})
        for phrase in ("4979 華星光", "量縮收復支撐", "收復支撐：508", "不是買進訊號", "交易限制待核對"):
            self.assertIn(phrase, text)
        self.assertNotIn("入選試算", text)
        cond = item["entry_conditions"][0]
        self.assertEqual(cond["deadline"], "等待確認")
        self.assertIsNone(cond["trigger_low"])
        self.assertIsNone(cond["trigger_high"])
        self.assertTrue(any("不是委託價" in line for line in cond["lines"]))
        checked = decision_combinations._check(item, lin.KEY, item["raw_close"])
        self.assertFalse(checked["passed"])

    def test_nonpositive_or_nonfinite_eps_and_insufficient_liquidity_are_not_notified(self):
        for eps in (0, -1, None, float("nan"), float("inf")):
            result = dg.build_response(scan(rows(), eps=eps), EMPTY, {"above": True}, gate=lin.KEY)
            self.assertEqual(lin.observation_items(result), [])
            self.assertNotIn("4979 華星光", dg.render_digest({"response": result, "holding_checks": []}))
        result = dg.build_response(scan(rows(), amt20=19_999_999), EMPTY, {"above": True}, gate=lin.KEY)
        self.assertEqual(lin.observation_items(result), [])

    def test_unqualified_observation_cannot_show_a_trial_position_in_general_decision_view(self):
        source = scan(rows(), eps=0)
        result = dc.build_decision_response(source, [], EMPTY, gate=lin.KEY, market={"above": True})
        lin.attach(result, source)
        self.assertEqual(result["lin_bojin_observation_count"], 0)
        self.assertFalse(result["items"][0]["position_plan"]["valid"])
        self.assertEqual(result["items"][0]["position_plan"]["suggested_shares"], 0)

    def test_bad_input_is_rejected_by_shared_analyzer_and_breakout_is_not_relabelled(self):
        for changes in (dict(volume=0), dict(raw_close=0), dict(high=500), dict(close=float("nan"))):
            data = rows()
            data[-1].update(changes)
            self.assertIsNone(lin.analyze(data, 92))
        from test_lin_bojin import base
        strategy = lin.analyze(base(), 90)
        self.assertEqual((strategy["status"], strategy["stage"]), ("priority", "first"))

    def test_turning_followup_remains_observation_even_when_market_allows_new_positions(self):
        result = dg.build_response(scan(following(rows())), EMPTY, {"above": True}, gate=lin.KEY)
        self.assertEqual(result["summary"]["selected"], 0)
        self.assertEqual(result["lin_bojin_observation_count"], 1)
        self.assertIn("暫不追價", dg.render_digest({"response": result, "holding_checks": []}))

    def test_coexisting_weekly_observation_does_not_hide_support_followup_or_create_orders(self):
        weekly = dict(eligible=True, type="challenge", label="放量挑戰週線壓力", volume_ratio=2,
                      extension_pct=-1, pressure_band=dict(low=529, high=533, touch_count=2))
        with patch.object(lin.weekly_resistance, "observation", return_value=weekly):
            result = dg.build_response(scan(rows()), EMPTY, {"above": True}, gate=lin.KEY)
        item = result["items"][0]
        self.assertEqual(item["lin_bojin"]["observation"]["type"], "challenge")
        self.assertEqual(result["lin_bojin_observation_count"], 1)
        text = dg.render_digest({"response": result, "holding_checks": []})
        self.assertIn("放量挑戰週線壓力", text)
        self.assertIn("另符合量縮收復支撐", text)
        self.assertTrue(any("不是委託價" in line for line in item["entry_conditions"][0]["lines"]))
        self.assertFalse(item["selected"])
