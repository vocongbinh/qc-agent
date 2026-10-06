"""Slash Commands Autocomplete Popup (OpenCode / Claude Code style).

Automatically pops up when typing '/' in the request prompt:
- Tab: Autocomplete selected command
- ↑↓: Navigate commands
- Enter: Execute directly
- Esc: Close popup
"""

from __future__ import annotations

from typing import Any, Optional

from rich.text import Text
from textual.app import ComposeResult
from textual.widgets import DataTable, Static

SLASH_COMMANDS = [
    {
        "cmd": "/test",
        "desc": "Switch to Test mode (Plan & run API/UI/Chaos/Perf tests)",
    },
    {
        "cmd": "/debug",
        "desc": "Switch to Debug mode (Root Cause Analysis & bug reproduction)",
    },
    {
        "cmd": "/fix",
        "desc": "Switch to Fix mode (Autonomous Search/Replace code patching)",
    },
    {
        "cmd": "/model",
        "desc": "Select AI model (Gemini 2.5 Flash/Pro, Claude, GPT-4o)",
    },
    {
        "cmd": "/login",
        "desc": "Sign in to Google Antigravity / Gemini Subscription",
    },
    {
        "cmd": "/history",
        "desc": "Browse previous test execution runs",
    },
    {
        "cmd": "/clear",
        "desc": "Clear current session timeline",
    },
    {
        "cmd": "/status",
        "desc": "Inspect active mode, LLM provider & model",
    },
    {
        "cmd": "/help",
        "desc": "View commands and keyboard shortcuts",
    },
]


class SlashAutocomplete(Static):
    """Slash command suggestions popup widget."""

    DEFAULT_CSS = """
    SlashAutocomplete {
        width: 1fr;
        height: auto;
        max-height: 8;
        background: transparent;
        border: none;
        padding: 0 1;
        display: none;
    }

    #ac_table {
        width: 1fr;
        height: auto;
        max-height: 7;
        background: transparent;
        border: none;
        overflow-x: hidden;
        scrollbar-size-horizontal: 0;
        scrollbar-size-vertical: 1;
        scrollbar-background: transparent;
        scrollbar-background-active: transparent;
        scrollbar-background-hover: transparent;
        scrollbar-color: #38bdf8;
        scrollbar-gutter: auto;
    }
    #ac_table > .datatable--even-row {
        background: transparent;
    }

    #ac_table > .datatable--odd-row {
        background: transparent;
    }

    #ac_table > .datatable--cursor {
        background: transparent;
        color: white;
        text-style: bold;
    }

    #ac_table:focus > .datatable--cursor {
        background: transparent;
        color: white;
        text-style: bold;
    }

    #ac_table .datatable--hover {
        background: transparent;
    }
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.filtered: list[dict[str, Any]] = list(SLASH_COMMANDS)
        self._selected_index: int = 0
        self._table: Optional[DataTable] = None

    def compose(self) -> ComposeResult:
        table = DataTable(id="ac_table", cursor_type="none", show_header=False)
        yield table

    def on_mount(self) -> None:
        self._table = self.query_one(DataTable)
        self._table.add_columns("Command", "Description")
        self._refresh_table()

    def _refresh_table(self) -> None:
        if self._table is None:
            return
        self._table.clear()
        for i, item in enumerate(self.filtered):
            is_selected = (i == self._selected_index)
            if is_selected:
                cmd_text = Text(f"❯ ✦ {item['cmd']:<10}", style="bold")
                desc_text = Text(item["desc"], style="bold")
            else:
                cmd_text = Text(f"  ✦ {item['cmd']:<10}", style="dim")
                desc_text = Text(item["desc"], style="dim")
            self._table.add_row(cmd_text, desc_text, key=str(i))

        if self.filtered:
            self._selected_index = min(self._selected_index, len(self.filtered) - 1)
            self._table.move_cursor(row=self._selected_index)

    def update_query(self, text: str) -> bool:
        """Update filter based on current user input."""
        stripped = (text or "").strip()
        if not stripped.startswith("/"):
            self.display = False
            return False

        # Hide suggestions once typing arguments after space
        if " " in (text or ""):
            self.display = False
            return False

        cmd_part = stripped.lower()
        self.filtered = [c for c in SLASH_COMMANDS if c["cmd"].startswith(cmd_part)]

        if not self.filtered:
            self.display = False
            return False

        self.display = True
        self._selected_index = 0
        self._refresh_table()
        return True

    def select_next(self) -> None:
        if self.filtered and self._table:
            self._selected_index = (self._selected_index + 1) % len(self.filtered)
            self._table.move_cursor(row=self._selected_index)
            self._refresh_table()

    def select_prev(self) -> None:
        if self.filtered and self._table:
            self._selected_index = (self._selected_index - 1) % len(self.filtered)
            self._table.move_cursor(row=self._selected_index)
            self._refresh_table()

    def current_command(self) -> str | None:
        if 0 <= self._selected_index < len(self.filtered):
            return self.filtered[self._selected_index]["cmd"]
        return None

    def hide(self) -> None:
        self.display = False
