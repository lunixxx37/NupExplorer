"""Small shared helpers."""
from __future__ import annotations

import threading
from collections import OrderedDict


class ByteLRU:
    """Thread-safe LRU cache bounded by the summed size of its values."""

    def __init__(self, max_bytes: int):
        self.max_bytes = max_bytes
        self._d: OrderedDict = OrderedDict()
        self._size = 0
        self._lock = threading.Lock()

    def get(self, key):
        with self._lock:
            v = self._d.get(key)
            if v is None:
                return None
            self._d.move_to_end(key)
            return v[0]

    def put(self, key, value, size: int | None = None):
        if size is None:
            size = len(value) if hasattr(value, '__len__') else 1
        if size > self.max_bytes:
            return value
        with self._lock:
            old = self._d.pop(key, None)
            if old is not None:
                self._size -= old[1]
            self._d[key] = (value, size)
            self._size += size
            while self._size > self.max_bytes and self._d:
                _, (_, s) = self._d.popitem(last=False)
                self._size -= s
        return value

    def clear(self):
        with self._lock:
            self._d.clear()
            self._size = 0


def human_size(n: int) -> str:
    for unit in ('B', 'KB', 'MB', 'GB'):
        if n < 1024 or unit == 'GB':
            return f'{n:.0f} {unit}' if unit == 'B' else f'{n:.1f} {unit}'
        n /= 1024
    return f'{n:.1f} GB'
