"""A small sliding-window rate limiter (in memory, per process).

Enough for one server in a demo. Several workers or servers would need a shared store
such as Redis, or each would allow its own quota.
"""

import time
from collections import defaultdict, deque
from threading import Lock


class RateLimiter:
    def __init__(self, limit: int, per_seconds: float, clock=time.monotonic):
        self.limit = limit
        self.per_seconds = per_seconds
        self._clock = clock
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = Lock()

    def allow(self, key: str) -> bool:
        now = self._clock()
        with self._lock:
            hits = self._hits[key]
            while hits and now - hits[0] >= self.per_seconds:
                hits.popleft()
            if len(hits) >= self.limit:
                return False
            hits.append(now)
            return True
