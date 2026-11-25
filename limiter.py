from __future__ import annotations

import threading
import time
from collections import OrderedDict


class RateLimiter:
    def __init__(
        self,
        max_tokens: int = 6,
        refill_rate: float = 2.0,
        ttl_seconds: float = 300.0,
        max_buckets: int = 10_000,
    ) -> None:
        if max_tokens <= 0 or refill_rate <= 0:
            raise ValueError("max_tokens and refill_rate must be positive")
        self.max_tokens = float(max_tokens)
        self.refill_rate = float(refill_rate)
        self.ttl_seconds = float(ttl_seconds)
        self.max_buckets = int(max_buckets)
        self.buckets: OrderedDict[str, tuple[float, float]] = OrderedDict()
        self.lock = threading.Lock()

    def _evict_stale(self, now: float) -> None:
        cutoff = now - self.ttl_seconds
        while self.buckets:
            oldest_ip, (_, last_time) = next(iter(self.buckets.items()))
            if last_time < cutoff:
                self.buckets.popitem(last=False)
            else:
                break

    def is_allowed(self, client_ip: str) -> bool:
        with self.lock:
            now = time.monotonic()
            self._evict_stale(now)

            if client_ip not in self.buckets:
                if len(self.buckets) >= self.max_buckets:
                    self.buckets.popitem(last=False)
                self.buckets[client_ip] = (self.max_tokens - 1.0, now)
                return True

            tokens, last_time = self.buckets[client_ip]
            elapsed = max(0.0, now - last_time)
            tokens = min(self.max_tokens, tokens + elapsed * self.refill_rate)

            if tokens >= 1.0:
                self.buckets[client_ip] = (tokens - 1.0, now)
                self.buckets.move_to_end(client_ip)
                return True

            self.buckets[client_ip] = (tokens, now)
            self.buckets.move_to_end(client_ip)
            return False