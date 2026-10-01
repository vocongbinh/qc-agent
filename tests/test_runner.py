from __future__ import annotations

import asyncio
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Optional

import pytest

from tui.bus import Event, EventBus
from tui.gate import ReviewGate
from tui.runner import _NODE_BY_STEP, JobRunner, build_initial_state

# Deadline rộng nhưng hữu hạn: test fail nhanh thay vì treo cả suite.
DEADLINE = 5.0
POLL = 0.005
TERMINAL_KINDS = ("run_done", "run_error", "cancelled")


# --------------------------------------------------------------------------
# Test doubles
# --------------------------------------------------------------------------


class RecordingBus:
    """EventBus thay thế cho test đồng bộ.

    `EventBus` thật gọi `loop.call_soon_threadsafe`, nên `emit` ngoài event loop
    là no-op im lặng — test sync sẽ không thấy event nào. Stub này ghi thẳng
    vào list dưới lock: không cần asyncio, không có race, vẫn dùng đúng
    dataclass `Event` mà JobRunner emit ra.
    """

    def __init__(self) -> None:
        self._events: list[Event] = []
        self._lock = threading.Lock()

    def emit(self, event: Event) -> None:
        with self._lock:
            self._events.append(event)

    def events(self) -> list[Event]:
        with self._lock:
            return list(self._events)

    def kinds(self) -> list[str]:
        return [e.kind for e in self.events()]

    def payloads(self, kind: str) -> list[dict[str, Any]]:
        return [e.payload for e in self.events() if e.kind == kind]

    def one(self, kind: str) -> dict[str, Any]:
        got = self.payloads(kind)
        assert len(got) == 1, f"cần đúng 1 event {kind!r}, thực tế {len(got)}"
        return got[0]

    def terminal(self) -> list[Event]:
        return [e for e in self.events() if e.kind in TERMINAL_KINDS]


class FakeGraph:
    """Giả lập qc_graph: có checkpoint, resume, ghi lại mọi update_state.

    Khác bản nháp trong plan (dùng `setdefault` để vá state), bản này cộng dồn
    state thật sự — giống MemorySaver + reducer: `update_state` ghi đè được key
    đã tồn tại và giá trị đó lọt vào snapshot của các node chạy sau. Đó là điều
    cần để chứng minh executor không bao giờ thấy type ngoài `phases`.
    """

    def __init__(
        self,
        steps: list[dict[str, Any]],
        *,
        fail_before_step: Optional[str] = None,
        on_yield: Optional[Callable[[dict[str, Any], int], None]] = None,
    ) -> None:
        self.steps = [dict(s) for s in steps]
        self.fail_before_step = fail_before_step
        self.on_yield = on_yield
        self.state: dict[str, Any] = {}
        self.cursor = 0
        self.update_calls: list[tuple[Any, dict[str, Any]]] = []
        self.stream_states: list[Any] = []
        self.stream_configs: list[Any] = []
        self.stream_modes: list[Any] = []
        self.snapshots: list[dict[str, Any]] = []
        self._lock = threading.Lock()

    def stream(self, state, config=None, stream_mode=None):
        with self._lock:
            self.stream_states.append(state)
            self.stream_configs.append(config)
            self.stream_modes.append(stream_mode)
            call_index = len(self.stream_states) - 1
            if state is not None:
                self.state.update(state)
        while True:
            with self._lock:
                if self.cursor >= len(self.steps):
                    return
                step = dict(self.steps[self.cursor])
                self.cursor += 1
            if step.get("current_step") == self.fail_before_step:
                raise RuntimeError(f"boom before {step.get('current_step')}")
            with self._lock:
                self.state.update(step)
                snapshot = dict(self.state)
                self.snapshots.append(snapshot)
            if self.on_yield is not None:
                self.on_yield(step, call_index)
            yield snapshot

    def update_state(self, config, values, as_node=None):
        with self._lock:
            self.update_calls.append((config, dict(values)))
            self.state.update(values)
        return dict(values)

    def snapshot_for(self, current_step: str) -> dict[str, Any]:
        for snap in reversed(self.snapshots):
            if snap.get("current_step") == current_step:
                return snap
        raise AssertionError(f"chưa yield step {current_step!r}")

    def pass_count(self) -> int:
        return len(self.stream_states)


class RaisingGate(ReviewGate):
    """Gate hỏng giữa chừng: wait() ném lỗi thay vì trả về."""

    def wait(self) -> Optional[list[dict]]:  # type: ignore[override]
        raise RuntimeError("gate hỏng")


class CloseSpyStream:
    """Iterator có `close()` như generator của LangGraph, nhưng close() được
    đếm lại. Generator thật bị refcount giải phóng nên không quan sát được
    close(); ở đây ta kiểm tra việc đóng generator là tường minh, không phụ
    thuộc GC."""

    def __init__(self, source: Any, on_close: Callable[[], None]) -> None:
        self._source = iter(source)
        self._on_close = on_close

    def __iter__(self) -> "CloseSpyStream":
        return self

    def __next__(self) -> Any:
        return next(self._source)

    def close(self) -> None:
        self._on_close()


