"""Live Animated Thinking Bar (OpenCode Spinner Style).

Hiển thị thanh suy luận động với spinner xoay tròn liên tục và thời gian nhảy giây:
⠋ Thinking: Analyzing test requirements... (3.2s)
Nằm ngay phía trên ô gõ Prompt theo chuẩn OpenCode routes/session/index.tsx.
"""

from __future__ import annotations

import time
from typing import Any, Optional

from rich.text import Text
from textual.app import ComposeResult
from textual.timer import Timer
from textual.widgets import Static

SPINNER_FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]


class ThinkingBar(Static):
    """Thanh hiển thị Thinking động liên tục ngay trên ô nhập lệnh."""

    DEFAULT_CSS = """
    ThinkingBar {
        width: 100%;
        height: 1;
        padding: 0 1;
        margin-bottom: 0;
        background: transparent;
        display: none;
    }
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.message: str = "Thinking..."
        self.start_time: float = 0.0
        self.frame_index: int = 0
        self._timer: Optional[Timer] = None
        self.is_thinking: bool = False

    def compose(self) -> ComposeResult:
        yield from ()

    def start_thinking(self, message: str = "Thinking...") -> None:
        """Bắt đầu animation thinking động."""
        self.message = message
        self.start_time = time.time()
        self.frame_index = 0
        self.is_thinking = True
        self.display = True
        self._render_frame()

        if self._timer is None:
            self._timer = self.set_interval(0.08, self._tick)
        else:
            self._timer.resume()

    def update_step(self, message: str) -> None:
        """Cập nhật nội dung bước suy luận hiện tại."""
        self.message = message
        self._render_frame()

    def stop_thinking(self) -> None:
        """Dừng animation thinking."""
        self.is_thinking = False
        self.display = False
        if self._timer is not None:
            self._timer.pause()
        self.update("")

    def _tick(self) -> None:
        if not self.is_thinking:
            return
        self.frame_index = (self.frame_index + 1) % len(SPINNER_FRAMES)
        self._render_frame()

    def _render_frame(self) -> None:
        frame = SPINNER_FRAMES[self.frame_index]
        elapsed = time.time() - self.start_time if self.start_time else 0.0
        
        content = Text()
        content.append(f"{frame} ", style="bold yellow")
        content.append(f"Thinking: {self.message} ", style="italic cyan")
        content.append(f"({elapsed:.1f}s)", style="dim white")
        self.update(content)
