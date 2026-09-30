from __future__ import annotations

from tui.filters import filter_by_phases, normalize_phases

ALL_PHASES = ["api", "ui", "chaos", "performance"]


def test_normalize_empty_returns_all():
    assert normalize_phases([]) == ALL_PHASES
    assert normalize_phases(None) == ALL_PHASES


def test_normalize_dedupes_and_preserves_order():
    assert normalize_phases(["api", "api", "ui"]) == ["api", "ui"]


def test_normalize_drops_unknown():
    assert normalize_phases(["api", "banana", "ui"]) == ["api", "ui"]


def test_normalize_is_case_insensitive():
    assert normalize_phases(["API", " Ui "]) == ["api", "ui"]


def test_filter_keeps_matching_types():
    tests = [
        {"id": "A", "type": "api"},
        {"id": "B", "type": "ui"},
        {"id": "C", "type": "api"},
        {"id": "D", "type": "API"},  # type viết hoa vẫn khớp phase lowercase
    ]
    got = filter_by_phases(tests, ["api"])
    assert [t["id"] for t in got] == ["A", "C", "D"]


def test_filter_missing_type_treated_as_api():
    tests = [{"id": "A"}, {"id": "B", "type": "ui"}]
    got = filter_by_phases(tests, ["api"])
    assert [t["id"] for t in got] == ["A"]


def test_filter_empty_phases_keeps_everything():
    tests = [{"id": "A", "type": "api"}, {"id": "B", "type": "ui"}]
    assert len(filter_by_phases(tests, [])) == 2


def test_filter_all_phases_unknown_returns_nothing():
    tests = [{"id": "A", "type": "api"}]
    assert filter_by_phases(tests, ["banana"]) == []


def test_filter_does_not_mutate_input():
    tests = [{"id": "A", "type": "api"}, {"id": "B", "type": "ui"}]
    snapshot = [dict(t) for t in tests]
    filter_by_phases(tests, ["api"])
    assert tests == snapshot


def test_filter_tolerates_none_input():
    assert filter_by_phases(None, ["api"]) == []
