"""ReviewGate — chặn worker thread cho tới khi TUI resolve."""

from __future__ import annotations

import threading
from typing import Any

DEFAULT_TIMEOUT = 300.0


class ReviewGate:
    def __init__(self, timeout: float = DEFAULT_TIMEOUT) -> None:
        self._timeout = timeout
        self._event = threading.Event()
        self._lock = threading.Lock()
        self._result: list[Any] | None = None

    def wait(self) -> list[dict] | None:
        """Block tới khi resolve. Trả list test case được giữ, None = reject/timeout."""
        if not self._event.wait(timeout=self._timeout):
            return None
        with self._lock:
            return self._result

    def resolve(self, kept_cases: list[dict] | None) -> None:
        """Giải phóng gate. kept_cases=None nghĩa là reject."""
        with self._lock:
            self._result = kept_cases
        self._event.set()

    def release(self) -> None:
        """Giải phóng với reject — dùng khi thread crash, tránh treo."""
        self.resolve(None)

    def is_resolved(self) -> bool:
        return self._event.is_set()
