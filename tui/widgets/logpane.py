"""LogPane — log realtime của một run.

Giữ `lines` (list thuần) làm nguồn sự thật để test được không cần terminal;
`RichLog` chỉ là bản render. Trước `on_mount` thì `self._log` là None và mọi
method vẫn phải chạy được.
"""

from __future__ import annotations

from typing import Any, Optional

from rich.text import Text
from textual.app import ComposeResult
from textual.widgets import RichLog, Static

from tui.widgets.timeline import (
    create_assistant_message_panel,
    create_plan_card_panel,
    create_summary_panel,
    create_thinking_panel,
    create_tool_card_panel,
    create_user_prompt_panel,
)
DEFAULT_MAX_LINES = 2000

# Level nào có mark riêng. Level lạ (hoặc None) coi như info: không mark.
_LEVEL_MARK = {
    "error": "! ",
    "warn": "△ ",
    "warning": "△ ",
    "success": "",
    "info": "",
}

# Màu cho RichLog. `lines` chỉ chứa text thuần — style không nằm trong state.
_LEVEL_STYLE = {
    "error": "bold red",
    "warn": "yellow",
    "warning": "yellow",
    "success": "green",
    "info": "",
}


def _level_key(level: Optional[str]) -> str:
    return (level or "").strip().lower()


def format_log_line(level: Optional[str], text: str) -> str:
    """`error` → `! boom`; mọi level khác giữ nguyên text."""
    return _LEVEL_MARK.get(_level_key(level), "") + (text or "")


class LogPane(Static):
    """Bọc `RichLog` để scroll, đồng thời giữ list text cho test."""

    def __init__(self, max_lines: int = DEFAULT_MAX_LINES, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.max_lines = max_lines
        self.lines: list[str] = []
        # Chỉ gán trong `on_mount` — compose() chạy trước khi widget có app.
        self._log: Optional[RichLog] = None

    def compose(self) -> ComposeResult:
        yield RichLog(highlight=False, markup=False, wrap=True)

    def on_mount(self) -> None:
        self._log = self.query_one(RichLog)

    # ----- state (test được không cần terminal) -----

    def append_line(self, text: str, style: str = "") -> None:
        self.lines.append(text)
        if len(self.lines) > self.max_lines:
            del self.lines[: len(self.lines) - self.max_lines]
        if self._log is not None:
            self._log.write(Text(text, style=style) if style else text)

    def append_event(self, level: str, text: str) -> None:
        self.append_line(
            format_log_line(level, text), style=_LEVEL_STYLE.get(_level_key(level), "")
        )

    def append_user_prompt(self, prompt: str) -> None:
        line = f"▶ Starting: {prompt}"
        self.lines.append(line)
        if len(self.lines) > self.max_lines:
            del self.lines[: len(self.lines) - self.max_lines]
        if self._log is not None:
            self._log.write(create_user_prompt_panel(prompt))

    def append_assistant_message(self, content: str) -> None:
        self.lines.append(f"🤖 {content}")
        if len(self.lines) > self.max_lines:
            del self.lines[: len(self.lines) - self.max_lines]
        if self._log is not None:
            self._log.write(create_assistant_message_panel(content))

    def append_thinking(self, title: str, steps: list[str]) -> None:
        line = f"⚡ Thinking: {title}"
        self.lines.append(line)
        if len(self.lines) > self.max_lines:
            del self.lines[: len(self.lines) - self.max_lines]
        if self._log is not None:
            self._log.write(create_thinking_panel(title, steps))

    def append_plan_card(self, plan: dict[str, Any], count: int) -> None:
        line = f"✓ Test Plan ready: {count} cases — review required"
        self.lines.append(line)
        if len(self.lines) > self.max_lines:
            del self.lines[: len(self.lines) - self.max_lines]
        if self._log is not None:
            self._log.write(create_plan_card_panel(plan, count))

    def append_tool_card(self, result: dict[str, Any], text_line: str) -> None:
        self.lines.append(text_line)
        if len(self.lines) > self.max_lines:
            del self.lines[: len(self.lines) - self.max_lines]
        if self._log is not None:
            self._log.write(create_tool_card_panel(result))

    def append_summary_card(self, summary: str, report_path: str) -> None:
        text = summary or "Run completed."
        self.lines.append(text)
        if report_path:
            self.lines.append(f"Report: {report_path}")
        if len(self.lines) > self.max_lines:
            del self.lines[: len(self.lines) - self.max_lines]
        if self._log is not None:
            self._log.write(create_summary_panel(summary, report_path))

    def clear(self) -> None:
        self.lines = []
        if self._log is not None:
            self._log.clear()

    def load_lines(self, lines: list[str]) -> None:
        self.clear()
        for line in (lines or [])[-self.max_lines:]:
            self.append_line(line)
