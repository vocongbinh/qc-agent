"""Cầu nối emit event từ executor sang TUI.

Bus là tuỳ chọn: CLI không set bus nên mọi hàm ở đây là no-op.

CHỈ emit `test_result`. `node_end` / `run_done` do JobRunner (tui/runner.py) phát
— emit thêm ở đây sẽ khiến TUI nhận trùng event.
"""

from __future__ import annotations

from typing import Any, Optional

_bus: Optional[Any] = None


def set_bus(bus: Optional[Any]) -> None:
    global _bus
    _bus = bus


def get_bus() -> Optional[Any]:
    return _bus


def emit_test_result(result: dict[str, Any]) -> None:
    if _bus is None:
        return
    from tui.bus import Event
    _bus.emit(Event(
        kind="test_result",
        payload={
            "id": result.get("id"),
            "title": result.get("title"),
            "type": (result.get("type") or "api"),
            "status": result.get("status"),
            "duration_ms": result.get("duration_ms"),
            "error_message": result.get("error_message"),
        },
    ))
