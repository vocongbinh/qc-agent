from __future__ import annotations

import inspect
import re

import pytest

pytest.importorskip("textual")

from tui.widgets.logpane import DEFAULT_MAX_LINES, LogPane, format_log_line


def _shadowed_names(cls) -> list[str]:
    """Tên method / thuộc tính tự khai trùng internal của base class.

    Đã hỏng thật: `_render` đè `Widget._render` khiến ListView không vẽ được,
    `_running` đè `MessagePump._running` khiến footer tưởng job đang chạy.
    """
    base = {n for klass in cls.__mro__[1:] for n in vars(klass)}
    # Attribute runtime của Textual (vd `MessagePump._running`) không nằm
    # trong class dict nên phải lấy từ một instance của base class.
    base |= set(vars(cls.__mro__[1]()))
    methods = {n for n, v in vars(cls).items() if inspect.isfunction(v)}
    attrs = set(
        re.findall(r"self\.(\w+)\s*(?::[^=\n]+)?=", inspect.getsource(cls.__init__))
    )
    allowed = {"__init__", "compose", "on_mount"} | {n for n in methods if n.startswith("on_")}
    return sorted(n for n in methods | attrs if n in base and n not in allowed)


def test_widget_does_not_shadow_textual_internals():
    assert _shadowed_names(LogPane) == []


def test_format_info_line_has_no_prefix():
    assert format_log_line("info", "hello") == "hello"


def test_format_error_prefixes_bang():
    assert format_log_line("error", "boom") == "! boom"


def test_format_warn_prefixes_triangle():
    assert format_log_line("warn", "careful") == "△ careful"


def test_format_warning_is_alias_of_warn():
    assert format_log_line("warning", "careful") == "△ careful"


def test_format_success_has_no_prefix():
    assert format_log_line("success", "done") == "done"


def test_format_unknown_level_plain():
    assert format_log_line("weird", "msg") == "msg"


def test_format_empty_level_plain():
    assert format_log_line("", "msg") == "msg"
    assert format_log_line(None, "msg") == "msg"  # type: ignore[arg-type]


def test_format_is_case_insensitive():
    assert format_log_line("ERROR", "boom") == "! boom"
    assert format_log_line("Warn", "careful") == "△ careful"


# ---------------------------------------------------------------------------
# LogPane giữ list thuần để test không cần terminal
# ---------------------------------------------------------------------------

def test_logpane_accumulates_lines():
    pane = LogPane()
    pane.append_line("first")
    pane.append_line("second")
    assert pane.lines == ["first", "second"]


def test_logpane_trims_oldest_lines():
    pane = LogPane(max_lines=10)
    for i in range(50):
        pane.append_line(str(i))
    assert pane.lines == [str(i) for i in range(40, 50)]


def test_logpane_exact_capacity_keeps_everything():
    pane = LogPane(max_lines=3)
    for i in range(3):
        pane.append_line(str(i))
    assert pane.lines == ["0", "1", "2"]


def test_logpane_default_max_lines_is_2000():
    assert DEFAULT_MAX_LINES == 2000
    assert LogPane().max_lines == 2000
    pane = LogPane()
    for i in range(2100):
        pane.append_line(str(i))
    assert len(pane.lines) == 2000
    assert pane.lines[0] == "100"
    assert pane.lines[-1] == "2099"


def test_logpane_append_event_formats_before_storing():
    pane = LogPane()
    pane.append_event("error", "boom")
    pane.append_event("info", "hello")
    assert pane.lines == ["! boom", "hello"]


def test_logpane_clear_empties_lines():
    pane = LogPane()
    pane.append_line("x")
    pane.clear()
    assert pane.lines == []


def test_logpane_load_lines_keeps_tail_only():
    pane = LogPane(max_lines=3)
    pane.append_line("stale")
    pane.load_lines([str(i) for i in range(10)])
    assert pane.lines == ["7", "8", "9"]


def test_logpane_load_lines_shorter_than_max_keeps_all():
    pane = LogPane(max_lines=10)
    pane.load_lines(["a", "b"])
    assert pane.lines == ["a", "b"]


def test_logpane_load_lines_empty_clears_previous():
    pane = LogPane()
    pane.append_line("old")
    pane.load_lines([])
    assert pane.lines == []


def test_logpane_works_before_mount():
    """`self._log` chỉ có sau on_mount — mọi method phải an toàn trước đó."""
    pane = LogPane()
    assert pane._log is None
    pane.append_line("pre-mount")
    pane.append_event("warn", "pre-mount")
    pane.load_lines(["x", "y"])
    pane.clear()
    assert pane.lines == []


def test_logpane_compose_yields_single_richlog():
    from textual.widgets import RichLog

    pane = LogPane()
    widgets = list(pane.compose())
    assert len(widgets) == 1
    log = widgets[0]
    assert isinstance(log, RichLog)
    assert log.highlight is False
    assert log.markup is False
    assert log.wrap is True


async def test_logpane_mounted_path_keeps_lines_in_sync():
    """Guard `_log is not None` phải là guard thật, không phải nhánh chết."""
    from textual.app import App

    class _App(App):
        def compose(self):
            yield LogPane(max_lines=5)

    app = _App()
    async with app.run_test():
        pane = app.query_one(LogPane)
        assert pane._log is not None
        pane.append_line("mounted-1")
        pane.append_event("error", "mounted-2")
        assert pane.lines == ["mounted-1", "! mounted-2"]
        pane.clear()
        assert pane.lines == []
