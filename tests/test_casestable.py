from __future__ import annotations

import inspect
import re

import pytest

pytest.importorskip("textual")

from tui.widgets.casestable import (
    COLUMNS,
    CaseTable,
    format_row,
    status_style,
)


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
    # `DataTable.rows` là dict cell của Textual nên kết quả thô nằm ở
    # `results`; tên method cũng phải tránh đè internal (đã từng đè
    # `Widget._render` khiến widget không vẽ được).
    assert _shadowed_names(CaseTable) == []


# ---------------------------------------------------------------------------
# status_style / status_mark
# ---------------------------------------------------------------------------

def test_status_style_passed_green():
    assert status_style("passed") == "green"


def test_status_style_failed_red():
    assert status_style("failed") == "red"


def test_status_style_error_red():
    assert status_style("error") == "red"


def test_status_style_blocked_yellow():
    assert status_style("blocked") == "yellow"


def test_status_style_skipped_dim():
    assert status_style("skipped") == "dim"


def test_status_style_running_cyan():
    assert status_style("running") == "cyan"


def test_status_style_unknown_dim():
    assert status_style("whatever") == "dim"
    assert status_style(None) == "dim"
    assert status_style("") == "dim"


def test_status_style_is_case_insensitive():
    assert status_style("PASSED") == "green"


def test_status_mark_covers_every_status():
    t = CaseTable()
    assert t.status_mark("passed") == "✓"
    assert t.status_mark("failed") == "✗"
    assert t.status_mark("error") == "!"
    assert t.status_mark("blocked") == "□"
    assert t.status_mark("skipped") == "–"
    assert t.status_mark("running") == "⋯"
    assert t.status_mark("untested") == "·"
    assert t.status_mark(None) == "·"


def test_status_mark_is_case_insensitive():
    assert CaseTable().status_mark("PASSED") == "✓"


# ---------------------------------------------------------------------------
# format_row — 5 ô theo thứ tự COLUMNS
# ---------------------------------------------------------------------------

def test_format_row_full():
    got = format_row(
        {
            "id": "A",
            "type": "ui",
            "status": "passed",
            "duration_ms": 12.7,
            "title": "login",
        },
        mark="✓",
    )
    assert got == ("A", "ui", "✓ passed", "13", "login")


def test_format_row_missing_type_defaults_api():
    assert format_row({"id": "A"}, mark="·")[1] == "api"


def test_format_row_duration_not_a_number_is_blank():
    assert format_row({"id": "A", "duration_ms": "n/a"}, mark="·")[3] == ""
    assert format_row({"id": "A", "duration_ms": None}, mark="·")[3] == ""
    assert format_row({"id": "A"}, mark="·")[3] == ""


def test_format_row_duration_int_stays_int():
    assert format_row({"id": "A", "duration_ms": 7}, mark="·")[3] == "7"
    assert format_row({"id": "A", "duration_ms": 7.0}, mark="·")[3] == "7"


def test_format_row_title_falls_back_to_error_message():
    got = format_row(
        {"id": "A", "status": "failed", "title": "", "error_message": "boom"},
        mark="✗",
    )
    assert got[4] == "boom"


def test_format_row_title_and_error_both_empty():
    assert format_row({"id": "A", "title": None, "error_message": None}, mark="·")[4] == ""


def test_format_row_status_missing_keeps_bare_mark():
    assert format_row({"id": "A"}, mark="·")[2] == "·"


def test_columns_order():
    assert COLUMNS == ("ID", "Type", "Status", "ms", "Title")


# ---------------------------------------------------------------------------
# state: add / upsert / clear / filter
# ---------------------------------------------------------------------------

def test_add_result_appends_in_order():
    t = CaseTable()
    t.add_result({"id": "A", "type": "api", "status": "passed"})
    t.add_result({"id": "B", "type": "ui", "status": "failed"})
    assert t.visible_ids() == ["A", "B"]


def test_add_result_upserts_instead_of_duplicating():
    t = CaseTable()
    t.add_result({"id": "A", "type": "api", "status": "running"})
    t.add_result({"id": "A", "type": "api", "status": "passed", "duration_ms": 5})
    assert t.visible_ids() == ["A"]
    assert t.results[0]["status"] == "passed"
    assert t.results[0]["duration_ms"] == 5


def test_add_result_merge_keeps_keys_absent_from_update():
    t = CaseTable()
    t.add_result({"id": "A", "title": "login"})
    t.add_result({"id": "A", "status": "passed"})
    assert t.results[0] == {"id": "A", "title": "login", "status": "passed"}


def test_add_result_keeps_upsert_position():
    t = CaseTable()
    t.add_result({"id": "A"})
    t.add_result({"id": "B"})
    t.add_result({"id": "A", "status": "passed"})
    assert t.visible_ids() == ["A", "B"]


