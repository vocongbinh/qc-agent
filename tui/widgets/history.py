"""HistoryPane — sidebar lịch sử run.

`_index_to_run` là nguồn sự thật cho `selected`; `ListView.index` chỉ được ghi
xuống khi đã mount. Lý do: `ListView.index` là reactive và watcher của nó
cần app đang chạy, nên đọc nó ngoài app là đọc rác.
"""

from __future__ import annotations

from typing import Any, Optional

from textual.app import ComposeResult
from textual.message import Message
from textual.widgets import Label, ListItem, ListView

from tui.history_reader import RunSummary

EMPTY_LABEL = "Chưa có lịch sử"

# Bề dài request trước khi cắt — vừa sidebar hẹp vừa đủ để nhận ra run.
_REQUEST_CHARS = 28


def format_run_line(run: RunSummary) -> str:
    """Hai dòng: mark + request, rồi số đếm."""
    mark = "✓" if run.failed == 0 and run.error == 0 and run.total > 0 else "✗"
    return (
        f"{mark} {run.request[:_REQUEST_CHARS] or '(no request)'}\n"
        f"   {run.total} tests · {run.passed} pass · {run.failed} fail"
    )


def run_item_id(index: int) -> str:
    """Id cho `ListItem`.

    Theo index chứ không theo nội dung run: `short_id` lấy từ tên file nên hai
    report ở hai thư mục khác nhau vẫn có thể trùng, mà id trùng thì Textual
    ném `DuplicateIds` lúc mount.
    """
    return f"run-{index}"


class HistoryPane(ListView):
    empty_label = EMPTY_LABEL

    class RunOpened(Message):
        """Người dùng mở một run trong lịch sử."""

        def __init__(self, run: RunSummary) -> None:
            self.run = run
            super().__init__()

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.runs: list[RunSummary] = []
        self.broken: int = 0
        self._index_to_run: dict[int, RunSummary] = {}
        self._index: Optional[int] = None

    def compose(self) -> ComposeResult:
        yield from ()

    # ----- state (test được không cần terminal) -----

    def set_runs(self, runs: list[RunSummary]) -> None:
        self.runs = list(runs or [])
        self._index_to_run = dict(enumerate(self.runs))
        self._index = None
        self._rebuild_items()

    def set_broken(self, n: int) -> None:
        self.broken = int(n or 0)

    @property
    def selected(self) -> Optional[RunSummary]:
        if self._index is None:
            return None
        return self._index_to_run.get(self._index)

    def select(self, index: int) -> Optional[RunSummary]:
        if index in self._index_to_run:
            self._index = index
            self._write_index(index)
        return self.selected

    # ----- render -----

    def _write_index(self, index: int) -> None:
        if not self.is_mounted:
            return
        try:
            self.index = index
        except Exception:
            pass

    def _rebuild_items(self) -> None:
        if not self.is_mounted:
            return
        self.clear()
        if not self.runs:
            self.append(ListItem(Label(self.empty_label), id="run-empty"))
            return
        for index, run in enumerate(self.runs):
            self.append(ListItem(Label(format_run_line(run)), id=run_item_id(index)))

    # ----- events -----

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        self._index = event.list_view.index

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        """Bấm/Enter một mục → báo `QCTApp` nạp run đó.

    `HistoryPane` tự post message thay vì để app đọc thẳng widget state: app có
    thể subscribe bằng `@on(HistoryPane.RunOpened)` mà không phụ thuộc vào việc
    widget có giữ index hay không.
    """
        self._index = event.list_view.index
        run = self.selected
        if run is not None:
            self.post_message(self.RunOpened(run))
