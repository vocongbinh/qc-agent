from __future__ import annotations

from tui.filters import ALL_PHASES as ALL_PHASES_EXPORTED
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


# ---------------------------------------------------------------------------
# `integration` là alias của phase `api` (api_executor chạy cả hai)
# ---------------------------------------------------------------------------

def test_filter_api_phase_keeps_integration_type():
    tests = [{"id": "B", "type": "integration"}]
    got = filter_by_phases(tests, ["api"])
    assert [t["id"] for t in got] == ["B"]


def test_filter_ui_phase_does_not_keep_integration_type():
    tests = [{"id": "B", "type": "integration"}]
    assert filter_by_phases(tests, ["ui"]) == []


def test_all_phases_stays_four_items_without_integration():
    assert ALL_PHASES_EXPORTED == ALL_PHASES
    assert len(ALL_PHASES_EXPORTED) == 4
    assert "integration" not in ALL_PHASES_EXPORTED
    # normalize không bao giờ phát ra "integration" — user chỉ tick 4 phase này
    for phases in ([], None, ["integration"], ["api", "integration"], ["INTEGRATION"]):
        assert "integration" not in normalize_phases(phases)
    assert normalize_phases(["integration"]) == []
    assert normalize_phases([]) == ALL_PHASES
    assert normalize_phases(["api"]) == ["api"]


def test_filter_mixed_list_keeps_api_and_integration_in_order():
    tests = [
        {"id": "A", "type": "api"},
        {"id": "B", "type": "integration"},
        {"id": "C", "type": "ui"},
    ]
    got = filter_by_phases(tests, ["api"])
    assert [t["id"] for t in got] == ["A", "B"]


def test_filter_empty_phases_keeps_integration():
    tests = [{"id": "A", "type": "api"}, {"id": "B", "type": "integration"}]
    got = filter_by_phases(tests, [])
    assert [t["id"] for t in got] == ["A", "B"]