class CloseSpyGraph(FakeGraph):
    def __init__(self, steps: list[dict[str, Any]]) -> None:
        super().__init__(steps)
        self.closes = 0

    def stream(self, state, config=None, stream_mode=None):
        return CloseSpyStream(super().stream(state, config, stream_mode), self._note_close)

    def _note_close(self) -> None:
        self.closes += 1


# --------------------------------------------------------------------------
# Kịch bản graph
# --------------------------------------------------------------------------

CASE_API = {"id": "TC_1", "type": "api", "title": "one"}
CASE_UI = {"id": "TC_2", "type": "ui", "title": "two"}
TEST_API = {"id": "TC_1", "type": "api", "status": "untested"}
TEST_UI = {"id": "TC_2", "type": "ui", "status": "untested"}

PLANNER_DONE = {
    "current_step": "planner_done",
    "test_plan": {"title": "P", "summary": "s", "test_cases": [CASE_API, CASE_UI]},
    "human_approved": False,
    "error": None,
}
GENERATOR_DONE = {
    "current_step": "generator_done",
    "generated_tests": [TEST_API, TEST_UI],
    "error": None,
}
API_DONE = {"current_step": "api_executor_done", "error": None}
UI_DONE = {"current_step": "ui_executor_done", "error": None}
CHAOS_DONE = {"current_step": "chaos_executor_done", "error": None}
PERF_DONE = {"current_step": "performance_executor_done", "error": None}
REPORTER_DONE = {
    "current_step": "reporter_done",
    "report_path": "/reports/report_x.json",
    "final_summary": "done",
    "error": None,
}
FULL_RUN = [
    PLANNER_DONE,
    GENERATOR_DONE,
    API_DONE,
    UI_DONE,
    CHAOS_DONE,
    PERF_DONE,
    REPORTER_DONE,
]


# --------------------------------------------------------------------------
# Helper điều khiển thread.
# Quy tắc: không bao giờ gọi gate.wait() trên main thread; luôn poll có deadline
# rồi join(timeout) + assert not is_alive() trước khi đọc state của worker.
# --------------------------------------------------------------------------


def _wait_until(pred: Callable[[], bool], timeout: float = DEADLINE) -> bool:
    deadline = time.monotonic() + timeout
    while True:
        if pred():
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(POLL)


def _make(graph: Optional[FakeGraph] = None, gate: Optional[ReviewGate] = None):
    bus = RecordingBus()
    graph = graph if graph is not None else FakeGraph(list(FULL_RUN))
    gate = gate if gate is not None else ReviewGate(timeout=10.0)
    return JobRunner(graph=graph, bus=bus, review_gate=gate), graph, bus, gate


def _ends_with(runner: JobRunner, bus: RecordingBus, timeout: float = DEADLINE) -> None:
    runner.join(timeout)
    assert not runner.is_alive(), f"worker treo, events={bus.kinds()}"


def _await_plan(bus: RecordingBus, gate: ReviewGate) -> None:
    """Chờ pause 1. Sau khi có plan_ready thì resolve là luôn kịp — không mất
    tín hiệu, vì `resolve` set event trước cả khi worker kịp gọi `wait()`."""
    assert _wait_until(lambda: "plan_ready" in bus.kinds()), (
        f"không thấy plan_ready, events={bus.kinds()}"
    )
    assert gate.is_resolved() is False, "gate phải chưa resolve trước khi UI quyết"


def _run_approved(
    phases: Optional[list[str]] = None,
    kept: Optional[list[dict[str, Any]]] = None,
    graph: Optional[FakeGraph] = None,
) -> tuple[JobRunner, FakeGraph, RecordingBus, ReviewGate]:
    """Spawn → plan_ready → approve → chờ thread kết thúc rồi mới assert."""
    runner, graph, bus, gate = _make(graph=graph)
    runner.spawn(
        build_initial_state("test login", phases=phases if phases is not None else ["api", "ui"])
    )
    _await_plan(bus, gate)
    gate.resolve([CASE_API] if kept is None else kept)
    _ends_with(runner, bus)
    return runner, graph, bus, gate


# --------------------------------------------------------------------------
# build_initial_state
# --------------------------------------------------------------------------


def test_build_initial_state_defaults():
    s = build_initial_state("Test login", phases=["api"])
    assert s["user_request"] == "Test login"
    assert s["phases"] == ["api"]
    assert s["human_approved"] is False
    assert s["current_step"] == "start"
    assert s["ui_headed"] is False
    assert s["job_id"]


def test_build_initial_state_ui_headed():
    assert build_initial_state("t", phases=["ui"], ui_headed=True)["ui_headed"] is True


def test_build_initial_state_optional_inputs_default_to_empty():
    s = build_initial_state("t", phases=["api"])
    assert s["documents"] == []
    assert s["code_paths"] == []
    assert s["openapi_spec"] is None
    assert s["test_plan"] is None
    assert s["generated_tests"] == []
    assert s["shared_context"] == {}
    assert s["messages"] == []
    assert s["execution_result"] is None
    assert s["report_path"] is None
    assert s["final_summary"] is None
    assert s["error"] is None


