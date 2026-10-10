"""短期掃描快取：同一條件冷啟動只計算一次，每位呼叫者取得獨立副本。"""
from concurrent.futures import Future
from copy import deepcopy
from threading import Lock
from time import monotonic


class ScanCache:
    def __init__(self, ttl=300, max_entries=20):
        self.ttl, self.max_entries = ttl, max_entries
        self.entries, self.pending, self.lock = {}, {}, Lock()

    def get(self, compute, **kwargs):
        key = tuple(sorted(kwargs.items()))
        with self.lock:
            hit = self.entries.get(key)
            if hit and monotonic() - hit[0] < self.ttl:
                return deepcopy(hit[1])
            future = self.pending.get(key)
            owner = future is None
            if owner:
                future = self.pending[key] = Future()
        if not owner:
            return deepcopy(future.result())
        try:
            result = compute(**kwargs)
            with self.lock:
                self.entries[key] = (monotonic(), result)
                if len(self.entries) > self.max_entries:
                    oldest = min(self.entries, key=lambda k: self.entries[k][0])
                    self.entries.pop(oldest)
            future.set_result(result)
            return deepcopy(result)
        except BaseException as exc:
            future.set_exception(exc)
            raise
        finally:
            with self.lock:
                self.pending.pop(key, None)
