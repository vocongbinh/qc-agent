from __future__ import annotations

import asyncio
import inspect
import re
import threading

import pytest

pytest.importorskip("textual")

from textual.app import App
from textual.message_pump import MessagePump
from textual.widgets import Button, Input

import tui.app as tui_app
from tui.app import REAP_AFTER_POLLS, QCTApp, format_result_line
from tui.bus import Event, EventBus
from tui.gate import ReviewGate
from tui.review import ReviewModel
from tui.runner import JobRunner
from tui.widgets.casestable import CaseTable
from tui.widgets.footer import StatusFooter
from tui.widgets.history import HistoryPane
from tui.widgets.logpane import LogPane
from tui.widgets.review_modal import ReviewModalScreen


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def plan(case_ids: list[str] | None = None) -> dict:
    ids = case_ids if case_ids is not None else ["A", "B"]
    return {
        "title": "Login flow",
        "scope": "api",
        "estimated_duration_min": 12,
        "risks": ["rate limit"],
        "test_cases": [
            {"id": cid, "title": f"case {cid}", "type": "api", "priority": "high"}
            for cid in ids
        ],
    }


def result(
    case_id: str = "A",
    status: str = "passed",
    case_type: str = "api",
    duration_ms: float = 12.0,
    title: str = "login works",
    error_message: str | None = None,
) -> dict:
    return {
        "id": case_id,
        "title": title,
        "type": case_type,
        "status": status,
        "duration_ms": duration_ms,
        "error_message": error_message,
    }


def report_file(directory, request: str = "test login") -> str:
    import json

    path = directory / "report_20260101_000000.json"
    path.write_text(
        json.dumps(
            {
                "timestamp": "20260101_000000",
                "user_request": request,
                "test_plan": {"title": "Login flow", "scope": "api"},
                "execution_result": {
                    "total": 1,
                    "passed": 1,
                    "failed": 0,
                    "error": 0,
                    "skipped": 0,
                    "duration_ms": 12.0,
                    "details": [],
                },
            }
        ),
        encoding="utf-8",
    )
    return str(path)


class FakeGraph:
    """Giả `qc_graph`: cùng surface `stream` / `update_state` mà JobRunner gọi."""

    def __init__(self, script: list[list[dict]] | None = None) -> None:
        self.script = list(script or [])
        self.updates: list[dict] = []
        self.stream_calls = 0

    def stream(self, state, config, stream_mode="values"):
        self.stream_calls += 1
        index = self.stream_calls - 1
        states = self.script[index] if 0 <= index < len(self.script) else []
        return iter([dict(s) for s in states])

    def update_state(self, config, values) -> None:
        self.updates.append(dict(values))


class PerThreadFakeGraph:
    """Giả `qc_graph` nhưng tách state theo `thread_id`, đúng như LangGraph.

    `FakeGraph` đếm `stream_calls` toàn cục nên chỉ dùng được cho một job. Muốn
    chạy hai job trên cùng một app thì phải mô phỏng việc mỗi `thread_id` có
    vị trí stream riêng — nếu không, job thứ 2 sẽ đọc tiếp chuỗi của job thứ 1.
    """

    def __init__(self) -> None:
        self._pos: dict[str, int] = {}
        self.updates: list[dict] = []
        self.threads_used: list[str] = []

    def stream(self, state, config, stream_mode="values"):
        thread_id = config["configurable"]["thread_id"]
        if thread_id not in self._pos:
            self.threads_used.append(thread_id)
        index = self._pos.setdefault(thread_id, 0)
        script = happy_script()
        # Mỗi thread một plan riêng, để assert 2 job là 2 lần chạy độc lập.
        for state in script[0]:
            if state.get("test_plan"):
                state["test_plan"] = {**state["test_plan"], "title": f"Plan {thread_id[:8]}"}
        # Tăng TRƯỚC khi yield: JobRunner break giữa chừng sẽ `close()` generator
        # nên code sau vòng lặp yield sẽ không bao giờ chạy.
        self._pos[thread_id] = index + 1
        states = script[index] if 0 <= index < len(script) else []
        return iter([dict(s) for s in states])

    def update_state(self, config, values) -> None:
        self.updates.append(dict(values))


class StubRunner:
    """Đứng thay JobRunner để test state machine không cần thread thật."""

    def __init__(self, alive: bool = True) -> None:
        self.cancelled = False
        self.cancel_calls = 0
        self.alive = alive

    def cancel(self) -> None:
        self.cancelled = True
        self.cancel_calls += 1

    def is_alive(self) -> bool:
        return self.alive


def happy_script() -> list[list[dict]]:
    """3 pass: planner_done → generator_done → reporter_done."""
    generated = [
        {"id": "A", "title": "login", "type": "api"},
        {"id": "B", "title": "logout", "type": "ui"},
    ]
    return [
        [{"current_step": "planner_done", "test_plan": plan()}],
        [{"current_step": "generator_done", "generated_tests": generated}],
        [
            {
                "current_step": "reporter_done",
                "report_path": "reports/report_1.json",
                "final_summary": "Đã chạy 2 test case.",
            }
        ],
    ]


class RecordingGate(ReviewGate):
    """Gate ghi lại mọi payload để assert — `JobRunner.finally` gọi
    `release()` (tức `resolve(None)`) nên `gate.wait()` sau khi run xong
    không còn phản ánh giá trị đã duyệt."""

    def __init__(self, timeout: float = 5.0) -> None:
        super().__init__(timeout)
        self.resolved: list[object] = []

    def resolve(self, kept_cases) -> None:
        self.resolved.append(kept_cases)
        super().resolve(kept_cases)

    @property
    def first_kept(self):
        for value in self.resolved:
            if value is not None:
                return value
        return None


async def wait_until(predicate, timeout: float = 5.0, step: float = 0.01) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not predicate():
        if loop.time() > deadline:
            raise AssertionError("timeout waiting for condition")
        await asyncio.sleep(step)