def test_build_initial_state_covers_every_agent_state_key():
    from agents.state import AgentState

    s = build_initial_state("t", phases=["api"])
    assert set(s) == set(AgentState.__annotations__)


def test_build_initial_state_job_id_is_a_fresh_uuid4():
    a = build_initial_state("t", phases=["api"])["job_id"]
    b = build_initial_state("t", phases=["api"])["job_id"]
    assert a != b
    assert uuid.UUID(a).version == 4


def test_build_initial_state_does_not_alias_caller_lists():
    phases, documents, code_paths = ["api"], ["doc"], ["src"]
    s = build_initial_state("t", phases=phases, documents=documents, code_paths=code_paths)
    phases.append("ui")
    documents.append("doc2")
    code_paths.append("src2")
    assert s["phases"] == ["api"]
    assert s["documents"] == ["doc"]
    assert s["code_paths"] == ["src"]


# --------------------------------------------------------------------------
# Vòng đời thread
# --------------------------------------------------------------------------


def test_is_alive_and_cancelled_before_spawn():
    runner, *_ = _make()
    assert runner.is_alive() is False
    assert runner.cancelled is False


def test_join_with_timeout_returns_while_gate_still_held():
    runner, graph, bus, gate = _make(gate=ReviewGate(timeout=1.0))
    runner.spawn(build_initial_state("t", phases=["api"]))
    _await_plan(bus, gate)
    runner.join(timeout=0.05)
    assert runner.is_alive() is True, "gate chưa resolve thì thread phải còn sống"
    gate.resolve([CASE_API])
    _ends_with(runner, bus)


def test_thread_is_daemon():
    runner, graph, bus, gate = _make()
    runner.spawn(build_initial_state("t", phases=["api"]))
    _await_plan(bus, gate)
    assert runner._thread.daemon is True
    gate.resolve([CASE_API])
    _ends_with(runner, bus)


def test_spawn_twice_is_a_programming_error():
    runner, graph, bus, gate = _make()
    runner.spawn(build_initial_state("t", phases=["api"]))
    try:
        with pytest.raises(RuntimeError):
            runner.spawn(build_initial_state("t", phases=["api"]))
    finally:
        gate.resolve([CASE_API])
        _ends_with(runner, bus)


def test_thread_id_is_the_job_id_and_stream_uses_values_mode():
    state = build_initial_state("t", phases=["api"])
    runner, graph, bus, gate = _make()
    runner.spawn(state)
    _await_plan(bus, gate)
    gate.resolve([CASE_API])
    _ends_with(runner, bus)

    assert graph.stream_states == [state, None, None], "pass 1 có input, pass sau resume"
    assert [c["configurable"]["thread_id"] for c in graph.stream_configs] == [
        state["job_id"]
    ] * 3
    assert graph.stream_modes == ["values"] * 3
    assert all(
        cfg["configurable"]["thread_id"] == state["job_id"] for cfg, _ in graph.update_calls
    )


def test_state_without_job_id_still_runs():
    graph = FakeGraph([PLANNER_DONE])
    runner, graph, bus, gate = _make(graph=graph)
    state = build_initial_state("t", phases=["api"])
    del state["job_id"]
    runner.spawn(state)
    _await_plan(bus, gate)
    gate.resolve(None)
    _ends_with(runner, bus)
    assert "cancelled" in bus.kinds()


# --------------------------------------------------------------------------
# Pause 1 — human review
# --------------------------------------------------------------------------


def test_approve_resumes_and_reaches_run_done():
    runner, graph, bus, gate = _run_approved(kept=[CASE_API, CASE_UI])
    done = bus.one("run_done")
    assert done["report_path"] == "/reports/report_x.json"
    assert done["summary"] == "done"
    assert [e.kind for e in bus.terminal()] == ["run_done"], "đúng 1 event kết thúc"
    assert graph.pass_count() == 3, "planner / generator / executors+reporter"


def test_reject_stops_before_the_generator():
    runner, graph, bus, gate = _make()
    runner.spawn(build_initial_state("t", phases=["api", "ui"]))
    _await_plan(bus, gate)
    gate.resolve(None)
    _ends_with(runner, bus)

    assert bus.one("cancelled")["phase"] == "review"
    assert "run_done" not in bus.kinds()
    assert graph.cursor == 1, "chỉ được tiêu thụ tới planner_done"
    assert graph.update_calls == [], "reject thì không ghi gì vào graph"
    assert "generator_done" not in [s.get("current_step") for s in graph.snapshots]


def test_unticking_everything_stops_the_run():
    runner, graph, bus, gate = _make()
    runner.spawn(build_initial_state("t", phases=["api", "ui"]))
    _await_plan(bus, gate)
    gate.resolve([])
    _ends_with(runner, bus)

    assert bus.one("cancelled")["phase"] == "review"
    assert "run_done" not in bus.kinds()
    assert graph.update_calls == []
    assert any(e.kind == "log" for e in bus.events()), "phải giải thích vì sao dừng"


