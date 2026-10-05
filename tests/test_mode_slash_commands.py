import pytest
from textual.widgets import Input

from tui.app import QCTApp
from tui.widgets.slash_autocomplete import SLASH_COMMANDS


def test_slash_autocomplete_contains_all_three_modes():
    commands = {c["cmd"]: c["desc"] for c in SLASH_COMMANDS}
    assert "/test" in commands
    assert "/debug" in commands
    assert "/fix" in commands


class FakeGraph:
    def stream(self, state, config, stream_mode="values"):
        yield {"current_step": "reporter_done"}


@pytest.mark.asyncio
async def test_slash_commands_switch_modes(tmp_path):
    app = QCTApp(graph=FakeGraph(), reports_dir=tmp_path)
    async with app.run_test() as pilot:
        app.action_focus_request()
        await pilot.pause()
        inp = app.query_one("#request", Input)

        # 1. Switch to /debug
        inp.value = "/debug"
        await pilot.press("enter")
        await pilot.pause()
        assert app.footer.get_mode() == "debug"
        assert "[DEBUG]" in inp.placeholder
        assert any("DEBUG" in line for line in app.log_pane.lines)

        # 2. Switch to /fix
        inp.value = "/fix"
        await pilot.press("enter")
        await pilot.pause()
        assert app.footer.get_mode() == "fix"
        assert "[FIX]" in inp.placeholder
        assert any("FIX" in line for line in app.log_pane.lines)

        # 3. Switch back to /test
        inp.value = "/test"
        await pilot.press("enter")
        await pilot.pause()
        assert app.footer.get_mode() == "test"
        assert "[TEST]" in inp.placeholder
        assert any("TEST" in line for line in app.log_pane.lines)