def bindings() -> dict[str, str]:
    out: dict[str, str] = {}
    for binding in QCTApp.BINDINGS:
        if isinstance(binding, tuple):
            out[binding[0]] = binding[1]
        else:
            out[binding.key] = binding.action
    return out


def shadowed_names(cls) -> list[str]:
    """Tên method / attribute của app trùng internal của base class."""
    base = {n for klass in cls.__mro__[1:] for n in vars(klass)}
    # `dir()` bắt cả thứ kế thừa qua property/slot mà `vars()` không lộ.
    for klass in cls.__mro__[1:]:
        base |= set(dir(klass))
    methods = {n for n, v in vars(cls).items() if inspect.isfunction(v)}
    attrs = set(re.findall(r"self\.(\w+)\s*(?::[^=\n]+)?=", inspect.getsource(cls.__init__)))
    # Hook của framework: cố ý đè, không phải shadowing ngoài ý muốn.
    allowed = (
        {"__init__", "compose", "on_mount", "on_unmount"}
        | {n for n in methods if n.startswith("on_")}
    )
    return sorted(n for n in methods | attrs if n in base and n not in allowed)


def emitted_event_kinds() -> set[str]:
    """Mọi kind mà runner và emitter thực sự phát ra."""
    import agents.emitter as emitter_module
    import tui.runner as runner_module

    kinds = set(
        re.findall(r'_emit\(\s*"([a-z_]+)"', inspect.getsource(runner_module))
    )
    kinds |= set(
        re.findall(r'kind="([a-z_]+)"', inspect.getsource(emitter_module))
    )
    return kinds


@pytest.fixture
def app(tmp_path) -> QCTApp:
    return QCTApp(graph=FakeGraph(), reports_dir=tmp_path)


@pytest.fixture
def runnable(app: QCTApp) -> QCTApp:
    app.footer.set_request("test login")
    app.footer.set_all_phases(True)
    return app


# ---------------------------------------------------------------------------
# gotcha: không đè internal của Textual
# ---------------------------------------------------------------------------


def test_app_does_not_shadow_textual_internals():
    assert shadowed_names(QCTApp) == []


def test_collision_helper_actually_detects_a_collision():
    """Bảo thân test shadow không im lặng khi hỏng."""

    class Sneaky(QCTApp):
        def notify(self, *args, **kwargs) -> None:  # trùng `App.notify`
            ...

        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.sub_title = None  # trùng reactive `App.sub_title`

    assert shadowed_names(Sneaky) == ["notify", "sub_title"]


def test_every_emitted_event_kind_is_handled():
    """Kind mới ở runner/emitter mà app quên xử lý thì test này phải đỏ."""
    assert emitted_event_kinds() <= tui_app.HANDLED_EVENT_KINDS


def test_handled_kinds_match_the_documented_contract():
    assert tui_app.HANDLED_EVENT_KINDS == {
        "node_start",
        "node_end",
        "plan_ready",
        "test_result",
        "log",
        "run_done",
        "run_error",
        "cancelled",
    }


def test_app_defines_no_render_method():
    """`_render` trả None làm app crash lúc mount ('render_strips')."""
    assert "_render" not in vars(QCTApp)
    assert not re.search(r"def _render\b", inspect.getsource(tui_app))


def test_app_does_not_reuse_message_pump_running_flag():
    """`MessagePump._running` là cờ nội bộ — app dùng `running` riêng."""
    assert "_running" not in vars(QCTApp)
    # `MessagePump.__init__` gán `self._running` (message_pump.py:120) —
    # app chỉ có `running` của riêng nó.
    assert "self._running" in inspect.getsource(MessagePump.__init__)
    assert "running" not in vars(MessagePump)
    a = QCTApp(graph=FakeGraph(), reports_dir=".")
    assert a.running is False
    assert a.is_running is False
    a.running = True
    assert a.is_running is False


def test_case_table_state_lives_in_results_not_rows():
    a = QCTApp(graph=FakeGraph(), reports_dir=".")
    assert isinstance(a.case_table, CaseTable)
    a._handle_event(Event(kind="test_result", payload=result("A")))
    assert [r["id"] for r in a.case_table.results] == ["A"]
    # `rows` là dict cell của chính Textual — app không được ghi vào đó.
    assert isinstance(a.case_table.rows, dict)
    assert list(a.case_table.rows.keys()) != ["A"]


# ---------------------------------------------------------------------------
# bindings
# ---------------------------------------------------------------------------


def test_bindings_cover_the_documented_keys():
    b = bindings()
    assert b["r"] == "start"
    assert b["c"] == "cancel_run"
    assert b["f"] == "focus_request"
    assert b["?"] == "show_help_panel"
    assert b["q"] == "request_quit"
    assert re.fullmatch(r"toggle_phase\('api'\)", b["1"])
    assert re.fullmatch(r"toggle_phase\('ui'\)", b["2"])
    assert re.fullmatch(r"toggle_phase\('chaos'\)", b["3"])
    assert re.fullmatch(r"toggle_phase\('performance'\)", b["4"])


def test_bindings_point_at_real_actions():
    app = QCTApp(graph=FakeGraph(), reports_dir=".")
    for action in ("start", "cancel_run", "focus_request", "request_quit"):
        assert hasattr(app, f"action_{action}"), action
    assert hasattr(app, "action_toggle_phase")
    # `?` dùng action có sẵn của Textual, không tự viết lại.
    assert "show_help_panel" not in vars(QCTApp)


def test_quit_binding_does_not_override_app_action_quit():
    """App.action_quit đã tồn tại (ctrl+q) — không được đè trong im lặng."""
    assert "action_quit" not in vars(QCTApp)
    assert hasattr(App, "action_quit")


# ---------------------------------------------------------------------------
# gotcha: EventBus phải bind sau khi loop tồn tại
# ---------------------------------------------------------------------------


def test_bus_created_in_init_is_unbound_before_mount(tmp_path):
    a = QCTApp(graph=FakeGraph(), reports_dir=tmp_path)
    assert isinstance(a.bus, EventBus)
    assert a.bus.loop is None


