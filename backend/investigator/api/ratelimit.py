"""Per-client limits on investigations, protecting the shared free LLM quota.

Groq's free tier gives each model 200,000 tokens a day and an investigation costs 3-18k
(median ~9k measured), so one visitor clicking repeatedly could spend the whole demo's day.
A sliding window per client (minute and day) keeps the demo usable for everyone. In memory:
limits reset when the app restarts, which is acceptable for a demo and needs no extra
infrastructure.
"""

import time
from collections import defaultdict, deque
from collections.abc import Callable

from fastapi import Request

DAY = 24 * 3600.0


class RateLimiter:
    def __init__(self, per_minute: int, per_day: int, clock: Callable[[], float] = time.monotonic):
        self.per_minute, self.per_day, self._clock = per_minute, per_day, clock
        self._events: dict[str, deque[float]] = defaultdict(deque)

    def acquire(self, key: str) -> float | None:
        """Record one use and return None, or return the seconds to wait if over a limit."""
        now = self._clock()
        events = self._events[key]
        while events and now - events[0] >= DAY:
            events.popleft()
        last_minute = [t for t in events if now - t < 60]
        if len(last_minute) >= self.per_minute:
            return 60 - (now - last_minute[0])
        if len(events) >= self.per_day:
            return DAY - (now - events[0])
        events.append(now)
        return None


def client_key(request: Request) -> str:
    """The caller's IP. Behind a proxy (Hugging Face Spaces) it's the first X-Forwarded-For."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"
