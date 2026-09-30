"""Lọc test case theo phase đã chọn trong TUI."""

from __future__ import annotations

from typing import Any, Iterable, Optional

ALL_PHASES: list[str] = ["api", "ui", "chaos", "performance"]

# `TestType` có `integration` nhưng TUI không có checkbox riêng cho nó:
# api_executor chạy cả `api` lẫn `integration` qua HTTP, nên type của test case
# được ánh xạ về phase thật sự điều khiển nó. `ALL_PHASES` giữ nguyên 4 mục —
# UI duyệt danh sách này để dựng checkbox.
PHASE_ALIASES: dict[str, str] = {"integration": "api"}


def _phase_of(test_type: str) -> str:
    return PHASE_ALIASES.get(test_type, test_type)


def normalize_phases(phases: Optional[Iterable[str]]) -> list[str]:
    """Rỗng = cho phép tất cả. Giữ thứ tự, bỏ trùng và giá trị lạ."""
    if not phases:
        return list(ALL_PHASES)
    seen: list[str] = []
    for p in phases:
        p = (p or "").strip().lower()
        if p in ALL_PHASES and p not in seen:
            seen.append(p)
    return seen


def filter_by_phases(
    tests: Optional[list[dict[str, Any]]], phases: Optional[Iterable[str]]
) -> list[dict[str, Any]]:
    """Giữ test case có `type` nằm trong phases. Thiếu type = coi là api.

    Type được ánh xạ qua `PHASE_ALIASES` trước, nên `integration` được giữ khi
    user chọn phase `api`.
    """
    allowed = set(normalize_phases(phases))
    if not allowed:
        return []
    return [
        t for t in (tests or [])
        if _phase_of((t.get("type") or "api").lower()) in allowed
    ]