def test_approve_writes_only_kept_cases_and_the_approved_flag():
    runner, graph, bus, gate = _run_approved(kept=[CASE_UI])
    _, values = graph.update_calls[0]
    assert [c["id"] for c in values["test_plan"]["test_cases"]] == ["TC_2"]
    assert values["test_plan"]["title"] == "P", "giữ nguyên phần còn lại của plan"
    assert values["test_plan"]["summary"] == "s"
    assert values["human_approved"] is True


def test_generator_never_sees_unticked_cases():
    """Chốt chặn ở pause 1: plan phải đã lọc *trước* khi generator chạy."""
    runner, graph, bus, gate = _run_approved(kept=[CASE_API])
    seen = graph.snapshot_for("generator_done")
    assert [c["id"] for c in seen["test_plan"]["test_cases"]] == ["TC_1"]
    assert seen["human_approved"] is True


def test_plan_ready_carries_the_plan_and_count():
    runner, graph, bus, gate = _run_approved()
    payload = bus.one("plan_ready")
    assert payload["count"] == 2
    assert payload["test_plan"] == PLANNER_DONE["test_plan"]


def test_plan_ready_count_is_zero_when_the_plan_has_no_cases():
    runner, graph, bus, gate = _make(
        graph=FakeGraph(
            [{"current_step": "planner_done", "test_plan": {"title": "P"}, "error": None}]
        )
    )
    runner.spawn(build_initial_state("t", phases=["api"]))
    _await_plan(bus, gate)
    gate.resolve(None)
    _ends_with(runner, bus)
    assert bus.one("plan_ready") == {"test_plan": {"title": "P"}, "count": 0}


def test_gate_timeout_emits_run_error_not_cancel():
    """Spec §4.3: hết giờ duyệt là LỖI, không phải "người dùng bấm huỷ".

    Trả `None` cho cả hai thì UI nói "Đã huỷ" y hệt lúc bấm Stop, người dùng
    tưởng mình chủ động dừng.
    """
    runner, graph, bus, gate = _make(gate=ReviewGate(timeout=0.05))
    runner.spawn(build_initial_state("t", phases=["api"]))
    _ends_with(runner, bus)
    kinds = bus.kinds()
    assert "run_error" in kinds, f"timeout phải báo run_error, got {kinds}"
    assert "cancelled" not in kinds, "timeout không được báo cancelled"
    assert "run_done" not in kinds
    msg = bus.one("run_error")["message"]
    assert "0s" in msg or "review" in msg.lower(), msg


def test_gate_reject_still_emits_cancelled():
    """Reject thật vẫn phải là `cancelled` — timeout sentinel không nuốt mất."""
    runner, graph, bus, gate = _make(gate=ReviewGate(timeout=5.0))
    runner.spawn(build_initial_state("t", phases=["api"]))
    time.sleep(0.2)
    gate.resolve(None)
    _ends_with(runner, bus)
    kinds = bus.kinds()
    assert "cancelled" in kinds, kinds
    assert "run_error" not in kinds


# --------------------------------------------------------------------------
# Pause 2 — lọc theo phases
# --------------------------------------------------------------------------


def test_phase_filter_removes_unselected_types_before_executors():
    runner, graph, bus, gate = _run_approved(phases=["api"], kept=[CASE_API, CASE_UI])
    _, values = graph.update_calls[1]
    assert [t["id"] for t in values["generated_tests"]] == ["TC_1"]
    # Executor đọc state sau update_state → không thấy case ui.
    assert [t["id"] for t in graph.snapshot_for("api_executor_done")["generated_tests"]] == [
        "TC_1"
    ]
    assert "run_done" in bus.kinds()


def test_phase_filter_keeps_matching_types_without_logging():
    graph = FakeGraph([PLANNER_DONE, GENERATOR_DONE, REPORTER_DONE])
    runner, graph, bus, gate = _run_approved(kept=[CASE_API, CASE_UI], graph=graph)
    _, values = graph.update_calls[1]
    assert [t["id"] for t in values["generated_tests"]] == ["TC_1", "TC_2"]
    assert bus.payloads("log") == [], "không giảm gì thì không cần log"
    assert "run_done" in bus.kinds()


def test_phase_filter_logs_when_it_reduces_the_count():
    graph = FakeGraph([PLANNER_DONE, GENERATOR_DONE, REPORTER_DONE])
    runner, graph, bus, gate = _run_approved(phases=["ui"], kept=[CASE_API, CASE_UI], graph=graph)
    log = bus.one("log")
    assert log["level"] == "info"
    assert "ui" in log["text"]
    _, values = graph.update_calls[1]
    assert [t["id"] for t in values["generated_tests"]] == ["TC_2"]


def test_phase_filter_removing_everything_errors_and_skips_executors():
    graph = FakeGraph([PLANNER_DONE, GENERATOR_DONE, API_DONE, REPORTER_DONE])
    runner, graph, bus, gate = _run_approved(
        phases=["chaos"], kept=[CASE_API, CASE_UI], graph=graph
    )
    err = bus.one("run_error")
    assert "chaos" in err["message"]
    assert "run_done" not in bus.kinds()
    assert len(graph.update_calls) == 1, "không được ghi generated_tests rỗng"
    assert graph.cursor == 2, "executor không được chạy"


