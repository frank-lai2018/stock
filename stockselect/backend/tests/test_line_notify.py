import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from unittest.mock import Mock, patch
from urllib.error import HTTPError

from app import line_notify as ln

AUTH = ("fake-token-for-unit-tests", "U" + "a" * 32)


class Response:
    def __init__(self, status=200, data=None, headers=None):
        self.status, self.data, self.headers = status, data or {}, headers or {}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return json.dumps(self.data).encode()


class Transport:
    def __init__(self, statuses=(200,), usage=0):
        self.statuses, self.usage = list(statuses), usage
        self.pushes = []

    def __call__(self, request, timeout):
        if request.full_url.endswith("/quota/consumption"):
            return Response(data={"totalUsage": self.usage})
        if request.full_url.endswith("/quota"):
            return Response(data={"type": "limited", "value": 200})
        self.pushes.append((json.loads(request.data), request.get_header("X-line-retry-key")))
        status = self.statuses.pop(0) if self.statuses else 200
        if status == 0:
            raise TimeoutError("do not echo private inputs")
        if status == 409:
            raise HTTPError(request.full_url, 409, "Conflict", {"x-line-accepted-request-id": "test"}, None)
        return Response(status=status)


class LineNotifyTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "state.sqlite3"

    def tearDown(self):
        self.temp.cleanup()

    def send(self, transport, text="日報", **kwargs):
        return ln.send_once("daily/2026-10-08", text, state_path=self.path, auth=AUTH,
                            opener=transport, sleep=Mock(), **kwargs)

    def test_accepted_event_is_not_sent_again_and_credentials_are_not_stored(self):
        transport = Transport()
        self.assertEqual(self.send(transport)["status"], "accepted")
        self.assertEqual(self.send(transport, "different text")["status"], "duplicate")
        self.assertEqual(len(transport.pushes), 1)
        self.assertEqual(len(transport.pushes[0][0]["messages"]), 1)
        self.assertNotIn(AUTH[0].encode(), self.path.read_bytes())
        self.assertNotIn(AUTH[1].encode(), self.path.read_bytes())

    def test_timeout_and_server_retry_reuse_identical_key_and_payload(self):
        transport = Transport((0, 500, 200))
        self.assertEqual(self.send(transport)["status"], "accepted")
        self.assertEqual(len(transport.pushes), 3)
        self.assertTrue(all(p == transport.pushes[0] for p in transport.pushes))

    def test_rerun_failed_event_keeps_original_content(self):
        transport = Transport((0, 0, 0))
        with self.assertRaises(ln.NotificationError):
            self.send(transport, "原始日報")
        self.assertEqual(self.send(transport, "重算後不同的日報")["status"], "accepted")
        self.assertTrue(all(p == transport.pushes[0] for p in transport.pushes))

    def test_409_with_accepted_request_header_is_success(self):
        self.assertEqual(self.send(Transport((409,)))["status"], "accepted")

    def test_permanent_http_failure_is_not_retried(self):
        transport = Transport((401, 200))
        with self.assertRaisesRegex(ln.NotificationError, "HTTP 401"):
            self.send(transport)
        self.assertEqual(len(transport.pushes), 1)

    def test_quota_exhaustion_prevents_push(self):
        transport = Transport(usage=200)
        with self.assertRaisesRegex(ln.NotificationError, "額度已用完"):
            self.send(transport)
        self.assertEqual(transport.pushes, [])

    def test_old_uncertain_request_is_not_sent_after_retry_expiry(self):
        transport = Transport((0, 0, 0))
        with self.assertRaises(ln.NotificationError):
            self.send(transport, now=lambda: 100)
        with self.assertRaisesRegex(ln.NotificationError, "重試期限"):
            self.send(transport, now=lambda: 100 + 24 * 3600)
        self.assertEqual(len(transport.pushes), 3)

    def test_parallel_worker_does_not_send_same_event(self):
        arrived, release = Event(), Event()
        transport = Transport()
        def blocking(request, timeout):
            if request.full_url.endswith("/message/push"):
                arrived.set()
                release.wait(5)
            return transport(request, timeout)
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(self.send, blocking)
            self.assertTrue(arrived.wait(3))
            second = self.send(transport)
            release.set()
            self.assertEqual(first.result()["status"], "accepted")
        self.assertEqual(second["status"], "busy")
        self.assertEqual(len(transport.pushes), 1)

    def test_invalid_user_id_does_not_reveal_value(self):
        with patch.object(ln, "environment_value", side_effect=["valid-token", "private-invalid-id"]):
            with self.assertRaises(ln.NotificationError) as raised:
                ln.credentials()
        self.assertNotIn("private-invalid-id", str(raised.exception))

    def test_utf16_limit_and_disabled_notification(self):
        text = ln.fit_text("🚀" * 3000)
        self.assertLessEqual(len(text.encode("utf-16-le")) // 2, 4500)
        self.assertIn("其餘內容", text)
        with patch.object(ln, "credentials", return_value=None):
            self.assertEqual(ln.send_once("x", "test")["status"], "disabled")


if __name__ == "__main__":
    unittest.main()
