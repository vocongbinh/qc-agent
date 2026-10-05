"""Inline Approval Bar Widget (OpenCode PermissionPrompt Style).

Hiển thị thanh xác nhận duyệt Test Plan mỏng nhẹ ngay phía trên ô Prompt:
📋 Test Plan: <Title> (3 cases) · [Enter] Approve & Run  ·  [Esc] Cancel  ·  [Space] Options
Không làm văng popup che khuất màn hình như cũ.
"""

from __future__ import annotations

from typing import Any, Optional

from rich.text import Text
from textual.app import ComposeResult
from textual.widgets import Static


class ApprovalBar(Static):
    """Thanh xác nhận Test Plan inline đặt ngay phía trên ô nhập prompt."""

    DEFAULT_CSS = """
    ApprovalBar {
        width: 100%;
        height: auto;
        min-height: 2;
        padding: 0 1;
        margin-bottom: 0;
        background: $surface-darken-1;
        border-left: thick $accent;
        border-top: solid $panel;
        border-bottom: solid $panel;
        display: none;
    }
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.plan: dict[str, Any] = {}
        self.count: int = 0
        self.is_active: bool = False

    def compose(self) -> ComposeResult:
        yield from ()

    def show_plan(self, plan: dict[str, Any], count: int) -> None:
        """Kích hoạt hiển thị thanh duyệt kế hoạch inline."""
        self.plan = plan or {}
        self.count = count or len(self.plan.get("test_cases") or [])
        self.is_active = True
        self.display = True
        self._render_bar()

    def hide_bar(self) -> None:
        """Ẩn thanh duyệt."""
        self.is_active = False
        self.display = False
        self.update("")

    def _render_bar(self) -> None:
        title = str(self.plan.get("title") or "Test Plan")
        cases = self.plan.get("test_cases") or []

        content = Text()
        content.append("📋 Test Plan: ", style="bold green")
        content.append(f"{title} ", style="bold white")
        content.append(f"({self.count} cases)\n", style="cyan")

        content.append("   Actions: ", style="dim")
        content.append("[Enter ↵ Approve & Run] ", style="bold black on green")
        content.append("  ", style="dim")
        content.append("[Esc Cancel] ", style="bold white on red")
        content.append("  ", style="dim")
        content.append("[Space Options] ", style="dim italic")

        self.update(content)
