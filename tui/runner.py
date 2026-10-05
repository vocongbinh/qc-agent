"""JobRunner — chạy qc_graph trong worker thread với 2 pause point cho human review.

Tái sử dụng đúng pattern của `main.py`: break khỏi `stream()` khi thấy
`current_step == "planner_done"`, `update_state(config, ...)`, rồi
`stream(None, config)`. Bổ sung pause thứ hai sau `generator_done` để lọc
`generated_tests` theo phase đã chọn — prompt của generator chỉ "ưu tiên
type=api" chứ không ràng buộc, nên đây là chốt chặn cuối trước executor.
"""

from __future__ import annotations

import threading
import uuid
from typing import Any, Iterable, Optional

from tui.bus import Event, EventBus
from tui.filters import filter_by_phases
from tui.gate import REVIEW_TIMED_OUT, ReviewGate

# Mọi `current_step` mà agents/*.py ghi ra, trừ sentinel "start" của
# build_initial_state (không phải node nào). Thêm node mà quên khai báo ở đây
# thì test_node_mapping_covers_every_step_the_agents_emit sẽ đỏ.
_NODE_BY_STEP = {
    "planner_done": "planner",
    "planner_failed": "planner",
    "waiting_human_review": "human_review",
    "generator_done": "generator",
    "generator_failed": "generator",
    "api_executor_done": "api_executor",
    "api_executor_failed": "api_executor",
    "ui_executor_done": "ui_executor",
    "chaos_executor_done": "chaos_executor",
    "performance_executor_done": "performance_executor",
    "reporter_done": "reporter",
}

# Node đầu tiên của mỗi pass, để breadcrumb có việc để tick trước khi
# `node_end` đầu tiên tới.
_PASS_ENTRY_NODE = {
    "planner_done": "planner",
    "generator_done": "generator",
    "reporter_done": "api_executor",
}


def build_initial_state(
    request: str,
    phases: Iterable[str],
    documents: Optional[list[str]] = None,
    code_paths: Optional[list[str]] = None,
    openapi_spec: Optional[str] = None,
    ui_headed: bool = False,
) -> dict[str, Any]:
    """State khởi tạo cho AgentState. `job_id` kiêm luôn là thread_id LangGraph."""
    return {
        "user_request": request,
        "documents": list(documents or []),
        "code_paths": list(code_paths or []),
        "openapi_spec": openapi_spec,
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
        "ui_headed": ui_headed,
        "phases": list(phases or []),
        "job_id": str(uuid.uuid4()),
        "code_intelligence_summary": None,
        "seed_manifest": None,
    }


