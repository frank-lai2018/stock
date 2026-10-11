import contextlib
import io
import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

import capture_daily_decisions as capture
import notify_daily_decision as cli
from app import decision_center as dc, decision_digest as dg, lin_bojin as lin, line_notify as ln
from test_decision_center import candidate, EMPTY_HOLDINGS
from test_lin_bojin import base, scan
from test_line_notify import AUTH


def stock(gate, sid, eps, rs=90):
    item = (scan(lin.analyze(base(), 90))["items"][0] if gate == lin.KEY
            else candidate(sid, sid))
    return {**item, "stock_id": sid, "name": sid, "industry": sid,
            "eps": eps, "eps_ttm": 10, "rs_rating": rs}


def scanned(items):
    return {"items": items, "as_of": str(dg.today()), "scanned": 832,
            "data_health": {"healthy": True}}


class NotificationEpsTest(unittest.TestCase):
    def test_both_strategies_require_strict_positive_finite_single_quarter_eps(self):
        for gate in ("breakout", lin.KEY):
            with self.subTest(gate=gate):
                invalid = [0, -1, None, float("nan"), float("inf"), "NaN", "invalid"]
                source = scanned([stock(gate, f"BAD{i}", eps, 99) for i, eps in enumerate(invalid)]
                                 + [stock(gate, "GOOD", 0.0001, 80)])
                result = dg.build_response(source, EMPTY_HOLDINGS, {"above": True}, gate=gate)
                self.assertEqual([x["stock_id"] for x in result["items"]], ["GOOD"])
                self.assertTrue(result["items"][0]["selected"])
                self.assertEqual(result["scanned"], 832)
                self.assertEqual(result["settings"]["latest_quarter_eps_gt"], 0)
                # 各 BAD 的近四季合計為正也不能代替單季數值；原始掃描仍供一般決策使用。
                self.assertEqual(len(source["items"]), len(invalid) + 1)
                self.assertTrue(all("selected" not in x for x in source["items"]))

    def test_filter_happens_before_three_position_limit(self):
        for gate in ("breakout", lin.KEY):
            with self.subTest(gate=gate):
                source = scanned([stock(gate, f"BAD{i}", -1, 99) for i in range(3)]
                                 + [stock(gate, f"GOOD{i}", 1, 80) for i in range(3)])
                result = dg.build_response(source, EMPTY_HOLDINGS, {"above": True}, gate=gate)
                self.assertEqual(result["summary"]["selected"], 3)
                self.assertEqual({x["stock_id"] for x in result["items"] if x["selected"]},
                                 {"GOOD0", "GOOD1", "GOOD2"})

    def test_both_reports_show_single_quarter_value_without_rounding_small_eps_to_zero(self):
        for gate in ("breakout", lin.KEY):
            with self.subTest(gate=gate):
                result = dg.build_response(scanned([stock(gate, "GOOD", 0.0001)]),
                                           EMPTY_HOLDINGS, {"above": True}, gate=gate)
                text = dg.render_digest({"response": result, "holding_checks": []})
                self.assertIn("最新單季EPS>0", text)
                self.assertIn("最新單季 EPS：0.0001元", text)

    def test_zero_eligible_stocks_sends_normal_empty_report_instead_of_data_failure(self):
        for gate in ("breakout", lin.KEY):
            with self.subTest(gate=gate), tempfile.TemporaryDirectory() as tmp:
                result = dg.build_response(scanned([stock(gate, "BAD", 0)]),
                                           EMPTY_HOLDINGS, {"above": True}, gate=gate)
                with patch.object(dg, "enrich_selected"):
                    snapshot = dg.make_snapshot(result, dg.today(), holding_checks=[])
                path = Path(tmp) / "snapshot.json"
                path.write_text(json.dumps(snapshot), encoding="utf-8")
                with patch.object(ln, "credentials", return_value=AUTH), \
                     patch.object(ln, "send_once", return_value={"message": "accepted"}) as send, \
                     contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(cli.main(["--strategy", gate, "--snapshot", str(path)]), 0)
                self.assertTrue(send.call_args.args[0].startswith("daily/"))
                self.assertNotIn("BAD", send.call_args.args[1])
                self.assertIn("最新單季EPS>0", send.call_args.args[1])

    def test_old_or_unfiltered_snapshots_never_send_normal_report(self):
        for gate in ("breakout", lin.KEY):
            with self.subTest(gate=gate), tempfile.TemporaryDirectory() as tmp:
                result = dg.build_response(scanned([stock(gate, "GOOD", 1)]),
                                           EMPTY_HOLDINGS, {"above": True}, gate=gate)
                with patch.object(dg, "enrich_selected"):
                    original = dg.make_snapshot(result, dg.today(), holding_checks=[])
                mutations = [lambda s: s.update(version="line-digest-v1"),
                             lambda s: s["response"]["items"][0].update(eps=0),
                             lambda s: s["response"]["settings"].pop("latest_quarter_eps_gt")]
                for mutate in mutations:
                    snapshot = deepcopy(original)
                    mutate(snapshot)
                    path = Path(tmp) / "snapshot.json"
                    path.write_text(json.dumps(snapshot), encoding="utf-8")
                    with patch.object(ln, "credentials", return_value=AUTH), \
                         patch.object(ln, "send_once") as send, \
                         contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                        self.assertEqual(cli.main(["--strategy", gate, "--snapshot", str(path)]), 1)
                    send.assert_not_called()

    def test_nonprofitable_holdings_still_limit_allocation_and_keep_exit_alerts(self):
        holdings = {"items": [{"stock_id": "BAD"}], "stock_ids": {"BAD"},
                    "industry_counts": {"BAD": 1}, "risk_amount": 40_000}
        alert = {"stock_id": "BAD", "name": "虧損持股", "buy_date": "2026-09-01",
                 "shares": 100, "alerts": ["今日盤中已觸及8%停損參考"]}
        for gate in ("breakout", lin.KEY):
            with self.subTest(gate=gate):
                result = dg.build_response(scanned([stock(gate, "BAD", -1), stock(gate, "GOOD", 1)]),
                                           holdings, {"above": True}, gate=gate)
                self.assertEqual(result["summary"]["held"], 1)
                self.assertEqual(result["summary"]["selected"], 0)
                self.assertEqual([x["stock_id"] for x in result["items"]], ["GOOD"])
                with patch.object(dg, "enrich_selected"):
                    snapshot = dg.make_snapshot(result, dg.today(), holding_checks=[alert])
                text = dg.render_digest(snapshot)
                self.assertIn("BAD 虧損持股", text)
                self.assertIn("今日盤中已觸及8%停損參考", text)

    def test_nightly_snapshots_filter_both_strategies_and_preserve_full_history_scan(self):
        items = []
        for sid, eps in (("BAD", -1), ("GOOD", 1)):
            item = stock(lin.KEY, sid, eps)
            item["strategies"] += candidate(sid, sid)["strategies"]
            items.append(item)
        source = scanned(items)
        with tempfile.TemporaryDirectory() as tmp:
            paths = [Path(tmp) / name for name in ("original.json", "lin.json")]
            with patch.object(dc, "scan_market", return_value=source), \
                 patch.object(dc, "settle_pending", return_value=0), \
                 patch.object(dc, "current_holdings", return_value=EMPTY_HOLDINGS), \
                 patch.object(dc, "market_regime", return_value={"above": True, "ma_days": 60}), \
                 patch.object(dc, "calibration_rows", return_value=[]), \
                 patch.object(dc, "record_candidates", return_value=0) as record, \
                 patch.object(dg, "load_holding_checks", return_value=[]), \
                 patch.object(dg, "enrich_selected"), contextlib.redirect_stdout(io.StringIO()):
                capture.main(["--line-snapshot", str(paths[0]), "--lin-line-snapshot", str(paths[1])])
            self.assertTrue(record.called)
            self.assertTrue(all(len(call.args[0]["items"]) == 2 for call in record.call_args_list))
            for path in paths:
                snapshot = json.loads(path.read_text(encoding="utf-8"))
                self.assertEqual([x["stock_id"] for x in snapshot["response"]["items"]], ["GOOD"])
                self.assertEqual(snapshot["version"], dg.VERSION)
                self.assertEqual(snapshot["response"]["settings"]["latest_quarter_eps_gt"], 0)
