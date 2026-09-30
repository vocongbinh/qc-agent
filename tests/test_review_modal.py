from __future__ import annotations

import inspect
import re

import pytest

pytest.importorskip("textual")

from tui.review import ReviewModel
from tui.widgets.review_modal import ReviewModalScreen

CASES = [
    {"id": "A", "type": "api", "priority": "critical", "title": "one"},
    {"id": "B", "type": "ui", "priority": "high", "title": "two"},
]


def _shadowed_names(cls) -> list[str]:
    """Tên method / thuộc tính tự khai trùng internal của base class."""
    base = {n for klass in cls.__mro__[1:] for n in vars(klass)}
    base |= set(vars(cls.__mro__[1]()))
    methods = {n for n, v in vars(cls).items() if inspect.isfunction(v)}
    attrs = set(
        re.findall(r"self\.(\w+)\s*(?::[^=\n]+)?=", inspect.getsource(cls.__init__))
    )
    # `action_toggle` đè `DOMNode.action_toggle` — cố ý: đó là action mà
    # binding `space` gọi, và không có gì gọi action đó trên Screen.
    allowed = (
        {"__init__", "compose", "on_mount", "action_toggle"}
        | {n for n in methods if n.startswith("on_")}
    )
    return sorted(n for n in methods | attrs if n in base and n not in allowed)


def _bindings() -> dict[str, str]:
    out: dict[str, str] = {}
    for binding in ReviewModalScreen.BINDINGS:
        if isinstance(binding, tuple):
            out[binding[0]] = binding[1]
        else:
            out[binding.key] = binding.action
    return out


def test_widget_does_not_shadow_textual_internals():
    assert _shadowed_names(ReviewModalScreen) == []


# ---------------------------------------------------------------------------
# model / plan
# ---------------------------------------------------------------------------

def test_modal_exposes_model():
    m = ReviewModel(CASES)
    screen = ReviewModalScreen(m)
    assert screen.model is m
    assert screen.plan == {}


def test_modal_summary_text_full_plan():
    plan = {
        "title": "Auth Plan",
        "test_cases": [{"id": "A"}, {"id": "B"}],
        "risks": ["r1", "r2"],
        "estimated_duration_min": 20,
    }
    text = ReviewModalScreen(ReviewModel(plan["test_cases"]), plan).summary_text()
    assert "Auth Plan" in text
    assert "2 cases" in text
    assert "20 min" in text
    assert "2 risks" in text
    assert " · " in text


def test_modal_summary_without_optional_fields():
    plan = {"title": "P", "test_cases": [{"id": "A"}]}
    text = ReviewModalScreen(ReviewModel(plan["test_cases"]), plan).summary_text()
    assert "P" in text
    assert "1 cases" in text
    assert "risks" not in text
    assert "min" not in text


def test_modal_summary_ignores_zero_optionals():
    plan = {"title": "P", "estimated_duration_min": 0, "risks": []}
    text = ReviewModalScreen(ReviewModel([{"id": "A"}]), plan).summary_text()
    assert "risks" not in text
    assert "min" not in text


def test_modal_summary_falls_back_to_default_title():
    text = ReviewModalScreen(ReviewModel([{"id": "A"}])).summary_text()
    assert "Test Plan" in text


def test_modal_summary_single_case_singular_free():
    plan = {"title": "P", "test_cases": [{"id": "A"}, {"id": "B"}, {"id": "C"}]}
    text = ReviewModalScreen(ReviewModel(plan["test_cases"]), plan).summary_text()
    assert "3 cases" in text


# ---------------------------------------------------------------------------
# approve / reject
# ---------------------------------------------------------------------------

def test_modal_approve_passes_kept_cases():
    cases = [{"id": "A", "type": "api"}, {"id": "B", "type": "ui"}]
    m = ReviewModel(cases)
    m.toggle(1)
    assert ReviewModalScreen(m).approve() == [{"id": "A", "type": "api"}]


def test_modal_approve_returns_none_when_nothing_ticked():
    m = ReviewModel([{"id": "A"}])
    m.toggle(0)
    assert ReviewModalScreen(m).approve() is None


def test_modal_approve_returns_none_with_no_cases():
    assert ReviewModalScreen(ReviewModel([])).approve() is None


def test_modal_approve_all_ticked():
    assert ReviewModalScreen(ReviewModel(CASES)).approve() == CASES


def test_modal_reject_returns_none():
    assert ReviewModalScreen(ReviewModel([{"id": "A"}])).reject() is None


# ---------------------------------------------------------------------------
# bindings + actions
# ---------------------------------------------------------------------------

def test_bindings_cover_toggle_all_none():
    bindings = _bindings()
    assert bindings["space"] == "toggle"
    assert bindings["a"] == "all"
    assert bindings["n"] == "none"


def test_escape_is_bound_to_dismiss():
    # Textual 8.x bỏ binding escape mặc định trên Screen, nên phải khai báo
    # rõ — escape phải ra `None` (reject) để gate được giải phóng.
    assert _bindings()["escape"] == "dismiss"


def test_action_toggle_flips_cursor_row_and_advances():
    m = ReviewModel(CASES)
    screen = ReviewModalScreen(m)
    screen.action_toggle()
    assert m.enabled == [False, True]
    assert m.cursor == 1
    screen.action_toggle()
    assert m.enabled == [False, False]


def test_action_toggle_stops_at_last_row():
    m = ReviewModel(CASES)
    m.cursor = 1
    screen = ReviewModalScreen(m)
    screen.action_toggle()
    assert m.enabled == [True, False]
    assert m.cursor == 1