class JobRunner:
    """Một job = một thread + một thread_id LangGraph.

    `spawn` là bất đồng bộ: thread chạy tới pause 1 thì block trong
    `review_gate.wait()` cho tới khi TUI gọi `resolve`. `cancel` là phối hợp
    (cooperative): không cắt được LLM đang chạy, chỉ dừng ở các mốc.
    """

    def __init__(
        self,
        graph: Any,
        bus: EventBus,
        review_gate: Optional[ReviewGate] = None,
    ) -> None:
        self.graph = graph
        self.bus = bus
        self.review_gate = review_gate or ReviewGate()
        self._thread: Optional[threading.Thread] = None
        self._cancelled = threading.Event()
        # Instance attribute, KHÔNG đặt ở class: hai job chạy song song dùng
        # chung singleton qc_graph và không được chia sẻ state.
        self._last_state: Optional[dict[str, Any]] = None
        self._emitted_nodes: set[tuple[str, str]] = set()

    # ----- public API -----

    @property
    def cancelled(self) -> bool:
        return self._cancelled.is_set()

    def is_alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def spawn(self, state: dict[str, Any]) -> None:
        if self._thread is not None:
            raise RuntimeError("JobRunner đã spawn — mỗi job cần một runner mới")
        thread = threading.Thread(
            target=self._run,
            args=(dict(state),),
            daemon=True,
            name="qc-job",
        )
        self._thread = thread
        thread.start()

    def join(self, timeout: Optional[float] = None) -> None:
        if self._thread is not None:
            self._thread.join(timeout)

    def cancel(self) -> None:
        self._cancelled.set()
        self.review_gate.release()

    # ----- worker thread -----

    def _run(self, state: dict[str, Any]) -> None:
        config = {
            "configurable": {"thread_id": state.get("job_id") or str(uuid.uuid4())}
        }
        try:
            if self._cancelled.is_set():
                self._emit("cancelled", phase="start")
                return
            # --- Pass 1: tới planner_done ---
            if self._stream_until(state, config, "planner_done"):
                return
            if self._cancelled.is_set():
                self._emit("cancelled", phase="planner")
                return

            # --- Pause 1: human review ---
            plan = (self._last_state or {}).get("test_plan") or {}
            cases = list(plan.get("test_cases") or [])
            self._emit("plan_ready", test_plan=plan, count=len(cases))
            kept = self.review_gate.wait()

            if kept is REVIEW_TIMED_OUT:
                # Spec §4.3: hết giờ duyệt là LỖI, không phải "người dùng huỷ".
                # Báo `cancelled` ở đây sẽ khiến UI nói "Đã huỷ" y hệt khi
                # bấm Stop, và người dùng tưởng mình chủ động dừng.
                self._emit(
                    "run_error",
                    message=f"No review response after {self.review_gate.timeout:.0f}s.",
                    step="planner_done",
                )
                return
            if kept is None:
                self._emit("cancelled", phase="review")
                return
            if not kept:
                self._emit(
                    "log",
                    level="warn",
                    text="No test cases selected — stopping run before generator.",
                )
                self._emit("cancelled", phase="review")
                return
            # Ghi plan đã lọc + human_approved *trước* khi generator chạy, để
            # generator không sinh lại những case người dùng vừa bỏ.
            self.graph.update_state(
                config,
                {
                    "test_plan": {**plan, "test_cases": list(kept)},
                    "human_approved": True,
                },
            )
            if self._cancelled.is_set():
                self._emit("cancelled", phase="review")
                return

            # --- Pass 2: tới generator_done ---
            if self._stream_until(None, config, "generator_done"):
                return
            if self._cancelled.is_set():
                self._emit("cancelled", phase="generator")
                return

            # --- Pause 2: chốt chặn theo phase đã chọn ---
            generated = list((self._last_state or {}).get("generated_tests") or [])
            phases = list(state.get("phases") or [])
            filtered = filter_by_phases(generated, phases)
            if len(filtered) != len(generated):
                self._emit(
                    "log",
                    level="info",
                    text=(
                        f"Phase {', '.join(phases) or 'tất cả'}: giữ "
                        f"{len(filtered)}/{len(generated)} test case sau khi lọc."
                    ),
                )
            if not filtered:
                self._emit(
                    "run_error",
                    message=(
                        "Generator không sinh ra test case nào."
                        if not generated
                        else f"Không còn test case nào thuộc phase "
                        f"{', '.join(phases) or 'đã chọn'} ({len(generated)} case bị lọc bỏ)."
                    ),
                    step="generator_done",
                )
                return
            self.graph.update_state(config, {"generated_tests": filtered})
            if self._cancelled.is_set():
                self._emit("cancelled", phase="generator")
                return

            # --- Pass 3: executors → reporter ---
            if self._stream_until(None, config, "reporter_done"):
                return
            final = self._last_state or {}
            self._emit(
                "run_done",
                report_path=str(final.get("report_path") or ""),
                summary=str(final.get("final_summary") or ""),
            )
        except Exception as exc:  # bắt rộng: thread chết âm thầm là bug khó truy
            self._emit(
                "run_error",
                message=f"{type(exc).__name__}: {exc}",
                step=self._current_step(),
            )
        finally:
            # Bất kể điều gì xảy ra, gate phải được giải phóng — UI đang chờ
            # `wait()` không được treo vì worker thread đã chết.
            self.review_gate.release()

    def _stream_until(self, state: Any, config: dict[str, Any], stop_step: str) -> bool:
        """Stream tới khi gặp `stop_step`. True = phải dừng run (lỗi/hết/không đạt)."""
        self._emit("node_start", node=_PASS_ENTRY_NODE[stop_step])
        self._last_state = None
        stream = self.graph.stream(state, config, stream_mode="values")
        try:
            for event in stream:
                if not isinstance(event, dict):
                    continue
                self._last_state = event
                step = event.get("current_step") or ""
                node = _NODE_BY_STEP.get(step)
                if node:
                    self._emit_node_end(node, step)
                if event.get("error"):
                    self._emit("run_error", message=str(event["error"]), step=step)
                    return True
                if step == stop_step:
                    return False
        finally:
            # Break giữa chừng phải đóng generator, nếu không graph còn giữ
            # task đang chạy dù ta đã quyết định dừng.
            close = getattr(stream, "close", None)
            if callable(close):
                close()

        # Generator hết mà không đạt stop_step → fail chứ không coi là xong.
        if self._last_state is None:
            self._emit("run_error", message="Graph không trả về state nào", step="")
        else:
            self._emit(
                "run_error",
                message=f"Graph kết thúc trước khi tới '{stop_step}'",
                step=self._current_step(),
            )
        return True

    # ----- helpers -----

    def _emit(self, kind: str, **payload: Any) -> None:
        self.bus.emit(Event(kind=kind, payload=payload))

    def _emit_node_end(self, node: str, step: str) -> None:
        # Pass resume có thể echo lại state của checkpoint (current_step của
        # node vừa xong) — đừng tick breadcrumb hai lần cho cùng một node.
        if (node, step) in self._emitted_nodes:
            return
        self._emitted_nodes.add((node, step))
        self._emit("node_end", node=node, step=step)

    def _current_step(self) -> str:
        return str((self._last_state or {}).get("current_step") or "")
