from __future__ import annotations

import json

from tui.history_reader import RunSummary, load_history, parse_report


def _write_report(reports_dir, name, payload):
    reports_dir.mkdir(exist_ok=True)
    f = reports_dir / name
    f.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return f


def test_parse_report_extracts_fields():
    payload = {
        "timestamp": "20260930_140000",
        "user_request": "Test auth",
        "test_plan": {"title": "Auth Plan", "scope": "api"},
        "execution_result": {
            "total": 10, "passed": 8, "failed": 2, "error": 0,
            "skipped": 0, "duration_ms": 1234.5,
            "details": [
                {"id": "TC_1", "type": "api", "status": "passed",
                 "duration_ms": 10, "title": "Login ok"},
                {"id": "TC_2", "type": "api", "status": "failed",
                 "duration_ms": 20, "error_message": "401", "title": "Bad pass"},
            ],
        },
    }
    s = parse_report(payload, "/reports/report_20260930_140000.json")
    assert s.request == "Test auth"
    assert s.plan_title == "Auth Plan"
    assert s.scope == "api"
    assert s.total == 10
    assert s.passed == 8
    assert s.failed == 2
    assert s.duration_ms == 1234.5
    assert len(s.details) == 2
    assert s.details[1]["error_message"] == "401"


def test_parse_report_tolerates_missing_keys():
    s = parse_report({}, "x.json")
    assert s.request == ""
    assert s.plan_title == ""
    assert s.total == 0
    assert s.passed == 0
    assert s.duration_ms == 0.0
    assert s.details == []


def test_parse_report_tolerates_null_values():
    payload = {
        "user_request": None,
        "test_plan": None,
        "execution_result": None,
    }
    s = parse_report(payload, "x.json")
    assert s.request == ""
    assert s.details == []


def test_parse_report_handles_string_numbers():
    """Report cũ hoặc viết tay có thể có số dạng string."""
    payload = {"execution_result": {"total": "5", "passed": "3", "duration_ms": "10.5"}}
    s = parse_report(payload, "x.json")
    assert s.total == 5
    assert s.passed == 3
    assert s.duration_ms == 10.5


def test_short_id_from_path():
    payload = {"timestamp": "20260930_140000"}
    s = parse_report(payload, "/reports/report_20260930_140000.json")
    assert s.short_id == "20260930_140000"


def test_parse_report_non_numeric_counts_become_zero():
    """Report hỏng có thể để số ở dạng không đọc được, không được phép raise."""
    payload = {"execution_result": {"total": "abc", "passed": {"a": 1}, "failed": []}}
    s = parse_report(payload, "x.json")
    assert s.total == 0
    assert s.passed == 0
    assert s.failed == 0


def test_parse_report_total_as_list_becomes_zero():
    payload = {"execution_result": {"total": [1, 2, 3]}}
    s = parse_report(payload, "x.json")
    assert s.total == 0


def test_parse_report_non_numeric_duration_becomes_zero():
    payload = {"execution_result": {"duration_ms": "abc"}}
    s = parse_report(payload, "x.json")
    assert s.duration_ms == 0.0


def test_parse_report_keeps_falsy_but_present_values():
    """0/False vẫn là dữ liệu thật – chỉ None mới thành chuỗi rỗng."""
    s = parse_report({"timestamp": False, "user_request": 0}, "x.json")
    assert s.request == "0"
    assert s.timestamp == "False"


def test_parse_report_details_dict_becomes_empty():
    """`details` lỡ là dict/string thì coi như không có chi tiết."""
    for bad in ({"a": 1}, "abc", 5):
        s = parse_report({"execution_result": {"details": bad}}, "x.json")
        assert s.details == [], bad


def test_parse_report_details_is_copied():
    """Sửa details của RunSummary không được đụng payload gốc."""
    payload = {"execution_result": {"details": [{"id": "TC_1"}]}}
    s = parse_report(payload, "x.json")
    s.details.append({"id": "TC_2"})
    assert len(payload["execution_result"]["details"]) == 1


def test_load_history_missing_dir(tmp_path):
    runs, broken = load_history(tmp_path / "nope")
    assert runs == []
    assert broken == 0


def test_load_history_empty_dir(tmp_path):
    d = tmp_path / "reports"
    d.mkdir()
    runs, broken = load_history(d)
    assert runs == []
    assert broken == 0


def test_load_history_sorted_newest_first(tmp_path):
    d = tmp_path / "reports"
    _write_report(d, "report_20260101_000000.json",
                  {"user_request": "old", "timestamp": "20260101_000000"})
    _write_report(d, "report_20261231_235959.json",
                  {"user_request": "new", "timestamp": "20261231_235959"})
    runs, _ = load_history(d)
    assert [r.request for r in runs] == ["new", "old"]


def test_load_history_counts_broken_files(tmp_path):
    d = tmp_path / "reports"
    d.mkdir()
    (d / "report_20260101_000000.json").write_text("{not json", encoding="utf-8")
    _write_report(d, "report_20260102_000000.json", {"user_request": "ok"})
    runs, broken = load_history(d)
    assert broken == 1
    assert len(runs) == 1


def test_load_history_counts_non_dict_json_as_broken(tmp_path):
    d = tmp_path / "reports"
    d.mkdir()
    (d / "report_20260103_000000.json").write_text("[1,2,3]", encoding="utf-8")
    runs, broken = load_history(d)
    assert broken == 1
    assert runs == []


def test_load_history_ignores_non_report_files(tmp_path):
    d = tmp_path / "reports"
    d.mkdir()
    (d / "random.json").write_text("{}", encoding="utf-8")
    (d / "notes.txt").write_text("hello", encoding="utf-8")
    runs, broken = load_history(d)
    assert runs == []
    assert broken == 0


def test_load_history_accepts_str_path(tmp_path):
    d = tmp_path / "reports"
    _write_report(d, "report_20260101_000000.json", {"user_request": "ok"})
    runs, _ = load_history(str(d))
    assert len(runs) == 1


def test_load_history_orders_many_runs_newest_first(tmp_path):
    """Nhiều file: thứ tự phải đúng, không được trùng hợp với thứ tự glob."""
    d = tmp_path / "reports"
    stamps = [
        "20260101_000000", "20260615_120000", "20251231_235959",
        "20260301_090000", "20260930_140000", "20260228_101010",
    ]
    for ts in stamps:
        _write_report(d, f"report_{ts}.json", {"user_request": ts, "timestamp": ts})
    runs, broken = load_history(d)
    assert broken == 0
    assert [r.timestamp for r in runs] == sorted(stamps, reverse=True)
    assert [r.request for r in runs] == sorted(stamps, reverse=True)


def test_run_path_is_str(tmp_path):
    d = tmp_path / "reports"
    _write_report(d, "report_20260101_000000.json", {"user_request": "ok"})
    runs, _ = load_history(d)
    assert isinstance(runs[0].path, str)


def test_load_history_missing_timestamp_sorts_last_and_does_not_raise(tmp_path):
    """Report thiếu timestamp không được làm hỏng phép sort."""
    d = tmp_path / "reports"
    _write_report(d, "report_20260101_000000.json",
                  {"user_request": "no ts", "timestamp": None})
    _write_report(d, "report_20260102_000000.json",
                  {"user_request": "has ts", "timestamp": "20260102_000000"})
    runs, broken = load_history(d)
    assert broken == 0
    assert [r.request for r in runs] == ["has ts", "no ts"]
