"""In-memory sliding-window limiter for login and registration attempts.

Per process: each replica counts on its own and counts reset on restart. Enough to slow down
password guessing against a demo, not a substitute for a shared limiter.
"""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable


class AttemptLimiter:
    def __init__(
        self, max_attempts: int, window_s: float, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self._max = max_attempts
        self._window = window_s
        self._clock = clock
        self._hits: dict[str, deque[float]] = {}

    def _recent(self, key: str) -> deque[float] | None:
        hits = self._hits.get(key)
        if hits is None:
            return None
        cutoff = self._clock() - self._window
        while hits and hits[0] <= cutoff:
            hits.popleft()
        if not hits:
            del self._hits[key]
            return None
        return hits

    def blocked(self, *keys: str) -> bool:
        return any(len(self._recent(k) or ()) >= self._max for k in keys)

    def record(self, *keys: str) -> None:
        now = self._clock()
        for k in keys:
            self._recent(k)
            self._hits.setdefault(k, deque()).append(now)

    def reset(self, *keys: str) -> None:
        for k in keys:
            self._hits.pop(k, None)
