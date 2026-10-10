import contextlib
import importlib.util
import io
import json
import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest.mock import Mock, patch

import notify_daily_decision as cli
from app import decision_digest as dg, notification_calendar as nc
from test_decision_center import candidate, EMPTY_HOLDINGS
from test_line_notify import Response, AUTH


def calendar(n=65):
    days, d = [], date(2026, 7, 1)
    while len(days) < n:
        if d.weekday() < 5:
            days.append(d)
        d += timedelta(days=1)
    return days


def bars(days, raw=100, factor=1):
    return [{"trade_date": d, "open": raw * factor, "high": (raw + 1) * factor,
             "low": (raw - 1) * factor, "close": raw * factor, "raw_open": raw,
             "raw_high": raw + 1, "raw_low": raw - 1, "raw_close": raw,
             "volume": 10000, "amount": 1000000} for d in days]


def txn(id, action, d, shares=100, price=100):
    return {"id": id, "stock_id": "A", "action": action, "trade_date": d,
            "shares": shares, "price": price, "fee": 0, "tax": 0}


def response(as_of):
    scan = {"as_of": str(as_of), "scanned": 1, "data_health": {"healthy": True},
            "items": [candidate("A", "電子") ]}
    return dg.build_response(scan, EMPTY_HOLDINGS, {"above": True})


class HoldingChecksTest(unittest.TestCase):
    def test_actual_fill_price_and_first_day_included_in_deadline(self):
        days = calendar(60)
        checks = dg.holding_checks([txn(1, "buy", days[0], price=110)],
                                   {"A": bars(days)}, days, days[-1])
        self.assertEqual(checks[0]["holding_days"], 60)
        self.assertEqual(checks[0]["stop"], 101.2)
        self.assertTrue(any("8%停損" in s for s in checks[0]["alerts"]))
        self.assertTrue(any("第60日收盤" in s for s in checks[0]["alerts"]))

    def test_day_59_warns_next_day_close_not_open(self):
        days = calendar(59)
        checks = dg.holding_checks([txn(1, "buy", days[0])], {"A": bars(days)}, days, days[-1])
        self.assertIn("次交易日為第60個交易日；本規則於該日收盤到期", checks[0]["alerts"])

    def test_fifo_partial_sale_keeps_each_lot_cost_and_date(self):
        days = calendar(65)
        txns = [txn(1, "buy", days[0], 300, 100), txn(2, "buy", days[20], 200, 110),
                txn(3, "sell", days[50], 150), txn(4, "buy", days[60], 1000, 80)]
        checks = dg.holding_checks(txns, {"A": bars(days)}, days, days[58])
        self.assertEqual([(c["shares"], c["buy_price"], c["holding_days"]) for c in checks],
                         [(150, 100, 59), (200, 110, 39)])
        self.assertFalse(any("8%停損" in s for s in checks[0]["alerts"]))
        self.assertTrue(any("8%停損" in s for s in checks[1]["alerts"]))

    def test_corporate_action_does_not_create_false_50_percent_loss(self):
        days = calendar(55)
        history = bars(days[:30], 100, 0.5) + bars(days[30:], 50, 1)
        checks = dg.holding_checks([txn(1, "buy", days[0])], {"A": history}, days, days[-1])
        self.assertEqual(checks[0]["stop"], 46)
        self.assertEqual(checks[0]["alerts"], [])

    def test_close_below_50day_average_and_missing_data_are_reported(self):
        days = calendar(55)
        history = bars(days)
        history[-1].update(close=95, low=94, raw_close=95, raw_low=94)
        checks = dg.holding_checks([txn(1, "buy", days[0])], {"A": history}, days, days[-1])
        self.assertTrue(any("50日均線" in s and "次交易日開盤" in s for s in checks[0]["alerts"]))
        checks = dg.holding_checks([txn(1, "buy", days[0])], {"A": history[:-1]}, days, days[-1])
        self.assertTrue(checks[0]["incomplete"])
        self.assertTrue(any("行情不足" in s for s in checks[0]["alerts"]))

    def test_missing_buy_calendar_does_not_claim_deadline_or_no_alert(self):
        days = calendar(65)
        checks = dg.holding_checks([txn(1, "buy", days[0] - timedelta(days=1))],
                                   {"A": bars(days)}, days, days[-1])
        self.assertIsNone(checks[0]["holding_days"])
        self.assertTrue(any("無法確認60日期限" in s for s in checks[0]["alerts"]))


