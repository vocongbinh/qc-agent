from __future__ import annotations

import inspect
import re

import pytest

pytest.importorskip("textual")

from tui.history_reader import RunSummary
from tui.widgets.history import HistoryPane, format_run_line


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
    # Đã hỏng thật: đặt tên `_render` đè `Widget._render`, ListView trả None
    # thay vì Visual nên app chết lúc vẽ.
    assert _shadowed_names(HistoryPane) == []


def _run(
    request: str = "x",
    total: int = 1,
    passed: int = 1,
    failed: int = 0,
    error: int = 0,
    ts: str = "20260101_000000",
) -> RunSummary:
    return RunSummary(
        path=f"/r/report_{ts}.json",
        timestamp=ts,
        request=request,
        plan_title="P",
        scope="api",
        total=total,
        passed=passed,
        failed=failed,
        error=error,
        skipped=0,
        duration_ms=10.0,
        details=[],
    )


# ---------------------------------------------------------------------------
# format_run_line
# ---------------------------------------------------------------------------

def test_format_run_line_shows_counts():
    line = format_run_line(_run("Test auth", 12, 10, 2))
    assert "12 tests" in line
    assert "10 pass" in line
    assert "2 fail" in line


def test_format_run_line_marks_all_pass():
    assert "✓" in format_run_line(_run("x", 5, 5, 0))


def test_format_run_line_marks_failures():
    assert "✗" in format_run_line(_run("x", 5, 3, 2))


def test_format_run_line_error_counts_as_failure():
    assert "✗" in format_run_line(_run("x", 5, 5, 0, error=2))


def test_format_run_line_empty_run_is_not_a_pass():
    assert "✗" in format_run_line(_run("x", 0, 0, 0))


def test_format_run_line_is_two_lines():
    assert len(format_run_line(_run("x", 1, 1, 0)).splitlines()) == 2


def test_format_run_line_truncates_long_request():
    line = format_run_line(_run("r" * 40))
    first = line.splitlines()[0]
    assert first.count("r") == 28


def test_format_run_line_keeps_short_request_intact():
    assert "Test auth" in format_run_line(_run("Test auth"))


def test_format_run_line_empty_request_placeholder():
    assert "(no request)" in format_run_line(_run(""))


# ---------------------------------------------------------------------------
# state: set_runs / set_broken / selected / select
# ---------------------------------------------------------------------------

def test_history_pane_loads_runs():
    pane = HistoryPane()
    pane.set_runs([_run("a", 1, 1, 0), _run("b", 2, 1, 1)])
    assert len(pane.runs) == 2
    assert pane.broken == 0


def test_history_pane_empty_state():
    pane = HistoryPane()
    pane.set_runs([])
    assert pane.runs == []
    assert pane.empty_label == "Chưa có lịch sử"
    assert pane.selected is None


def test_history_pane_none_runs_is_empty():
    pane = HistoryPane()
    pane.set_runs(None)  # type: ignore[arg-type]
    assert pane.runs == []


def test_history_pane_broken_notice():
    pane = HistoryPane()
    pane.set_broken(2)
    assert pane.broken == 2
    pane.set_broken(0)
    assert pane.broken == 0
    pane.set_broken(None)  # type: ignore[arg-type]
    assert pane.broken == 0


def test_history_pane_select_returns_run():
    pane = HistoryPane()
    runs = [_run("a", 1, 1, 0), _run("b", 2, 1, 1)]
    pane.set_runs(runs)
    assert pane.select(1) is runs[1]
    assert pane.selected is runs[1]


def test_history_pane_select_out_of_range_keeps_current():
    pane = HistoryPane()
    runs = [_run("a"), _run("b", ts="20260101_000001")]
    pane.set_runs(runs)
    pane.select(1)
    assert pane.select(9) is runs[1]
    assert pane.select(-1) is runs[1]


def test_history_pane_selected_is_none_before_any_select():
    pane = HistoryPane()
    pane.set_runs([_run("a")])
    assert pane.selected is None


def test_history_pane_reload_resets_selection():
    pane = HistoryPane()
    pane.set_runs([_run("a"), _run("b", ts="20260101_000001")])
    pane.select(1)
    pane.set_runs([_run("c", ts="20260101_000002")])
    assert pane.selected is None
    assert pane.select(0) is pane.runs[0]
    assert pane.runs[0].request == "c"


def test_history_pane_tolerates_short_list():
    runs = [_run("a"), _run("b", ts="20260101_000001"), _run("c", ts="20260101_000002")]
    pane = HistoryPane()
    pane.set_runs(runs)
    assert pane.select(2) is runs[2]


# ---------------------------------------------------------------------------
# mounted behaviour
# ---------------------------------------------------------------------------

async def _settle(pilot, pane, ready) -> None:
    """`ListView.clear()` trả AwaitComplete — item mới hiện sau một frame."""
    for _ in range(10):
        if ready(pane):
            return
        await pilot.pause()
    assert ready(pane)


def _items_ready(count: int):
    return lambda p: len(p.children) == count and all(
        len(item.children) == 1 for item in p.children
    )


async def test_render_adds_one_item_per_run_with_explicit_id():
    from textual.app import App

    class _App(App):
        def compose(self):
            yield HistoryPane()

    app = _App()
    async with app.run_test() as pilot:
        pane = app.query_one(HistoryPane)
        pane.set_runs([_run("a", 1, 1, 0), _run("b", 2, 1, 1)])
        await _settle(pilot, pane, _items_ready(2))
        assert [str(c.id) for c in pane.children] == ["run-0", "run-1"]
        assert pane.children[0].children[0].content == format_run_line(
            _run("a", 1, 1, 0)
        )
        assert "1 fail" in pane.children[1].children[0].content


async def test_render_empty_state_shows_placeholder():
    from textual.app import App

    class _App(App):
        def compose(self):
            yield HistoryPane()

    app = _App()
    async with app.run_test() as pilot:
        pane = app.query_one(HistoryPane)
        pane.set_runs([_run("a")])
        await _settle(pilot, pane, _items_ready(1))
        pane.set_runs([])
        await _settle(pilot, pane, _items_ready(1))
        assert [str(c.id) for c in pane.children] == ["run-empty"]
        assert pane.children[0].children[0].content == "Chưa có lịch sử"
        assert pane.selected is None


async def test_select_sets_listview_index_when_mounted():
    from textual.app import App

    class _App(App):
        def compose(self):
            yield HistoryPane()

    app = _App()
    async with app.run_test() as pilot:
        pane = app.query_one(HistoryPane)
        runs = [_run("a"), _run("b", ts="20260101_000001")]
        pane.set_runs(runs)
        await _settle(pilot, pane, _items_ready(2))
        assert pane.select(1) is runs[1]
        assert pane.index == 1
        assert pane.selected is runs[1]
