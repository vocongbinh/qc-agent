"""ReviewModalScreen — modal duyệt Test Plan.

`ReviewModel` giữ trạng thái tick; modal chỉ lo phần hiển thị. Worker thread
đang chờ ở `ReviewGate.wait()` nên `dismiss(...)` là đường duy nhất giải
phóng gate: `None` = reject.
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

HINT = "↑↓ chọn · space bật/tắt · a tất cả · n bỏ tất cả · esc từ chối"


class ReviewModalScreen(ModalScreen):
    BINDINGS = [
        ("space", "toggle", "Bật/tắt"),
        ("a", "all", "Bật tất cả"),
        ("n", "none", "Bỏ tất cả"),
        # Textual 8.x không còn binding escape→dismiss trên Screen, phải khai
        # báo rõ. `dismiss(None)` là nhánh reject nên gate luôn được giải phóng.
        ("escape", "dismiss", "Từ chối"),
    ]

    def __init__(self, model: ReviewModel, plan: Optional[dict[str, Any]] = None) -> None:
        super().__init__()
        self.model = model
        self.plan = plan or {}
        # Chỉ gán trong `on_mount` — trước đó mọi render phải là no-op.
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
            # Tiêu đề/case do LLM sinh: tắt markup để `[` trong text không
            # ném MarkupError giữa lúc render.
            yield Label(f"Review: {self.summary_text()}", markup=False)
            yield DataTable(id="review-table")
            yield Label(HINT, classes="hint", markup=False)
            with Horizontal(id="review-buttons"):
                yield Button("✗ Reject", id="reject", variant="error")
                yield Button("✓ Approve & Run", id="approve", variant="success")
        yield Footer()

    def on_mount(self) -> None:
        self._table = self.query_one("#review-table", DataTable)
        for name in COLUMNS:
            self._table.add_column(name, key=name)
        self._render_table()

    # ----- render -----

    def _render_table(self) -> None:
        if self._table is None:
            return
        self._table.clear()
        for checked, case_id, priority, case_type, title in self.model.rows():
            self._table.add_row(
                "✓" if checked else " ",
                Text(case_id),
                Text(priority),
                Text(case_type),
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
