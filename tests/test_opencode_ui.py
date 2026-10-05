"""Tests cho các component UI OpenCode style (Autocomplete, Command Palette, Footer Model Badge)."""

import pytest
from textual.app import App, ComposeResult
from textual.widgets import Input

from tui.app import QCTApp
from tui.widgets.command_palette import CommandPaletteScreen
from tui.widgets.slash_autocomplete import SlashAutocomplete, SLASH_COMMANDS


class AutocompleteTestApp(App):
    def compose(self) -> ComposeResult:
        yield SlashAutocomplete(id="test_ac")


async def test_slash_autocomplete_filtering():
    app = AutocompleteTestApp()
    async with app.run_test() as pilot:
        ac = app.query_one("#test_ac", SlashAutocomplete)

        # Chưa gõ hoặc gõ text thường -> ẩn
        assert ac.update_query("chạy test") is False
        assert ac.display is False

        # Gõ / -> hiện tất cả
        assert ac.update_query("/") is True
        assert ac.display is True
        assert len(ac.filtered) == len(SLASH_COMMANDS)

        # Gõ /m -> chỉ còn /model
        assert ac.update_query("/m") is True
        assert len(ac.filtered) == 1
        assert ac.current_command() == "/model"

        # Gõ /l -> chỉ còn /login
        assert ac.update_query("/l") is True
        assert len(ac.filtered) == 1
        assert ac.current_command() == "/login"

        # Lệnh lạ -> ẩn
        assert ac.update_query("/xyz") is False
        assert ac.display is False


async def test_slash_autocomplete_navigation():
    app = AutocompleteTestApp()
    async with app.run_test() as pilot:
        ac = app.query_one("#test_ac", SlashAutocomplete)
        ac.update_query("/")

        initial = ac.current_command()
        ac.select_next()
        next_cmd = ac.current_command()
        assert initial != next_cmd

        ac.select_prev()
        assert ac.current_command() == initial


class PaletteTestApp(App):
    def __init__(self, actions):
        super().__init__()
        self.actions = actions

    def on_mount(self):
        self.push_screen(CommandPaletteScreen(self.actions))


async def test_command_palette_filtering():
    actions = [
        {"id": "run", "title": "Run Test", "desc": "Chạy test"},
        {"id": "model", "title": "Select Model", "desc": "Chọn model"},
    ]
    app = PaletteTestApp(actions)
    async with app.run_test() as pilot:
        palette = app.screen
        assert isinstance(palette, CommandPaletteScreen)
        assert len(palette.filtered_actions) == 2

        # Giả lập gõ 'model'
        inp = palette.query_one("#palette_input", Input)
        inp.value = "model"
        palette.on_input_changed(Input.Changed(inp, "model"))
        await pilot.pause()

        assert len(palette.filtered_actions) == 1
        assert palette.filtered_actions[0]["id"] == "model"


class FakeGraph:
    def stream(self, *args, **kwargs):
        return []


async def test_tui_slash_autocomplete_and_tab(tmp_path):
    a = QCTApp(graph=FakeGraph(), reports_dir=tmp_path)
    async with a.run_test() as pilot:
        a.action_focus_request()
        await pilot.pause()

        inp = a.query_one("#request", Input)
        inp.value = "/m"
        a.on_input_changed(Input.Changed(inp, "/m"))
        await pilot.pause()

        assert a.slash_ac.display is True
        assert a.slash_ac.current_command() == "/model"

        # Nhấn Tab -> autocomplete thành "/model "
        await pilot.press("tab")
        await pilot.pause()

        assert inp.value == "/model "
        assert a.slash_ac.display is False


async def test_tui_ctrl_p_opens_command_palette(tmp_path):
    a = QCTApp(graph=FakeGraph(), reports_dir=tmp_path)
    async with a.run_test() as pilot:
        await pilot.press("ctrl+p")
        await pilot.pause()

        assert isinstance(a.screen, CommandPaletteScreen)

        # Đóng bằng Esc
        await pilot.press("escape")
        await pilot.pause()
        assert not isinstance(a.screen, CommandPaletteScreen)