def test_empty_phases_means_no_filtering():
    """phases rỗng = cho phép tất cả, giống CLI."""
    graph = FakeGraph([PLANNER_DONE, GENERATOR_DONE, REPORTER_DONE])
    runner, graph, bus, gate = _run_approved(phases=[], kept=[CASE_API, CASE_UI], graph=graph)
    _, values = graph.update_calls[1]
    assert [t["id"] for t in values["generated_tests"]] == ["TC_1", "TC_2"]
    assert "run_done" in bus.kinds()


def test_generator_producing_nothing_is_an_error():
    graph = FakeGraph(
        [
            PLANNER_DONE,
            {"current_step": "generator_done", "generated_tests": [], "error": None},
            REPORTER_DONE,
        ]
    )
    runner, graph, bus, gate = _run_approved(kept=[CASE_API], graph=graph)
    err = bus.one("run_error")
    assert err["step"] == "generator_done"
    assert "run_done" not in bus.kinds()
    assert graph.cursor == 2


# --------------------------------------------------------------------------
# Lỗi
# --------------------------------------------------------------------------


def test_error_in_state_stops_the_run_and_reports_the_step():
    graph = FakeGraph(
        [
            PLANNER_DONE,
            GENERATOR_DONE,
            {"current_step": "api_executor_failed", "error": "toxiproxy chết"},
            REPORTER_DONE,
        ]
    )
    runner, graph, bus, gate = _run_approved(kept=[CASE_API, CASE_UI], graph=graph)
    err = bus.one("run_error")
    assert err["message"] == "toxiproxy chết"
    assert err["step"] == "api_executor_failed"
    assert "run_done" not in bus.kinds()
    assert graph.cursor == 3, "reporter không được chạy"


def test_planner_error_stops_before_review():
    graph = FakeGraph([{"current_step": "planner_failed", "error": "không sinh được plan"}])
    runner, graph, bus, gate = _make(graph=graph)
    runner.spawn(build_initial_state("t", phases=["api"]))
    _ends_with(runner, bus)
    assert bus.one("run_error")["step"] == "planner_failed"
    assert "plan_ready" not in bus.kinds(), "plan hỏng thì không mở modal review"
    assert graph.update_calls == []


def test_graph_raising_in_first_pass_emits_run_error():
    graph = FakeGraph(list(FULL_RUN), fail_before_step="planner_done")
    runner, graph, bus, gate = _make(graph=graph)
    runner.spawn(build_initial_state("t", phases=["api"]))
    _ends_with(runner, bus)
    err = bus.one("run_error")
    assert "boom before planner_done" in err["message"]
    assert "RuntimeError" in err["message"]
    assert "run_done" not in bus.kinds()
    assert gate.is_resolved() is True, "thread chết thì gate phải được giải phóng"


def test_crash_at_the_gate_emits_run_error_and_releases_the_gate():
    """Chết đúng lúc UI đang chờ: modal không được treo vĩnh viễn."""
    runner, graph, bus, gate = _make(gate=RaisingGate(timeout=10.0))
    runner.spawn(build_initial_state("t", phases=["api"]))
    _ends_with(runner, bus)
    assert "gate hỏng" in bus.one("run_error")["message"]
    assert "run_done" not in bus.kinds()
    assert gate.is_resolved() is True


def test_crash_in_update_state_emits_run_error():
    class ExplodingUpdate(FakeGraph):
        def update_state(self, config, values, as_node=None):
            raise RuntimeError("checkpoint chết")

    runner, graph, bus, gate = _make(graph=ExplodingUpdate(list(FULL_RUN)))
    runner.spawn(build_initial_state("t", phases=["api"]))
    _await_plan(bus, gate)
    gate.resolve([CASE_API])
    _ends_with(runner, bus)
    assert "checkpoint chết" in bus.one("run_error")["message"]
    assert "run_done" not in bus.kinds()


def test_graph_ending_before_stop_step_is_an_error_not_a_silent_success():
    graph = FakeGraph([{"current_step": "waiting_human_review", "error": None}])
    runner, graph, bus, gate = _make(graph=graph)
    runner.spawn(build_initial_state("t", phases=["api"]))
    _ends_with(runner, bus)
    err = bus.one("run_error")
    assert "planner_done" in err["message"]
    assert "run_done" not in bus.kinds()


def test_graph_yielding_nothing_is_an_error():
    runner, graph, bus, gate = _make(graph=FakeGraph([]))
    runner.spawn(build_initial_state("t", phases=["api"]))
    _ends_with(runner, bus)
    err = bus.one("run_error")
    assert "không trả về state nào" in err["message"]
    assert "run_done" not in bus.kinds()


