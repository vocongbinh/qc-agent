from __future__ import annotations

import inspect
import re

import pytest

pytest.importorskip("textual")

from textual.containers import Horizontal
from textual.widgets import Button, Input, Static
from textual.widgets._checkbox import Checkbox

from tui.widgets.footer import PHASE_LABELS, StatusFooter, format_elapsed

ALL = ["api", "ui", "chaos", "performance"]


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
    assert _shadowed_names(StatusFooter) == []


# ---------------------------------------------------------------------------
# phases
# ---------------------------------------------------------------------------

def test_phase_labels_cover_four_phases():
    assert set(PHASE_LABELS) == set(ALL)
    assert PHASE_LABELS == {
        "api": "API",
        "ui": "UI",
        "chaos": "Chaos",
        "performance": "Perf",
    }


def test_selected_phases_default_api():
    f = StatusFooter()
    assert f.selected_phases() == ["api"]


def test_toggle_phase_adds_and_removes():
    f = StatusFooter()
    f.toggle_phase("ui")
    assert f.selected_phases() == ["api", "ui"]
    f.toggle_phase("api")
    assert f.selected_phases() == ["ui"]


def test_toggle_ignores_unknown_phase():
    f = StatusFooter()
    f.toggle_phase("banana")
    f.toggle_phase("")
    f.toggle_phase("integration")
    # `selected_phases()` chỉ lọc theo ALL_PHASES nên phase lạ lọt vào đây sẽ
    # không lộ ra — phải kiểm tra thẳng state.
    assert f._selected == {"api"}
    assert f.selected_phases() == ["api"]


def test_toggle_all_off_then_on():
    f = StatusFooter()
    f.set_all_phases(False)
    assert f.selected_phases() == []
    f.set_all_phases(True)
    assert len(f.selected_phases()) == 4


def test_selected_phases_follows_declared_order():
    """Thứ tự phải theo PHASE_LABELS, không theo thứ tự `set`."""
    f = StatusFooter()
    f.set_all_phases(False)
    for phase in reversed(ALL):
        f.toggle_phase(phase)
    assert f.selected_phases() == ALL
    f.toggle_phase("api")
    f.toggle_phase("chaos")
    assert f.selected_phases() == ["ui", "performance"]


def test_selected_phases_ignores_set_iteration_order():
    """Bản xác định của test trên.

    `set` không giữ thứ tự và thứ tự duyệt phụ thuộc hash seed, nên test bình
    thường chỉ bắt được mutation `return list(self._selected)` ở ~95% số
    process. Ở đây `_selected` là set cài đặt `__iter__` trả thứ tự ngược, nên
    mutant sai ở mọi process.
    """

    class _ReversedSet(set):
        def __iter__(self):
            return iter(sorted(set.__iter__(self), reverse=True))

    f = StatusFooter()
    f._selected = _ReversedSet(ALL)
    assert f.selected_phases() == ALL
    f._selected = _ReversedSet(["performance", "api"])
    assert f.selected_phases() == ["api", "performance"]


def test_set_all_phases_preserves_selection_after_toggle():
    f = StatusFooter()
    f.toggle_phase("chaos")
    f.set_all_phases(True)
    assert f.selected_phases() == ALL
    f.toggle_phase("chaos")
    assert f.selected_phases() == ["api", "ui", "performance"]


# ---------------------------------------------------------------------------
# request / status / progress
# ---------------------------------------------------------------------------

def test_request_set_and_get():
    f = StatusFooter()
    assert f.request() == ""
    f.set_request("Test auth API")
    assert f.request() == "Test auth API"


def test_set_request_none_becomes_empty():
    f = StatusFooter()
    f.set_request("x")
    f.set_request(None)  # type: ignore[arg-type]
    assert f.request() == ""


def test_status_set_and_get():
    f = StatusFooter()
    assert f.status() == ""
    f.set_status("Planner đang chạy…")
    assert f.status() == "Planner đang chạy…"
    f.set_status("")
    assert f.status() == ""


def test_progress_text():
    f = StatusFooter()
    f.set_progress("api", 6, 12)
    assert "api" in f.progress_text()
    assert "6/12" in f.progress_text()


def test_progress_text_idle():
    f = StatusFooter()
    assert f.progress_text() == ""


def test_progress_text_blank_when_total_zero():
    f = StatusFooter()
    f.set_progress("api", 3, 0)
    assert f.progress_text() == ""


def test_progress_text_replaces_previous_phase():
    f = StatusFooter()
    f.set_progress("api", 1, 2)
    f.set_progress("ui", 3, 4)
    assert f.progress_text() == "ui 3/4"


def test_progress_tolerates_string_numbers():
    f = StatusFooter()
    f.set_progress("api", "6", "12")
    assert f.progress_text() == "api 6/12"


