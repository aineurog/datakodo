"""Token bucket rate limiter per provider instance.

Sits between core dispatch and adapter calls so every provider
automatically respects its configured rate limit without each
adapter reimplementing the logic.
"""

import threading
import time
from collections.abc import Callable
from functools import wraps

from datakodo.core.exceptions import RateLimitError


class TokenBucket:
    """Thread-safe token bucket for rate limiting.

    Tokens refill at *rate* tokens per second, up to *burst* capacity.
    """

    def __init__(self, rate: float, burst: int = 1) -> None:
        self._rate = rate
        self._burst = burst
        self._tokens = float(burst)
        self._last_refill = time.monotonic()
        self._lock = threading.Lock()

    def consume(self, weight: int = 1) -> bool:
        """Try to consume *weight* tokens. Returns True if allowed."""
        with self._lock:
            now = time.monotonic()
            elapsed = now - self._last_refill
            self._tokens = min(self._burst, self._tokens + elapsed * self._rate)
            self._last_refill = now

            if self._tokens >= weight:
                self._tokens -= weight
                return True
            return False

    def wait_time(self, weight: int = 1) -> float:
        """Estimated seconds until *weight* tokens are available."""
        with self._lock:
            now = time.monotonic()
            elapsed = now - self._last_refill
            current = min(self._burst, self._tokens + elapsed * self._rate)
            if current >= weight:
                return 0.0
            return (weight - current) / self._rate


def rate_limited(limiter: TokenBucket, weight_fn: Callable[..., int] | None = None):
    """Decorator that gates a function behind a TokenBucket.

    If *weight_fn* is provided it receives the same args/kwargs as the
    wrapped function and must return the token cost. Otherwise each call
    costs 1 token.

    Raises RateLimitError (with retry_after) when the bucket is empty.
    """

    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            weight = weight_fn(*args, **kwargs) if weight_fn else 1

            if not limiter.consume(weight):
                retry_after = limiter.wait_time(weight)
                raise RateLimitError(
                    f"Rate limit exceeded. Retry after {retry_after:.1f}s.",
                    retry_after=retry_after,
                )

            return func(*args, **kwargs)

        return wrapper

    return decorator