def test_graph_yielding_non_dict_is_skipped_not_crashed():
    class SloppyGraph(FakeGraph):
        def stream(self, state, config=None, stream_mode=None):
            if state is not None:
                yield "không phải dict"
                yield None
            yield PLANNER_DONE

    runner, graph, bus, gate = _make(graph=SloppyGraph(list(FULL_RUN)))
    runner.spawn(build_initial_state("t", phases=["api"]))
    _await_plan(bus, gate)
    gate.resolve(None)
    _ends_with(runner, bus)
    assert "plan_ready" in bus.kinds()


@pytest.mark.parametrize(
    "steps, expect",
    [
        ([{"current_step": "planner_failed", "error": "a"}], "a"),
        ([{"current_step": "waiting_human_review"}], "planner_done"),
        ([], "không trả về state nào"),
    ],
)
def test_error_events_carry_message_and_step(steps, expect):
    runner, graph, bus, gate = _make(graph=FakeGraph(steps))
    runner.spawn(build_initial_state("t", phases=["api"]))
    _ends_with(runner, bus)
    payload = bus.one("run_error")
    assert set(payload) == {"message", "step"}
    assert isinstance(payload["message"], str)
    assert expect in payload["message"]


# --------------------------------------------------------------------------
# Bất biến: mọi outcome đều kết thúc thread + giải phóng gate
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "scenario, terminal",
    [
        ("success", "run_done"),
        ("reject", "cancelled"),
        ("empty_selection", "cancelled"),
        ("cancel", "cancelled"),
        ("state_error", "run_error"),
        ("early_end", "run_error"),
        ("crash", "run_error"),
    ],
)
def test_every_outcome_ends_the_thread_and_releases_the_gate(scenario, terminal):
    graphs = {
        "success": FakeGraph(list(FULL_RUN)),
        "reject": FakeGraph(list(FULL_RUN)),
        "empty_selection": FakeGraph(list(FULL_RUN)),
        "cancel": FakeGraph(list(FULL_RUN)),
        "state_error": FakeGraph([{"current_step": "planner_failed", "error": "x"}]),
        "early_end": FakeGraph([{"current_step": "waiting_human_review"}]),
        "crash": FakeGraph(list(FULL_RUN), fail_before_step="planner_done"),
    }
    runner, graph, bus, gate = _make(graph=graphs[scenario])
    runner.spawn(build_initial_state("t", phases=["api"]))

    if scenario == "cancel":
        assert _wait_until(lambda: graph.cursor >= 1), "worker chưa vào pass 1"
        runner.cancel()
    elif scenario == "reject":
        _await_plan(bus, gate)
        gate.resolve(None)
    elif scenario == "empty_selection":
        _await_plan(bus, gate)
        gate.resolve([])
    else:
        # success + 3 nhánh lỗi: gate có resolve hay không đều phải tự kết thúc.
        _wait_until(lambda: "plan_ready" in bus.kinds() or bus.terminal())
        gate.resolve([CASE_API])

    _ends_with(runner, bus)
    assert gate.is_resolved() is True, f"{scenario}: gate bị bỏ rơi"
    assert [e.kind for e in bus.terminal()] == [terminal], f"{scenario}: {bus.kinds()}"


# --------------------------------------------------------------------------
# Cancel
# --------------------------------------------------------------------------


def test_cancel_before_spawn_never_starts_the_graph():
    runner, graph, bus, gate = _make()
    runner.cancel()
    assert runner.cancelled is True
    runner.spawn(build_initial_state("t", phases=["api"]))
    _ends_with(runner, bus)
    assert bus.one("cancelled")["phase"] == "start"
    assert graph.stream_states == [], "graph không được chạy"
    assert gate.is_resolved() is True


def test_cancel_while_first_pass_runs_stops_at_the_boundary():
    entered, proceed = threading.Event(), threading.Event()

    def on_yield(step, call_index):
        if call_index == 0:
            entered.set()
            proceed.wait(DEADLINE)

    runner, graph, bus, gate = _make(graph=FakeGraph(list(FULL_RUN), on_yield=on_yield))
    runner.spawn(build_initial_state("t", phases=["api"]))
    assert entered.wait(DEADLINE), "worker không vào pass 1"
    runner.cancel()
    assert runner.cancelled is True
    proceed.set()
    _ends_with(runner, bus)

    assert bus.one("cancelled")["phase"] == "planner"
    assert "plan_ready" not in bus.kinds()
    assert graph.update_calls == []
    assert "run_done" not in bus.kinds()


def test_cancel_between_passes_stops_before_the_executors():
    """Chặn worker trong pass 2 rồi cancel → executor không được chạy."""
    entered, proceed = threading.Event(), threading.Event()

    def on_yield(step, call_index):
        if call_index == 1 and step.get("current_step") == "generator_done":
            entered.set()
            proceed.wait(DEADLINE)

    runner, graph, bus, gate = _make(graph=FakeGraph(list(FULL_RUN), on_yield=on_yield))
    runner.spawn(build_initial_state("t", phases=["api"]))
    _await_plan(bus, gate)
    gate.resolve([CASE_API, CASE_UI])
    assert entered.wait(DEADLINE), "worker không vào pass 2"
    runner.cancel()
    proceed.set()
    _ends_with(runner, bus)

    assert bus.one("cancelled")["phase"] == "generator"
    assert "run_done" not in bus.kinds()
    assert len(graph.update_calls) == 1, "chưa được ghi generated_tests"
    assert graph.cursor == 2, "executor không được chạy"


