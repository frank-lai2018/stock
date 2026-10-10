from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event
from unittest import TestCase
from unittest.mock import Mock, patch

from app.scan_cache import ScanCache


class ScanCacheTest(TestCase):
    def test_concurrent_same_query_computes_once_and_returns_independent_copies(self):
        cache = ScanCache()
        started, release, together = Event(), Event(), Barrier(4)

        def scan(**kwargs):
            started.set()
            if not release.wait(5):
                raise RuntimeError("test did not release scan")
            return {"items": [{"stock_id": "6672"}]}

        compute = Mock(side_effect=scan)

        def call():
            together.wait(timeout=5)
            return cache.get(compute, recent=3)

        with ThreadPoolExecutor(max_workers=3) as pool:
            jobs = [pool.submit(call) for _ in range(3)]
            together.wait(timeout=5)
            self.assertTrue(started.wait(5))
            release.set()
            results = [job.result(timeout=5) for job in jobs]
        compute.assert_called_once_with(recent=3)
        results[0]["items"][0]["stock_id"] = "changed"
        self.assertEqual(results[1]["items"][0]["stock_id"], "6672")
        self.assertEqual(cache.get(compute, recent=3)["items"][0]["stock_id"], "6672")

    def test_different_query_can_complete_while_first_scan_is_pending(self):
        cache, started, release = ScanCache(), Event(), Event()

        def compute(recent):
            if recent == 3:
                started.set()
                if not release.wait(5):
                    raise RuntimeError("test did not release scan")
            return {"recent": recent}

        with ThreadPoolExecutor(max_workers=2) as pool:
            slow = pool.submit(cache.get, compute, recent=3)
            self.assertTrue(started.wait(5))
            try:
                fast = pool.submit(cache.get, compute, recent=5)
                self.assertEqual(fast.result(timeout=2), {"recent": 5})
            finally:
                release.set()
            self.assertEqual(slow.result(timeout=5), {"recent": 3})

    def test_failed_scan_can_retry_and_expired_cache_refreshes(self):
        cache = ScanCache(ttl=10)
        compute = Mock(side_effect=[RuntimeError("scan failed"), {"items": []}, {"items": [1]}])
        with self.assertRaisesRegex(RuntimeError, "scan failed"):
            cache.get(compute, recent=3)
        with patch("app.scan_cache.monotonic", return_value=20):
            self.assertEqual(cache.get(compute, recent=3), {"items": []})
        with patch("app.scan_cache.monotonic", return_value=29):
            self.assertEqual(cache.get(compute, recent=3), {"items": []})
        with patch("app.scan_cache.monotonic", return_value=30):
            self.assertEqual(cache.get(compute, recent=3), {"items": [1]})
        self.assertEqual(compute.call_count, 3)