async def test_bus_is_bound_to_textual_loop_after_mount(tmp_path):
    a = QCTApp(graph=FakeGraph(), reports_dir=tmp_path)
    async with a.run_test() as pilot:
        # Textual 8.2.8 gán `App._loop` trong `run_async` (app.py:2281) trước
        # khi mount — xác nhận điều đó thay vì giả định.
        assert a._loop is not None
        assert a.bus.loop is a._loop
        assert a.bus.loop is asyncio.get_running_loop()
        assert not a.bus.loop.is_closed()


async def test_emit_from_worker_thread_reaches_the_app(tmp_path):
    """Bus đã bind loop → emit từ thread thật không bị rơi im lặng nữa."""
    a = QCTApp(graph=FakeGraph(), reports_dir=tmp_path)
    async with a.run_test():
        event = Event(kind="log", payload={"level": "info", "text": "ping"})
        thread = threading.Thread(target=a.bus.emit, args=(event,))
        thread.start()
        thread.join(2)
        await wait_until(lambda: a.log_pane.lines == ["ping"])


def test_unbound_bus_emit_is_a_silent_noop(tmp_path):
    a = QCTApp(graph=FakeGraph(), reports_dir=tmp_path)
    a.bus.emit(Event(kind="log", payload={"text": "lost"}))
    assert a.bus.queue.qsize() == 0


# ---------------------------------------------------------------------------
# start_run / spawn_job tách rõ
# ---------------------------------------------------------------------------


def test_start_run_requires_a_request(app: QCTApp):
    assert app.start_run() is False
    assert app.running is False
    assert app.last_error


def test_start_run_requires_at_least_one_phase(app: QCTApp):
    app.footer.set_request("test login")
    app.footer.set_all_phases(False)
    assert app.start_run() is False
    assert app.running is False
    assert app.last_error


def test_start_run_refuses_while_running(runnable: QCTApp):
    assert runnable.start_run() is True
    assert runnable.start_run() is False
    assert runnable.running is True


def test_silence_agent_console_redirects_a_real_agent_console(monkeypatch):
    """Chỉ TUI được đổi `console` của agents.* — không redirect stdout."""
    import io
    import sys

    import agents.reporter as reporter_module
    from rich.console import Console

    original = reporter_module.console
    assert isinstance(original, Console)
    monkeypatch.setattr(reporter_module, "console", original)

    tui_app.silence_agent_console()

    assert isinstance(reporter_module.console, Console)
    assert isinstance(reporter_module.console.file, io.StringIO)
    assert reporter_module.console.file is not sys.stdout
    assert reporter_module.console.width == 200


def test_start_run_does_not_spawn_a_thread(runnable: QCTApp):
    assert runnable.start_run() is True
    assert runnable.running is True
    assert runnable.runner is None
    assert runnable.pending_state is not None
    assert runnable.pending_state["user_request"] == "test login"
    assert runnable.pending_state["job_id"]
    assert runnable.pending_state["current_step"] == "start"


def test_start_run_passes_selected_phases_to_state(app: QCTApp):
    app.footer.set_request("ui only")
    app.footer.set_all_phases(False)
    app.footer.toggle_phase("ui")
    assert app.start_run() is True
    assert app.pending_state["phases"] == ["ui"]


def test_start_run_clears_previous_run_state(runnable: QCTApp):
    runnable._handle_event(Event(kind="test_result", payload=result("OLD")))
    runnable._handle_event(Event(kind="log", payload={"level": "info", "text": "old"}))
    assert runnable.received_results == 1
    assert "old" in runnable.log_pane.lines

    assert runnable.start_run() is True
    assert runnable.received_results == 0
    assert runnable.log_pane.lines == ["▶ Starting: test login"]
    assert runnable.case_table.results == []
    assert runnable.pending_plan is None
    assert runnable.current_phase == "start"


def test_start_run_clears_last_error(app: QCTApp):
    assert app.start_run() is False
    assert app.last_error
    app.footer.set_request("test login")
    app.footer.set_all_phases(True)
    assert app.start_run() is True
    assert app.last_error is None


def test_spawn_job_without_start_run_is_a_noop(app: QCTApp):
    app.spawn_job()
    assert app.runner is None
    assert app.running is False


def test_spawn_job_creates_runner_and_sets_agent_bus(runnable: QCTApp, monkeypatch):
    monkeypatch.setattr(tui_app, "silence_agent_console", lambda: None)
    graph = FakeGraph([[{"current_step": "planner_done", "error": "boom"}]])
    runnable._graph = graph
    runnable.start_run()
    runnable.spawn_job()
    assert isinstance(runnable.runner, JobRunner)
    assert runnable.runner.graph is graph
    assert runnable.runner.bus is runnable.bus
    # Gate phải là bản `spawn_job` vừa tạo, không phải gate cũ đã dùng xong.
    assert runnable.runner.review_gate is runnable.review_gate
    runnable.runner.join(5)


def test_spawn_job_builds_a_fresh_gate_each_time(runnable: QCTApp, monkeypatch):
    """`ReviewGate` latch vĩnh viễn — dùng chung gate sẽ chỉ chạy được 1 job."""
    monkeypatch.setattr(tui_app, "silence_agent_console", lambda: None)

    def run_once():
        graph = FakeGraph([[{"current_step": "planner_done", "error": "boom"}]])
        runnable._graph = graph
        runnable.start_run()
        runnable.spawn_job()
        gate = runnable.review_gate
        runnable.runner.join(5)
        return gate

    first = run_once()
    second = run_once()
    assert first is not second, "phải tạo gate mới cho mỗi job"
    assert second.is_resolved() is True


