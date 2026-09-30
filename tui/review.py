"""ReviewModel — trạng thái bật/tắt test case trong modal review.

Tách khỏi widget Textual để test không cần terminal.
"""

from __future__ import annotations

from typing import Any, Optional


class ReviewModel:
    def __init__(self, cases: Optional[list[dict[str, Any]]]) -> None:
        self.cases: list[dict[str, Any]] = list(cases or [])
        self.enabled: list[bool] = [True] * len(self.cases)
        self.cursor: int = 0

    @property
    def can_approve(self) -> bool:
        return any(self.enabled)

    def toggle(self, index: int) -> None:
        if 0 <= index < len(self.enabled):
            self.enabled[index] = not self.enabled[index]

    def set_all(self, value: bool) -> None:
        self.enabled = [value] * len(self.cases)

    def move_cursor(self, delta: int) -> None:
        if not self.cases:
            return
        self.cursor = max(0, min(len(self.cases) - 1, self.cursor + delta))

    def kept_cases(self) -> list[dict[str, Any]]:
        return [c for c, on in zip(self.cases, self.enabled) if on]

    def selected_count(self) -> int:
        return sum(self.enabled)

    def total_count(self) -> int:
        return len(self.cases)

    def rows(self) -> list[tuple[bool, str, str, str, str]]:
        """(checked, id, priority, type, title) cho DataTable."""
        out = []
        for case, on in zip(self.cases, self.enabled):
            out.append((
                on,
                str(case.get("id") or ""),
                str(case.get("priority") or ""),
                str(case.get("type") or "api"),
                str(case.get("title") or ""),
            ))
        return out