def test_add_result_without_id_is_not_addressable():
    t = CaseTable()
    t.add_result({"id": "A"})
    t.add_result({"status": "passed"})
    assert t.visible_ids() == ["A", ""]
    assert len(t.results) == 2


def test_clear_rows_empties_state():
    t = CaseTable()
    t.add_result({"id": "A", "type": "api", "status": "passed"})
    t.clear_rows()
    assert t.results == []
    assert t.visible_ids() == []


def test_add_result_does_not_mutate_input():
    payload = {"id": "A", "type": "api"}
    t = CaseTable()
    t.add_result(payload)
    payload["status"] = "failed"
    assert "status" not in t.results[0]


def test_filter_by_phase_keeps_only_matching_type():
    t = CaseTable()
    t.add_result({"id": "A", "type": "api"})
    t.add_result({"id": "B", "type": "ui"})
    t.add_result({"id": "C", "type": "chaos"})
    t.set_phase_filter(["ui"])
    assert t.visible_ids() == ["B"]


def test_filter_empty_keeps_everything():
    t = CaseTable()
    t.add_result({"id": "A", "type": "api"})
    t.add_result({"id": "B", "type": "ui"})
    t.set_phase_filter([])
    assert t.visible_ids() == ["A", "B"]


def test_filter_ignores_unknown_phase():
    t = CaseTable()
    t.add_result({"id": "A", "type": "api"})
    t.set_phase_filter(["banana"])
    assert t.visible_ids() == []


def test_filter_keeps_missing_type_as_api():
    t = CaseTable()
    t.add_result({"id": "A"})
    t.add_result({"id": "B", "type": "ui"})
    t.set_phase_filter(["api"])
    assert t.visible_ids() == ["A"]


def test_filter_api_phase_keeps_integration_type():
    """`integration` chạy qua api_executor nên phải sống cùng phase api."""
    t = CaseTable()
    t.add_result({"id": "A", "type": "api"})
    t.add_result({"id": "B", "type": "integration"})
    t.add_result({"id": "C", "type": "ui"})
    t.set_phase_filter(["api"])
    assert t.visible_ids() == ["A", "B"]


def test_filter_ui_phase_drops_integration_type():
    t = CaseTable()
    t.add_result({"id": "B", "type": "integration"})
    t.set_phase_filter(["ui"])
    assert t.visible_ids() == []


def test_visible_results_returns_dicts():
    t = CaseTable()
    t.add_result({"id": "A", "type": "api"})
    t.add_result({"id": "B", "type": "ui"})
    t.set_phase_filter(["ui"])
    got = t.visible_results()
    assert [r["id"] for r in got] == ["B"]


def test_filter_does_not_mutate_stored_rows():
    t = CaseTable()
    t.add_result({"id": "A", "type": "api"})
    t.set_phase_filter(["ui"])
    assert len(t.results) == 1


def test_set_phase_filter_replaces_previous_filter():
    t = CaseTable()
    t.add_result({"id": "A", "type": "api"})
    t.add_result({"id": "B", "type": "ui"})
    t.set_phase_filter(["ui"])
    t.set_phase_filter(["api"])
    assert t.visible_ids() == ["A"]


# ---------------------------------------------------------------------------
# mounted behaviour — `_render` chỉ chạy được khi có app
# ---------------------------------------------------------------------------

async def test_render_builds_columns_and_rows_when_mounted():
    from textual.app import App

    class _App(App):
        def compose(self):
            yield CaseTable()

    app = _App()
    async with app.run_test():
        t = app.query_one(CaseTable)
        t.add_result(
            {
                "id": "A",
                "type": "api",
                "status": "passed",
                "duration_ms": 12.7,
                "title": "login",
            }
        )
        t.add_result(
            {"id": "B", "type": "ui", "status": "failed", "error_message": "boom"}
        )
        assert [str(c.label) for c in t.columns.values()] == list(COLUMNS)
        assert list(t.get_row_at(0)) == ["A", "api", "✓ passed", "13", "login"]
        assert list(t.get_row_at(1)) == ["B", "ui", "✗ failed", "", "boom"]


async def test_render_hides_filtered_out_rows_and_upserts_in_place():
    from textual.app import App

    class _App(App):
        def compose(self):
            yield CaseTable()

    app = _App()
    async with app.run_test():
        t = app.query_one(CaseTable)
        t.add_result({"id": "A", "type": "api", "status": "running"})
        t.add_result({"id": "B", "type": "ui", "status": "failed"})
        assert t.row_count == 2

        t.set_phase_filter(["api"])
        assert t.row_count == 1
        assert list(t.get_row_at(0))[0] == "A"

        t.add_result({"id": "A", "type": "api", "status": "passed"})
        assert t.row_count == 1
        assert list(t.get_row_at(0))[2] == "✓ passed"

        t.set_phase_filter([])
        assert t.row_count == 2
        t.clear_rows()
        assert t.row_count == 0
