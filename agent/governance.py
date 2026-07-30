from __future__ import annotations

import shutil
import threading
import time
from collections import defaultdict, deque
from pathlib import Path


class QuotaExceededError(RuntimeError):
    pass


class SlidingWindowLimiter:
    def __init__(self) -> None:
        self._events: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def check(self, key: str, *, limit: int, window_seconds: int) -> None:
        now = time.monotonic()
        cutoff = now - max(1, window_seconds)
        with self._lock:
            entries = self._events[key]
            while entries and entries[0] < cutoff:
                entries.popleft()
            if len(entries) >= max(1, limit):
                raise QuotaExceededError("请求过于频繁，请稍后重试")
            entries.append(now)


def ensure_disk_capacity(path: Path, minimum_free_bytes: int) -> None:
    if minimum_free_bytes <= 0:
        return
    path.mkdir(parents=True, exist_ok=True)
    free = shutil.disk_usage(path).free
    if free < minimum_free_bytes:
        raise QuotaExceededError(
            f"磁盘可用空间不足，至少需要 {minimum_free_bytes} 字节"
        )


def prune_old_files(directory: Path, pattern: str, *, keep: int) -> int:
    if keep < 1 or not directory.is_dir():
        return 0
    files = sorted(
        (path for path in directory.glob(pattern) if path.is_file()),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    removed = 0
    for path in files[keep:]:
        path.unlink(missing_ok=True)
        removed += 1
    return removed


registration_limiter = SlidingWindowLimiter()
request_limiter = SlidingWindowLimiter()