def test_cancel_releases_the_gate_so_a_waiter_wakes():
    runner, graph, bus, gate = _make(gate=ReviewGate(timeout=1.0))
    runner.spawn(build_initial_state("t", phases=["api"]))
    _await_plan(bus, gate)
    runner.cancel()
    _ends_with(runner, bus)
    assert gate.is_resolved() is True
    assert bus.one("cancelled")["phase"] == "review"


def test_cancelled_flag_stays_set_after_the_run_ends():
    runner, graph, bus, gate = _make()
    runner.spawn(build_initial_state("t", phases=["api"]))
    runner.cancel()
    _ends_with(runner, bus)
    assert runner.cancelled is True
    assert "run_done" not in bus.kinds()


# --------------------------------------------------------------------------
# Chuỗi event
# --------------------------------------------------------------------------


def test_event_sequence_for_a_full_run():
    runner, graph, bus, gate = _run_approved(kept=[CASE_API, CASE_UI])
    assert [
        (e.kind, e.payload.get("node"), e.payload.get("step")) for e in bus.events()
    ] == [
        ("node_start", "planner", None),
        ("node_end", "planner", "planner_done"),
        ("plan_ready", None, None),
        ("node_start", "generator", None),
        ("node_end", "generator", "generator_done"),
        ("node_start", "api_executor", None),
        ("node_end", "api_executor", "api_executor_done"),
        ("node_end", "ui_executor", "ui_executor_done"),
        ("node_end", "chaos_executor", "chaos_executor_done"),
        ("node_end", "performance_executor", "performance_executor_done"),
        ("node_end", "reporter", "reporter_done"),
        ("run_done", None, None),
    ]


def test_node_event_payloads_have_exact_keys():
    runner, graph, bus, gate = _run_approved()
    for payload in bus.payloads("node_start"):
        assert set(payload) == {"node"}
    for payload in bus.payloads("node_end"):
        assert set(payload) == {"node", "step"}


def test_stream_is_closed_at_every_stop_step():
    """Break giữa chừng phải đóng generator, không đợi GC mới dọn task."""
    runner, graph, bus, gate = _run_approved(kept=[CASE_API], graph=CloseSpyGraph(list(FULL_RUN)))
    assert graph.closes == 3, "mỗi pass đóng stream một lần"
    assert graph.pass_count() == 3


def test_stream_is_closed_even_when_the_run_ends_early():
    graph = CloseSpyGraph([{"current_step": "waiting_human_review"}])
    runner, graph, bus, gate = _make(graph=graph)
    runner.spawn(build_initial_state("t", phases=["api"]))
    _ends_with(runner, bus)
    assert graph.closes == 1
    assert "run_error" in bus.kinds()


def test_failed_step_still_emits_node_end_before_run_error():
    graph = FakeGraph([PLANNER_DONE, {"current_step": "generator_failed", "error": "x"}])
    runner, graph, bus, gate = _run_approved(kept=[CASE_API], graph=graph)
    kinds = [e.kind for e in bus.events()]
    assert kinds.index("node_end") < kinds.index("run_error")
    assert bus.payloads("node_end")[-1] == {
        "node": "generator",
        "step": "generator_failed",
    }


def test_resumed_pass_echoing_the_previous_step_does_not_duplicate_node_end():
    """LangGraph có thể echo lại state của checkpoint khi resume — đừng tick 2 lần."""
    graph = FakeGraph([PLANNER_DONE, GENERATOR_DONE, GENERATOR_DONE, REPORTER_DONE])
    runner, graph, bus, gate = _run_approved(kept=[CASE_API], graph=graph)
    assert [p["step"] for p in bus.payloads("node_end")] == [
        "planner_done",
        "generator_done",
        "reporter_done",
    ]


def test_sentinel_start_step_is_not_mapped_to_a_node():
    graph = FakeGraph([{"current_step": "start"}, PLANNER_DONE])
    runner, graph, bus, gate = _make(graph=graph)
    runner.spawn(build_initial_state("t", phases=["api"]))
    _await_plan(bus, gate)
    gate.resolve([CASE_API])
    _ends_with(runner, bus)
    assert [p["step"] for p in bus.payloads("node_end")] == ["planner_done"]


def test_node_mapping_covers_every_step_the_agents_emit():
    root = Path(__file__).resolve().parent.parent / "agents"
    steps: set[str] = set()
    for path in sorted(root.glob("*.py")):
        steps |= set(
            re.findall(r'"current_step":\s*"([a-z_]+)"', path.read_text(encoding="utf-8"))
        )
    steps.add("start")  # sentinel của build_initial_state, không phải node
    assert steps, "regex phải tìm được current_step trong agents/"
    unmapped = {s for s in steps if s != "start" and s not in _NODE_BY_STEP}
    assert not unmapped, f"thiếu node mapping cho: {sorted(unmapped)}"


