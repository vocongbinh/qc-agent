"""CaseTable — bảng kết quả test case, lọc theo phase đã chọn.

Lưu ý tên thuộc tính: kết quả thô nằm ở `results`, KHÔNG phải `rows` —
`DataTable.rows` là dict lưu cell của chính Textual, ghi đè vào sẽ làm hỏng
bảng (`add_column` gọi `self.rows.keys()`).
"""

from __future__ import annotations

from typing import Any, Optional

from textual.app import ComposeResult
from textual.widgets import DataTable

from tui.filters import filter_by_phases

COLUMNS = ("ID", "Type", "Status", "ms", "Title")

_STATUS_STYLE = {
    "passed": "green",
    "failed": "red",
    "error": "red",
    "blocked": "yellow",
    "skipped": "dim",
    "running": "cyan",
}

_STATUS_MARK = {
    "passed": "✓",
    "failed": "✗",
    "error": "!",
    "blocked": "□",
    "skipped": "–",
    "running": "⋯",
}


def _key(value: Optional[str]) -> str:
    return (value or "").strip().lower()


def status_style(status: Optional[str]) -> str:
    return _STATUS_STYLE.get(_key(status), "dim")


def result_key(result: dict[str, Any]) -> str:
    """Khoá của một dòng. Kết quả không có `id` không định danh được → `""`."""
    return str(result.get("id") or "")


def format_duration(value: Any) -> str:
    """Ô `ms`: số thì làm tròn, không phải số (kể cả None) thì để trống."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return ""
    return f"{round(float(value))}"


def format_row(result: dict[str, Any], mark: str) -> tuple[str, str, str, str, str]:
    """5 ô theo thứ tự `COLUMNS`."""
    status = str(result.get("status") or "")
    return (
        result_key(result),
        str(result.get("type") or "api"),
        f"{mark} {status}".strip(),
        format_duration(result.get("duration_ms")),
        str(result.get("title") or result.get("error_message") or ""),
    )


class CaseTable(DataTable):
    """`results` là nguồn sự thật; `_render_rows` chỉ vẽ lại từ nó."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.results: list[dict[str, Any]] = []
        self._phase_filter: list[str] = []

    def compose(self) -> ComposeResult:
        yield from ()

    def on_mount(self) -> None:
        # `ScrollView.on_mount` mới là nơi tính scrollbar — không được bỏ.
        super().on_mount()
        for name in COLUMNS:
            self.add_column(name, key=name)
        self._render_rows()

    # ----- state (test được không cần terminal) -----

    def status_mark(self, status: Optional[str]) -> str:
        return _STATUS_MARK.get(_key(status), "·")

    def set_phase_filter(self, phases: list[str]) -> None:
        # Giữ nguyên giá trị thô: `filter_by_phases` mới là nơi quyết định
        # rỗng = tất cả và "banana" = không có dòng nào.
        self._phase_filter = list(phases or [])
        self._render_rows()

    def visible_results(self) -> list[dict[str, Any]]:
        return filter_by_phases(self.results, self._phase_filter)

    def visible_ids(self) -> list[str]:
        return [result_key(r) for r in self.visible_results()]

    def add_result(self, result: dict[str, Any]) -> None:
        """Upsert theo `id` — executor có thể báo lại cùng một case."""
        key = result_key(result)
        for i, existing in enumerate(self.results):
            if result_key(existing) == key:
                self.results[i] = {**existing, **result}
                break
        else:
            self.results.append(dict(result))
        self._render_rows()

    def clear_rows(self) -> None:
        self.results = []
        self._render_rows()

    # ----- render -----

    def _render_rows(self) -> None:
        # Chưa mount thì `add_row` ném NoActiveAppError — bỏ qua, state đã đúng.
        if not self.is_mounted:
            return
        self.clear()
        for result in self.visible_results():
            cells = format_row(result, self.status_mark(result.get("status")))
            key = cells[0]
            self.add_row(*cells, **({"key": key} if key else {}))