def test_spawn_job_silences_agents_after_graph_resolution(runnable: QCTApp, monkeypatch):
    """Console của agents.* chỉ được đổi SAU khi module đã import."""
    order: list[str] = []
    graph = FakeGraph([[{"current_step": "planner_done", "error": "boom"}]])
    original_stream = graph.stream

    def traced(*args, **kwargs):
        order.append("stream")
        return original_stream(*args, **kwargs)

    graph.stream = traced
    runnable._graph = graph
    monkeypatch.setattr(
        tui_app, "silence_agent_console", lambda: order.append("silence")
    )
    monkeypatch.setattr(tui_app, "set_bus", lambda bus: order.append("set_bus"))

    runnable.start_run()
    runnable.spawn_job()
    runnable.runner.join(5)
    assert "silence" in order
    # silence phải tới trước lúc worker thực sự chạy graph.
    assert order.index("silence") < order.index("stream")


# ---------------------------------------------------------------------------
# event contract — từng dòng một
# ---------------------------------------------------------------------------


def test_node_start_sets_phase_and_logs(app: QCTApp):
    app._handle_event(Event(kind="node_start", payload={"node": "planner"}))
    assert app.current_phase == "planner"
    assert app.log_pane.lines == ["▶ planner"]


def test_node_end_logs_the_completed_step(app: QCTApp):
    app._handle_event(
        Event(kind="node_end", payload={"node": "planner", "step": "planner_done"})
    )
    assert len(app.log_pane.lines) == 1
    assert "planner" in app.log_pane.lines[0]
    assert "planner_done" in app.log_pane.lines[0]


def test_plan_ready_stores_plan_and_resolves_gate(app: QCTApp):
    app._handle_event(
        Event(kind="plan_ready", payload={"test_plan": plan(["A", "B"]), "count": 2})
    )
    assert app.pending_plan == plan(["A", "B"])
    assert app.plan_count == 2
    assert isinstance(app.review_gate, ReviewGate)
    assert app.review_gate.is_resolved()


def test_plan_ready_falls_back_to_count_when_missing(app: QCTApp):
    app._handle_event(Event(kind="plan_ready", payload={"test_plan": plan(["A"])}))
    assert app.plan_count == 1


def test_test_result_upserts_into_case_table(app: QCTApp):
    app._handle_event(Event(kind="test_result", payload=result("A")))
    assert app.case_table.results == [result("A")]
    assert app.received_results == 1
    assert app.footer.progress_text() == "api 1/1"


def test_test_result_uses_plan_count_for_progress_total(app: QCTApp):
    app._handle_event(
        Event(kind="plan_ready", payload={"test_plan": plan(["A", "B"]), "count": 4})
    )
    app._handle_event(Event(kind="test_result", payload=result("A", "passed", "ui")))
    assert app.footer.progress_text() == "ui 1/4"


def test_test_result_duplicate_id_updates_in_place(app: QCTApp):
    app._handle_event(Event(kind="test_result", payload=result("A", "running", "api")))
    app._handle_event(Event(kind="test_result", payload=result("A", "passed", "api")))
    assert app.received_results == 1
    assert len(app.case_table.results) == 1
    assert app.case_table.results[0]["status"] == "passed"


def test_test_result_logs_one_line(app: QCTApp):
    app._handle_event(Event(kind="test_result", payload=result("A", "failed")))
    assert len(app.log_pane.lines) == 1
    assert "A" in app.log_pane.lines[0]


def test_format_result_line_covers_shape_variants():
    assert format_result_line(result("A", "passed", "api", 12.0)) == "A passed 12ms · login works"
    assert format_result_line(
        {"id": "B", "status": "error", "type": "ui", "duration_ms": None,
         "error_message": "boom", "title": None}
    ) == "! B error · boom"
    assert format_result_line({}) == "? unknown"


def test_log_event_appends_with_level_mark(app: QCTApp):
    app._handle_event(Event(kind="log", payload={"level": "error", "text": "kaboom"}))
    app._handle_event(Event(kind="log", payload={"level": "info", "text": "fine"}))
    assert app.log_pane.lines == ["! kaboom", "fine"]


def test_run_done_marks_stopped_and_refreshes_history(runnable: QCTApp, tmp_path):
    report_file(tmp_path, "test login")
    runnable.start_run()
    assert runnable.running is True
    runnable._handle_event(
        Event(
            kind="run_done",
            payload={"report_path": "reports/report_1.json", "summary": "xong"},
        )
    )
    assert runnable.running is False
    assert runnable.stopping is False
    assert runnable.footer.running() is False
    assert runnable.last_report_path == "reports/report_1.json"
    assert [r.request for r in runnable.history_pane.runs] == ["test login"]
    assert runnable.pending_plan is None


def test_run_error_marks_stopped_and_surfaces_message(runnable: QCTApp):
    runnable.start_run()
    runnable._handle_event(
        Event(kind="run_error", payload={"message": "Graph chết", "step": "planner_done"})
    )
    assert runnable.running is False
    assert runnable.stopping is False
    assert runnable.last_error == "Graph chết"
    assert "Graph chết" in runnable.footer.status()
    assert runnable.log_pane.lines[-1].startswith("!")


def test_cancelled_marks_stopped_with_cancelled_state(runnable: QCTApp):
    runnable.start_run()
    runnable._handle_event(Event(kind="cancelled", payload={"phase": "review"}))
    assert runnable.running is False
    assert runnable.stopping is False
    assert "review" in runnable.footer.status()
    assert runnable.footer.running() is False


@pytest.mark.parametrize(
    "kind,payload",
    [
        ("run_done", {"report_path": "r.json", "summary": "ok"}),
        ("run_error", {"message": "boom", "step": "planner_done"}),
        ("cancelled", {"phase": "generator"}),
    ],
)
def test_every_terminal_event_clears_running(runnable: QCTApp, kind, payload):
    runnable.start_run()
    assert runnable.running is True
    runnable._handle_event(Event(kind=kind, payload=payload))
    assert runnable.running is False
    assert runnable.stopping is False
    assert runnable.footer.running() is False
    assert runnable.runner is None


def test_terminal_event_also_clears_stopping(runnable: QCTApp):
    runnable.start_run()
    runnable.stopping = True
    runnable._handle_event(Event(kind="cancelled", payload={"phase": "api_executor"}))
    assert runnable.stopping is False