class DigestAndCliTest(unittest.TestCase):
    def test_profile_passes_market_gate_and_uses_lot1(self):
        result = response(date(2026, 10, 8))
        self.assertEqual(result["summary"]["selected"], 1)
        self.assertEqual(result["settings"]["lot_size"], 1)
        self.assertEqual(result["settings"]["horizon"], 60)
        text = dg.render_digest({"response": result, "holding_checks": []})
        self.assertIn("次交易日新買候選：1檔", text)
        self.assertIn("成交後重算", text)
        self.assertIn("第60個交易日收盤到期", text)

    def snapshot_file(self, directory, *, as_of=None, created=None):
        today = dg.today()
        snapshot = {"version": dg.VERSION, "profile": dg.PROFILE, "requested_date": str(today),
                    "created_at": (created or datetime.now(dg.TAIPEI)).isoformat(),
                    "response": response(as_of or today), "holding_checks": []}
        path = Path(directory) / "snapshot.json"
        path.write_text(json.dumps(snapshot), encoding="utf-8")
        return path

    def test_dry_run_never_reads_credentials_sends_or_writes_tracking(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self.snapshot_file(tmp, as_of=dg.today() - timedelta(days=2))
            with patch.object(cli.line_notify, "credentials") as auth, \
                 patch.object(cli.line_notify, "send_once") as send, contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(cli.main(["--dry-run", "--snapshot", str(path)]), 0)
            auth.assert_not_called()
            send.assert_not_called()

    def run_snapshot(self, path, holiday=None):
        with patch.object(cli.line_notify, "credentials", return_value=AUTH), \
             patch.object(cli.line_notify, "send_once", return_value={"message": "accepted"}) as send, \
             patch.object(nc, "closed_reason", return_value=holiday), \
             contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            rc = cli.main(["--snapshot", str(path)])
        return rc, send

    def test_stale_trading_day_sends_failure_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self.snapshot_file(tmp, as_of=dg.today() - timedelta(days=2))
            rc, send = self.run_snapshot(path)
        self.assertEqual(rc, 2)
        self.assertIn("failure/", send.call_args.args[0])
        self.assertIn("不推播舊買點", send.call_args.args[1])
        self.assertNotIn("新買候選", send.call_args.args[1])

    def test_holiday_does_not_send_old_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self.snapshot_file(tmp, as_of=dg.today() - timedelta(days=2))
            rc, send = self.run_snapshot(path, "國慶日")
        self.assertEqual(rc, 0)
        send.assert_not_called()

    def test_old_snapshot_is_rejected_without_send(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self.snapshot_file(tmp, created=datetime.now(dg.TAIPEI) - timedelta(hours=13))
            rc, send = self.run_snapshot(path)
        self.assertEqual(rc, 1)
        send.assert_not_called()

    def test_upstream_failure_alert_does_not_need_db(self):
        with patch.object(cli.line_notify, "credentials", return_value=AUTH), \
             patch.object(cli.line_notify, "send_once", return_value={"message": "accepted"}) as send, \
             patch.object(dg.db, "query", side_effect=AssertionError("no DB")), \
             contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cli.main(["--failed-jobs", "daily,refresh"]), 0)
        self.assertIn("daily、refresh", send.call_args.args[1])


class NotificationCalendarTest(unittest.TestCase):
    def test_official_informational_trading_dates_are_not_holidays(self):
        rows = [{"Date": "1150102", "Name": "國曆新年開始交易日"},
                {"Date": "1150211", "Name": "農曆春節前最後交易日"},
                {"Date": "1150212", "Name": "市場無交易，僅辦理結算交割作業"},
                {"Date": "1150213", "Name": "市場無交易，僅辦理結算交割作業"},
                {"Date": "1151009", "Name": "國慶日"}]
        result = nc.parse_holidays(rows, 2026)
        self.assertNotIn("2026-01-02", result)
        self.assertNotIn("2026-02-11", result)
        self.assertIn("2026-02-12", result)
        self.assertIn("2026-10-09", result)
        with self.assertRaises(ValueError):
            nc.parse_holidays(rows, 2027)

    def test_weekend_does_not_fetch_calendar(self):
        opener = Mock()
        self.assertEqual(nc.closed_reason(date(2026, 10, 10), opener=opener), "週末休市")
        opener.assert_not_called()


class NightlyNotificationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = Path(__file__).resolve().parents[3]
        spec = importlib.util.spec_from_file_location("nightly_for_notification_test", root / "nightly.py")
        cls.nightly = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.nightly)

    def test_capture_failure_sends_alert_and_does_not_pass_snapshot(self):
        results = [("daily", True), ("refresh", True)]
        with patch.object(self.nightly, "run_cmd", side_effect=[1, 0]) as run, \
             contextlib.redirect_stdout(io.StringIO()):
            self.nightly.capture_and_notify("test-dsn", date(2026, 10, 8), results)
        self.assertIn("--failed-jobs", run.call_args.args[0])
        self.assertNotIn("--snapshot", run.call_args.args[0])
        self.assertIn(("decision", False), results)

    def test_refresh_failure_does_not_capture_but_still_alerts(self):
        results = [("daily", True), ("refresh", False)]
        with patch.object(self.nightly, "run_cmd", return_value=0) as run, \
             contextlib.redirect_stdout(io.StringIO()):
            self.nightly.capture_and_notify("test-dsn", date(2026, 10, 8), results)
        self.assertEqual(run.call_count, 1)
        self.assertIn("refresh,decision", run.call_args.args[0])

    def test_success_reuses_same_capture_snapshot_and_other_job_warning(self):
        results = [("daily", True), ("usprice", False), ("refresh", True)]
        with patch.object(self.nightly, "run_cmd", return_value=0) as run, \
             contextlib.redirect_stdout(io.StringIO()):
            self.nightly.capture_and_notify("test-dsn", date(2026, 10, 8), results)
        capture, notify = [c.args[0] for c in run.call_args_list]
        self.assertEqual(capture[capture.index("--line-snapshot") + 1], notify[notify.index("--snapshot") + 1])
        self.assertIn("usprice", notify)

    def test_skip_line_preserves_normal_capture_without_notification(self):
        results = [("daily", True), ("refresh", True)]
        with patch.object(self.nightly, "run_cmd", return_value=0) as run, \
             contextlib.redirect_stdout(io.StringIO()):
            self.nightly.capture_and_notify("test-dsn", date(2026, 10, 8), results, notify=False)
        self.assertEqual(run.call_count, 1)
        self.assertNotIn("--line-snapshot", run.call_args.args[0])


if __name__ == "__main__":
    unittest.main()
