from __future__ import annotations

import threading
from contextlib import contextmanager
from typing import Iterator


class ProjectDeletingError(RuntimeError):
    pass


_guard = threading.RLock()
_locks: dict[tuple[str, str], threading.RLock] = {}
_deleting: set[tuple[str, str]] = set()


def _project_lock(key: tuple[str, str]) -> threading.RLock:
    with _guard:
        return _locks.setdefault(key, threading.RLock())


@contextmanager
def project_operation(user_id: str, project_id: str) -> Iterator[None]:
    key = (user_id, project_id)
    lock = _project_lock(key)
    with lock:
        if key in _deleting:
            raise ProjectDeletingError("项目正在删除，不能创建新任务")
        yield


@contextmanager
def project_deletion(user_id: str, project_id: str) -> Iterator[None]:
    key = (user_id, project_id)
    lock = _project_lock(key)
    with lock:
        if key in _deleting:
            raise ProjectDeletingError("项目已经在删除")
        _deleting.add(key)
        try:
            yield
        finally:
            _deleting.discard(key)