def test_unknown_event_kind_is_ignored(app: QCTApp):
    app._handle_event(Event(kind="teleport", payload={"x": 1}))
    assert app.log_pane.lines == []
    assert app.running is False


def test_event_without_payload_is_survivable(app: QCTApp):
    app._handle_event(Event(kind="log"))
    assert app.log_pane.lines == [""]
    app._handle_event(Event(kind="test_result"))


# ---------------------------------------------------------------------------
# cancel
# ---------------------------------------------------------------------------


def test_stop_run_shows_stopping_not_stopped(runnable: QCTApp):
    runnable.start_run()
    runnable.runner = StubRunner()
    runnable.current_phase = "planner"
    runnable.stop_run()
    # Không giả vờ đã dừng: worker có thể còn đang trong LLM call.
    assert runnable.running is True
    assert runnable.stopping is True
    assert "planner" in runnable.footer.status()
    assert runnable.runner.cancel_calls == 1


def test_stop_run_does_not_fake_a_stop_while_stopping(runnable: QCTApp):
    runnable.start_run()
    runnable.runner = StubRunner()
    runnable.stop_run()
    assert runnable.stopping is True
    # Cờ chỉ rụng khi terminal event tới — không phải lúc bấm `c`.
    assert runnable.running is True


def test_stop_run_asks_the_runner(runnable: QCTApp, monkeypatch):
    monkeypatch.setattr(tui_app, "silence_agent_console", lambda: None)
    monkeypatch.setattr(tui_app, "set_bus", lambda bus: None)
    runnable._graph = FakeGraph([[{"current_step": "planner_done", "error": "boom"}]])
    runnable.start_run()
    runnable.spawn_job()
    runnable.stop_run()
    assert runnable.runner.cancelled is True
    runnable.runner.join(5)


def test_stop_run_without_a_run_is_harmless(app: QCTApp):
    app.stop_run()
    assert app.running is False
    assert app.stopping is False
    assert app.runner is None


def test_stop_run_without_a_worker_does_not_stick_the_ui(runnable: QCTApp):
    """Cờ `running` bật mà chưa spawn thì không có terminal event để gỡ nó."""
    assert runnable.start_run() is True
    assert runnable.runner is None
    runnable.stop_run()
    assert runnable.running is False
    assert runnable.stopping is False
    assert runnable.footer.running() is False


# ---------------------------------------------------------------------------
# reaper: lưới an toàn chống UI kẹt ở trạng thái running
# ---------------------------------------------------------------------------


def test_reaper_ignores_a_live_worker(runnable: QCTApp):
    runnable.start_run()
    runnable.runner = StubRunner(alive=True)
    for _ in range(10):
        runnable._reap_dead_worker()
    assert runnable.running is True
    assert runnable.last_error is None


def test_reaper_grants_a_grace_period_before_firing(runnable: QCTApp):
    """Thread vừa emit xong rồi chết: event có thể chưa kịp tới queue."""
    runnable.start_run()
    runnable.runner = StubRunner(alive=False)
    for _ in range(REAP_AFTER_POLLS - 1):
        runnable._reap_dead_worker()
        assert runnable.running is True
        assert runnable.last_error is None


def test_reaper_clears_running_when_the_worker_vanished(runnable: QCTApp):
    runnable.start_run()
    runnable.runner = StubRunner(alive=False)
    for _ in range(REAP_AFTER_POLLS):
        runnable._reap_dead_worker()
    assert runnable.running is False
    assert runnable.stopping is False
    assert runnable.footer.running() is False
    assert runnable.last_error


def test_reaper_resets_after_a_terminal_event(runnable: QCTApp):
    runnable.start_run()
    runnable.runner = StubRunner(alive=False)
    runnable._reap_dead_worker()
    assert runnable._dead_worker_polls == 1
    runnable._handle_event(Event(kind="run_done", payload={"report_path": "", "summary": "x"}))
    assert runnable._dead_worker_polls == 0
    # Terminal event đã xử lý xong thì reaper không được bắn thêm lần nữa.
    for _ in range(REAP_AFTER_POLLS + 2):
        runnable._reap_dead_worker()
    assert runnable.runner is None
    assert runnable.running is False


def test_reaper_does_nothing_when_idle(app: QCTApp):
    for _ in range(REAP_AFTER_POLLS + 2):
        app._reap_dead_worker()
    assert app.last_error is None
    assert app.running is False


# ---------------------------------------------------------------------------
# phase toggles / quit confirm
# ---------------------------------------------------------------------------


def test_toggle_phase_updates_footer_and_table(app: QCTApp):
    app._handle_event(Event(kind="test_result", payload=result("A", "passed", "api")))
    app._handle_event(Event(kind="test_result", payload=result("B", "passed", "ui")))
    assert app.case_table.visible_ids() == ["A", "B"]

    app.action_toggle_phase("ui")
    assert app.footer.selected_phases() == ["api", "ui"]
    assert app.case_table.visible_ids() == ["A", "B"]

    app.action_toggle_phase("api")
    assert app.footer.selected_phases() == ["ui"]
    assert app.case_table.visible_ids() == ["B"]


def test_toggle_phase_ignores_unknown_phase(app: QCTApp):
    app.action_toggle_phase("banana")
    assert app.footer.selected_phases() == ["api"]


def test_quit_confirms_first_while_running(runnable: QCTApp):
    runnable.start_run()
    runnable.action_request_quit()
    assert runnable.quit_pending is True
    assert runnable.running is True


def test_quit_confirm_is_cleared_when_the_run_finishes(runnable: QCTApp):
    runnable.start_run()
    runnable.action_request_quit()
    assert runnable.quit_pending is True
    runnable._handle_event(Event(kind="cancelled", payload={"phase": "planner"}))
    assert runnable.quit_pending is False


# ---------------------------------------------------------------------------
# mounted app
# ---------------------------------------------------------------------------