# --------------------------------------------------------------------------
# Cô lập giữa các job
# --------------------------------------------------------------------------


def test_last_state_is_an_instance_attribute():
    assert "_last_state" not in JobRunner.__dict__, (
        "_last_state phải là instance attribute; đặt ở class sẽ khiến các job "
        "chạy song song dùng chung một state"
    )
    a = JobRunner(graph=None, bus=None)
    b = JobRunner(graph=None, bus=None)
    a._last_state = {"current_step": "planner_done"}
    assert b._last_state is None
    assert a._last_state == {"current_step": "planner_done"}


def test_two_runners_do_not_share_state_while_running_concurrently():
    plan_b = {
        "current_step": "planner_done",
        "test_plan": {"title": "UI plan", "test_cases": [CASE_UI]},
        "human_approved": False,
        "error": None,
    }
    graph_a = FakeGraph(list(FULL_RUN))
    graph_b = FakeGraph(
        [
            plan_b,
            {"current_step": "generator_done", "generated_tests": [TEST_UI], "error": None},
            REPORTER_DONE,
        ]
    )
    runner_a, _, bus_a, gate_a = _make(graph=graph_a)
    runner_b, _, bus_b, gate_b = _make(graph=graph_b)

    runner_a.spawn(build_initial_state("job A", phases=["api", "ui"]))
    runner_b.spawn(build_initial_state("job B", phases=["ui"]))
    assert _wait_until(
        lambda: "plan_ready" in bus_a.kinds() and "plan_ready" in bus_b.kinds()
    ), "cả hai job phải tới pause 1"

    gate_a.resolve([CASE_API])
    gate_b.resolve([CASE_UI])
    _ends_with(runner_a, bus_a)
    _ends_with(runner_b, bus_b)

    assert [c["id"] for c in graph_a.update_calls[0][1]["test_plan"]["test_cases"]] == ["TC_1"]
    assert [c["id"] for c in graph_b.update_calls[0][1]["test_plan"]["test_cases"]] == ["TC_2"]
    assert bus_a.one("plan_ready")["count"] == 2
    assert bus_b.one("plan_ready")["count"] == 1
    assert [t["id"] for t in graph_a.update_calls[1][1]["generated_tests"]] == ["TC_1", "TC_2"]
    assert [t["id"] for t in graph_b.update_calls[1][1]["generated_tests"]] == ["TC_2"]
    assert bus_a.one("run_done")["report_path"] == "/reports/report_x.json"
    assert bus_b.one("run_done")["report_path"] == "/reports/report_x.json"
    assert graph_a.pass_count() == 3 and graph_b.pass_count() == 3


# --------------------------------------------------------------------------
# Tích hợp bus thật (event loop thật)
# --------------------------------------------------------------------------


async def test_events_reach_the_real_eventbus_in_order():
    loop = asyncio.get_running_loop()
    bus = EventBus()
    assert bus.loop is loop, "bus phải bind vào loop đang chạy"

    graph = FakeGraph(list(FULL_RUN))
    gate = ReviewGate(timeout=10.0)
    runner = JobRunner(graph=graph, bus=bus, review_gate=gate)
    runner.spawn(build_initial_state("t", phases=["api", "ui"]))

    async def collect(until: str) -> list[Event]:
        got: list[Event] = []
        deadline = loop.time() + DEADLINE
        while True:
            while not bus.queue.empty():
                got.append(bus.queue.get_nowait())
            if any(e.kind == until for e in got) or any(
                e.kind in TERMINAL_KINDS for e in got
            ):
                return got
            if loop.time() >= deadline:
                return got
            await asyncio.sleep(POLL)  # nhường loop cho call_soon_threadsafe

    got = await collect("plan_ready")
    assert [e.kind for e in got] == ["node_start", "node_end", "plan_ready"]
    assert got[0].payload == {"node": "planner"}
    assert got[1].payload == {"node": "planner", "step": "planner_done"}
    assert got[2].payload["count"] == 2
    assert gate.is_resolved() is False

    gate.resolve([CASE_API])
    # Không block event loop: poll cho tới khi worker kết thúc.
    deadline = loop.time() + DEADLINE
    while runner.is_alive() and loop.time() < deadline:
        await asyncio.sleep(POLL)
    assert runner.is_alive() is False, f"worker treo, đã nhận {[e.kind for e in got]}"

    got = await collect("run_done")
    assert [e.kind for e in got] == [
        "node_start",  # generator
        "node_end",  # generator_done
        "node_start",  # api_executor
        "node_end",  # api_executor_done
        "node_end",  # ui_executor_done
        "node_end",  # chaos_executor_done
        "node_end",  # performance_executor_done
        "node_end",  # reporter_done
        "run_done",
    ]
    assert got[0].payload == {"node": "generator"}
    assert got[1].payload == {"node": "generator", "step": "generator_done"}
    assert got[2].payload == {"node": "api_executor"}
    assert got[3].payload == {"node": "api_executor", "step": "api_executor_done"}
    assert got[-1].payload == {"report_path": "/reports/report_x.json", "summary": "done"}
