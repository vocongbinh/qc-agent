"""ReviewModalScreen — Modal dialog duyệt Test Plan (OpenCode Style).

Thiết kế popup nổi giữa màn hình, viền nổi bật, phím tắt nhanh:
- ↑↓ di chuyển, space bật/tắt, a chọn tất cả, n bỏ tất cả
- Enter: Duyệt & Chạy (Approve & Run)
- Esc: Từ chối (Reject)
"""

from __future__ import annotations

from typing import Any, Optional

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, Footer, Label

from tui.review import ReviewModel

COLUMNS = ("On", "ID", "Priority", "Type", "Title")

HINT = "↑↓ navigate · space toggle · a all · n none"


class ReviewModalScreen(ModalScreen):
    DEFAULT_CSS = """
    ReviewModalScreen {
        align: center middle;
        background: rgba(0, 0, 0, 0.7);
    }

    #review-dialog {
        width: 85%;
        max-width: 92;
        height: auto;
        max-height: 80%;
        background: $surface;
        border: round $panel-lighten-2;
        padding: 1 2;
    }

    #review-title {
        text-style: bold;
        color: $text;
        margin-bottom: 1;
    }

    #review-table {
        height: auto;
        max-height: 12;
        margin-bottom: 1;
    }

    #review-hint {
        color: $text-muted;
        margin-bottom: 1;
    }

    #review-buttons {
        width: 100%;
        align: right middle;
        height: 1;
        margin-top: 1;
    }

    #review-buttons Button {
        border: none;
        background: transparent;
        height: 1;
        min-width: 14;
        padding: 0 1;
        margin-left: 2;
    }

    #reject {
        color: $text-muted;
        background: transparent !important;
        border: none;
    }

    #reject:hover, #reject:focus {
        color: $text;
        background: transparent !important;
        text-style: bold;
    }

    #approve {
        color: $accent;
        background: transparent !important;
        border: none;
        text-style: bold;
    }

    #approve:hover, #approve:focus {
        color: $primary;
        background: transparent !important;
        text-style: bold underline;
    }
    """

    BINDINGS = [
        ("space", "toggle", "Toggle"),
        ("a", "all", "Select all"),
        ("n", "none", "Deselect all"),
        ("escape", "dismiss", "Reject"),
        ("enter", "submit_approve", "Approve & Run"),
    ]

    def __init__(self, model: ReviewModel, plan: Optional[dict[str, Any]] = None) -> None:
        super().__init__()
        self.model = model
        self.plan = plan or {}
        self._table: Optional[DataTable] = None

    # ----- state (test được không cần terminal) -----

    def summary_text(self) -> str:
        parts = [str(self.plan.get("title") or "Test Plan")]
        parts.append(f"{len(self.model.cases)} cases")
        est = self.plan.get("estimated_duration_min")
        if est:
            parts.append(f"est. {est} min")
        risks = self.plan.get("risks") or []
        if risks:
            parts.append(f"{len(risks)} risks")
        return " · ".join(parts)

    def approve(self) -> Optional[list[dict[str, Any]]]:
        """None khi không còn case nào được tick — Approve không được đóng modal."""
        if not self.model.can_approve:
            return None
        return self.model.kept_cases()

    def reject(self) -> None:
        return None

    # ----- compose -----

    def compose(self) -> ComposeResult:
        with Vertical(id="review-dialog"):
            yield Label(f"📋 Review: {self.summary_text()}", id="review-title", markup=False)
            yield DataTable(id="review-table")
            yield Label(HINT, id="review-hint", classes="hint", markup=False)
            with Horizontal(id="review-buttons"):
                yield Button("[Esc] Cancel", id="reject", variant="error")
                yield Button("[Enter] Approve ↵", id="approve", variant="success")
        yield Footer()

    def on_mount(self) -> None:
        self._table = self.query_one("#review-table", DataTable)
        for name in COLUMNS:
            self._table.add_column(name, key=name)
        self._render_table()
        self.set_focus(self._table)

    # ----- render -----

    def _render_table(self) -> None:
        if self._table is None:
            return
        self._table.clear()
        for checked, case_id, priority, case_type, title in self.model.rows():
            self._table.add_row(
                "✓" if checked else " ",
                Text(case_id, style="bold cyan"),
                Text(priority, style="yellow" if priority == "high" else ("bold red" if priority == "critical" else "dim")),
                Text(case_type, style="green"),
                Text(title),
            )
        self._sync_approve_button()

    def _sync_approve_button(self) -> None:
        try:
            self.query_one("#approve", Button).disabled = not self.model.can_approve
        except Exception:
            pass

    # ----- actions -----

    def action_toggle(self) -> None:
        self.model.toggle(self.model.cursor)
        self.model.move_cursor(1)
        self._render_table()

    def action_all(self) -> None:
        self.model.set_all(True)
        self._render_table()

    def action_none(self) -> None:
        self.model.set_all(False)
        self._render_table()

    def action_submit_approve(self) -> None:
        if self.model.can_approve:
            self.dismiss(self.approve())

    # ----- events -----

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "approve":
            self.dismiss(self.approve())
        elif event.button.id == "reject":
            self.dismiss(self.reject())

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        row = event.cursor_row
        if 0 <= row < len(self.model.cases):
            self.model.cursor = row
            self.model.toggle(row)
            self.model.move_cursor(1)
            self._render_table()

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        if 0 <= event.cursor_row < len(self.model.cases):
            self.model.cursor = event.cursor_row
