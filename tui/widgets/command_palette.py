"""Command Palette Modal Screen for TUI (OpenCode / VS Code style).

Open via Ctrl+P or F1:
- Quick search across all actions in QC Agent
- Navigate with ↑↓, execute with Enter, dismiss with Esc
"""

from __future__ import annotations

from typing import Any, Callable, Optional

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import DataTable, Input, Label


class CommandPaletteScreen(ModalScreen):
    """Command Palette action search popup."""

    DEFAULT_CSS = """
    CommandPaletteScreen {
        align: center top;
        background: rgba(0, 0, 0, 0.7);
        padding-top: 3;
    }

    #palette_container {
        width: 75%;
        max-width: 85;
        height: auto;
        max-height: 24;
        background: $background;
        border: round #3f3f46;
        padding: 1 2;
    }

    #palette_input {
        margin-bottom: 1;
        border: round #3f3f46;
        background: transparent;
    }

    #palette_input:focus {
        border: round $primary;
    }

    #palette_table {
        height: auto;
        max-height: 14;
        background: transparent;
    }

    #palette_table > .datatable--even-row {
        background: transparent;
    }

    #palette_table > .datatable--odd-row {
        background: transparent;
    }

    #palette_table > .datatable--cursor {
        background: transparent;
        color: white;
        text-style: bold;
    }

    #palette_table:focus > .datatable--cursor {
        background: transparent;
        color: white;
        text-style: bold;
    }

    #palette_table .datatable--hover {
        background: transparent;
    }

    #palette_hint {
        color: $text-muted;
        margin-top: 1;
    }
    """

    BINDINGS = [
        ("escape", "dismiss_palette", "Close"),
        ("up", "move_up", "Up"),
        ("down", "move_down", "Down"),
    ]

    def __init__(
        self,
        actions: list[dict[str, Any]],
        on_run_action: Optional[Callable[[dict[str, Any]], None]] = None,
    ) -> None:
        super().__init__()
        self.all_actions = actions
        self.filtered_actions: list[dict[str, Any]] = list(actions)
        self.on_run_action = on_run_action
        self._table: Optional[DataTable] = None
        self._input: Optional[Input] = None

    def compose(self) -> ComposeResult:
        with Vertical(id="palette_container"):
            yield Input(placeholder="> Type a command or search action...", id="palette_input")
            table = DataTable(id="palette_table", cursor_type="row")
            yield table
            yield Label("↑↓ navigate · enter execute · esc close", id="palette_hint")

    def on_mount(self) -> None:
        self._table = self.query_one(DataTable)
        self._table.add_columns("Command / Action", "Description", "Shortcut")
        self._input = self.query_one(Input)
        self._populate_table()
        self.set_focus(self._input)

    def _populate_table(self) -> None:
        if self._table is None:
            return
        self._table.clear()
        for i, act in enumerate(self.filtered_actions):
            title = act.get("title", "")
            desc = act.get("desc", "")
            keybind = act.get("shortcut", "")
            self._table.add_row(
                Text(title, style="bold"),
                Text(desc, style="dim"),
                Text(keybind, style="dim"),
                key=str(i),
            )
        if self.filtered_actions:
            self._table.move_cursor(row=0)

    def on_input_changed(self, event: Input.Changed) -> None:
        query = (event.value or "").strip().lower()
        if not query:
            self.filtered_actions = list(self.all_actions)
        else:
            self.filtered_actions = [
                act for act in self.all_actions
                if query in act.get("title", "").lower() or query in act.get("desc", "").lower()
            ]
        self._populate_table()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self._execute_current()

    def action_move_up(self) -> None:
        if self._table and self.filtered_actions:
            cur = self._table.cursor_row
            if cur > 0:
                self._table.move_cursor(row=cur - 1)

    def action_move_down(self) -> None:
        if self._table and self.filtered_actions:
            cur = self._table.cursor_row
            if cur < len(self.filtered_actions) - 1:
                self._table.move_cursor(row=cur + 1)

    def _execute_current(self) -> None:
        if not self.filtered_actions or self._table is None:
            return
        idx = self._table.cursor_row
        if 0 <= idx < len(self.filtered_actions):
            action = self.filtered_actions[idx]
            if self.on_run_action:
                self.on_run_action(action)
            self.dismiss(action)

    def action_dismiss_palette(self) -> None:
        self.dismiss(None)
