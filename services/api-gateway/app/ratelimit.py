"""Rate limiting en memoria por origen (CHG-007).

Ventana deslizante simple, suficiente para una instancia única local.
Un despliegue con réplicas debe sustituirlo por un almacén compartido
manteniendo esta interfaz.
"""

import time
from collections import deque


class SlidingWindowRateLimiter:
    def __init__(self, limit_per_minute: int, window_seconds: float = 60.0):
        self._limit = limit_per_minute
        self._window = window_seconds
        self._hits: dict[str, deque[float]] = {}

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        hits = self._hits.setdefault(key, deque())
        while hits and now - hits[0] > self._window:
            hits.popleft()
        if len(hits) >= self._limit:
            return False
        hits.append(now)
        return True
