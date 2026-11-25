import time
import threading


class RateLimiter:
    def __init__(self, max_tokens=6, refill_rate=2.0):
        self.max_tokens = max_tokens
        self.refill_rate = refill_rate
        self.buckets = {}  # ip -> [tokens, last_updated]
        self.lock = threading.Lock()

    def is_allowed(self, client_ip):
        with self.lock:
            now = time.time()

            if client_ip not in self.buckets:
                self.buckets[client_ip] = [self.max_tokens - 1.0, now]
                return True

            tokens, last_time = self.buckets[client_ip]
            elapsed = now - last_time
            tokens = min(self.max_tokens, tokens + elapsed * self.refill_rate)

            if tokens >= 1.0:
                self.buckets[client_ip] = [tokens - 1.0, now]
                return True

            self.buckets[client_ip] = [tokens, now]
            return False