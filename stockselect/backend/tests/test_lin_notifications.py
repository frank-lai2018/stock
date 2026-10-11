import contextlib
import io
import json
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import capture_daily_decisions as capture
import notify_daily_decision as cli
from app import decision_center as dc, decision_digest as dg, lin_bojin as lin, line_notify as ln, notification_calendar as nc
from test_lin_bojin import base, scan, EMPTY
from test_line_notify import AUTH, Transport


def response(*, risk_blocked=False, rs=90):
    scanned = scan(lin.analyze(base(), rs))
    scanned["as_of"] = str(dg.today())
    scanned["items"][0]["eps"] = 0.01
    holdings = {**EMPTY, "risk_amount": 40_000} if risk_blocked else EMPTY
    result = dg.build_response(scanned, holdings, {"above": True}, gate=lin.KEY)
    result["items"][0]["display_themes"] = [{"name": "CCL"}]
    return result


class LinNotificationsTest(unittest.TestCase):
    def snapshot_file(self, folder, **kwargs):
        result = response(**kwargs)
        with patch.object(dg, "enrich_selected"):
            snapshot = dg.make_snapshot(result, dg.today(), holding_checks=[])
        path = Path(folder) / "lin.json"
        path.write_text(json.dumps(snapshot), encoding="utf-8")
        return path

    def run_cli(self, arguments, holiday=None):
        output = io.StringIO()
        with patch.object(ln, "credentials", return_value=AUTH), \
             patch.object(ln, "send_once", return_value={"message": "accepted"}) as send, \
             patch.object(nc, "closed_reason", return_value=holiday), \
             contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            rc = cli.main(arguments)
        return rc, send, output.getvalue()

    def test_lin_profile_uses_same_real_buy_point_and_60day_exit(self):
        result = response()
        self.assertEqual(result["gate"], lin.KEY)
        self.assertTrue(result["items"][0]["selected"])
        self.assertEqual(result["settings"]["horizon"], 60)
        self.assertEqual(result["settings"]["lot_size"], 1)
        text = dg.render_digest({"response": result, "holding_checks": []})
        for phrase in ("林柏錦選股條件", "首次頸線突破", "固定頸線：100.0", "追價上限：105.0", "CCL", "第60個交易日收盤", "非本人官方公式"):
            self.assertIn(phrase, text)
        self.assertLessEqual(len(text.encode("utf-16-le")) // 2, 4500)

    def test_risk_blocked_still_lists_passed_stock_without_allocation(self):
        result = response(risk_blocked=True)
        self.assertFalse(result["items"][0]["selected"])
        text = dg.render_digest({"response": result, "holding_checks": []})
        self.assertIn("通過買點條件：1檔｜資金配置入選：0檔", text)
        self.assertIn("TEST 測試", text)
        self.assertIn("未配置部位", text)
        self.assertNotIn("入選試算：", text)

    def test_unconfirmed_candidate_is_not_reported_as_buy_point(self):
        text = dg.render_digest({"response": response(rs=69), "holding_checks": []})
        self.assertIn("通過買點條件：0檔", text)
        self.assertNotIn("TEST 測試", text)

    def test_lin_cli_sends_own_profile_and_breakout_rejects_lin_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self.snapshot_file(tmp)
            rc, send, _ = self.run_cli(["--strategy", lin.KEY, "--snapshot", str(path)])
            self.assertEqual(rc, 0)
            self.assertEqual(send.call_args.args[0], f"daily/{dg.today()}/{dg.LIN_PROFILE}")
            rc, send, _ = self.run_cli(["--snapshot", str(path)])
            self.assertEqual(rc, 1)
            send.assert_not_called()

    def test_stale_lin_quotes_send_failure_only_and_holiday_skips(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self.snapshot_file(tmp)
            snapshot = json.loads(path.read_text(encoding="utf-8"))
            snapshot["response"]["as_of"] = str(dg.today() - timedelta(days=2))
            path.write_text(json.dumps(snapshot), encoding="utf-8")
            args = ["--strategy", lin.KEY, "--snapshot", str(path)]
            rc, send, _ = self.run_cli(args)
            self.assertEqual(rc, 2)
            self.assertIn("failure/", send.call_args.args[0])
            self.assertIn("林柏錦", send.call_args.args[1])
            self.assertNotIn("TEST 測試", send.call_args.args[1])
            rc, send, _ = self.run_cli(args, holiday="休市")
            self.assertEqual(rc, 0)
            send.assert_not_called()

    def test_wrong_rule_version_lot_mode_or_expired_snapshot_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self.snapshot_file(tmp)
            original = path.read_text(encoding="utf-8")
            mutations = [
                lambda s: s["response"]["lin_bojin_config"].update(version="other"),
                lambda s: s["response"]["settings"].update(lot_size=1000),
                lambda s: s["response"].update(gate="breakout"),
                lambda s: s.update(created_at=(datetime.now(dg.TAIPEI) - timedelta(hours=13)).isoformat()),
            ]
            for mutate in mutations:
                snapshot = json.loads(original)
                mutate(snapshot)
                path.write_text(json.dumps(snapshot), encoding="utf-8")
                rc, send, _ = self.run_cli(["--strategy", lin.KEY, "--snapshot", str(path)])
                self.assertEqual(rc, 1)
                send.assert_not_called()

    def test_dry_run_does_not_read_credentials_or_send(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self.snapshot_file(tmp)
            with patch.object(ln, "credentials") as auth, patch.object(ln, "send_once") as send, \
                 contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(cli.main(["--strategy", lin.KEY, "--snapshot", str(path), "--dry-run"]), 0)
            auth.assert_not_called()
            send.assert_not_called()

    def test_two_profiles_deduplicate_independently(self):
        with tempfile.TemporaryDirectory() as tmp:
            transport = Transport()
            for _ in range(2):
                for profile in (dg.PROFILE, dg.LIN_PROFILE):
                    ln.send_once(f"daily/2026-10-08/{profile}", profile, auth=AUTH,
                                 state_path=Path(tmp) / "state.sqlite3", opener=transport)
            self.assertEqual(len(transport.pushes), 2)

    def test_capture_writes_both_from_one_scan_and_one_holding_check(self):
        scanned = scan(lin.analyze(base(), 90))
        scanned["as_of"] = str(dg.today())
        scanned["items"][0]["eps"] = 0.01
        with tempfile.TemporaryDirectory() as tmp:
            original, lin_path = Path(tmp) / "original.json", Path(tmp) / "lin.json"
            with patch.object(dc, "scan_market", return_value=scanned) as scanner, \
                 patch.object(dc, "settle_pending", return_value=0), \
                 patch.object(dc, "current_holdings", return_value=EMPTY), \
                 patch.object(dc, "market_regime", return_value={"above": True, "ma_days": 60}), \
                 patch.object(dc, "calibration_rows", return_value=[]), \
                 patch.object(dc, "record_candidates", return_value=0), \
                 patch.object(dg, "load_holding_checks", return_value=[]) as checks, \
                 patch.object(dg, "enrich_selected"), contextlib.redirect_stdout(io.StringIO()):
                capture.main(["--line-snapshot", str(original), "--lin-line-snapshot", str(lin_path)])
            scanner.assert_called_once()
            self.assertTrue(scanner.call_args.kwargs["include_lin_bojin"])
            checks.assert_called_once()
            before, after = (json.loads(p.read_text(encoding="utf-8")) for p in (original, lin_path))
            self.assertEqual(before["profile"], dg.PROFILE)
            self.assertEqual(after["profile"], dg.LIN_PROFILE)
            self.assertEqual(before["response"]["as_of"], after["response"]["as_of"])
            self.assertEqual(before["holding_checks"], after["holding_checks"])
