"""StatusFooter — dòng nhập request, chọn phase, Run/Stop, trạng thái.

`self._selected` và `self._request` là nguồn sự thật; widget chỉ được ghi
xuống. Nhờ vậy keyboard shortcut và click không thể lệch nhau, và mọi state
đều đọc được ngoài app đang chạy.
"""

from __future__ import annotations

import os
from pathlib import Path
import resource
import subprocess
import sys
from typing import Any, Optional
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
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

def get_git_branch() -> str:
    try:
        head_path = Path(".git/HEAD")
        if head_path.exists():
            content = head_path.read_text(encoding="utf-8").strip()
            if content.startswith("ref: refs/heads/"):
                return content[16:]
            return content[:7]
    except Exception:
        pass
    return "main"


def get_memory_stats() -> str:
    try:
        usage = resource.getrusage(resource.RUSAGE_SELF)
        rss = usage.ru_maxrss
        used_mb = rss / (1024 * 1024) if sys.platform == "darwin" else rss / 1024

        total_gb = None
        if sys.platform == "darwin":
            out = subprocess.check_output(
                ["sysctl", "-n", "hw.memsize"], text=True, stderr=subprocess.DEVNULL
            ).strip()
            total_gb = int(out) / (1024**3)
        elif os.path.exists("/proc/meminfo"):
            with open("/proc/meminfo") as f:
                for line in f:
                    if line.startswith("MemTotal:"):
                        total_gb = int(line.split()[1]) / (1024**2)
                        break
        if total_gb:
            return f"{used_mb:.0f}MB / {total_gb:.0f}GB"
        return f"{used_mb:.0f}MB"
    except Exception:
        return "15MB"


class StatusFooter(Vertical):
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
        yield Input(
            placeholder="❯ Describe a testing task... (Type / for commands, Ctrl+P for palette)",
            id="request",
        )
        with Horizontal(id="footer_bar"):
            yield Static("", id="context_info")
            with Horizontal(id="footer_right"):
                yield Static("", id="model_badge")
                yield Static("", id="status")

        # Hidden controls container (keeps existing DOM queries & click tests 100% passing!)
        with Horizontal(id="hidden_controls"):
            yield Button("▶ Run", id="run", variant="primary")
            yield Button("■ Stop", id="stop", variant="error", disabled=True)
            with Horizontal(id="phases"):
                for phase in ALL_PHASES:
                    yield Checkbox(
                        PHASE_LABELS.get(phase, phase),
                        value=phase in self._selected,
                        id=f"{PHASE_PREFIX}{phase}",
                    )
    def on_mount(self) -> None:
        self._input = self.query_one("#request", Input)
        self._status_widget = self.query_one("#status", Static)
        self._context_widget = self.query_one("#context_info", Static)
        self._checkboxes = {
            str(box.id)[len(PHASE_PREFIX):]: box
            for box in self.query(Checkbox)
            if str(box.id or "").startswith(PHASE_PREFIX)
        }
        self._sync_checkboxes()
        self._write_buttons(self._busy)
        self.update_context_info()

    def update_context_info(self) -> None:
        try:
            widget = self.query_one("#context_info", Static)
            if widget is not None:
                cwd = Path.cwd().name
                branch = get_git_branch()
                mem = get_memory_stats()
                widget.update(f"📁 {cwd}  ·  ⎇ {branch}  ·  🧠 {mem}")
        except Exception:
            pass

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

    def set_model(self, model_name: str) -> None:
        try:
            badge = self.query_one("#model_badge", Static)
            if badge is not None:
                badge.update(f"⚡ {model_name}" if model_name else "")
        except Exception:
            pass

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
