import unittest

from app.breakout_rank import score_breakout


class BreakoutRankTest(unittest.TestCase):
    def _row(self):
        return {
            "security_type": "stock", "rs_rating": 90, "trend_template": True,
            "ret_12_1": 0.30, "tight_recent": 0.08, "eps_yoy_accel": True,
            "eps_accel": True, "rev_yoy": 25, "gross_margin_chg": 1.2,
            "per_pctile": 25, "big1000_chg": 1, "inst_net_20d": 100,
            "amt20": 120_000_000,
            "breakout": {"vol_ratio": 2.2, "current_adj_close": 102,
                         "neckline": 100, "target": 120, "atr14": 3},
        }

    def test_strong_candidate_is_priority(self):
        result = score_breakout(self._row(), {"n": 1000, "avg_excess": 1.2,
                                               "median_ret": -1, "win_rate": 45,
                                               "avg_ret": 2})
        self.assertEqual(result["status"], "priority")
        self.assertGreaterEqual(result["score"], 75)
        self.assertEqual(result["blockers"], [])

    def test_chasing_and_weak_rs_are_blocked(self):
        row = self._row()
        row["rs_rating"] = 55
        row["breakout"]["current_adj_close"] = 112
        result = score_breakout(row)
        self.assertEqual(result["status"], "skip")
        self.assertTrue(any("RS" in x for x in result["blockers"]))
        self.assertTrue(any("追價" in x for x in result["blockers"]))


if __name__ == "__main__":
    unittest.main()