# ---------------------------------------------------------------------------
# running state — phải an toàn trước khi mount
# ---------------------------------------------------------------------------

def test_set_running_before_mount_is_safe():
    f = StatusFooter()
    f.set_running(True)
    assert f.running() is True
    f.set_running(False)
    assert f.running() is False


def test_set_running_keeps_other_state():
    f = StatusFooter()
    f.set_request("x")
    f.toggle_phase("ui")
    f.set_running(True)
    assert f.request() == "x"
    assert f.selected_phases() == ["api", "ui"]


# ---------------------------------------------------------------------------
# format_elapsed
# ---------------------------------------------------------------------------

def test_format_elapsed_seconds():
    assert format_elapsed(0) == "0:00"
    assert format_elapsed(9) == "0:09"
    assert format_elapsed(65) == "1:05"


def test_format_elapsed_hours():
    assert format_elapsed(3661) == "1:01:01"
    assert format_elapsed(3600) == "1:00:00"


def test_format_elapsed_clamps_negative_and_truncates_float():
    assert format_elapsed(-5) == "0:00"
    assert format_elapsed(65.9) == "1:05"
    assert format_elapsed(None) == "0:00"  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# mounted behaviour
# ---------------------------------------------------------------------------

async def _settle(pilot, ready) -> None:
    for _ in range(10):
        if ready():
            return
        await pilot.pause()
    assert ready()


async def test_compose_lays_out_input_phases_buttons_status():
    from textual.app import App

    class _App(App):
        def compose(self):
            yield StatusFooter()

    app = _App()
    async with app.run_test() as pilot:
        f = app.query_one(StatusFooter)

        request = f.query_one("#request", Input)
        assert request is not None

        box = f.query_one("#phases", Horizontal)
        boxes = list(box.query(Checkbox))
        assert [str(c.id) for c in boxes] == [f"phase-{p}" for p in ALL]
        assert [c.value for c in boxes] == [True, False, False, False]
        labels = [str(boxes[i].label) for i in range(4)]
        assert labels == [PHASE_LABELS[p] for p in ALL]

        run_btn = f.query_one("#run", Button)
        stop_btn = f.query_one("#stop", Button)
        assert run_btn.variant == "primary"
        assert stop_btn.variant == "error"
        assert run_btn.disabled is False
        assert stop_btn.disabled is True

        assert f.query_one("#status", Static) is not None


async def test_state_methods_write_through_to_widgets():
    from textual.app import App

    class _App(App):
        def compose(self):
            yield StatusFooter()

    app = _App()
    async with app.run_test():
        f = app.query_one(StatusFooter)

        f.set_request("Test auth API")
        assert f.query_one("#request", Input).value == "Test auth API"

        f.set_status("Planner đang chạy…")
        assert f.query_one("#status", Static).content == "Planner đang chạy…"

        f.set_running(True)
        assert f.query_one("#run", Button).disabled is True
        assert f.query_one("#stop", Button).disabled is False
        f.set_running(False)
        assert f.query_one("#run", Button).disabled is False
        assert f.query_one("#stop", Button).disabled is True


async def test_toggle_phase_syncs_checkbox_widgets():
    from textual.app import App

    class _App(App):
        def compose(self):
            yield StatusFooter()

    app = _App()
    async with app.run_test() as pilot:
        f = app.query_one(StatusFooter)
        f.toggle_phase("ui")
        await pilot.pause()
        assert f.query_one("#phase-ui", Checkbox).value is True
        f.set_all_phases(True)
        await pilot.pause()
        assert all(
            f.query_one(f"#phase-{p}", Checkbox).value for p in ALL
        )
        f.set_all_phases(False)
        await pilot.pause()
        assert not any(
            f.query_one(f"#phase-{p}", Checkbox).value for p in ALL
        )


async def test_checkbox_click_updates_selected_phases():
    from textual.app import App

    class _App(App):
        def compose(self):
            yield StatusFooter()

    app = _App()
    async with app.run_test() as pilot:
        f = app.query_one(StatusFooter)
        f.query_one("#phase-chaos", Checkbox).value = True
        await pilot.pause()
        assert f.selected_phases() == ["api", "chaos"]

        f.query_one("#phase-api", Checkbox).value = False
        await pilot.pause()
        assert f.selected_phases() == ["chaos"]


async def test_typing_in_input_updates_request_state():
    from textual.app import App

    class _App(App):
        def compose(self):
            yield StatusFooter()

    app = _App()
    async with app.run_test() as pilot:
        f = app.query_one(StatusFooter)
        f.query_one("#request", Input).value = "Gõ tay"
        await pilot.pause()
        assert f.request() == "Gõ tay"
