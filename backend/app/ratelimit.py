"""Ограничение частоты в памяти процесса: неудачные входы и загрузки файлов.

API работает одним процессом uvicorn, поэтому общего хранилища не нужно. При нескольких
воркерах лимиты станут «на воркер» — это ослабит защиту, но не сломает вход; тогда
счётчики стоит перенести в PostgreSQL или Redis. Перед API в production стоит ещё
limit_req Nginx (frontend/docker/nginx.conf).
"""

import threading
import time
from collections import defaultdict, deque


class SlidingWindow:
    """Сколько событий по ключу было за последние window секунд."""

    def __init__(self) -> None:
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def _trim(self, key: str, window: float, now: float) -> deque[float]:
        q = self._hits[key]
        while q and q[0] <= now - window:
            q.popleft()
        if not q:
            del self._hits[key]
            return deque()
        return q

    def count(self, key: str, window: float) -> int:
        with self._lock:
            return len(self._trim(key, window, time.monotonic()))

    def retry_after(self, key: str, window: float) -> int:
        """Через сколько секунд освободится одно место в окне."""
        with self._lock:
            q = self._trim(key, window, time.monotonic())
            return max(1, int(q[0] + window - time.monotonic()) + 1) if q else 0

    def hit(self, key: str) -> None:
        with self._lock:
            self._hits[key].append(time.monotonic())

    def reset(self, key: str | None = None) -> None:
        with self._lock:
            if key is None:
                self._hits.clear()
            else:
                self._hits.pop(key, None)


login_failures = SlidingWindow()
uploads = SlidingWindow()


def reset_all() -> None:
    login_failures.reset()
    uploads.reset()
