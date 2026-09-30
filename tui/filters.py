"""Lọc test case theo phase đã chọn trong TUI."""

from __future__ import annotations

from typing import Any, Iterable, Optional

ALL_PHASES: list[str] = ["api", "ui", "chaos", "performance"]


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
    """Giữ test case có `type` nằm trong phases. Thiếu type = coi là api."""
    allowed = set(normalize_phases(phases))
    if not allowed:
        return []
    return [
        t for t in (tests or [])
        if (t.get("type") or "api").lower() in allowed
    ]
