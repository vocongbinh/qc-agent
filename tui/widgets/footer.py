"""StatusFooter — dòng nhập request, chọn phase, Run/Stop, trạng thái.

`self._selected` và `self._request` là nguồn sự thật; widget chỉ được ghi
xuống. Nhờ vậy keyboard shortcut và click không thể lệch nhau, và mọi state
đều đọc được ngoài app đang chạy.
"""

from __future__ import annotations

from typing import Any, Optional

from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.widgets import Button, Input, Static
from textual.widgets._checkbox import Checkbox

from tui.filters import ALL_PHASES

PHASE_LABELS: dict[str, str] = {
    "api": "API",
    "ui": "UI",
    "chaos": "Chaos",
    "performance": "Perf",
}

PHASE_PREFIX = "phase-"


def _as_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def format_elapsed(seconds: Any) -> str:
    """`0`→`0:00`, `65`→`1:05`, `3661`→`1:01:01`."""
    total = max(0, _as_int(seconds))
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


class StatusFooter(Horizontal):
    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._selected: set[str] = {"api"}
        self._request: str = ""
        self._status: str = ""
        self._phase: str = ""
        self._done: int = 0
        self._total: int = 0
        self._busy: bool = False
        self._input: Optional[Input] = None
        self._status_widget: Optional[Static] = None
        self._checkboxes: dict[str, Checkbox] = {}

    def compose(self) -> ComposeResult:
        yield Input(placeholder="Mô tả nhiệm vụ test…", id="request")
        with Horizontal(id="phases"):
            for phase in ALL_PHASES:
                yield Checkbox(
                    PHASE_LABELS.get(phase, phase),
                    value=phase in self._selected,
                    id=f"{PHASE_PREFIX}{phase}",
                )
        yield Button("▶ Run", id="run", variant="primary")
        yield Button("■ Stop", id="stop", variant="error", disabled=True)
        yield Static("", id="status")

    def on_mount(self) -> None:
        self._input = self.query_one("#request", Input)
        self._status_widget = self.query_one("#status", Static)
        self._checkboxes = {
            str(box.id)[len(PHASE_PREFIX):]: box
            for box in self.query(Checkbox)
            if str(box.id or "").startswith(PHASE_PREFIX)
        }
        self._sync_checkboxes()
        self._write_buttons(self._busy)

    # ----- state (test được không cần terminal) -----

    def selected_phases(self) -> list[str]:
        return [p for p in ALL_PHASES if p in self._selected]

    def toggle_phase(self, phase: str) -> None:
        if phase not in PHASE_LABELS:
            return
        if phase in self._selected:
            self._selected.discard(phase)
        else:
            self._selected.add(phase)
        self._sync_checkboxes()

    def set_all_phases(self, value: bool) -> None:
        self._selected = set(ALL_PHASES) if value else set()
        self._sync_checkboxes()

    def request(self) -> str:
        return self._request

    def set_request(self, text: str) -> None:
        self._request = text or ""
        if self._input is not None:
            try:
                self._input.value = self._request
            except Exception:
                pass

    def status(self) -> str:
        return self._status

    def set_status(self, text: str) -> None:
        self._status = text or ""
        if self._status_widget is not None:
            self._status_widget.update(self._status)

    def set_progress(self, phase: str, done: Any, total: Any) -> None:
        self._phase = phase or ""
        self._done = _as_int(done)
        self._total = _as_int(total)

    def progress_text(self) -> str:
        if not self._total:
            return ""
        return f"{self._phase} {self._done}/{self._total}"

    def set_running(self, running: bool) -> None:
        self._busy = bool(running)
        self._write_buttons(self._busy)

    def running(self) -> bool:
        return self._busy

    # ----- ghi xuống widget -----

    def _sync_checkboxes(self) -> None:
        for phase, box in self._checkboxes.items():
            try:
                box.value = phase in self._selected
            except Exception:
                pass

    def _write_buttons(self, running: bool) -> None:
        # Chưa mount thì `query_one` ném NoMatches — đây là đường đi bình
        # thường lúc app startup, không phải lỗi.
        try:
            self.query_one("#run", Button).disabled = running
            self.query_one("#stop", Button).disabled = not running
        except Exception:
            pass

    # ----- events -----

    def on_checkbox_changed(self, event: Checkbox.Changed) -> None:
        # Chiều ngược lại của `_sync_checkboxes`: click của user.
        raw_id = str(event.checkbox.id or "")
        if not raw_id.startswith(PHASE_PREFIX):
            return
        phase = raw_id[len(PHASE_PREFIX):]
        if phase not in PHASE_LABELS:
            return
        if event.value:
            self._selected.add(phase)
        else:
            self._selected.discard(phase)

    def on_input_changed(self, event: Input.Changed) -> None:
        self._request = event.value
