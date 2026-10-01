"""ReviewGate — chặn worker thread cho tới khi TUI resolve."""

from __future__ import annotations

import threading
from typing import Any

DEFAULT_TIMEOUT = 300.0


class _ReviewTimedOut:
    """Sentinel: `wait()` hết giờ — KHÁC với người dùng bấm Reject (`None`).

    Spec §4.3 yêu cầu timeout phải báo `run_error`, còn reject thì báo `cancelled`.
    Trả `None` cho cả hai khiến timeout bị báo nhầm thành "đã huỷ".
    """

    __slots__ = ()

    def __repr__(self) -> str:  # pragma: no cover - chỉ phục vụ debug
        return "<REVIEW_TIMED_OUT>"


REVIEW_TIMED_OUT = _ReviewTimedOut()


class ReviewGate:
    def __init__(self, timeout: float = DEFAULT_TIMEOUT) -> None:
        self._timeout = timeout
        self._event = threading.Event()
        self._lock = threading.Lock()
        self._result: list[Any] | None = None

    @property
    def timeout(self) -> float:
        return self._timeout

    def wait(self) -> list[dict] | None | _ReviewTimedOut:
        """Block tới khi resolve.

        - `list[dict]` — duyệt, giữ lại các case đó
        - `None`       — người dùng bấm Reject
        - `REVIEW_TIMED_OUT` — hết `timeout` mà không ai phản hồi
        """
        if not self._event.wait(timeout=self._timeout):
            return REVIEW_TIMED_OUT
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