async def test_mounted_app_has_all_widgets(tmp_path):
    a = QCTApp(graph=FakeGraph(), reports_dir=tmp_path)
    async with a.run_test() as pilot:
        assert isinstance(a.footer, StatusFooter)
        assert isinstance(a.history_pane, HistoryPane)
        assert isinstance(a.log_pane, LogPane)
        assert isinstance(a.case_table, CaseTable)
        await pilot.pause()
        assert a.query_one("#run", Button)
        assert a.query_one("#stop", Button)
        assert a.query_one("#request", Input)


async def test_pump_drains_bus_events_into_the_app(tmp_path):
    a = QCTApp(graph=FakeGraph(), reports_dir=tmp_path)
    async with a.run_test() as pilot:
        a.bus.emit(Event(kind="node_start", payload={"node": "generator"}))
        await wait_until(lambda: a.current_phase == "generator")
        assert a.log_pane.lines == ["▶ generator"]


async def test_run_button_stays_clickable_on_a_narrow_terminal(tmp_path):
    a = QCTApp(graph=FakeGraph(), reports_dir=tmp_path)
    async with a.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        for selector in ("#run", "#stop"):
            region = a.query_one(selector).region
            assert region.x + region.width <= a.screen.size.width
            assert region.y + region.height <= a.screen.size.height


async def test_run_button_and_r_key_share_the_same_path(tmp_path):
    """Cả hai đều phải qua `action_start()`: start_run() rồi spawn_job()."""
    a = QCTApp(graph=FakeGraph(happy_script()), reports_dir=tmp_path)
    async with a.run_test(size=(160, 48)) as pilot:
        # 1. Click #run khi request rỗng → start_run() từ chối, không spawn.
        await pilot.click("#run")
        await pilot.pause()
        assert a.last_error
        assert a.running is False
        assert a.runner is None

        # 2. Binding `r` đi đúng đường đó.
        a.footer.set_request("test login")
        a.footer.set_all_phases(True)
        await pilot.press("r")
        await wait_until(lambda: a.runner is not None)
        assert a.running is True
        assert a.pending_state["user_request"] == "test login"
        assert isinstance(a.runner, JobRunner)
        assert a.runner.is_alive()

        a.stop_run()
        await wait_until(lambda: not a.running)


async def test_focus_binding_focuses_the_request_input(tmp_path):
    a = QCTApp(graph=FakeGraph(), reports_dir=tmp_path)
    async with a.run_test() as pilot:
        a.action_focus_request()
        await pilot.pause()
        assert a.focused is a.query_one("#request", Input)


async def test_typing_in_the_request_input_never_starts_a_run(tmp_path):
    """`r` là binding của app nhưng phải nhường cho Input khi nó có focus."""
    a = QCTApp(graph=FakeGraph(), reports_dir=tmp_path)
    async with a.run_test() as pilot:
        a.action_focus_request()
        await pilot.pause()
        for key in ("r", "u", "n"):
            await pilot.press(key)
            await pilot.pause()
        assert a.query_one("#request", Input).value == "run"
        assert a.running is False
        assert a.runner is None


async def test_two_jobs_run_through_one_app(tmp_path):
    """Regression: gate dùng chung latch vĩnh viễn nên job thứ 2 tự huỷ.

    Nếu `spawn_job` không tạo gate mới, job 2 sẽ `wait()` trả ngay kết quả
    của job 1 và emit `cancelled(phase="review")` — dashboard chỉ chạy được
    đúng một job rồi treo.
    """
    graph = PerThreadFakeGraph()
    a = QCTApp(graph=graph, reports_dir=tmp_path)
    async with a.run_test() as pilot:
        await pilot.pause()
        seen_titles = []
        for _ in range(2):
            a.footer.set_request("test login")
            a.footer.set_all_phases(True)
            assert a.start_run() is True
            a.spawn_job()

            await wait_until(lambda: a.review_modal is not None or not a.running)
            assert a.review_modal is not None, "modal review không mở"
            seen_titles.append(a.pending_plan.get("title"))
            a.review_modal.dismiss(a.review_modal.approve())

            await wait_until(lambda: not a.running)
            assert a.last_error is None, f"job lỗi: {a.last_error}"
            # Không modal nào được sót lại che dashboard.
            assert a.review_modal is None
            assert len(a.screen_stack) == 1, f"còn {a.screen_stack} trên stack"

    assert len(set(seen_titles)) == 2, f"2 job phải có 2 plan khác nhau: {seen_titles}"
    assert len(set(graph.threads_used)) == 2, "phải là 2 thread_id khác nhau"


async def test_cancelling_during_review_dismisses_the_modal(tmp_path):
    """Terminal event tới khi modal còn mở phải gỡ modal, không để nó treo."""
    a = QCTApp(graph=PerThreadFakeGraph(), reports_dir=tmp_path)
    async with a.run_test() as pilot:
        await pilot.pause()
        a.footer.set_request("test login")
        a.footer.set_all_phases(True)
        a.start_run()
        a.spawn_job()

        await wait_until(lambda: a.review_modal is not None)
        assert len(a.screen_stack) == 2, "modal phải đang nằm trên stack"

        a.stop_run()
        await wait_until(lambda: not a.running)

        assert a.review_modal is None, "modal phải được gỡ"
        assert len(a.screen_stack) == 1, f"modal còn che: {a.screen_stack}"


