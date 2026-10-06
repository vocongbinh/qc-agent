"""Selection Modal Screen for TUI (OpenCode / Claude Code style).

Provides an interactive popup selection dialog:
- Number keys (1, 2, 3...) or ↑/↓ + Enter to select
- Esc to close modal
"""

from __future__ import annotations

from typing import Any, Callable, Optional

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, Footer, Label

HINT = "↑↓ navigate · enter or number key to select · esc close"


class SelectionModalScreen(ModalScreen):
    """Selection list modal with quick keyboard interaction."""

    DEFAULT_CSS = """
    SelectionModalScreen {
        align: center middle;
        background: rgba(0, 0, 0, 0.7);
    }

    #modal_container {
        width: 85%;
        max-width: 100;
        height: auto;
        max-height: 80%;
        background: $background;
        border: round #3f3f46;
        padding: 1 2;
    }

    #modal_title {
        text-style: bold;
        color: $text;
        margin-bottom: 1;
    }

    #modal_hint {
        color: $text-muted;
        margin-bottom: 1;
    }

    #modal_table {
        height: auto;
        max-height: 16;
        margin-bottom: 1;
        background: transparent;
        scrollbar-size-horizontal: 1;
        scrollbar-size-vertical: 1;
        scrollbar-background: transparent;
        scrollbar-background-active: transparent;
        scrollbar-background-hover: transparent;
        scrollbar-color: #38bdf8;
        scrollbar-gutter: auto;
    }

    #modal_table > .datatable--even-row {
        background: transparent;
    }

    #modal_table > .datatable--odd-row {
        background: transparent;
    }

    #modal_table > .datatable--cursor {
        background: transparent;
        color: white;
        text-style: bold;
    }

    #modal_table:focus > .datatable--cursor {
        background: transparent;
        color: white;
        text-style: bold;
    }

    #modal_table .datatable--hover {
        background: transparent;
    }

    #modal_actions {
        width: 100%;
        align: right middle;
    }

    #modal_actions Button {
        height: 1;
        min-width: 12;
        padding: 0 2;
        margin-left: 1;
    }
    """

    BINDINGS = [
        ("escape", "dismiss_modal", "Close"),
        ("1", "select_index(0)", "1"),
        ("2", "select_index(1)", "2"),
        ("3", "select_index(2)", "3"),
        ("4", "select_index(3)", "4"),
        ("5", "select_index(4)", "5"),
        ("6", "select_index(5)", "6"),
        ("7", "select_index(6)", "7"),
        ("8", "select_index(7)", "8"),
        ("9", "select_index(8)", "9"),
    ]

    def __init__(
        self,
        title: str,
        options: list[dict[str, Any]],
        on_select: Optional[Callable[[dict[str, Any]], None]] = None,
    ) -> None:
        super().__init__()
        self.title_text = title
        self.options = options
        self.on_select = on_select
        self._table: Optional[DataTable] = None
        self._current_row: int = -1
    def compose(self) -> ComposeResult:
        with Vertical(id="modal_container"):
            yield Label(self.title_text, id="modal_title")
            yield Label(HINT, id="modal_hint")
            table = DataTable(id="modal_table", cursor_type="row")
            table.add_columns("#", "Name", "Description", "Status")
            yield table
            with Horizontal(id="modal_actions"):
                yield Button("Select", id="btn_select", variant="primary")
                yield Button("Close (Esc)", id="btn_close", variant="default")

    def on_mount(self) -> None:
        self._table = self.query_one(DataTable)
        self._current_row = -1
        for i, opt in enumerate(self.options, 1):
            key = str(i)
            name = opt.get("name", "")
            desc = opt.get("desc", "")
            status = opt.get("status", "")
            is_active = any(k in status for k in ["Active", "Signed In", "Configured", "✔"])

            # Row 0 starts selected by default cursor
            is_selected = (i == 1)
            style_main = "bold" if is_selected else "dim"
            style_status = ("bold green" if is_active else "bold") if is_selected else ("green" if is_active else "dim")

            self._table.add_row(
                Text(key, style=style_main),
                Text(name, style=style_main),
                Text(desc, style=style_main),
                Text(status, style=style_status),
                key=opt.get("id", str(i)),
            )
        if self.options:
            self._current_row = 0
        self.set_focus(self._table)

    def _style_row(self, row_idx: int, is_selected: bool) -> None:
        if self._table is None or not (0 <= row_idx < len(self.options)):
            return
        opt = self.options[row_idx]
        status = opt.get("status", "")
        is_active = any(k in status for k in ["Active", "Signed In", "Configured", "✔"])

        if is_selected:
            style_main = "bold"
            style_status = "bold green" if is_active else "bold"
        else:
            style_main = "dim"
            style_status = "green" if is_active else "dim"

        self._table.update_cell_at((row_idx, 0), Text(str(row_idx + 1), style=style_main))
        self._table.update_cell_at((row_idx, 1), Text(opt.get("name", ""), style=style_main))
        self._table.update_cell_at((row_idx, 2), Text(opt.get("desc", ""), style=style_main))
        self._table.update_cell_at((row_idx, 3), Text(status, style=style_status))

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        new_row = event.cursor_row
        if new_row != self._current_row:
            if self._current_row != -1:
                self._style_row(self._current_row, is_selected=False)
            self._style_row(new_row, is_selected=True)
            self._current_row = new_row
    def action_dismiss_modal(self) -> None:
        self.dismiss(None)

    def action_select_index(self, index: int) -> None:
        if 0 <= index < len(self.options):
            selected = self.options[index]
            if self.on_select:
                self.on_select(selected)
            self.dismiss(selected)

    def _select_current_row(self) -> None:
        if self._table is None:
            return
        row_idx = self._table.cursor_row
        if 0 <= row_idx < len(self.options):
            selected = self.options[row_idx]
            if self.on_select:
                self.on_select(selected)
            self.dismiss(selected)

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        self._select_current_row()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn_select":
            self._select_current_row()
        elif event.button.id == "btn_close":
            self.dismiss(None)
