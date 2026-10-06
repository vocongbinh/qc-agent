from prompt_toolkit.document import Document

from cli.repl import (
    SlashCommandCompleter,
    _format_top_left,
    _physical_rows,
    _wrapped_rows_above_cursor,
    _get_git_branch,
    _handle_slash_command,
)


def test_slash_command_completer():
    completer = SlashCommandCompleter()
    doc = Document("/")
    completions = list(completer.get_completions(doc, None))
    cmds = [c.text for c in completions]
    assert "/test" in cmds
    assert "/help" in cmds
    assert "/model" in cmds


def test_slash_command_completer_filtered():
    completer = SlashCommandCompleter()
    doc = Document("/m")
    completions = list(completer.get_completions(doc, None))
    cmds = [c.text for c in completions]
    assert cmds == ["/model"]


def test_handle_slash_command_exit():
    assert _handle_slash_command("/exit") is False
    assert _handle_slash_command("/quit") is False


def test_handle_slash_command_help():
    assert _handle_slash_command("/help") is True


def test_handle_slash_command_model():
    assert _handle_slash_command("/model") is True
    assert _handle_slash_command("/model gemini-2.5-flash") is True


def test_format_top_left_returns_banner():
    tb = _format_top_left("test")
    assert "π" in tb
    assert tb.startswith("╭─ [TEST]")


def test_get_git_branch():
    branch = _get_git_branch()
    assert isinstance(branch, str)
    assert len(branch) > 0


def test_format_top_left_truncates_on_narrow_terminal(monkeypatch):
    import shutil
    from collections import namedtuple

    Size = namedtuple("Size", ["columns", "lines"])
    monkeypatch.setattr(shutil, "get_terminal_size", lambda *a, **k: Size(30, 24))
    top = _format_top_left("test")
    # Must never exceed terminal width (no wrap -> no broken box on resize)
    assert len(top) <= 30
    assert top.startswith("╭─ [TEST]")


def test_physical_rows_counts_terminal_wrap():
    assert _physical_rows(0, 40) == 1
    assert _physical_rows(40, 40) == 1
    assert _physical_rows(41, 40) == 2
    assert _physical_rows(80, 40) == 2


def test_wrapped_rows_above_cursor_includes_reflow():
    class Cell:
        def __init__(self, width=1):
            self.width = width

    class Screen:
        def __init__(self):
            self.height = 3
            # A 100-column top border, then the input row, then the bottom border.
            self.data_buffer = [
                {i: Cell() for i in range(100)},
                {i: Cell() for i in range(12)},
                {i: Cell() for i in range(100)},
            ]

    # Cursor sits on the input row. After shrinking to 40 columns the top
    # border occupies 3 physical rows, so the cursor is 3 rows below the frame.
    assert _wrapped_rows_above_cursor(Screen(), 40, cursor_x=6, cursor_y=1) == 3
