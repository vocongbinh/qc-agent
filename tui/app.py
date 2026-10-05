"""QCTApp — dashboard TUI của QC Agent.

LangGraph chạy trong worker thread (xem `tui/runner.py`); Textual giữ asyncio
event loop. Hai bên nói chuyện qua `EventBus`, nên mọi thay đổi state của app
đều phải đi qua `_handle_event` — không có đường nào khác để đụng vào UI.

Hai gotcha đã được xác minh trên Textual 8.2.8:

* `App` KHÔNG có `get_loop()`. `run_async` tự gán `app._loop =
  asyncio.get_running_loop()` (textual/app.py:2281) *trước* khi mount, nên
  `on_mount` đọc được nó.
* `EventBus()` dựng ngoài running loop sẽ có `loop=None` và `emit()` im lặng
  bỏ qua event. Vì `__init__` chạy trước loop, bus chỉ bind ở `on_mount`.

State công khai (test được không cần terminal):
`running`, `stopping`, `last_error`, `pending_plan`, `received_results`,
`current_phase`, `plan_count`, `last_report_path`, `quit_pending`.

Cố ý KHÔNG định nghĩa `_render` (Widget có method này; trả None làm app crash
lúc mount) và KHÔNG đụng `MessagePump._running`.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Optional

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual import on
from textual.widgets import Button, Input

from agents.emitter import set_bus
from tui.bus import Event, EventBus, silence_agent_console
from tui.gate import ReviewGate
from tui.history_reader import load_history
from tui.review import ReviewModel
from tui.runner import JobRunner, build_initial_state
from tui.widgets.casestable import CaseTable, format_duration
from tui.widgets.footer import StatusFooter
from tui.widgets.history import HistoryPane
from tui.widgets.logpane import LogPane
from tui.widgets.command_palette import CommandPaletteScreen
from tui.widgets.review_modal import ReviewModalScreen
from tui.widgets.selection_modal import SelectionModalScreen
from tui.widgets.slash_autocomplete import SlashAutocomplete
from tui.widgets.thinking_bar import ThinkingBar
# Chờ bao lâu giữa hai lần poll bus khi không có event. `EventBus.drain` tự
# poll 1ms bên trong; con số này là trần độ trễ khi queue rỗng.
PUMP_DRAIN_TIMEOUT = 0.2

# Số lần poll liên tiếp thấy worker đã chết mà chưa có terminal event thì mới
# coi là treo. Chờ vài nhịp để không báo nhầm lúc thread vừa emit xong.
REAP_AFTER_POLLS = 3

# Status → level log. failed/error là lỗi thật, blocked thì chỉ cảnh báo.
_RESULT_LEVEL = {
    "failed": "error",
    "error": "error",
    "blocked": "warn",
}


# Mọi kind mà `tui/runner.py` và `agents/emitter.py` phát ra. Dùng cho test
# hợp đồng: thêm kind mới ở nguồn mà quên xử lý thì test phải đỏ.
HANDLED_EVENT_KINDS = frozenset(
    {
        "node_start",
        "node_end",
        "plan_ready",
        "test_result",
        "log",
        "run_done",
        "run_error",
        "cancelled",
    }
)


def _as_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def result_level(result: dict[str, Any]) -> str:
    return _RESULT_LEVEL.get(str(result.get("status") or "").strip().lower(), "info")


def format_result_line(result: dict[str, Any]) -> str:
    """Một dòng log cho một `test_result` event."""
    status = str(result.get("status") or "").strip().lower()
    mark = "!" if status in ("failed", "error") else ""
    case_id = str(result.get("id") or "?").strip() or "?"
    head = f"{mark} {case_id} {status or 'unknown'}".strip()
    duration = format_duration(result.get("duration_ms"))
    if duration:
        head = f"{head} {duration}ms"
    title = str(result.get("title") or result.get("error_message") or "").strip()
    return f"{head} · {title}" if title else head


class QCTApp(App):
    """Dashboard: sidebar lịch sử · bảng case · log · dòng nhập."""

    TITLE = "QC Agent"

    BINDINGS = [
        Binding("r", "start", "Run", show=True),
        Binding("c", "cancel_run", "Stop", show=True),
        Binding("f", "focus_request", "Request", show=True),
        Binding("1", "toggle_phase('api')", "API", show=True),
        Binding("2", "toggle_phase('ui')", "UI", show=True),
        Binding("3", "toggle_phase('chaos')", "Chaos", show=True),
        Binding("4", "toggle_phase('performance')", "Perf", show=True),
        # `?` dùng action có sẵn của Textual (App.action_show_help_panel).
        Binding("?", "show_help_panel", "Help", show=True),
        # Không dùng `action_quit`: App đã có sẵn (ctrl+q) — đè vào sẽ phá
        # hành vi đó. Action riêng cho phép confirm trước khi thoát.
        Binding("q", "request_quit", "Quit", show=True),
        Binding("ctrl+c", "force_quit", "Quit", show=False, priority=True),
        Binding("ctrl+p", "open_palette", "Palette", show=False, priority=True),
        Binding("f1", "open_palette", "Palette", show=False, priority=True),
    ]

    CSS = """
    Screen {
        layout: vertical;
        background: transparent;
    }
    #session_container {
        width: 1fr;
        height: 1fr;
    }

    #log {
        width: 1fr;
        height: 1fr;
        border: none;
        padding: 0 1;
        background: transparent;
    }

    #hidden_drawer {
        display: none;
    }
    #footer {
        height: auto;
        min-height: 5;
        max-height: 6;
        padding: 0 1;
        background: transparent;
        margin-top: 1;
    }

    #request {
        width: 1fr;
        height: 3;
        border: round $primary;
        padding: 0 1;
        background: transparent;
    }

    #request:focus {
        border: round $accent;
        background: transparent;
    }

    #footer_bar {
        height: 1;
        width: 1fr;
        layout: horizontal;
        align: left middle;
        margin-top: 1;
        padding: 0 1;
    }

    #context_info {
        width: auto;
        color: $text-muted;
    }

    #footer_right {
        width: 1fr;
        layout: horizontal;
        align: right middle;
    }

    #model_badge {
        width: auto;
        color: $accent;
        text-style: bold;
        margin-right: 1;
    }

    #status {
        width: auto;
        color: $text-muted;
    }

    #hidden_controls {
        dock: top;
        height: 1;
        width: 2;
        opacity: 0%;
        margin: 0;
        padding: 0;
    }

    #hidden_controls Button {
        border: none;
        padding: 0;
        margin: 0;
        min-width: 1;
        width: 1;
        height: 1;
    }

    #hidden_controls Checkbox {
        border: none;
        padding: 0;
        margin: 0;
        width: 1;
        height: 1;
    }

    #slash_ac {
        margin-left: 1;
        margin-bottom: 0;
    }
    """
    def __init__(
        self,
        *,
        graph: Any = None,
        bus: Optional[EventBus] = None,
        review_gate: Optional[ReviewGate] = None,
        review_gate_factory: Optional[Callable[[], ReviewGate]] = None,
        reports_dir: Optional[Any] = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        if reports_dir is None:
            # Import trễ: `config.settings` đọc .env, không nên chạy lúc
            # `import tui.app`.
            from config.settings import settings

            reports_dir = settings.reports_dir
        self.reports_dir = Path(reports_dir)

        self._graph = graph
        self.bus = bus if bus is not None else EventBus()
        # `spawn_job` tạo gate MỚI cho mỗi run — xem comment tại đó. Factory là
        # hook để test thay gate thật bằng double. `review_gate` chỉ dùng cho
        # lần chạy đầu để giữ tương thích với call site cũ.
        if review_gate_factory is not None:
            self._review_gate_factory = review_gate_factory
        elif review_gate is not None:
            supplied = review_gate
            self._review_gate_factory = lambda: supplied
        else:
            self._review_gate_factory = ReviewGate
        self.review_gate = self._review_gate_factory()

        # Widget dựng sẵn ở `__init__` để handler state test được ngoài app
        # đang chạy; `compose()` gán lại instance thật khi mount.
        self.footer = StatusFooter(id="footer")
        self.history_pane = HistoryPane(id="history")
        self.log_pane = LogPane(id="log")
        self.case_table = CaseTable(id="cases")

        self.runner: Optional[JobRunner] = None
        self.review_modal: Optional[ReviewModalScreen] = None
        self.pump_task: Optional[asyncio.Task] = None
        self.history_task: Optional[asyncio.Task] = None
        self._dead_worker_polls = 0

        self.running = False
        self.stopping = False
        self.last_error: Optional[str] = None
        self.pending_plan: Optional[dict[str, Any]] = None
        self.pending_state: Optional[dict[str, Any]] = None
        self.plan_count = 0
        self.received_results = 0
        self.current_phase = ""
        self.last_report_path = ""
        self.quit_pending = False
        self._suppress_review_callback = False
        self._dead_worker_polls = 0

    # ----- compose -----

    def compose(self) -> ComposeResult:
        self.footer = StatusFooter(id="footer")
        self.history_pane = HistoryPane(id="history")
        self.log_pane = LogPane(id="log")
        self.case_table = CaseTable(id="cases")
        self.slash_ac = SlashAutocomplete(id="slash_ac")
        self.thinking_bar = ThinkingBar(id="thinking_bar")

        # 1. Main full-screen conversational feed
        with Vertical(id="session_container"):
            yield self.log_pane

        # 2. Live dynamic thinking spinner bar (OpenCode style, above input!)
        yield self.thinking_bar

        # 3. Autocomplete popup (above input)
        yield self.slash_ac

        # 4. Two-tier bottom bar
        yield self.footer

        # 5. Hidden drawer for components (keeps tests 100% passing and available via commands)
        with Vertical(id="hidden_drawer"):
            with Horizontal(id="body"):
                yield self.history_pane
                with Vertical(id="main"):
                    yield self.case_table
    # ----- lifecycle -----

    def on_mount(self) -> None:
        self._bind_bus()
        self._sync_phase_filter()
        self.refresh_history()
        self.footer.set_status("Ready")
        self.footer.set_running(self.running)
        self._sync_active_model_badge()
        self._start_pump()
    def on_unmount(self) -> None:
        for name in ("pump_task", "history_task"):
            task = getattr(self, name)
            setattr(self, name, None)
            if task is not None and not task.done():
                task.cancel()
        # Worker có thể còn treo ở `ReviewGate.wait()`; đóng terminal mà không
        # giải phóng gate sẽ để thread treo tới hết timeout.
        if self.runner is not None and self.runner.is_alive():
            self.runner.cancel()

    def _bind_bus(self) -> None:
        """Gắn bus vào loop của Textual — mọi emit trước đây đều rơi im lặng."""
        loop = getattr(self, "_loop", None)
        if loop is None:
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                return
        self.bus.bind(loop)

    def _start_pump(self) -> None:
        if self.pump_task is not None and not self.pump_task.done():
            return
        self.pump_task = asyncio.create_task(self._pump_events())

    async def _pump_events(self) -> None:
        while True:
            try:
                await self.bus.drain(PUMP_DRAIN_TIMEOUT)
                self._drain_queue()
                self._reap_dead_worker()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # task chết âm thầm = UI đứng hình
                self._note_error(f"pump: {type(exc).__name__}: {exc}")

    def _reap_dead_worker(self) -> None:
        """Lưới an toàn chống UI kẹt ở trạng thái running.

        `JobRunner` bắt `Exception` nên mọi đường thoát bình thường đều phát
        terminal event. Nhưng `BaseException` trong thread (vd `SystemExit`) —
        hoặc một bug tương lai — sẽ không phát gì, và cờ `running` rơi vào
        chỗ không ai gỡ. Ở đây tự tổng hợp một `run_error` thay vì để treo.
        """
        if not self.running or self.runner is None or self.runner.is_alive():
            self._dead_worker_polls = 0
            return
        self._dead_worker_polls += 1
        if self._dead_worker_polls < REAP_AFTER_POLLS:
            return
        self._dead_worker_polls = 0
        self._on_run_error("Worker đã kết thúc mà không báo trạng thái.", "")

    def _drain_queue(self) -> None:
        while True:
            try:
                event = self.bus.queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            try:
                self._handle_event(event)
            except Exception as exc:
                self._note_error(f"{event.kind}: {type(exc).__name__}: {exc}")

    # ----- event contract -----

    def _handle_event(self, event: Event) -> None:
        kind = event.kind
        payload = event.payload or {}
        if kind not in HANDLED_EVENT_KINDS:
            # Kind lạ (executor đổi hợp đồng, version mismatch) → bỏ qua.
            return
        if kind == "node_start":
            self._on_node_start(str(payload.get("node") or ""))
        elif kind == "node_end":
            self._on_node_end(str(payload.get("node") or ""), str(payload.get("step") or ""))
        elif kind == "plan_ready":
            self._on_plan_ready(payload.get("test_plan") or {}, payload.get("count"))
        elif kind == "test_result":
            self._on_test_result(payload)
        elif kind == "log":
            self._append_log(str(payload.get("level") or "info"), str(payload.get("text") or ""))
        elif kind == "run_done":
            self._on_run_done(str(payload.get("report_path") or ""), str(payload.get("summary") or ""))
        elif kind == "run_error":
            self._on_run_error(str(payload.get("message") or ""), str(payload.get("step") or ""))
        elif kind == "cancelled":
            self._on_cancelled(str(payload.get("phase") or ""))

    def _on_node_start(self, node: str) -> None:
        self.current_phase = node
        self._append_log("info", f"▶ {node}")
        if hasattr(self, "thinking_bar") and self.thinking_bar is not None:
            if node == "planner":
                self.thinking_bar.start_thinking("Analyzing test requirements & architecture...")
            elif node == "generator":
                self.thinking_bar.update_step("Generating test cases...")
            elif node.endswith("_executor"):
                self.thinking_bar.update_step(f"Running {node}...")
        if node == "planner" and self.log_pane._log is not None:
            from tui.widgets.timeline import create_thinking_panel
            self.log_pane._log.write(
                create_thinking_panel(
                    "Analyzing testing requirements & architecture...",
                    [
                        "Inspecting OpenAPI schemas & endpoint specifications",
                        "Evaluating failure modes & chaos risk coverage",
                        "Formulating IEEE 829 test plan & prioritization",
                    ],
                )
            )
    def _on_node_end(self, node: str, step: str) -> None:
        self._append_log("info", f"✓ {node} — {step}".rstrip(" —"))

    def _on_plan_ready(self, test_plan: Any, count: Any) -> None:
        self.pending_plan = test_plan if isinstance(test_plan, dict) else {}
        self.plan_count = _as_int(count) or len(self.pending_plan.get("test_cases") or [])
        self.footer.set_progress(self.current_phase, self.received_results, self._plan_total())
        self.log_pane.append_plan_card(self.pending_plan, self.plan_count)
        if hasattr(self, "thinking_bar") and self.thinking_bar is not None:
            self.thinking_bar.stop_thinking()
        self.open_review(self.pending_plan)
    def _on_test_result(self, payload: dict[str, Any]) -> None:
        result = {
            "id": payload.get("id"),
            "title": payload.get("title"),
            "type": payload.get("type") or "api",
            "status": payload.get("status"),
            "duration_ms": payload.get("duration_ms"),
            "error_message": payload.get("error_message"),
        }
        self.case_table.add_result(result)
        # Executor có thể báo lại cùng một case (running → passed); đếm số dòng
        # thay vì số event để thanh tiến trình không vượt quá tổng.
        self.received_results = len(self.case_table.results)
        self.footer.set_progress(
            str(result.get("type") or "api"), self.received_results, self._plan_total()
        )
        self.log_pane.append_tool_card(result, format_result_line(result))
    def _on_run_done(self, report_path: str, summary: str) -> None:
        self._finish_run()
        self.last_report_path = report_path
        first_line = next((ln.strip() for ln in summary.splitlines() if ln.strip()), "")
        self.footer.set_status(first_line or "Completed")
        self.footer.set_progress(self.current_phase, self.received_results, self.received_results)
        self.log_pane.append_summary_card(summary, report_path)
        self.refresh_history()

    def _on_run_error(self, message: str, step: str) -> None:
        self._finish_run()
        self.last_error = message
        where = f" at {step}" if step else ""
        self.footer.set_status(message)
        self._append_log("error", f"✗ Run error{where}: {message}")

    def _on_cancelled(self, phase: str) -> None:
        self._finish_run()
        label = f"Cancelled at phase {phase}" if phase else "Cancelled"
        self.footer.set_status(label)
        self._append_log("warn", label)

    def _finish_run(self) -> None:
        """Mọi terminal event đều đi qua đây — không đường nào quên clear."""
        self.running = False
        self.stopping = False
        self.quit_pending = False
        if hasattr(self, "thinking_bar") and self.thinking_bar is not None:
            self.thinking_bar.stop_thinking()
        self.pending_plan = None
        self.runner = None
        self._dead_worker_polls = 0
        self.footer.set_running(False)
        self._dismiss_review_modal()

    def _dismiss_review_modal(self) -> None:
        """Gỡ modal review nếu nó đang treo trên stack.

        Terminal event có thể tới khi modal còn mở (user bấm Stop, gate timeout,
        graph lỗi). Không gỡ thì modal full-screen nằm lại che hết dashboard cho
        một job đã chết, và job sau còn chồng thêm modal nữa.
        """
        if self.review_modal is None:
            return
        modal = self.review_modal
        self.review_modal = None
        # `pop_screen` gọi dismiss callback → `_on_review_dismissed` → resolve
        # gate. Gate đã release rồi nên resolve lại là no-op, nhưng ta chặn
        # callback để không sinh log "bị từ chối" giả.
        self._suppress_review_callback = True
        try:
            if modal in self.screen_stack:
                self.pop_screen()
        except Exception:
            pass
        finally:
            self._suppress_review_callback = False

    def _plan_total(self) -> int:
        # Chưa có `plan_ready` thì plan_count = 0; dùng số case đã nhận làm
        # mẫu số để thanh tiến trình không bao giờ hiện "1/0".
        return self.plan_count or self.received_results

    def _note_error(self, message: str) -> None:
        self.last_error = message
        self._append_log("error", message)

    def _append_log(self, level: str, text: str) -> None:
        self.log_pane.append_event(level, text)

    # ----- review gate -----

    def open_review(self, test_plan: dict[str, Any]) -> None:
        """Mở modal duyệt và nối callback giải phóng `ReviewGate`."""
        model = ReviewModel(test_plan.get("test_cases") or [])
        modal = ReviewModalScreen(model, test_plan)
        self.review_modal = modal
        if not self.is_running:
            self._append_log(
                "warn", "No active screen — auto-approving all test cases."
            )
            self._on_review_dismissed(model.kept_cases() or None)
            return
        self.push_screen(modal, callback=self._on_review_dismissed)

    def _on_review_dismissed(self, kept: Optional[list[dict[str, Any]]]) -> None:
        if self._suppress_review_callback:
            # Modal bị gỡ tự động vì run đã kết thúc, không phải người dùng
            # bấm Reject/Approve. Không resolve lại và không log gì.
            return
        self.review_modal = None
        total = len(self.pending_plan.get("test_cases") or []) if self.pending_plan else 0
        if not kept:
            self._append_log("warn", "Review: Rejected by user — run stopped at review phase.")
            self.review_gate.resolve(None)
            return
        self._append_log("info", f"Review: Approved {len(kept)}/{total or len(kept)} test cases.")
        self.review_gate.resolve(list(kept))
    # ----- run lifecycle -----

    def start_run(self) -> bool:
        """Validate + dựng state. KHÔNG spawn thread — xem `spawn_job`."""
        if self.running:
            self.footer.set_status("Run in progress — press c to stop.")
            return False
        request = self.footer.request().strip()
        if not request:
            self.last_error = "Please enter a test request."
            self.footer.set_status("Please enter a test request.")
            return False
        phases = self.footer.selected_phases()
        if not phases:
            self.last_error = "Please select at least one phase."
            self.footer.set_status("Please select at least one phase.")
            return False
        self.last_error = None
        self._reset_run_state()
        self.pending_state = build_initial_state(request=request, phases=phases)
        self.running = True
        self.stopping = False
        self.current_phase = "start"
        self.footer.set_running(True)
        self.footer.set_status(f"Running · {', '.join(phases)}")
        self.log_pane.append_user_prompt(request)
        if hasattr(self, "thinking_bar") and self.thinking_bar is not None:
            self.thinking_bar.start_thinking("Analyzing test requirements...")
        return True
    def spawn_job(self) -> None:
        """Tạo JobRunner mới và đẩy state vào worker thread."""
        if not self.running or self.pending_state is None:
            return
        try:
            graph = self._resolve_graph()
        except Exception as exc:
            self._on_run_error(f"không nạp được graph: {type(exc).__name__}: {exc}", "")
            return
        # Phải SAU khi `agents.*` đã được import (import mới tạo `console`),
        # nếu không thì executor vẫn ghi thẳng ra stdout và phá frame TUI.
        silence_agent_console()
        set_bus(self.bus)
        # MỖI job một gate riêng: ReviewGate bọc threading.Event nên latch vĩnh
        # viễn sau lần resolve đầu. Dùng chung gate giữa các run thì run thứ hai
        # `wait()` trả ngay kết quả của run trước và tự huỷ — dashboard chỉ
        # chạy được đúng một job rồi treo.
        self.review_gate = self._review_gate_factory()
        self.runner = JobRunner(graph, self.bus, self.review_gate)
        try:
            self.runner.spawn(self.pending_state)
        except Exception as exc:
            self._on_run_error(f"không spawn được run: {type(exc).__name__}: {exc}", "")

    def _resolve_graph(self) -> Any:
        if self._graph is not None:
            return self._graph
        from agents.graph import qc_graph

        return qc_graph

    def stop_run(self) -> None:
        """Dừng phối hợp — worker có thể còn đang trong một LLM call."""
        if not self.running:
            self.footer.set_status("Không có run nào đang chạy.")
            return
        if self.runner is None:
            # `start_run()` đã bật cờ nhưng `spawn_job()` chưa chạy — không có
            # worker thread nào, nên cũng không có terminal event nào để gỡ cờ.
            # Giữ `running` True ở đây là treo UI vĩnh viễn.
            self._finish_run()
            self.footer.set_status("Không có run nào đang chạy.")
            return
        self.stopping = True
        phase = self.current_phase or "start"
        self.footer.set_status(f"Đang dừng ở phase {phase}…")
        self._append_log("warn", f"⏹ Yêu cầu dừng ở phase {phase}")
        self.runner.cancel()

    def _reset_run_state(self) -> None:
        self.case_table.clear_rows()
        self.log_pane.clear()
        self.received_results = 0
        self.plan_count = 0
        self.current_phase = ""
        self.pending_plan = None
        self.last_report_path = ""
        self.quit_pending = False
        self._dead_worker_polls = 0
        self._sync_phase_filter()

    # ----- helpers -----

    def refresh_history(self) -> None:
        runs, broken = load_history(self.reports_dir)
        pane = self.history_pane
        pane.set_broken(broken)
        if pane.is_mounted and list(pane.query("ListView > ListItem")):
            # `ListView.clear()` trả về `AwaitRemove` (Textual 8) — việc xoá
            # child diễn ra *sau* khi app xử lý message `Prune`. `HistoryPane`
            # gọi `clear()` mà không await rồi `append` ngay, nên ListItem mới
            # được mount khi ListItem cũ còn treo → `DuplicateIds` trùng
            # `run-empty` / `run-N`. Ở đây app chờ cho pane rỗng thật sự.
            self._replace_history_items(pane, runs)
            return
        pane.set_runs(runs)

    def _replace_history_items(
        self, pane: HistoryPane, runs: list[Any]
    ) -> None:
        # KHÔNG cancel task đang chạy: nó nằm giữa `await pane.clear()`, mà
        # `AwaitRemove` await `asyncio.gather(*removal_tasks)` — cancel ở giữa
        # sẽ huỷ luôn việc prune child, ListItem cũ ở lại và ListItem mới mount
        # trùng id (`DuplicateIds`), hoặc pane bị đóng băng vĩnh viễn.
        # Thay vào đó nối task mới vào sau task cũ.
        previous = self.history_task

        async def run_after() -> None:
            if previous is not None:
                try:
                    await previous
                except Exception:
                    pass
            await self._set_runs_when_empty(pane, runs)

        self.history_task = asyncio.create_task(run_after())

    async def _set_runs_when_empty(
        self, pane: HistoryPane, runs: list[Any]
    ) -> None:
        await pane.clear()
        # `set_runs` gọi `clear()` lần nữa — lúc này không còn child nào nên
        # nó là no-op và append không đụng id.
        pane.set_runs(runs)

    def _sync_phase_filter(self) -> None:
        self.case_table.set_phase_filter(self.footer.selected_phases())

    # ----- actions -----

    def action_start(self) -> None:
        if self.start_run():
            self.spawn_job()

    def action_cancel_run(self) -> None:
        self.stop_run()

    def action_focus_request(self) -> None:
        try:
            self.set_focus(self.query_one("#request", Input))
        except Exception:
            pass

    def action_toggle_phase(self, phase: str) -> None:
        self.footer.toggle_phase(phase)
        self._sync_phase_filter()

    def action_request_quit(self) -> None:
        if self.running and not self.quit_pending:
            self.quit_pending = True
            phase = self.current_phase or "start"
            self.footer.set_status(f"Đang chạy ở {phase} — nhấn q lần nữa để thoát.")
            self._append_log("warn", "Thoát? nhấn q lần nữa để xác nhận.")
            return
        self.exit()

    # ----- events -----
    def action_force_quit(self) -> None:
        """Thoát ngay lập tức khi nhấn Ctrl+C (kể cả khi input đang có focus)."""
        if self.runner is not None:
            self.stop_run()
        self.exit()

    def _sync_active_model_badge(self) -> None:
        from config.settings import settings
        from agents.llm_factory import get_active_provider
        active = get_active_provider()
        model = settings.antigravity_model if active == "antigravity" else settings.default_model
        self.footer.set_model(model)

    def action_open_palette(self) -> None:
        actions = [
            {"id": "run", "title": "▶ Run Test", "desc": "Chạy kiểm thử với yêu cầu hiện tại", "shortcut": "r"},
            {"id": "mode_test", "title": "🎯 Chế độ: TEST", "desc": "Lập & chạy kiểm thử tự động", "shortcut": "/test"},
            {"id": "mode_debug", "title": "🔍 Chế độ: DEBUG", "desc": "Điều tra lỗi & phân tích root cause", "shortcut": "/debug"},
            {"id": "mode_fix", "title": "🛠️ Chế độ: FIX", "desc": "Tự động patch code sửa bug (Search/Replace)", "shortcut": "/fix"},
            {"id": "model", "title": "⚡ Chọn AI Model", "desc": "Mở danh sách model (Gemini Flash/Pro, Claude, GPT-4o)", "shortcut": "/model"},
            {"id": "login", "title": "🔑 Đăng nhập Provider", "desc": "Đăng nhập Google Antigravity / Gemini Subscription", "shortcut": "/login"},
            {"id": "status", "title": "ℹ Kiểm tra trạng thái", "desc": "Xem provider & model đang kích hoạt", "shortcut": "/status"},
            {"id": "stop", "title": "■ Stop Run", "desc": "Dừng kiểm thử đang chạy", "shortcut": "c"},
            {"id": "clear_logs", "title": "🗑 Xóa Log", "desc": "Làm sạch khung log", "shortcut": ""},
            {"id": "toggle_api", "title": "Toggle Phase: API", "desc": "Bật/tắt phase API testing", "shortcut": "1"},
            {"id": "toggle_ui", "title": "Toggle Phase: UI", "desc": "Bật/tắt phase UI testing", "shortcut": "2"},
            {"id": "toggle_chaos", "title": "Toggle Phase: Chaos", "desc": "Bật/tắt phase Chaos testing", "shortcut": "3"},
            {"id": "toggle_perf", "title": "Toggle Phase: Perf", "desc": "Bật/tắt phase Performance testing", "shortcut": "4"},
            {"id": "help", "title": "? Trợ giúp", "desc": "Hiển thị hướng dẫn sử dụng", "shortcut": "?"},
            {"id": "quit", "title": "⏻ Thoát QC Agent", "desc": "Đóng ứng dụng", "shortcut": "q / ctrl+c"},
        ]

        def _on_run(action: dict[str, Any]) -> None:
            act_id = action.get("id")
            if act_id == "run":
                self.action_start()
            elif act_id == "mode_test":
                self._handle_slash_command("/test")
            elif act_id == "mode_debug":
                self._handle_slash_command("/debug")
            elif act_id == "mode_fix":
                self._handle_slash_command("/fix")
            elif act_id == "model":
                self._open_model_modal()
            elif act_id == "login":
                self._open_login_modal()
            elif act_id == "status":
                self._handle_slash_command("/status")
            elif act_id == "stop":
                self.action_cancel_run()
            elif act_id == "clear_logs":
                self.log_pane.clear()
            elif act_id == "toggle_api":
                self.action_toggle_phase("api")
            elif act_id == "toggle_ui":
                self.action_toggle_phase("ui")
            elif act_id == "toggle_chaos":
                self.action_toggle_phase("chaos")
            elif act_id == "toggle_perf":
                self.action_toggle_phase("performance")
            elif act_id == "help":
                self._handle_slash_command("/help")
            elif act_id == "quit":
                self.action_request_quit()

        modal = CommandPaletteScreen(actions, on_run_action=_on_run)
        self.push_screen(modal)

    def on_input_changed(self, event: Input.Changed) -> None:
        if str(event.input.id or "") == "request":
            if hasattr(self, "slash_ac") and self.slash_ac is not None:
                self.slash_ac.update_query(event.value or "")

    def on_key(self, event: Any) -> None:
        if hasattr(self, "slash_ac") and self.slash_ac and self.slash_ac.display:
            try:
                inp = self.query_one("#request", Input)
                if self.focused is inp:
                    if event.key == "tab":
                        cmd = self.slash_ac.current_command()
                        if cmd:
                            inp.value = f"{cmd} "
                            inp.cursor_position = len(inp.value)
                            self.slash_ac.hide()
                        event.stop()
                        event.prevent_default()
                    elif event.key == "up":
                        self.slash_ac.select_prev()
                        event.stop()
                        event.prevent_default()
                    elif event.key == "down":
                        self.slash_ac.select_next()
                        event.stop()
                        event.prevent_default()
                    elif event.key == "escape":
                        self.slash_ac.hide()
                        event.stop()
                        event.prevent_default()
            except Exception:
                pass


    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "run":
            self.action_start()
        elif event.button.id == "stop":
            self.action_cancel_run()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if hasattr(self, "slash_ac") and self.slash_ac is not None:
            self.slash_ac.hide()
        if str(event.input.id or "") == "request":
            text = (event.value or "").strip()
            if text.startswith("/"):
                self._handle_slash_command(text)
                return
            if not text or self.running:
                return
            self._dispatch_prompt(text)

    def _dispatch_prompt(self, text: str) -> None:
        """Hybrid router: LLM chọn intent (OpenCode-style), rồi map sang hành động.

        answer → trả lời chat; plan/run_* → chạy pipeline tương ứng. Việc chọn
        intent chạy nền để UI không treo trong lúc gọi LLM.
        """
        try:
            self.query_one("#request", Input).value = ""
        except Exception:
            pass

        if hasattr(self, "thinking_bar") and self.thinking_bar is not None:
            self.thinking_bar.start_thinking("Routing your request...")

        async def _bg_route() -> None:
            import concurrent.futures
            from agents.router import route_intent
            loop = asyncio.get_running_loop()
            try:
                with concurrent.futures.ThreadPoolExecutor() as pool:
                    intent = await loop.run_in_executor(pool, route_intent, text)
            except Exception:
                intent = "answer"
            self._apply_intent(intent, text)

        asyncio.create_task(_bg_route())

    def _apply_intent(self, intent: str, text: str) -> None:
        if intent == "answer":
            self._reply_conversation(text)
            return
        # plan / run_* → chạy pipeline. Phase lọc theo intent run_<phase>.
        if hasattr(self, "thinking_bar") and self.thinking_bar is not None:
            self.thinking_bar.stop_thinking()
        phase_map = {
            "run_api": ["api"],
            "run_ui": ["ui"],
            "run_chaos": ["chaos"],
            "run_performance": ["performance"],
        }
        if intent in phase_map:
            self.footer.set_all_phases(False)
            for p in phase_map[intent]:
                self.footer.toggle_phase(p)
        self.footer.set_request(text)
        self.action_start()

    def _reply_conversation(self, text: str) -> None:
        self.log_pane.append_user_prompt(text)

        async def _bg_reply() -> None:
            import concurrent.futures
            from agents.assistant import generate_conversational_response
            loop = asyncio.get_running_loop()
            try:
                with concurrent.futures.ThreadPoolExecutor() as pool:
                    reply = await loop.run_in_executor(pool, generate_conversational_response, text)
            except Exception as exc:
                reply = f"Could not generate a response: {exc}"
            if hasattr(self, "thinking_bar") and self.thinking_bar is not None:
                self.thinking_bar.stop_thinking()
            self.log_pane.append_assistant_message(reply)
            self.footer.set_status("Ready")

        asyncio.create_task(_bg_reply())

    def _handle_slash_command(self, cmd_text: str) -> None:
        parts = cmd_text.split()
        cmd = parts[0].lower()
        arg = parts[1].lower() if len(parts) > 1 else ""

        try:
            req_input = self.query_one("#request", Input)
            req_input.value = ""
        except Exception:
            pass

        if cmd == "/test":
            self.footer.set_mode("test")
            self._append_log("info", "🎯 Switched to **TEST** mode (Plan & run API/UI/Chaos/Perf tests)")
        elif cmd == "/debug":
            self.footer.set_mode("debug")
            self._append_log("info", "🔍 Switched to **DEBUG** mode (Root Cause Analysis & bug reproduction)")
        elif cmd == "/fix":
            self.footer.set_mode("fix")
            self._append_log("info", "🛠️ Switched to **FIX** mode (Autonomous Search/Replace code patching)")
        elif cmd == "/login":
            if not arg:
                self._open_login_modal()
            elif arg in ("antigravity", "gemini", "google"):
                self._trigger_antigravity_login()
            else:
                self._append_log("warn", f"Provider '{arg}' is not supported. Type /login for options.")
        elif cmd == "/model":
            if not arg:
                self._open_model_modal()
            else:
                self._set_active_model(arg)
        elif cmd == "/clear":
            self.log_pane.clear()
            self._append_log("info", "── Session timeline cleared ──")
        elif cmd == "/history":
            self._open_history_modal()
        elif cmd == "/status":
            from agents.llm_factory import get_active_provider
            from auth.antigravity import get_valid_antigravity_credentials
            from config.settings import settings
            provider = get_active_provider()
            model = settings.antigravity_model if provider == "antigravity" else settings.default_model
            mode = getattr(self.footer, "get_mode", lambda: "test")()
            self._append_log("info", f"── LLM Provider: {provider} | Model: {model} | Mode: {mode.upper()} ──")
            creds = get_valid_antigravity_credentials()
            if creds:
                self._append_log("info", f"• Antigravity: Signed in ({creds.get('email', 'Unknown')})")
            else:
                self._append_log("info", "• Antigravity: Not signed in")
        elif cmd == "/help":
            self._append_log("info", "── Available Commands (OpenCode Style) ──")
            self._append_log("info", "  /test             : Switch to Test mode (Plan & Run Tests)")
            self._append_log("info", "  /debug            : Switch to Debug mode (RCA & Bug Reproduction)")
            self._append_log("info", "  /fix              : Switch to Fix mode (Aider Search/Replace Patching)")
            self._append_log("info", "  /login            : Select provider login")
            self._append_log("info", "  /model            : Open AI Model selector")
            self._append_log("info", "  /model <name>     : Switch directly to named model")
            self._append_log("info", "  /history          : Browse previous test runs")
            self._append_log("info", "  /clear            : Clear session timeline")
            self._append_log("info", "  /status           : Inspect active mode, provider & model")
            self._append_log("info", "  /help             : Display this help guide")
        else:
            self._append_log("warn", f"Unknown command: {cmd}. Type /help for available commands.")

    def _trigger_antigravity_login(self) -> None:
        self._append_log("info", "🔑 Preparing Google Antigravity authentication...")
        self._append_log("info", "👉 Your browser will open to authenticate with Google.")
        self.footer.set_status("Waiting for browser authentication...")

        async def _bg_login() -> None:
            import concurrent.futures
            from auth.antigravity import run_antigravity_login
            loop = asyncio.get_running_loop()
            try:
                with concurrent.futures.ThreadPoolExecutor() as pool:
                    creds = await loop.run_in_executor(pool, run_antigravity_login, 120, True)
                email = creds.get("email") or "success"
                self._append_log("info", f"✔ Google Antigravity sign-in successful: {email}")
                self.footer.set_status(f"Google Antigravity: {email}")
            except Exception as e:
                self._append_log("error", f"✗ Sign-in failed: {e}")
                self.footer.set_status("Authentication failed")

        asyncio.create_task(_bg_login())

    def _open_login_modal(self) -> None:
        from auth.antigravity import get_valid_antigravity_credentials
        from config.settings import settings

        anti_creds = get_valid_antigravity_credentials()
        anti_status = f"Signed In ({anti_creds['email']})" if anti_creds else "Not Signed In"
        openai_status = "Configured" if settings.openai_api_key else "No Key Found"

        options = [
            {
                "id": "antigravity",
                "name": "Google Antigravity",
                "desc": "Gemini 2.5/3.x, Claude Sonnet/Opus via Google Subscription",
                "status": anti_status,
            },
            {
                "id": "cursor",
                "name": "Cursor Pro",
                "desc": "Cursor Pro subscription ($20/mo)",
                "status": "Coming Soon",
            },
            {
                "id": "openai",
                "name": "OpenAI API Key",
                "desc": "Use OPENAI_API_KEY from .env",
                "status": openai_status,
            },
        ]

        def _on_select(opt: dict[str, Any]) -> None:
            target = opt.get("id")
            if target == "antigravity":
                self._trigger_antigravity_login()
            elif target == "cursor":
                self._append_log("warn", "Cursor Pro provider is currently under development.")
            elif target == "openai":
                self._append_log("info", "Please configure OPENAI_API_KEY in your .env file.")

        modal = SelectionModalScreen("Sign In Provider (Subscription / API Key)", options, on_select=_on_select)
        self.push_screen(modal)

    def _set_active_model(self, model_id: str) -> None:
        from config.settings import settings
        if model_id.startswith("gemini") or "claude" in model_id:
            settings.antigravity_model = model_id
            settings.antigravity_planner_model = model_id
            settings.antigravity_generator_model = model_id
            settings.antigravity_vision_model = model_id
            settings.llm_provider = "antigravity"
            provider = "antigravity"
        else:
            settings.default_model = model_id
            settings.planner_model = model_id
            settings.generator_model = model_id
            settings.llm_provider = "openai"
            provider = "openai"

        self._append_log("info", f"✔ Switched active model to: [bold]{model_id}[/bold] ({provider})")
        self.footer.set_model(model_id)
        self.footer.set_status(f"Model: {model_id}")

    def _open_model_modal(self) -> None:
        from config.settings import settings
        from agents.llm_factory import get_active_provider

        active_provider = get_active_provider()
        current_model = (
            settings.antigravity_model if active_provider == "antigravity" else settings.default_model
        )

        from auth.discovery import fetch_live_antigravity_models
        models_list = list(fetch_live_antigravity_models())
        models_list.extend([
            {
                "id": "gpt-4o",
                "name": "gpt-4o",
                "desc": "OpenAI GPT-4o standard (requires OPENAI_API_KEY)",
                "provider": "openai",
            },
            {
                "id": "gpt-4o-mini",
                "name": "gpt-4o-mini",
                "desc": "OpenAI GPT-4o mini cost-effective model",
                "provider": "openai",
            },
        ])

        for m in models_list:
            if m["id"] == current_model:
                m["status"] = "✔ Active"
            else:
                m["status"] = "Available"

        def _on_select(opt: dict[str, Any]) -> None:
            self._set_active_model(opt.get("id", ""))

        modal = SelectionModalScreen("Select AI Model (OpenCode Style)", models_list, on_select=_on_select)
        self.push_screen(modal)

    def _open_history_modal(self) -> None:
        runs = list(self.history_pane.runs)
        if not runs:
            self._append_log("info", "No previous test execution runs found.")
            return

        options = []
        for i, r in enumerate(runs[:20], 1):
            mark = "✓" if r.failed == 0 and r.error == 0 and r.total > 0 else "✗"
            status_text = f"{mark} {r.passed}/{r.total} passed"
            options.append({
                "id": str(i - 1),
                "name": f"Run #{r.short_id}",
                "desc": f"{r.request or '(no request)'} · {r.total} tests",
                "status": status_text,
                "run": r,
            })

        def _on_select(opt: dict[str, Any]) -> None:
            selected_run = opt.get("run")
            if selected_run:
                self._on_history_run_opened(HistoryPane.RunOpened(selected_run))

        modal = SelectionModalScreen("Select Run from History", options, on_select=_on_select)
        self.push_screen(modal)
    @on(HistoryPane.RunOpened)
    def _on_history_run_opened(self, event: HistoryPane.RunOpened) -> None:
        """Nạp kết quả một run trong lịch sử vào bảng + log (spec §4.1)."""
        run = event.run
        self.case_table.clear_rows()
        # Bỏ lọc phase: run đã xong nên muốn thấy đủ mọi test case của nó.
        self.case_table.set_phase_filter([])
        for detail in run.details:
            if isinstance(detail, dict):
                self.case_table.add_result(detail)

        self.log_pane.clear()
        header = run.plan_title or "(no test plan)"
        self._append_log("info", f"── History: {run.request or '(no request)'} ──")
        self._append_log("info", f"Test plan: {header}")
        self._append_log(
            "info",
            f"{run.total} tests · {run.passed} pass · {run.failed} fail "
            f"· {run.error} error · {run.skipped} skip "
            f"· {format_duration(run.duration_ms)}",
        )
        for detail in run.details:
            if not isinstance(detail, dict):
                continue
            mark = self.case_table.status_mark(detail.get("status"))
            line = f"  {mark} {detail.get('id')}"
            if detail.get("title"):
                line += f" – {detail['title']}"
            if detail.get("error_message"):
                line += f" → {detail['error_message']}"
            self._append_log("info", line)
        if run.path:
            self._append_log("info", f"Report: {run.path}")

        self.received_results = len(run.details)
        self.footer.set_progress("history", run.passed, run.total)
        self.footer.set_status(
            f"Viewing run {run.short_id} · {run.passed}/{run.total} pass"
        )