async def test_opening_a_history_run_loads_its_details(tmp_path):
    """Spec §4.1: bấm một run trong lịch sử phải nạp kết quả vào bảng + log."""
    import json

    payload = {
        "timestamp": "20260101_000000",
        "user_request": "Test auth",
        "test_plan": {"title": "Auth Plan", "scope": "api"},
        "execution_result": {
            "total": 2,
            "passed": 1,
            "failed": 1,
            "error": 0,
            "skipped": 0,
            "duration_ms": 1234.0,
            "details": [
                {"id": "TC_1", "type": "api", "status": "passed",
                 "duration_ms": 10, "title": "login ok"},
                {"id": "TC_2", "type": "ui", "status": "failed",
                 "duration_ms": 20, "title": "ui login", "error_message": "timeout"},
            ],
        },
    }
    (tmp_path / "report_20260101_000000.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )

    a = QCTApp(graph=FakeGraph(), reports_dir=tmp_path)
    async with a.run_test() as pilot:
        await pilot.pause()
        assert len(a.history_pane.runs) == 1

        a.history_pane.post_message(
            HistoryPane.RunOpened(a.history_pane.runs[0])
        )
        for _ in range(30):
            await pilot.pause()

        assert a.case_table.visible_ids() == ["TC_1", "TC_2"]
        assert "Test auth" in "\n".join(a.log_pane.lines)
        assert "Auth Plan" in "\n".join(a.log_pane.lines)
        assert "timeout" in "\n".join(a.log_pane.lines)
        assert "1/2 pass" in a.footer.status()


async def test_rapid_history_refreshes_converge_without_duplicate_ids(tmp_path):
    """Regression: nhiều `refresh_history()` liên tiếp không được hỏng sidebar.

    Task dọn ListItem cũ nằm giữa `await pane.clear()`. Nếu ta `cancel()` task
    đó giữa chừng, child cũ không bị prune, ListItem mới mount trùng id →
    `DuplicateIds`; hoặc tệ hơn, pane đóng băng và mọi refresh sau là no-op.
    """
    import json

    async def write(n: int) -> None:
        for i in range(n):
            (tmp_path / f"report_2026010{i}_000000.json").write_text(
                json.dumps({"timestamp": f"2026010{i}_000000", "user_request": f"run {i}"}),
                encoding="utf-8",
            )

    await write(3)
    a = QCTApp(graph=FakeGraph(), reports_dir=tmp_path)
    async with a.run_test() as pilot:
        await pilot.pause()
        for _ in range(6):
            a.refresh_history()
            await pilot.pause()

        # Nội dung phải khớp đúng số report trên đĩa.
        assert len(a.history_pane.runs) == 3
        assert len(a.history_pane._index_to_run) == 3

        # Thêm report rồi refresh một lần nữa — pane phải cập nhật, không đóng băng.
        await write(5)
        a.refresh_history()
        for _ in range(30):
            await pilot.pause()
            if len(a.history_pane.runs) == 5:
                break
        assert len(a.history_pane.runs) == 5, "pane đóng băng: refresh sau bị bỏ qua"

        # Không còn task treo.
        if a.history_task is not None:
            assert a.history_task.done() or a.history_task.cancelled()


async def test_plan_ready_pushes_review_modal_and_dismiss_resolves_gate(tmp_path):
    gate = RecordingGate()
    a = QCTApp(
        graph=FakeGraph(happy_script()), review_gate=gate, reports_dir=tmp_path
    )
    async with a.run_test() as pilot:
        a.footer.set_request("test login")
        a.footer.set_all_phases(True)
        assert a.start_run() is True
        a.spawn_job()

        await wait_until(lambda: a.pending_plan is not None)
        modal = a.review_modal
        assert isinstance(modal, ReviewModalScreen)
        assert isinstance(modal.model, ReviewModel)
        assert [c["id"] for c in modal.model.cases] == ["A", "B"]
        # Worker đang block trong `ReviewGate.wait()`.
        assert not gate.is_resolved()

        modal.dismiss(modal.approve())
        await wait_until(lambda: gate.is_resolved())
        assert [c["id"] for c in gate.first_kept] == ["A", "B"]

        # JobRunner coi `kept` rỗng là cancel → run_done chứng minh gate đã
        # được giải phóng đúng và không bị treo.
        await wait_until(lambda: not a.running)
        assert a.last_error is None
        assert a.running is False


async def test_rejecting_the_plan_resolves_gate_with_none(tmp_path):
    gate = RecordingGate()
    a = QCTApp(
        graph=FakeGraph(happy_script()), review_gate=gate, reports_dir=tmp_path
    )
    async with a.run_test() as pilot:
        a.footer.set_request("test login")
        a.footer.set_all_phases(True)
        a.start_run()
        a.spawn_job()
        await wait_until(lambda: a.pending_plan is not None)
        a.review_modal.dismiss(None)
        await wait_until(lambda: gate.is_resolved())
        await wait_until(lambda: not a.running)
        assert gate.first_kept is None
        assert a.running is False


async def test_cancelling_while_the_review_modal_is_open_still_terminates(tmp_path):
    gate = RecordingGate()
    a = QCTApp(
        graph=FakeGraph(happy_script()), review_gate=gate, reports_dir=tmp_path
    )
    async with a.run_test() as pilot:
        a.footer.set_request("test login")
        a.footer.set_all_phases(True)
        a.start_run()
        a.spawn_job()
        await wait_until(lambda: a.pending_plan is not None)
        assert not gate.is_resolved()
        a.stop_run()
        await wait_until(lambda: gate.is_resolved(), timeout=5.0)
        await wait_until(lambda: not a.running, timeout=5.0)
        assert a.running is False
        assert a.stopping is False


async def test_full_run_through_a_fake_graph(tmp_path):
    """JobRunner thật chạy trong thread thật, chỉ graph là giả."""
    graph = FakeGraph(happy_script())
    a = QCTApp(graph=graph, reports_dir=tmp_path)
    async with a.run_test() as pilot:
        a.footer.set_request("test login")
        a.footer.set_all_phases(True)
        assert a.start_run() is True
        a.spawn_job()
        await wait_until(lambda: a.pending_plan is not None)
        a.review_modal.dismiss(a.review_modal.approve())
        await wait_until(lambda: not a.running, timeout=10.0)

        assert a.last_error is None
        assert a.stopping is False
        assert a.current_phase in ("api_executor", "reporter")
        assert graph.stream_calls == 3
        assert graph.updates[0]["human_approved"] is True
        assert [g["id"] for g in graph.updates[1]["generated_tests"]] == ["A", "B"]


async def test_history_refreshes_after_run_done(tmp_path):
    a = QCTApp(graph=FakeGraph(), reports_dir=tmp_path)
    async with a.run_test() as pilot:
        assert a.history_pane.runs == []
        report_file(tmp_path, "past run")
        a._handle_event(Event(kind="run_done", payload={"report_path": "x", "summary": ""}))
        await pilot.pause()
        assert [r.request for r in a.history_pane.runs] == ["past run"]


async def test_broken_history_reports_count(tmp_path):
    (tmp_path / "report_bad.json").write_text("{not json", encoding="utf-8")
    a = QCTApp(graph=FakeGraph(), reports_dir=tmp_path)
    async with a.run_test() as pilot:
        await pilot.pause()
        assert a.history_pane.broken == 1


async def test_missing_reports_dir_is_not_fatal(tmp_path):
    a = QCTApp(graph=FakeGraph(), reports_dir=tmp_path / "nope")
    async with a.run_test() as pilot:
        await pilot.pause()
        assert a.history_pane.runs == []
        assert a.history_pane.broken == 0


async def test_quit_while_running_confirms_then_exits(tmp_path):
    a = QCTApp(graph=FakeGraph(), reports_dir=tmp_path)
    async with a.run_test() as pilot:
        a.footer.set_request("test login")
        a.footer.set_all_phases(True)
        a.start_run()
        a.action_request_quit()
        await pilot.pause()
        assert a.quit_pending is True
        assert a.running is True
        assert a.is_running is True
        a.stop_run()
        a._handle_event(Event(kind="cancelled", payload={"phase": "start"}))
        await pilot.pause()
        assert a.running is False


async def test_pump_task_is_cleaned_up_on_exit(tmp_path):
    a = QCTApp(graph=FakeGraph(), reports_dir=tmp_path)
    async with a.run_test():
        task = a.pump_task
        assert task is not None and not task.done()
    assert task.cancelled() or task.done()


def test_plan_total_falls_back_to_received_when_no_plan_yet(app: QCTApp):
    """Chưa có `plan_ready` thì mẫu số là số case đã nhận, không phải 0."""
    assert app.footer.progress_text() == ""
    app._handle_event(Event(kind="test_result", payload=result("A")))
    assert app.footer.progress_text() == "api 1/1"
    app._handle_event(Event(kind="test_result", payload=result("B", "passed", "ui")))
    assert app.footer.progress_text() == "ui 2/2"

async def test_slash_command_help_prints_help(tmp_path):
    a = QCTApp(graph=FakeGraph(), reports_dir=tmp_path)
    async with a.run_test() as pilot:
        a.action_focus_request()
        await pilot.pause()
        inp = a.query_one("#request", Input)
        inp.value = "/help"
        await pilot.press("enter")
        await pilot.pause()
        assert any("Available Commands" in t for t in a.log_pane.lines)
        assert inp.value == ""


async def test_slash_command_status_prints_status(tmp_path):
    a = QCTApp(graph=FakeGraph(), reports_dir=tmp_path)
    async with a.run_test() as pilot:
        a.action_focus_request()
        await pilot.pause()
        inp = a.query_one("#request", Input)
        inp.value = "/status"
        await pilot.press("enter")
        await pilot.pause()
        assert any("LLM Provider" in t for t in a.log_pane.lines)


async def test_slash_command_login_triggers_login(tmp_path, monkeypatch):
    from unittest.mock import MagicMock
    mock_login = MagicMock(return_value={"email": "tester@gmail.com"})
    monkeypatch.setattr("auth.antigravity.run_antigravity_login", mock_login)

    a = QCTApp(graph=FakeGraph(), reports_dir=tmp_path)
    async with a.run_test() as pilot:
        a.action_focus_request()
        await pilot.pause()
        inp = a.query_one("#request", Input)
        inp.value = "/login antigravity"
        await pilot.press("enter")
        import asyncio
        await asyncio.sleep(0.05)
        await pilot.pause()
        assert any("tester@gmail.com" in t for t in a.log_pane.lines)

async def test_slash_command_login_opens_modal(tmp_path):
    from tui.widgets.selection_modal import SelectionModalScreen
    a = QCTApp(graph=FakeGraph(), reports_dir=tmp_path)
    async with a.run_test() as pilot:
        a.action_focus_request()
        await pilot.pause()
        inp = a.query_one("#request", Input)
        inp.value = "/login"
        await pilot.press("enter")
        await pilot.pause()
        assert isinstance(a.screen, SelectionModalScreen)
        await pilot.press("escape")
        await pilot.pause()


async def test_slash_command_model_opens_modal(tmp_path):
    from tui.widgets.selection_modal import SelectionModalScreen
    a = QCTApp(graph=FakeGraph(), reports_dir=tmp_path)
    async with a.run_test() as pilot:
        a.action_focus_request()
        await pilot.pause()
        inp = a.query_one("#request", Input)
        inp.value = "/model"
        await pilot.press("enter")
        await pilot.pause()
        assert isinstance(a.screen, SelectionModalScreen)
        await pilot.press("escape")
        await pilot.pause()


async def test_slash_command_model_direct_switch(tmp_path, monkeypatch):
    from config.settings import settings
    old_provider = settings.llm_provider
    old_model = settings.antigravity_model
    try:
        a = QCTApp(graph=FakeGraph(), reports_dir=tmp_path)
        async with a.run_test() as pilot:
            a.action_focus_request()
            await pilot.pause()
            inp = a.query_one("#request", Input)
            inp.value = "/model gemini-2.5-flash"
            await pilot.press("enter")
            await pilot.pause()
            assert settings.antigravity_model == "gemini-2.5-flash"
            assert any("gemini-2.5-flash" in t for t in a.log_pane.lines)
    finally:
        settings.llm_provider = old_provider
        settings.antigravity_model = old_model


async def test_ctrl_c_exits_app(tmp_path):
    a = QCTApp(graph=FakeGraph(), reports_dir=tmp_path)
    async with a.run_test() as pilot:
        a.action_focus_request()
        await pilot.pause()
        await pilot.press("ctrl+c")
        await pilot.pause()
        assert not a.is_running