def test_action_all_and_none():
    m = ReviewModel(CASES)
    screen = ReviewModalScreen(m)
    screen.action_none()
    assert m.enabled == [False, False]
    assert screen.approve() is None
    screen.action_all()
    assert m.enabled == [True, True]
    assert screen.approve() == CASES


def test_actions_on_empty_model_do_not_raise():
    m = ReviewModel([])
    screen = ReviewModalScreen(m)
    screen.action_toggle()
    screen.action_all()
    screen.action_none()
    assert screen.approve() is None


# ---------------------------------------------------------------------------
# table events (dựng event trực tiếp, không cần app)
# ---------------------------------------------------------------------------

def _row_event(kind, table, row):
    return kind(table, row, f"key-{row}")


def test_row_highlighted_syncs_cursor():
    from textual.widgets import DataTable

    table = DataTable()
    m = ReviewModel(CASES)
    screen = ReviewModalScreen(m)
    screen.on_data_table_row_highlighted(_row_event(DataTable.RowHighlighted, table, 1))
    assert m.cursor == 1


def test_row_highlighted_ignores_out_of_range():
    from textual.widgets import DataTable

    table = DataTable()
    m = ReviewModel(CASES)
    m.cursor = 1
    screen = ReviewModalScreen(m)
    screen.on_data_table_row_highlighted(_row_event(DataTable.RowHighlighted, table, 9))
    assert m.cursor == 1


def test_row_selected_toggles_and_advances():
    from textual.widgets import DataTable

    table = DataTable()
    m = ReviewModel(CASES)
    screen = ReviewModalScreen(m)
    screen.on_data_table_row_selected(_row_event(DataTable.RowSelected, table, 0))
    assert m.enabled == [False, True]
    assert m.cursor == 1


def test_row_selected_out_of_range_is_ignored():
    from textual.widgets import DataTable

    table = DataTable()
    m = ReviewModel(CASES)
    screen = ReviewModalScreen(m)
    screen.on_data_table_row_selected(_row_event(DataTable.RowSelected, table, 7))
    assert m.enabled == [True, True]
    assert m.cursor == 0


# ---------------------------------------------------------------------------
# mounted behaviour
# ---------------------------------------------------------------------------

async def test_mounted_table_shows_plan_columns_and_rows():
    from textual.app import App
    from textual.widgets import DataTable

    model = ReviewModel(CASES)
    modal = ReviewModalScreen(model, {"title": "Auth Plan"})

    class _App(App):
        def on_mount(self) -> None:
            self.push_screen(modal)

    app = _App()
    async with app.run_test() as pilot:
        await pilot.pause()
        table = modal.query_one("#review-table", DataTable)
        assert table.row_count == 2
        labels = [str(c.label) for c in table.columns.values()]
        assert labels == ["On", "ID", "Priority", "Type", "Title"]
        row = [str(cell) for cell in table.get_row_at(0)]
        assert row == ["✓", "A", "critical", "api", "one"]
        assert "Auth Plan" in str(modal.query_one("Label").content)


async def test_approve_button_dismisses_with_kept_cases():
    from textual.app import App

    model = ReviewModel([{"id": "A"}, {"id": "B"}])
    model.toggle(1)
    modal = ReviewModalScreen(model)

    class _App(App):
        def on_mount(self) -> None:
            self.push_screen(modal, self._capture)

        def _capture(self, value) -> None:
            self.result = value

    app = _App()
    app.result = "unset"
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.click("#approve")
        await pilot.pause()
        assert app.result == [{"id": "A"}]


async def test_reject_button_dismisses_none():
    from textual.app import App
    from textual.widgets import Button

    modal = ReviewModalScreen(ReviewModel([{"id": "A"}]))

    class _App(App):
        def on_mount(self) -> None:
            self.push_screen(modal, self._capture)

        def _capture(self, value) -> None:
            self.result = value

    app = _App()
    app.result = "unset"
    async with app.run_test() as pilot:
        await pilot.pause()
        assert modal.query_one("#reject", Button).variant == "error"
        await pilot.click("#reject")
        await pilot.pause()
        assert app.result is None


async def test_approve_button_disabled_when_nothing_ticked():
    from textual.app import App
    from textual.widgets import Button

    model = ReviewModel([{"id": "A"}])
    modal = ReviewModalScreen(model)

    class _App(App):
        def on_mount(self) -> None:
            self.push_screen(modal)

    app = _App()
    async with app.run_test() as pilot:
        await pilot.pause()
        assert modal.query_one("#approve", Button).disabled is False
        await pilot.press("n")
        await pilot.pause()
        assert modal.query_one("#approve", Button).disabled is True
        await pilot.press("a")
        await pilot.pause()
        assert modal.query_one("#approve", Button).disabled is False


async def test_space_toggles_row_through_keyboard():
    from textual.app import App
    from textual.widgets import DataTable

    model = ReviewModel(CASES)
    modal = ReviewModalScreen(model)

    class _App(App):
        def on_mount(self) -> None:
            self.push_screen(modal)

    app = _App()
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("space")
        await pilot.pause()
        assert model.enabled == [False, True]
        table = modal.query_one("#review-table", DataTable)
        assert str(table.get_row_at(0)[0]) == " "


async def test_escape_dismisses_with_none():
    from textual.app import App

    modal = ReviewModalScreen(ReviewModel([{"id": "A"}]))

    class _App(App):
        def on_mount(self) -> None:
            self.push_screen(modal, self._capture)

        def _capture(self, value) -> None:
            self.result = value

    app = _App()
    app.result = "unset"
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
        assert app.result is None
