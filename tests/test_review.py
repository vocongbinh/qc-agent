from __future__ import annotations

from tui.review import ReviewModel


def _cases():
    return [
        {"id": "TC_1", "type": "api", "priority": "critical", "title": "one"},
        {"id": "TC_2", "type": "ui", "priority": "high", "title": "two"},
        {"id": "TC_3", "type": "api", "priority": "low", "title": "three"},
    ]


def test_initial_state_all_checked():
    m = ReviewModel(_cases())
    assert m.enabled == [True, True, True]
    assert m.can_approve is True


def test_toggle_flips_one():
    m = ReviewModel(_cases())
    m.toggle(1)
    assert m.enabled == [True, False, True]
    assert m.can_approve is True


def test_toggle_twice_restores():
    m = ReviewModel(_cases())
    m.toggle(0)
    m.toggle(0)
    assert m.enabled == [True, True, True]


def test_toggle_out_of_range_ignored():
    m = ReviewModel(_cases())
    m.toggle(99)
    m.toggle(-1)
    assert m.enabled == [True, True, True]


def test_toggle_one_past_end_ignored():
    """index == len(cases) là ngoài phạm vi, không được IndexError."""
    m = ReviewModel(_cases())
    m.toggle(3)
    assert m.enabled == [True, True, True]


def test_toggle_all_off_disables_approve():
    m = ReviewModel(_cases())
    for i in range(3):
        m.toggle(i)
    assert m.enabled == [False, False, False]
    assert m.can_approve is False


def test_set_all_false_then_true():
    m = ReviewModel(_cases())
    m.set_all(False)
    assert m.enabled == [False, False, False]
    m.set_all(True)
    assert m.enabled == [True, True, True]


def test_kept_cases_returns_only_enabled():
    m = ReviewModel(_cases())
    m.toggle(1)
    assert [c["id"] for c in m.kept_cases()] == ["TC_1", "TC_3"]


def test_cursor_stops_at_both_ends():
    m = ReviewModel(_cases())
    m.move_cursor(1)
    assert m.cursor == 1
    m.move_cursor(5)
    assert m.cursor == 2, "không vượt quá cuối"
    m.move_cursor(-9)
    assert m.cursor == 0, "không vượt quá đầu"


def test_cursor_on_empty_model_does_not_crash():
    m = ReviewModel([])
    m.move_cursor(1)
    assert m.cursor == 0


def test_empty_cases_model():
    m = ReviewModel([])
    assert m.enabled == []
    assert m.can_approve is False
    assert m.kept_cases() == []


def test_none_cases_tolerated():
    m = ReviewModel(None)
    assert m.cases == []
    assert m.can_approve is False


def test_constructor_copies_input_list():
    cases = _cases()
    m = ReviewModel(cases)
    m.cases.append({"id": "X"})
    assert len(cases) == 3, "không được mutate input"


def test_rows_for_display():
    m = ReviewModel(_cases())
    rows = m.rows()
    assert len(rows) == 3
    assert rows[0] == (True, "TC_1", "critical", "api", "one")
    m.toggle(2)
    assert m.rows()[2][0] is False


def test_rows_tolerates_missing_fields():
    m = ReviewModel([{"id": "TC_9"}])
    assert m.rows() == [(True, "TC_9", "", "api", "")]


def test_rows_tolerates_case_with_no_fields():
    """Case rỗng không được làm DataTable crash; ô trống là giá trị an toàn."""
    m = ReviewModel([{}])
    assert m.rows() == [(True, "", "", "api", "")]


def test_selected_count():
    m = ReviewModel(_cases())
    m.toggle(0)
    m.toggle(2)
    assert m.selected_count() == 1
    assert m.total_count() == 3
