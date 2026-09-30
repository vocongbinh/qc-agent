from __future__ import annotations

from typing import NotRequired, get_origin, get_type_hints

from agents.state import AgentState


def test_state_without_new_fields_is_still_valid():
    """main.py build state theo literal dict, không có phases/job_id."""
    state: AgentState = {
        "user_request": "test login",
        "documents": [],
        "code_paths": [],
        "openapi_spec": None,
        "messages": [],
        "test_plan": None,
        "generated_tests": [],
        "human_approved": False,
        "shared_context": {},
        "execution_result": None,
        "report_path": None,
        "final_summary": None,
        "current_step": "start",
        "error": None,
        "ui_headed": False,
    }
    assert state["current_step"] == "start"


def test_state_with_new_fields_is_valid():
    state: AgentState = {
        "user_request": "test",
        "documents": [],
        "code_paths": [],
        "openapi_spec": None,
        "messages": [],
        "test_plan": None,
        "generated_tests": [],
        "human_approved": False,
        "shared_context": {},
        "execution_result": None,
        "report_path": None,
        "final_summary": None,
        "current_step": "start",
        "error": None,
        "ui_headed": False,
        "phases": ["api"],
        "job_id": "abc123",
    }
    assert state["phases"] == ["api"]
    assert state["job_id"] == "abc123"


def test_new_fields_are_declared_optional():
    hints = get_type_hints(AgentState, include_extras=True)
    assert "phases" in hints
    assert "job_id" in hints
    # LangGraph đọc schema qua get_type_hints(include_extras=True) và tự bóc
    # NotRequired, nên đây mới là thứ quyết định field là optional hay không.
    assert get_origin(hints["phases"]) is NotRequired
    assert get_origin(hints["job_id"]) is NotRequired
    # Lưu ý: do module dùng `from __future__ import annotations`, TypedDict không
    # bóc được qualifier khi dựng class (giới hạn CPython, chỉ sửa ở 3.14) →
    # AgentState.__required_keys__ vẫn liệt kê 2 key này. Không consumer nào
    # trong repo đọc __required_keys__; đừng dựa vào nó để kết luận optional.
    assert "user_request" in AgentState.__required_keys__
