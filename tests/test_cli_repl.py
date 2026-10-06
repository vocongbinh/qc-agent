from prompt_toolkit.document import Document

from cli.repl import (
    SlashCommandCompleter,
    _bottom_toolbar,
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


def test_bottom_toolbar_returns_string():
    tb = _bottom_toolbar()
    assert "π" in tb
    assert "Ready" in tb


def test_get_git_branch():
    branch = _get_git_branch()
    assert isinstance(branch, str)
    assert len(branch) > 0
