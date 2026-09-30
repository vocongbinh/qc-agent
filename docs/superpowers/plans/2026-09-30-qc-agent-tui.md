# QC Agent TUI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Xây Textual TUI dashboard cho QC Agent, chạy cùng lệnh `main.py run` hiện có, cho phép theo dõi job realtime, review/chọn lọc Test Plan, và xem lịch sử từ `reports/`.

**Architecture:** LangGraph chạy trong worker thread; TUI chạy trên asyncio event loop. Giao tiếp một chiều qua `EventBus` (thread-safe, `call_soon_threadsafe` → `asyncio.Queue`). Worker thread block tại `ReviewGate` (`threading.Event`) khi chờ human review, main thread giải phóng gate. Executors giữ nguyên `console.print` cho CLI, chỉ thêm `bus.emit(...)`.

**Tech Stack:** Python 3.11+, Textual ≥0.60, LangGraph 0.6 (đã có), pytest + pytest-asyncio.

Spec: `docs/superpowers/specs/2026-09-30-qc-agent-tui-design.md`

---

## Cấu trúc file

| File | Trách nhiệm |
|---|---|
| `tui/__init__.py` | Package marker |
| `tui/bus.py` | `Event` dataclass, `EventBus`, `get_bus()`, `silence_agent_console()` |
| `tui/gate.py` | `ReviewGate` — block thread chờ approve/reject |
| `tui/runner.py` | `JobRunner` — thread wrapper, 2 pause point, cancel flag |
| `tui/app.py` | `QCTApp` — layout, bindings, điều phối event |
| `tui/widgets/logpane.py` | `LogPane` — RichLog realtime |
| `tui/widgets/casestable.py` | `CaseTable` — DataTable + filter phase |
| `tui/widgets/history.py` | `HistoryPane` — đọc `reports/report_*.json` |
| `tui/widgets/review.py` | `ReviewModal` — duyệt plan, bật/tắt case |
| `tui/widgets/footer.py` | `StatusFooter` — request input, phase select, Run/Stop, progress |
| `tests/conftest.py` | Fixtures chung (event loop, bus, fake graph) |
| `tests/test_bus.py` | Event bus + silence console |
| `tests/test_gate.py` | ReviewGate |
| `tests/test_runner.py` | JobRunner: resume/reject/cancel/crash |
| `tests/test_review.py` | ReviewModal logic + phase filter |
| `tests/test_history.py` | Parse report, empty dir, JSON hỏng |
| `tests/test_casestable.py` | CaseTable rows + filter |

Ràng buộc: `agents/graph.py` **không sửa**. `main.py run` **không đổi hành vi**.

---

## Task 1: Nền tảng — Python 3.11 + dependencies

Code hiện tại **không chạy** trên Python 3.9 vì `config/settings.py:13` dùng `str | None`. Task này dựng nền cho mọi task sau.

**Files:**
- Modify: `requirements.txt`
- Create: `pyproject.toml` (cấu hình pytest)

- [ ] **Step 1: Cài Python 3.11+ và tạo venv mới**

```bash
brew install python@3.12
cd /Users/binhvc/qc-agent
rm -rf .venv
python3.12 -m venv .venv
source .venv/bin/activate
python --version
```

Expected: `Python 3.12.x`

- [ ] **Step 2: Thêm textual và pytest vào requirements.txt**

Sửa `requirements.txt`, thêm vào cuối file:

```
# TUI
textual>=0.60.0

# Testing
pytest>=8.0.0
pytest-asyncio>=0.24.0
```

- [ ] **Step 3: Cài dependencies**

```bash
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

- [ ] **Step 4: Tạo pyproject.toml cho pytest**

Create `pyproject.toml`:

```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
asyncio_mode = "auto"
asyncio_default_fixture_loop_scope = "function"
filterwarnings = [
    "ignore::DeprecationWarning",
]
```

- [ ] **Step 5: Tạo thư mục tests với conftest tối thiểu**

Create `tests/__init__.py` (file rỗng).

Create `tests/conftest.py`:

```python
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
```

- [ ] **Step 6: Xác nhận CLI cũ import được**

```bash
source .venv/bin/activate
python -c "from config.settings import settings; print('OK', settings.default_base_url)"
```

Expected: `OK http://localhost:8000`

- [ ] **Step 7: Xác nhận Textual cài đúng**

```bash
source .venv/bin/activate
python -c "import textual; print('textual', textual.__version__)"
```

Expected: in ra version ≥ 0.60

**LƯU Ý QUAN TRỌNG — Textual 8.x API deviations:**

Plan này viết cho Textual 0.60 nhưng `pip install` sẽ lấy bản mới nhất (8.x).
Đã verify các sai lệch sau, **phải áp dụng khi implement**:

| API trong plan | Textual 8.x | Sửa thành |
|---|---|---|
| `from textual.widgets import CheckboxSet` | `CheckboxSet` đã bị **xoá** | Dùng `Checkbox` riêng cho mỗi phase, import từ `textual.widgets._checkbox` |
| `CheckboxSet(*[(label, key)...], value={...})` | không tồn tại | Nhiều `Checkbox(label, value=bool, id=...)` trong container |
| `self.query_one("#phases", CheckboxSet)` | không tồn tại | `query("#phase-checkboxes Checkbox")` |
| `app.get_loop()` | không tồn tại | `self._loop` (Textual tự set trong `run_async`) |
| `RichLog(highlight=, markup=, wrap=)` | OK, có thêm `max_lines` | giữ nguyên, có thể thêm `max_lines` |
| `DataTable.add_column(name, key=)` | OK | giữ nguyên |
| `DataTable.RowSelected.cursor_row` | OK | giữ nguyên |
| `ListView`, `ListItem`, `Label` | OK | giữ nguyên |

Verify trước khi code:

```bash
source .venv/bin/activate
python -c "
from textual.widgets import RichLog, DataTable, Button, Input, Label, Static, Footer, ListItem, ListView
from textual.widgets._checkbox import Checkbox
from textual.screen import ModalScreen
from textual.containers import Horizontal, Vertical
print('imports OK')
"
```

Expected: `imports OK`

- [ ] **Step 8: Commit**

```bash
git add requirements.txt pyproject.toml tests/__init__.py tests/conftest.py
git commit -m "chore: add textual + pytest, configure pytest

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: Event bus

Nền tảng giao tiếp giữa worker thread và TUI. Mọi task sau phụ thuộc task này.

**Files:**
- Create: `tui/__init__.py`
- Create: `tui/bus.py`
- Test: `tests/test_bus.py`

- [ ] **Step 1: Viết test failing**

Create `tui/__init__.py` (file rỗng).

Create `tests/test_bus.py`:

```python
from __future__ import annotations

import asyncio
import io
import sys
import threading
import types

import pytest
from rich.console import Console

from tui.bus import Event, EventBus, get_bus, reset_bus, silence_agent_console


def test_event_is_frozen():
    ev = Event(kind="log", payload={"text": "hi"})
    with pytest.raises(Exception):
        ev.kind = "other"  # type: ignore[misc]


@pytest.mark.asyncio
async def test_emit_then_drain_preserves_order():
    bus = EventBus()
    for i in range(5):
        bus.emit(Event(kind="log", payload={"text": str(i)}))
    await bus.drain(0.05)
    drained = []
    while not bus.queue.empty():
        drained.append(bus.queue.get_nowait())
    assert [e.payload["text"] for e in drained] == ["0", "1", "2", "3", "4"]


@pytest.mark.asyncio
async def test_emit_from_thread_is_thread_safe():
    loop = asyncio.get_running_loop()
    bus = EventBus(loop=loop)
    N = 200

    def worker():
        for i in range(N):
            bus.emit(Event(kind="log", payload={"i": i}))

    t = threading.Thread(target=worker)
    t.start()
    t.join()

    await bus.drain(0.2)
    count = bus.queue.qsize()
    assert count == N, f"expected {N} events, got {count}"


@pytest.mark.asyncio
async def test_drain_stops_early_when_queue_empty():
    bus = EventBus()
    bus.emit(Event(kind="log", payload={}))
    await bus.drain(0.1)
    await bus.drain(0.01)
    assert bus.queue.qsize() == 1


def test_emit_without_running_loop_is_noop():
    """emit() ngoài event loop không được raise (worker thread chưa có loop)."""
    bus = EventBus(loop=None)
    bus.emit(Event(kind="log", payload={"text": "orphan"}))  # không raise


def test_get_bus_is_singleton():
    reset_bus()
    a = get_bus()
    b = get_bus()
    assert a is b
    reset_bus()


def test_silence_agent_console_replaces_agent_consoles():
    mod = types.ModuleType("agents.fake_executor")
    mod.console = Console()
    sys.modules["agents.fake_executor"] = mod
    try:
        original = mod.console
        silence_agent_console()

        assert mod.console is not original, "phải thay object console"
        mod.console.print("SHOULD_NOT_APPEAR")
        assert "SHOULD_NOT_APPEAR" not in sys.stdout.getvalue()
    finally:
        del sys.modules["agents.fake_executor"]


def test_silence_agent_console_is_idempotent():
    mod = types.ModuleType("agents.fake2")
    mod.console = Console()
    sys.modules["agents.fake2"] = mod
    try:
        silence_agent_console()
        first = mod.console.file
        silence_agent_console()
        assert mod.console.file is first
    finally:
        del sys.modules["agents.fake2"]


def test_silence_ignores_non_agent_modules():
    mod = types.ModuleType("some_other_pkg")
    mod.console = Console()
    sys.modules["some_other_pkg"] = mod
    try:
        original = mod.console
        silence_agent_console()
        assert mod.console is original
    finally:
        del sys.modules["some_other_pkg"]
```

**Lưu ý khi chạy:** `test_silence_agent_console_replaces_agent_consoles` đọc
`sys.stdout.getvalue()`. Dưới pytest, `sys.stdout` là `EncodedFile`/`CaptureFixture`
không có `getvalue()`. Thêm ở đầu test:

```python
    import io as _io
    captured = _io.StringIO()
    real_stdout = sys.stdout
    sys.stdout = captured
    try:
        ...
    finally:
        sys.stdout = real_stdout
        del sys.modules["agents.fake_executor"]
```

- [ ] **Step 2: Chạy test để xác nhận fail**

```bash
source .venv/bin/activate
pytest tests/test_bus.py -v
```

Expected: FAIL với `ModuleNotFoundError: No module named 'tui'`

- [ ] **Step 3: Viết implementation**

Create `tui/bus.py`:

```python
"""Event bus: kênh giao tiếp một chiều từ worker thread sang TUI event loop."""

from __future__ import annotations

import asyncio
import io
import sys
from dataclasses import dataclass, field
from typing import Any

from rich.console import Console

_AGENT_CONSOLE_BUFS: dict[str, io.StringIO] = {}


@dataclass(frozen=True)
class Event:
    kind: str
    payload: dict[str, Any] = field(default_factory=dict)


class EventBus:
    """Bus đẩy event từ thread bất kỳ vào asyncio.Queue của event loop chính."""

    def __init__(self, loop: asyncio.AbstractEventLoop | None = None) -> None:
        self.loop = loop
        self.queue: asyncio.Queue[Event] = asyncio.Queue()

    def bind(self, loop: asyncio.AbstractEventLoop) -> None:
        self.loop = loop

    def emit(self, event: Event) -> None:
        if self.loop is None or self.loop.is_closed():
            return
        try:
            self.loop.call_soon_threadsafe(self.queue.put_nowait, event)
        except RuntimeError:
            pass

    async def drain(self, timeout: float = 0.05) -> None:
        """Đợi queue có phần tử, hoặc hết timeout."""
        if self.queue.empty():
            try:
                await asyncio.wait_for(self.queue.get(), timeout=timeout)
            except asyncio.TimeoutError:
                return
        await asyncio.sleep(0)


_bus: EventBus | None = None


def get_bus() -> EventBus:
    global _bus
    if _bus is None:
        _bus = EventBus()
    return _bus


def reset_bus() -> None:
    global _bus
    _bus = None


def agent_console_buffer(module_name: str) -> io.StringIO:
    if module_name not in _AGENT_CONSOLE_BUFS:
        _AGENT_CONSOLE_BUFS[module_name] = io.StringIO()
    return _AGENT_CONSOLE_BUFS[module_name]


def silence_agent_console() -> None:
    """Chuyển Console của mọi module `agents.*` sang buffer trong bộ nhớ.

    Rich resolve `sys.stdout` lúc ghi nên không thể redirect stdout (sẽ phá
    Textual). Thay object console từng module là cách an toàn.
    """
    for name, mod in list(sys.modules.items()):
        if not name.startswith("agents."):
            continue
        console = getattr(mod, "console", None)
        if not isinstance(console, Console):
            continue
        buf = agent_console_buffer(name)
        mod.console = Console(file=buf, width=200)
```

- [ ] **Step 4: Chạy test để xác nhận pass**

```bash
source .venv/bin/activate
pytest tests/test_bus.py -v
```

Expected: 8 passed

- [ ] **Step 5: Commit**

```bash
git add tui/__init__.py tui/bus.py tests/test_bus.py
git commit -m "feat(tui): add event bus with thread-safe emit and console silencing

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: ReviewGate

Thread chờ human approve/reject. Dùng `threading.Event` + lock, timeout có kiểm soát.

**Files:**
- Create: `tui/gate.py`
- Test: `tests/test_gate.py`

- [ ] **Step 1: Viết test failing**

Create `tests/test_gate.py`:

```python
from __future__ import annotations

import threading
import time

from tui.gate import ReviewGate


def test_wait_blocks_until_resolved():
    gate = ReviewGate(timeout=5.0)
    result: list = []

    def worker():
        result.append(gate.wait())

    t = threading.Thread(target=worker)
    t.start()
    time.sleep(0.1)
    assert not result, "phải block khi chưa resolve"

    kept = [{"id": "TC_001"}]
    gate.resolve(kept)
    t.join(timeout=3)

    assert result == [kept]


def test_resolve_none_means_reject():
    gate = ReviewGate(timeout=5.0)
    result: list = []
    t = threading.Thread(target=lambda: result.append(gate.wait()))
    t.start()
    time.sleep(0.05)
    gate.resolve(None)
    t.join(timeout=3)
    assert result == [None]


def test_timeout_returns_none():
    gate = ReviewGate(timeout=0.1)
    start = time.perf_counter()
    got = gate.wait()
    elapsed = time.perf_counter() - start
    assert got is None
    assert 0.05 < elapsed < 2.0, f"timeout sai: {elapsed}s"


def test_is_resolved_flag():
    gate = ReviewGate(timeout=5.0)
    assert gate.is_resolved() is False
    gate.resolve([])
    assert gate.is_resolved() is True


def test_wait_after_resolve_returns_immediately():
    gate = ReviewGate(timeout=0.1)
    gate.resolve([{"id": "X"}])
    start = time.perf_counter()
    got = gate.wait()
    assert got == [{"id": "X"}]
    assert time.perf_counter() - start < 0.05


def test_release_unblocks_with_none():
    """Dùng khi thread crash — gate không được treo vĩnh viễn."""
    gate = ReviewGate(timeout=10.0)
    result: list = []
    t = threading.Thread(target=lambda: result.append(gate.wait()))
    t.start()
    time.sleep(0.05)
    gate.release()
    t.join(timeout=3)
    assert result == [None]
```

- [ ] **Step 2: Chạy test để xác nhận fail**

```bash
source .venv/bin/activate
pytest tests/test_gate.py -v
```

Expected: FAIL với `ModuleNotFoundError: No module named 'tui.gate'`

- [ ] **Step 3: Viết implementation**

Create `tui/gate.py`:

```python
"""ReviewGate — chặn worker thread cho tới khi TUI resolve."""

from __future__ import annotations

import threading
from typing import Any

DEFAULT_TIMEOUT = 300.0


class ReviewGate:
    def __init__(self, timeout: float = DEFAULT_TIMEOUT) -> None:
        self._timeout = timeout
        self._event = threading.Event()
        self._lock = threading.Lock()
        self._result: list[Any] | None = None

    def wait(self) -> list[dict] | None:
        """Block tới khi resolve. Trả list test case được giữ, None = reject/timeout."""
        if not self._event.wait(timeout=self._timeout):
            return None
        with self._lock:
            return self._result

    def resolve(self, kept_cases: list[dict] | None) -> None:
        """Giải phóng gate. kept_cases=None nghĩa là reject."""
        with self._lock:
            self._result = kept_cases
        self._event.set()

    def release(self) -> None:
        """Giải phóng với reject — dùng khi thread crash, tránh treo."""
        self.resolve(None)

    def is_resolved(self) -> bool:
        return self._event.is_set()
```

- [ ] **Step 4: Chạy test để xác nhận pass**

```bash
source .venv/bin/activate
pytest tests/test_gate.py -v
```

Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add tui/gate.py tests/test_gate.py
git commit -m "feat(tui): add ReviewGate with timeout and release

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: AgentState — thêm `phases` và `job_id`

`AgentState` là `TypedDict` không có `total=False`, thêm key bắt buộc sẽ khiến type
checker báo lỗi cho `main.py`. Dùng `NotRequired` để không phá code cũ.

**Files:**
- Modify: `agents/state.py:110-134`
- Test: `tests/test_state.py`

- [ ] **Step 1: Viết test failing**

Create `tests/test_state.py`:

```python
from __future__ import annotations

from agents.state import AgentState


def test_state_accepts_new_optional_fields():
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
    assert "phases" not in state
    assert "job_id" not in state


def test_state_accepts_new_fields_when_present():
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
```

- [ ] **Step 2: Chạy test — phải pass sẵn vì TypedDict không validate runtime**

```bash
source .venv/bin/activate
pytest tests/test_state.py -v
```

Expected: 2 passed. Đây là test bảo vệ: sau khi thêm field, `main.py` vẫn type-check được.
Nếu FAIL vì `phases` chưa tồn tại trong TypedDict, đó là lúc cần sửa.

- [ ] **Step 3: Thêm field mới vào AgentState**

Sửa `agents/state.py`, thay import đầu file:

```python
from __future__ import annotations

from enum import Enum
from typing import Annotated, Any, NotRequired
from typing_extensions import TypedDict
```

Sửa `agents/state.py:132-134`, thay:

```python
    # Control
    current_step: str
    error: str | None
    ui_headed: bool  # Phase 2: chạy browser headed
```

bằng:

```python
    # Control
    current_step: str
    error: str | None
    ui_headed: bool  # Phase 2: chạy browser headed

    # TUI (optional — không bắt buộc, main.py không cần điền)
    phases: NotRequired[list[str]]   # các phase được chọn: api/ui/chaos/performance
    job_id: NotRequired[str]        # uuid4, dùng làm thread_id cho LangGraph
```

- [ ] **Step 4: Chạy test lại**

```bash
source .venv/bin/activate
pytest tests/test_state.py -v
```

Expected: 2 passed

- [ ] **Step 5: Xác nhận main.py không hỏng**

```bash
source .venv/bin/activate
python -c "import main; print('main imports OK')"
```

Expected: `main imports OK`

- [ ] **Step 6: Commit**

```bash
git add agents/state.py tests/test_state.py
git commit -m "feat(agents): add optional phases and job_id to AgentState

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: Phase filtering

Logic thuần, không phụ thuộc UI hay thread. Tách ra để test được độc lập.

**Files:**
- Create: `tui/filters.py`
- Test: `tests/test_filters.py`

- [ ] **Step 1: Viết test failing**

Create `tests/test_filters.py`:

```python
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


def test_filter_keeps_matching_types():
    tests = [
        {"id": "A", "type": "api"},
        {"id": "B", "type": "ui"},
        {"id": "C", "type": "api"},
    ]
    got = filter_by_phases(tests, ["api"])
    assert [t["id"] for t in got] == ["A", "C"]


def test_filter_missing_type_treated_as_api():
    tests = [{"id": "A"}, {"id": "B", "type": "ui"}]
    got = filter_by_phases(tests, ["api"])
    assert [t["id"] for t in got] == ["A"]


def test_filter_empty_phases_keeps_everything():
    tests = [{"id": "A", "type": "api"}, {"id": "B", "type": "ui"}]
    got = filter_by_phases(tests, [])
    assert len(got) == 2


def test_filter_unknown_phase_returns_nothing():
    tests = [{"id": "A", "type": "api"}]
    assert filter_by_phases(tests, ["banana"]) == []


def test_filter_does_not_mutate_input():
    tests = [{"id": "A", "type": "api"}, {"id": "B", "type": "ui"}]
    snapshot = list(tests)
    filter_by_phases(tests, ["api"])
    assert tests == snapshot
```

- [ ] **Step 2: Chạy test để xác nhận fail**

```bash
source .venv/bin/activate
pytest tests/test_filters.py -v
```

Expected: FAIL với `ModuleNotFoundError: No module named 'tui.filters'`

- [ ] **Step 3: Viết implementation**

Create `tui/filters.py`:

```python
"""Lọc test case theo phase đã chọn trong TUI."""

from __future__ import annotations

from typing import Any, Iterable, Optional

ALL_PHASES: list[str] = ["api", "ui", "chaos", "performance"]


def normalize_phases(phases: Optional[Iterable[str]]) -> list[str]:
    """Rỗng = cho phép tất cả. Giữ thứ tự, bỏ trùng và giá trị lạ."""
    if not phases:
        return list(ALL_PHASES)
    seen: list[str] = []
    for p in phases:
        p = (p or "").strip().lower()
        if p in ALL_PHASES and p not in seen:
            seen.append(p)
    return seen


def filter_by_phases(
    tests: list[dict[str, Any]], phases: Optional[Iterable[str]]
) -> list[dict[str, Any]]:
    """Giữ test case có `type` nằm trong phases. Thiếu type = coi là api."""
    allowed = set(normalize_phases(phases))
    if not allowed:
        return []
    return [
        t for t in (tests or [])
        if (t.get("type") or "api").lower() in allowed
    ]
```

- [ ] **Step 4: Chạy test để xác nhận pass**

```bash
source .venv/bin/activate
pytest tests/test_filters.py -v
```

Expected: 8 passed

- [ ] **Step 5: Commit**

```bash
git add tui/filters.py tests/test_filters.py
git commit -m "feat(tui): add phase filtering helpers

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 6: History reader

Đọc `reports/report_*.json`. Pure function, không đụng UI.

**Files:**
- Create: `tui/history_reader.py`
- Test: `tests/test_history_reader.py`

- [ ] **Step 1: Viết test failing**

Create `tests/test_history_reader.py`:

```python
from __future__ import annotations

import json

import pytest

from tui.history_reader import RunSummary, load_history, parse_report


def _write_report(tmp_path, name, payload):
    d = tmp_path / "reports"
    d.mkdir(exist_ok=True)
    f = d / name
    f.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return f


def test_parse_report_extracts_fields(tmp_path):
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
    assert s.total == 10
    assert s.passed == 8
    assert s.failed == 2
    assert s.duration_ms == 1234.5
    assert len(s.details) == 2
    assert s.details[1]["error_message"] == "401"


def test_parse_report_tolerates_missing_keys():
    s = parse_report({}, "x.json")
    assert s.request == ""
    assert s.total == 0
    assert s.details == []


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
    _write_report(tmp_path, "report_20260101_000000.json",
                  {"user_request": "old", "timestamp": "20260101_000000"})
    _write_report(tmp_path, "report_20261231_235959.json",
                  {"user_request": "new", "timestamp": "20261231_235959"})
    runs, _ = load_history(tmp_path / "reports")
    assert [r.request for r in runs] == ["new", "old"]


def test_load_history_counts_broken_files(tmp_path):
    d = tmp_path / "reports"
    d.mkdir()
    (d / "report_20260101_000000.json").write_text("{not json", encoding="utf-8")
    _write_report(tmp_path, "report_20260102_000000.json", {"user_request": "ok"})
    runs, broken = load_history(d)
    assert broken == 1
    assert len(runs) == 1


def test_load_history_ignores_non_report_files(tmp_path):
    d = tmp_path / "reports"
    d.mkdir()
    (d / "random.json").write_text("{}", encoding="utf-8")
    runs, _ = load_history(d)
    assert runs == []
```

- [ ] **Step 2: Chạy test để xác nhận fail**

```bash
source .venv/bin/activate
pytest tests/test_history_reader.py -v
```

Expected: FAIL với `ModuleNotFoundError: No module named 'tui.history_reader'`

- [ ] **Step 3: Viết implementation**

Create `tui/history_reader.py`:

```python
"""Đọc lịch sử run từ reports/report_*.json."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class RunSummary:
    path: str
    timestamp: str
    request: str
    plan_title: str
    scope: str
    total: int
    passed: int
    failed: int
    error: int
    skipped: int
    duration_ms: float
    details: list[dict[str, Any]] = field(default_factory=list)

    @property
    def short_id(self) -> str:
        return self.path.rsplit("/", 1)[-1].replace("report_", "").replace(".json", "")


def parse_report(data: dict[str, Any], path: str) -> RunSummary:
    plan = data.get("test_plan") or {}
    ex = data.get("execution_result") or {}
    return RunSummary(
        path=path,
        timestamp=str(data.get("timestamp") or ""),
        request=str(data.get("user_request") or ""),
        plan_title=str(plan.get("title") or ""),
        scope=str(plan.get("scope") or ""),
        total=int(ex.get("total") or 0),
        passed=int(ex.get("passed") or 0),
        failed=int(ex.get("failed") or 0),
        error=int(ex.get("error") or 0),
        skipped=int(ex.get("skipped") or 0),
        duration_ms=float(ex.get("duration_ms") or 0.0),
        details=list(ex.get("details") or []),
    )


def load_history(reports_dir: Path) -> tuple[list[RunSummary], int]:
    """Trả (runs mới nhất trước, số file JSON hỏng)."""
    reports_dir = Path(reports_dir)
    if not reports_dir.is_dir():
        return [], 0

    runs: list[RunSummary] = []
    broken = 0
    for f in reports_dir.glob("report_*.json"):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            broken += 1
            continue
        if not isinstance(data, dict):
            broken += 1
            continue
        runs.append(parse_report(data, str(f)))

    runs.sort(key=lambda r: r.timestamp, reverse=True)
    return runs, broken
```

- [ ] **Step 4: Chạy test để xác nhận pass**

```bash
source .venv/bin/activate
pytest tests/test_history_reader.py -v
```

Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add tui/history_reader.py tests/test_history_reader.py
git commit -m "feat(tui): add report history reader

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 7: JobRunner — trái tim của TUI

Task rủi ro nhất, làm trước UI. Chạy `qc_graph` trong thread, 2 pause point, cancel flag.

**Files:**
- Create: `tui/runner.py`
- Test: `tests/test_runner.py`

- [ ] **Step 1: Viết test failing**

Create `tests/test_runner.py`:

```python
from __future__ import annotations

import threading
import time

import pytest

from tui.bus import Event, EventBus
from tui.gate import ReviewGate
from tui.runner import JobRunner, build_initial_state


class FakeGraph:
    """Giả lập qc_graph: yield các state theo cấu hình, có checkpoint."""

    def __init__(self, steps, error_at=None):
        self.steps = steps
        self.error_at = error_at
        self.updates: list[tuple[str, dict]] = []
        self.stream_calls: list[object] = []
        self.approve_on_resume = True
        self._lock = threading.Lock()

    def stream(self, state, config=None, stream_mode=None):
        with self._lock:
            self.stream_calls.append(state)
        start = 0 if state is not None else 1
        for st in self.steps[start:]:
            if self.error_at and st.get("current_step") == self.error_at:
                st = {**st, "error": "boom", "current_step": "planner_failed"}
            yield st

    def update_state(self, config, values, node=None):
        with self._lock:
            self.updates.append((node, values))
        # Patch state tương lai
        for i, st in enumerate(self.steps):
            for k, v in values.items():
                st.setdefault(k, v)
        if not self.approve_on_resume:
            # Giả lập graph không chạy tiếp khi chưa approve
            self.steps = [self.steps[0]]
        return {}


def _state(**kw):
    base = {
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
    }
    base.update(kw)
    return base


PLANNER_DONE = {
    "current_step": "planner_done",
    "test_plan": {
        "title": "P", "test_cases": [
            {"id": "TC_1", "type": "api", "title": "one"},
            {"id": "TC_2", "type": "ui", "title": "two"},
        ],
    },
    "human_approved": False,
}

GENERATOR_DONE = {
    "current_step": "generator_done",
    "generated_tests": [
        {"id": "TC_1", "type": "api"},
        {"id": "TC_2", "type": "ui"},
    ],
}

REPORTER_DONE = {
    "current_step": "reporter_done",
    "report_path": "/reports/report_x.json",
    "final_summary": "done",
}


def test_build_initial_state_defaults():
    s = build_initial_state("Test login", phases=["api"])
    assert s["user_request"] == "Test login"
    assert s["phases"] == ["api"]
    assert s["human_approved"] is False
    assert s["current_step"] == "start"
    assert s["ui_headed"] is False
    assert s["job_id"]


def test_build_initial_state_ui_headed():
    s = build_initial_state("t", phases=["ui"], ui_headed=True)
    assert s["ui_headed"] is True


def _drain(bus, timeout=3.0):
    events = []
    deadline = time.time() + timeout
    while time.time() < deadline:
        while not bus.queue.empty():
            events.append(bus.queue.get_nowait())
        if any(e.kind in ("run_done", "run_error", "cancelled") for e in events):
            break
        time.sleep(0.01)
    return events


def test_run_emits_plan_ready_and_waits():
    bus = EventBus()
    graph = FakeGraph([_state(**PLANNER_DONE), _state(**GENERATOR_DONE),
                       _state(**REPORTER_DONE)])
    gate = ReviewGate(timeout=5.0)
    r = JobRunner(graph=graph, bus=bus, review_gate=gate)
    r.spawn(build_initial_state("t", phases=["api"]))

    seen = []
    deadline = time.time() + 2
    while time.time() < deadline and "plan_ready" not in seen:
        while not bus.queue.empty():
            seen.append(bus.queue.get_nowait().kind)
        time.sleep(0.01)
    assert "plan_ready" in seen
    assert gate.is_resolved() is False

    gate.resolve([{"id": "TC_1", "type": "api"}])
    events = _drain(bus)
    assert "run_done" in [e.kind for e in events]


def test_reject_stops_before_generator():
    bus = EventBus()
    graph = FakeGraph([_state(**PLANNER_DONE), _state(**GENERATOR_DONE),
                       _state(**REPORTER_DONE)])
    gate = ReviewGate(timeout=5.0)
    r = JobRunner(graph=graph, bus=bus, review_gate=gate)
    r.spawn(build_initial_state("t", phases=["api"]))
    time.sleep(0.2)
    deadline = time.time() + 2
    while time.time() < deadline and not gate.is_resolved():
        time.sleep(0.01)
    gate.resolve(None)
    r.join(timeout=3)

    events = _drain(bus)
    kinds = [e.kind for e in events]
    assert "cancelled" in kinds
    assert "run_done" not in kinds


def test_approve_filters_plan_test_cases():
    bus = EventBus()
    graph = FakeGraph([_state(**PLANNER_DONE), _state(**GENERATOR_DONE),
                       _state(**REPORTER_DONE)])
    gate = ReviewGate(timeout=5.0)
    r = JobRunner(graph=graph, bus=bus, review_gate=gate)
    r.spawn(build_initial_state("t", phases=["api", "ui"]))
    time.sleep(0.2)
    deadline = time.time() + 2
    while time.time() < deadline and not gate.is_resolved():
        time.sleep(0.01)
    gate.resolve([{"id": "TC_1", "type": "api"}])
    _drain(bus)

    node, values = graph.updates[0]
    kept = values["test_plan"]["test_cases"]
    assert [c["id"] for c in kept] == ["TC_1"]


def test_phases_filter_generated_tests():
    bus = EventBus()
    graph = FakeGraph([_state(**PLANNER_DONE), _state(**GENERATOR_DONE),
                       _state(**REPORTER_DONE)])
    gate = ReviewGate(timeout=5.0)
    r = JobRunner(graph=graph, bus=bus, review_gate=gate)
    r.spawn(build_initial_state("t", phases=["api"]))
    time.sleep(0.2)
    deadline = time.time() + 2
    while time.time() < deadline and not gate.is_resolved():
        time.sleep(0.01)
    gate.resolve(PLANNER_DONE["test_plan"]["test_cases"])
    _drain(bus)

    node, values = graph.updates[1]
    assert [t["id"] for t in values["generated_tests"]] == ["TC_1"]


def test_error_emits_run_error():
    bus = EventBus()
    graph = FakeGraph([_state(**PLANNER_DONE)], error_at="planner_done")
    gate = ReviewGate(timeout=2.0)
    r = JobRunner(graph=graph, bus=bus, review_gate=gate)
    r.spawn(build_initial_state("t", phases=["api"]))
    time.sleep(0.2)
    gate.resolve([])
    events = _drain(bus)
    assert "run_error" in [e.kind for e in events]


def test_crash_releases_gate():
    class ExplodingGraph:
        def stream(self, state, config=None, stream_mode=None):
            yield {"current_step": "planner_done",
                   "test_plan": {"title": "P", "test_cases": []},
                   "human_approved": False}
            raise RuntimeError("kaboom")

        def update_state(self, config, values, node=None):
            raise AssertionError("không được gọi update_state")

    bus = EventBus()
    gate = ReviewGate(timeout=5.0)
    r = JobRunner(graph=ExplodingGraph(), bus=bus, review_gate=gate)
    r.spawn(build_initial_state("t", phases=["api"]))
    time.sleep(0.2)
    gate.resolve([])
    r.join(timeout=3)
    events = _drain(bus)
    kinds = [e.kind for e in events]
    assert "run_error" in kinds
    assert r.is_alive() is False


def test_cancel_sets_flag_and_stops_between_pauses():
    bus = EventBus()
    graph = FakeGraph([_state(**PLANNER_DONE), _state(**GENERATOR_DONE),
                       _state(**REPORTER_DONE)])
    gate = ReviewGate(timeout=5.0)
    r = JobRunner(graph=graph, bus=bus, review_gate=gate)
    r.spawn(build_initial_state("t", phases=["api"]))
    time.sleep(0.2)
    r.cancel()
    assert r.cancelled is True
    gate.resolve([])
    r.join(timeout=3)
    kinds = [e.kind for e in _drain(bus)]
    assert "cancelled" in kinds
    assert "run_done" not in kinds


def test_emits_node_start_and_end():
    bus = EventBus()
    graph = FakeGraph([_state(**PLANNER_DONE)])
    gate = ReviewGate(timeout=0.2)
    r = JobRunner(graph=graph, bus=bus, review_gate=gate)
    r.spawn(build_initial_state("t", phases=["api"]))
    r.join(timeout=3)
    kinds = [e.kind for e in _drain(bus)]
    assert "node_start" in kinds
    assert "plan_ready" in kinds
```

- [ ] **Step 2: Chạy test để xác nhận fail**

```bash
source .venv/bin/activate
pytest tests/test_runner.py -v
```

Expected: FAIL với `ModuleNotFoundError: No module named 'tui.runner'`

- [ ] **Step 3: Viết implementation**

Create `tui/runner.py`:

```python
"""JobRunner — chạy qc_graph trong worker thread, có pause point cho human review."""

from __future__ import annotations

import threading
import uuid
from typing import Any, Optional

from tui.bus import Event, EventBus
from tui.filters import filter_by_phases
from tui.gate import ReviewGate

_NODE_BY_STEP = {
    "planner_done": "planner",
    "planner_failed": "planner",
    "generator_done": "generator",
    "generator_failed": "generator",
    "api_executor_done": "api_executor",
    "api_executor_failed": "api_executor",
    "ui_executor_done": "ui_executor",
    "chaos_executor_done": "chaos_executor",
    "performance_executor_done": "performance_executor",
    "reporter_done": "reporter",
}


def build_initial_state(
    request: str,
    phases: list[str],
    documents: Optional[list[str]] = None,
    code_paths: Optional[list[str]] = None,
    openapi_spec: Optional[str] = None,
    ui_headed: bool = False,
) -> dict[str, Any]:
    return {
        "user_request": request,
        "documents": documents or [],
        "code_paths": code_paths or [],
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
        "phases": phases,
        "job_id": str(uuid.uuid4()),
    }


class JobRunner:
    """Một job = một thread + một thread_id LangGraph."""

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

    @property
    def cancelled(self) -> bool:
        return self._cancelled.is_set()

    def is_alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def cancel(self) -> None:
        self._cancelled.set()
        self.review_gate.release()

    def spawn(self, state: dict[str, Any]) -> None:
        self._thread = threading.Thread(
            target=self._run, args=(state,), daemon=True, name="qc-job"
        )
        self._thread.start()

    def join(self, timeout: Optional[float] = None) -> None:
        if self._thread is not None:
            self._thread.join(timeout=timeout)

    # ----- internals -----

    def _emit(self, kind: str, **payload: Any) -> None:
        self.bus.emit(Event(kind=kind, payload=payload))

    def _run(self, state: dict[str, Any]) -> None:
        config = {
            "configurable": {"thread_id": state.get("job_id") or str(uuid.uuid4())}
        }
        try:
            # --- Pass 1: tới planner_done ---
            stopped = self._stream_until(
                state, config, stop_step="planner_done"
            )
            if stopped:
                return
            if self._cancelled.is_set():
                self._emit("cancelled", phase="planner")
                return

            # --- Pause 1: human review ---
            plan = (self._last_state or {}).get("test_plan") or {}
            cases = list(plan.get("test_cases") or [])
            self._emit("plan_ready", test_plan=plan, count=len(cases))
            kept = self.review_gate.wait()

            if kept is None:
                self._emit("cancelled", phase="review")
                return
            if not kept:
                self._emit("run_error", message="Không có test case nào được chọn")
                return

            self.graph.update_state(
                config,
                {
                    "test_plan": {**plan, "test_cases": kept},
                    "human_approved": True,
                },
            )
            if self._cancelled.is_set():
                self._emit("cancelled", phase="generator")
                return

            # --- Pass 2: tới generator_done ---
            self._last_state = None
            stopped = self._stream_until(
                None, config, stop_step="generator_done"
            )
            if stopped:
                return
            if self._cancelled.is_set():
                self._emit("cancelled", phase="generator")
                return

            # --- Pause 2: lọc theo phases (chốt chặn cuối) ---
            gen = (self._last_state or {}).get("generated_tests") or []
            phases = state.get("phases") or []
            filtered = filter_by_phases(gen, phases)
            if len(filtered) != len(gen):
                self._emit("log", level="info",
                           text=f"Lọc phase {phases}: {len(gen)} → {len(filtered)} test case")
            if not filtered:
                self._emit("run_error",
                           message=f"Không còn test case nào thuộc phase {phases}")
                return
            self.graph.update_state(config, {"generated_tests": filtered})

            # --- Pass 3: chạy tới reporter_done ---
            self._last_state = None
            self._stream_until(None, config, stop_step="reporter_done")

            final = self._last_state or {}
            if final.get("error"):
                self._emit("run_error", message=str(final["error"]))
                return
            self._emit(
                "run_done",
                report_path=final.get("report_path") or "",
                summary=final.get("final_summary") or "",
            )

        except Exception as exc:  # noqa: BLE001 - không được để thread chết âm thầm
            self.review_gate.release()
            self._emit("run_error", message=f"{type(exc).__name__}: {exc}")
        finally:
            self.review_gate.release()

    _last_state: Optional[dict[str, Any]] = None

    def _stream_until(
        self, state: Any, config: dict, stop_step: str
    ) -> bool:
        """Chạy graph cho tới khi gặp stop_step. Trả True nếu phải dừng (lỗi/hủy)."""
        self._emit("node_start", node=stop_step.replace("_done", ""))
        for st in self.graph.stream(state, config, stream_mode="values"):
            if not isinstance(st, dict):
                continue
            self._last_state = st
            step = st.get("current_step") or ""
            node = _NODE_BY_STEP.get(step)
            if node:
                self._emit("node_end", node=node, step=step)
            if st.get("error"):
                self._emit("run_error", message=str(st["error"]), step=step)
                return True
            if step == stop_step:
                return False
        # Generator kết thúc mà không đạt stop_step
        if self._last_state is not None:
            self._emit(
                "run_error",
                message=f"Graph kết thúc trước khi tới '{stop_step}'",
            )
        else:
            self._emit("run_error", message="Graph không trả về state nào")
        return True
```

- [ ] **Step 4: Chạy test để xác nhận pass**

```bash
source .venv/bin/activate
pytest tests/test_runner.py -v
```

Expected: 11 passed

- [ ] **Step 5: Chạy toàn bộ test**

```bash
source .venv/bin/activate
pytest -q
```

Expected: tất cả pass

- [ ] **Step 6: Commit**

```bash
git add tui/runner.py tests/test_runner.py
git commit -m "feat(tui): add JobRunner with two pause points and cooperative cancel

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 8: Emit event từ các executor

Thêm `bus.emit()` vào executor, giữ nguyên `console.print`. Đây là thay đổi duy nhất
chạm `agents/`. Mọi executor vẫn chạy độc lập khi không có bus (CLI).

> **QUAN TRỌNG — tránh duplicate event:**
> `JobRunner` (Task 7) **đã emit** `node_end` cho mọi `current_step` và `run_done` khi
> reporter xong. `agents/emitter.py` **KHÔNG được** emit `node_end` / `node_start` /
> `run_done` / `run_error` — nếu không TUI sẽ nhận mỗi event 2 lần.
>
> `emitter.py` chỉ emit **2 loại**:
> - `test_result` — từ 4 executor (api, ui, chaos, performance)
> - `log` — chỉ khi cần báo điều executor không thể tự log (giữ tối thiểu)

**Files:**
- Create: `agents/emitter.py`
- Modify: `agents/api_executor.py:200-266`
- Modify: `agents/ui_executor.py:413-475`
- Modify: `agents/chaos_executor.py:475-532`
- Modify: `agents/performance_executor.py:354-405`
- **Không sửa**: `agents/planner.py`, `agents/generator.py`, `agents/reporter.py`
- Test: `tests/test_emit.py`

- [ ] **Step 1: Viết test failing**

Create `tests/test_emit.py`:

```python
from __future__ import annotations

from agents import emitter


def test_emit_without_bus_is_noop():
    emitter.set_bus(None)
    emitter.emit_test_result({"id": "X", "status": "passed"})


def test_emit_with_bus_sends_one_event():
    from tui.bus import EventBus
    bus = EventBus()
    emitter.set_bus(bus)
    try:
        emitter.emit_test_result({
            "id": "TC_1", "type": "api", "status": "failed",
            "duration_ms": 12.5, "error_message": "401",
            "title": "Bad login",
        })
        assert bus.queue.qsize() == 1
        ev = bus.queue.get_nowait()
        assert ev.kind == "test_result"
        assert ev.payload["id"] == "TC_1"
        assert ev.payload["status"] == "failed"
        assert ev.payload["error_message"] == "401"
    finally:
        emitter.set_bus(None)


def test_emit_test_result_tolerates_sparse_dict():
    from tui.bus import EventBus
    bus = EventBus()
    emitter.set_bus(bus)
    try:
        emitter.emit_test_result({"id": "X"})
        assert bus.queue.qsize() == 1
        assert bus.queue.get_nowait().payload["type"] == "api"
    finally:
        emitter.set_bus(None)


def test_set_bus_roundtrip():
    from tui.bus import EventBus
    bus = EventBus()
    emitter.set_bus(bus)
    try:
        assert emitter.get_bus() is bus
    finally:
        emitter.set_bus(None)
    assert emitter.get_bus() is None
```

> Lưu ý: `EventBus()` tạo ngoài event loop sẽ có `loop=None` và `emit()` là no-op,
> nên `bus.queue.qsize()` sẽ luôn bằng 0. Test này sẽ **fail**. Hãy dùng
> `RecordingBus` — một stub thread-safe thu thập event vào list — hoặc chạy test
> trong event loop thật với `@pytest.mark.asyncio`.

- [ ] **Step 2: Chạy test để xác nhận fail**

```bash
source .venv/bin/activate
pytest tests/test_emit.py -v
```

Expected: FAIL với `ModuleNotFoundError: No module named 'agents.emitter'`

- [ ] **Step 3: Tạo agents/emitter.py**

Create `agents/emitter.py`:

```python
"""Cầu nối emit event từ executor sang TUI.

Bus là tuỳ chọn: CLI không set bus nên mọi hàm ở đây là no-op.

CHỈ emit `test_result`. `node_end` / `run_done` do JobRunner (tui/runner.py) phát
— emit thêm ở đây sẽ khiến TUI nhận trùng event.
"""

from __future__ import annotations

from typing import Any, Optional

_bus: Optional[Any] = None


def set_bus(bus: Optional[Any]) -> None:
    global _bus
    _bus = bus


def get_bus() -> Optional[Any]:
    return _bus


def emit_test_result(result: dict[str, Any]) -> None:
    if _bus is None:
        return
    from tui.bus import Event
    _bus.emit(Event(
        kind="test_result",
        payload={
            "id": result.get("id"),
            "title": result.get("title"),
            "type": (result.get("type") or "api"),
            "status": result.get("status"),
            "duration_ms": result.get("duration_ms"),
            "error_message": result.get("error_message"),
        },
    ))
```

- [ ] **Step 4: Chạy test để xác nhận pass**

```bash
source .venv/bin/activate
pytest tests/test_emit.py -v
```

Expected: 3 passed

- [ ] **Step 5: Emit `test_result` trong api_executor.py**

Thêm `from agents import emitter` sau `from config.settings import settings`.

Trong vòng lặp for (khoảng dòng 223-224), sửa thành:

```python
        res = _run_single_api_test(test, base_url=base_url)
        res.setdefault("type", test.get("type") or "api")
        emitter.emit_test_result(res)
        details.append(res)
```

Đặt emit **trước** `details.append` để TUI nhận event ngay khi test xong.

- [ ] **Step 6: Emit trong ui_executor.py, chaos_executor.py, performance_executor.py**

Trong mỗi executor, ngay sau dòng `res = _run_single_...(...)`:

```python
        res.setdefault("type", "ui")           # ui_executor
        emitter.emit_test_result(res)
```

```python
        res.setdefault("type", "chaos")        # chaos_executor
        emitter.emit_test_result(res)
```

```python
        res.setdefault("type", "performance")  # performance_executor
        emitter.emit_test_result(res)
```

Thêm `from agents import emitter` vào cả 3 file.

- [ ] **Step 7: KHÔNG sửa planner.py, generator.py, reporter.py**

`JobRunner` đã theo dõi `current_step` qua `stream()` và tự emit `node_end` cho mọi
node, cùng `run_done` khi reporter xong. Thêm emit ở đây sẽ tạo event trùng.
Ba file này **giữ nguyên**.

- [ ] **Step 8: Chạy toàn bộ test**

```bash
source .venv/bin/activate
pytest -q
```

Expected: tất cả pass

- [ ] **Step 9: Xác nhận CLI không hỏng (bus = None → no-op)**

```bash
source .venv/bin/activate
python -c "from agents.graph import qc_graph; print('graph OK')"
python main.py run --help
```

Expected: `graph OK`, và help hiện ra bình thường

- [ ] **Step 10: Commit**

```bash
git add agents/emitter.py agents/api_executor.py agents/ui_executor.py \
        agents/chaos_executor.py agents/performance_executor.py \
        tests/test_emit.py
git commit -m "feat(agents): emit TUI test_result events from executors

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 9: ReviewModal — logic bật/tắt test case

Tách logic toggle ra khỏi widget để test được không cần terminal thật.

**Files:**
- Create: `tui/review.py`
- Test: `tests/test_review.py`

- [ ] **Step 1: Viết test failing**

Create `tests/test_review.py`:

```python
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


def test_toggle_all_off_disables_approve():
    m = ReviewModel(_cases())
    for i in range(3):
        m.toggle(i)
    assert m.enabled == [False, False, False]
    assert m.can_approve is False


def test_toggle_out_of_range_ignored():
    m = ReviewModel(_cases())
    m.toggle(99)
    assert m.enabled == [True, True, True]


def test_select_all_and_none():
    m = ReviewModel(_cases())
    m.toggle(0)
    m.set_all(False)
    assert m.enabled == [False, False, False]
    m.set_all(True)
    assert m.enabled == [True, True, True]


def test_kept_cases_returns_only_enabled():
    m = ReviewModel(_cases())
    m.toggle(1)
    kept = m.kept_cases()
    assert [c["id"] for c in kept] == ["TC_1", "TC_3"]


def test_cursor_moves_within_bounds():
    m = ReviewModel(_cases())
    m.move_cursor(1)
    assert m.cursor == 2
    m.move_cursor(1)
    assert m.cursor == 2, "không vượt quá cuối"
    m.move_cursor(-5)
    assert m.cursor == 0, "không vượt quá đầu"


def test_empty_cases_model():
    m = ReviewModel([])
    assert m.enabled == []
    assert m.can_approve is False
    assert m.kept_cases() == []


def test_rows_for_display():
    m = ReviewModel(_cases())
    rows = m.rows()
    assert len(rows) == 3
    assert rows[0][0] is True          # checked
    assert rows[0][1] == "TC_1"
    assert rows[0][2] == "critical"
    assert rows[0][3] == "api"
```

- [ ] **Step 2: Chạy test để xác nhận fail**

```bash
source .venv/bin/activate
pytest tests/test_review.py -v
```

Expected: FAIL với `ModuleNotFoundError: No module named 'tui.review'`

- [ ] **Step 3: Viết implementation**

Create `tui/review.py`:

```python
"""ReviewModel — trạng thái bật/tắt test case trong modal review.

Tách khỏi widget Textual để test không cần terminal.
"""

from __future__ import annotations

from typing import Any, Optional


class ReviewModel:
    def __init__(self, cases: list[dict[str, Any]]) -> None:
        self.cases: list[dict[str, Any]] = list(cases or [])
        self.enabled: list[bool] = [True] * len(self.cases)
        self.cursor: int = 0

    @property
    def can_approve(self) -> bool:
        return any(self.enabled)

    def toggle(self, index: int) -> None:
        if 0 <= index < len(self.enabled):
            self.enabled[index] = not self.enabled[index]

    def set_all(self, value: bool) -> None:
        self.enabled = [value] * len(self.cases)

    def move_cursor(self, delta: int) -> None:
        if not self.cases:
            return
        self.cursor = max(0, min(len(self.cases) - 1, self.cursor + delta))

    def kept_cases(self) -> list[dict[str, Any]]:
        return [c for c, on in zip(self.cases, self.enabled) if on]

    def rows(self) -> list[tuple[bool, str, str, str, str]]:
        """(checked, id, priority, type, title) cho DataTable."""
        out = []
        for case, on in zip(self.cases, self.enabled):
            out.append((
                on,
                str(case.get("id") or ""),
                str(case.get("priority") or ""),
                str(case.get("type") or "api"),
                str(case.get("title") or ""),
            ))
        return out
```

- [ ] **Step 4: Chạy test để xác nhận pass**

```bash
source .venv/bin/activate
pytest tests/test_review.py -v
```

Expected: 9 passed

- [ ] **Step 5: Commit**

```bash
git add tui/review.py tests/test_review.py
git commit -m "feat(tui): add ReviewModel for test case selection

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 10: Widget LogPane

**Files:**
- Create: `tui/widgets/__init__.py`
- Create: `tui/widgets/logpane.py`
- Test: `tests/test_logpane.py`

- [ ] **Step 1: Viết test failing**

Create `tui/widgets/__init__.py` (file rỗng).

Create `tests/test_logpane.py`:

```python
from __future__ import annotations

import pytest

from tui.widgets.logpane import LogPane, format_log_line

TEXTUAL = pytest.importorskip("textual")


def test_format_info_line():
    assert format_log_line("info", "hello") == "hello"


def test_format_error_prefixes_bang():
    assert format_log_line("error", "boom") == "! boom"


def test_format_warn_prefixes_triangle():
    assert format_log_line("warn", "careful") == "△ careful"


def test_format_unknown_level_plain():
    assert format_log_line("weird", "msg") == "msg"


def test_logpane_accumulates_lines():
    pane = LogPane()
    pane.append_line("first")
    pane.append_line("second")
    assert len(pane.lines) == 2
    assert pane.lines[0] == "first"


def test_logpane_caps_at_max_lines():
    pane = LogPane(max_lines=10)
    for i in range(50):
        pane.append_line(str(i))
    assert len(pane.lines) == 10
    assert pane.lines[-1] == "49"
    assert pane.lines[0] == "40"


def test_logpane_clear():
    pane = LogPane()
    pane.append_line("x")
    pane.clear()
    assert pane.lines == []
```

- [ ] **Step 2: Chạy test để xác nhận fail**

```bash
source .venv/bin/activate
pytest tests/test_logpane.py -v
```

Expected: FAIL với `ModuleNotFoundError: No module named 'tui.widgets.logpane'`

- [ ] **Step 3: Viết implementation**

Create `tui/widgets/logpane.py`:

```python
"""LogPane — hiển thị log realtime."""

from __future__ import annotations

from typing import Any

from rich.text import Text
from textual.app import ComposeResult
from textual.widgets import RichLog, Static

DEFAULT_MAX_LINES = 2000

_LEVEL_STYLE = {
    "error": "bold red",
    "warn": "yellow",
    "warning": "yellow",
    "success": "green",
    "info": "",
}
_LEVEL_MARK = {
    "error": "! ",
    "warn": "△ ",
    "warning": "△ ",
}


def format_log_line(level: str, text: str) -> str:
    return _LEVEL_MARK.get((level or "").lower(), "") + text


class LogPane(Static):
    """Bọc RichLog, giữ thêm list text thuần để test."""

    def __init__(self, max_lines: int = DEFAULT_MAX_LINES, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.max_lines = max_lines
        self.lines: list[str] = []
        self._log: RichLog | None = None

    def compose(self) -> ComposeResult:
        self._log = RichLog(highlight=False, markup=False, wrap=True)
        yield self._log

    def on_mount(self) -> None:
        self._log = self.query_one(RichLog)

    def append_line(self, text: str) -> None:
        self.lines.append(text)
        if len(self.lines) > self.max_lines:
            del self.lines[: len(self.lines) - self.max_lines]
        if self._log is not None:
            self._log.write(text)

    def append_event(self, level: str, text: str) -> None:
        style = _LEVEL_STYLE.get((level or "").lower(), "")
        self.append_line(format_log_line(level, text))

    def clear(self) -> None:
        self.lines = []
        if self._log is not None:
            self._log.clear()

    def load_lines(self, lines: list[str]) -> None:
        self.clear()
        for line in lines[-self.max_lines:]:
            self.append_line(line)
```

- [ ] **Step 4: Chạy test để xác nhận pass**

```bash
source .venv/bin/activate
pytest tests/test_logpane.py -v
```

Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add tui/widgets/__init__.py tui/widgets/logpane.py tests/test_logpane.py
git commit -m "feat(tui): add LogPane widget

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 11: Widget CaseTable

**Files:**
- Create: `tui/widgets/casestable.py`
- Test: `tests/test_casestable.py`

- [ ] **Step 1: Viết test failing**

Create `tests/test_casestable.py`:

```python
from __future__ import annotations

import pytest

from tui.widgets.casestable import CaseTable, status_style

TEXTUAL = pytest.importorskip("textual")


def test_status_style_passed():
    assert status_style("passed") == "green"


def test_status_style_failed():
    assert status_style("failed") == "red"


def test_status_style_error_orange():
    assert status_style("error") == "red"


def test_status_style_blocked_yellow():
    assert status_style("blocked") == "yellow"


def test_status_style_unknown_dim():
    assert status_style("whatever") == "dim"


def test_status_mark():
    t = CaseTable()
    assert t.status_mark("passed") == "✓"
    assert t.status_mark("failed") == "✗"
    assert t.status_mark("error") == "!"
    assert t.status_mark("blocked") == "□"
    assert t.status_mark("skipped") == "–"
    assert t.status_mark("running") == "⋯"
    assert t.status_mark(None) == "·"


def test_add_rows_and_filter_by_phase():
    t = CaseTable()
    t.add_result({"id": "A", "type": "api", "status": "passed",
                  "duration_ms": 10, "title": "one"})
    t.add_result({"id": "B", "type": "ui", "status": "failed",
                  "duration_ms": 20, "error_message": "x", "title": "two"})
    t.set_phase_filter(["api"])
    assert t.visible_ids() == ["A"]

    t.set_phase_filter([])
    assert sorted(t.visible_ids()) == ["A", "B"]


def test_add_result_sparse_dict():
    t = CaseTable()
    t.add_result({"id": "Z"})
    assert t.visible_ids() == ["Z"]


def test_clear_rows():
    t = CaseTable()
    t.add_result({"id": "A", "type": "api", "status": "passed"})
    t.clear_rows()
    assert t.visible_ids() == []


def test_update_existing_row_does_not_duplicate():
    t = CaseTable()
    t.add_result({"id": "A", "type": "api", "status": "running"})
    t.add_result({"id": "A", "type": "api", "status": "passed",
                  "duration_ms": 5})
    assert t.visible_ids() == ["A"]
    assert t.rows[0]["status"] == "passed"
```

- [ ] **Step 2: Chạy test để xác nhận fail**

```bash
source .venv/bin/activate
pytest tests/test_casestable.py -v
```

Expected: FAIL với `ModuleNotFoundError: No module named 'tui.widgets.casestable'`

- [ ] **Step 3: Viết implementation**

Create `tui/widgets/casestable.py`:

```python
"""CaseTable — bảng test case, filter theo phase."""

from __future__ import annotations

from typing import Any, Optional

from textual.widgets import DataTable

_STATUS_STYLE = {
    "passed": "green",
    "failed": "red",
    "error": "red",
    "blocked": "yellow",
    "skipped": "dim",
    "running": "cyan",
}
_STATUS_MARK = {
    "passed": "✓",
    "failed": "✗",
    "error": "!",
    "blocked": "□",
    "skipped": "–",
    "running": "⋯",
}

COLUMNS = ("ID", "Type", "Status", "ms", "Title")


def status_style(status: Optional[str]) -> str:
    return _STATUS_STYLE.get((status or "").lower(), "dim")


class CaseTable(DataTable):
    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.rows: list[dict[str, Any]] = []
        self._phase_filter: list[str] = []

    def on_mount(self) -> None:
        for name in COLUMNS:
            self.add_column(name, key=name)

    def status_mark(self, status: Optional[str]) -> str:
        return _STATUS_MARK.get((status or "").lower(), "·")

    def set_phase_filter(self, phases: list[str]) -> None:
        self._phase_filter = list(phases or [])

    def visible_ids(self) -> list[str]:
        if not self._phase_filter:
            return [str(r.get("id")) for r in self.rows]
        allowed = set(self._phase_filter)
        return [
            str(r.get("id")) for r in self.rows
            if (r.get("type") or "api").lower() in allowed
        ]

    def add_result(self, result: dict[str, Any]) -> None:
        rid = str(result.get("id") or "?")
        for i, r in enumerate(self.rows):
            if str(r.get("id")) == rid:
                self.rows[i] = {**self.rows[i], **result}
                break
        else:
            self.rows.append(dict(result))
        self._render()

    def clear_rows(self) -> None:
        self.rows = []
        self._render()

    def _render(self) -> None:
        self.clear()
        visible = set(self.visible_ids())
        for r in self.rows:
            if str(r.get("id")) not in visible:
                continue
            status = r.get("status")
            ms = r.get("duration_ms")
            self.add_row(
                str(r.get("id") or ""),
                str(r.get("type") or "api"),
                f"{self.status_mark(status)} {status or ''}".strip(),
                f"{ms:.0f}" if isinstance(ms, (int, float)) else "",
                str(r.get("title") or r.get("error_message") or ""),
                key=str(r.get("id")),
            )
```

- [ ] **Step 4: Chạy test để xác nhận pass**

```bash
source .venv/bin/activate
pytest tests/test_casestable.py -v
```

Expected: 11 passed

- [ ] **Step 5: Commit**

```bash
git add tui/widgets/casestable.py tests/test_casestable.py
git commit -m "feat(tui): add CaseTable widget with phase filter

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 12: Widget HistoryPane

**Files:**
- Create: `tui/widgets/history.py`
- Test: `tests/test_history_pane.py`

- [ ] **Step 1: Viết test failing**

Create `tests/test_history_pane.py`:

```python
from __future__ import annotations

import pytest

from tui.history_reader import RunSummary
from tui.widgets.history import HistoryPane, format_run_line

TEXTUAL = pytest.importorskip("textual")


def _run(request, total, passed, failed, ts="20260101_000000"):
    return RunSummary(
        path=f"/r/report_{ts}.json", timestamp=ts, request=request,
        plan_title="P", scope="api", total=total, passed=passed,
        failed=failed, error=0, skipped=0, duration_ms=10.0, details=[],
    )


def test_format_run_line_shows_counts():
    line = format_run_line(_run("Test auth", 12, 10, 2))
    assert "12" in line
    assert "10" in line
    assert "2" in line


def test_format_run_line_marks_all_pass():
    line = format_run_line(_run("x", 5, 5, 0))
    assert "✓" in line


def test_format_run_line_marks_failures():
    line = format_run_line(_run("x", 5, 3, 2))
    assert "✗" in line


def test_history_pane_loads_runs():
    pane = HistoryPane()
    pane.set_runs([_run("a", 1, 1, 0), _run("b", 2, 1, 1)])
    assert len(pane.runs) == 2


def test_history_pane_empty_state():
    pane = HistoryPane()
    pane.set_runs([])
    assert pane.runs == []
    assert pane.empty_label == "Chưa có lịch sử"


def test_history_pane_broken_notice():
    pane = HistoryPane()
    pane.set_broken(2)
    assert pane.broken == 2


def test_history_pane_select():
    pane = HistoryPane()
    runs = [_run("a", 1, 1, 0), _run("b", 2, 1, 1)]
    pane.set_runs(runs)
    pane.select(1)
    assert pane.selected is runs[1]
```

- [ ] **Step 2: Chạy test để xác nhận fail**

```bash
source .venv/bin/activate
pytest tests/test_history_pane.py -v
```

Expected: FAIL với `ModuleNotFoundError: No module named 'tui.widgets.history'`

- [ ] **Step 3: Viết implementation**

Create `tui/widgets/history.py`:

```python
"""HistoryPane — sidebar lịch sử run."""

from __future__ import annotations

from typing import Any, Optional

from textual.widgets import Label, ListItem, ListView

from tui.history_reader import RunSummary


def format_run_line(run: RunSummary) -> str:
    mark = "✓" if run.failed == 0 and run.error == 0 and run.total > 0 else "✗"
    return (
        f"{mark} {run.request[:28] or '(no request)'}\n"
        f"   {run.total} tests · {run.passed} pass · {run.failed} fail"
    )


class HistoryPane(ListView):
    empty_label = "Chưa có lịch sử"

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.runs: list[RunSummary] = []
        self.broken: int = 0
        self._index_to_run: dict[int, RunSummary] = {}

    def set_runs(self, runs: list[RunSummary]) -> None:
        self.runs = list(runs or [])
        self._index_to_run.clear()
        self.clear()
        if not self.runs:
            self.append(ListItem(Label(self.empty_label)))
            return
        for run in self.runs:
            item = ListItem(Label(format_run_line(run)))
            self.append(item)
            self._index_to_run[len(self._index_to_run)] = run

    def set_broken(self, n: int) -> None:
        self.broken = int(n or 0)

    @property
    def selected(self) -> Optional[RunSummary]:
        idx = self.index
        if idx is None or idx < 0:
            return None
        return self._index_to_run.get(idx)

    def select(self, index: int) -> Optional[RunSummary]:
        if 0 <= index < len(self.runs):
            self.index = index
        return self.selected
```

- [ ] **Step 4: Chạy test để xác nhận pass**

```bash
source .venv/bin/activate
pytest tests/test_history_pane.py -v
```

Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add tui/widgets/history.py tests/test_history_pane.py
git commit -m "feat(tui): add HistoryPane widget reading reports directory

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 13: Widget StatusFooter

Input request, chọn phase, nút Run/Stop, progress.

**Files:**
- Create: `tui/widgets/footer.py`
- Test: `tests/test_footer.py`

- [ ] **Step 1: Viết test failing**

Create `tests/test_footer.py`:

```python
from __future__ import annotations

import pytest

from tui.widgets.footer import PHASE_LABELS, StatusFooter, format_elapsed

TEXTUAL = pytest.importorskip("textual")


def test_phase_labels_cover_four_phases():
    assert set(PHASE_LABELS) == {"api", "ui", "chaos", "performance"}


def test_selected_phases_default_api():
    f = StatusFooter()
    assert f.selected_phases() == ["api"]


def test_toggle_phase_adds_and_removes():
    f = StatusFooter()
    f.toggle_phase("ui")
    assert f.selected_phases() == ["api", "ui"]
    f.toggle_phase("api")
    assert f.selected_phases() == ["ui"]


def test_toggle_ignores_unknown_phase():
    f = StatusFooter()
    f.toggle_phase("banana")
    assert f.selected_phases() == ["api"]


def test_toggle_all_off_then_on():
    f = StatusFooter()
    f.set_all_phases(False)
    assert f.selected_phases() == []
    f.set_all_phases(True)
    assert len(f.selected_phases()) == 4


def test_request_set_and_get():
    f = StatusFooter()
    f.set_request("Test auth API")
    assert f.request() == "Test auth API"


def test_format_elapsed_seconds():
    assert format_elapsed(0) == "0:00"
    assert format_elapsed(9) == "0:09"
    assert format_elapsed(65) == "1:05"
    assert format_elapsed(3661) == "1:01:01"


def test_progress_text():
    f = StatusFooter()
    f.set_progress("api", 6, 12)
    assert "6/12" in f.progress_text()


def test_progress_text_idle():
    f = StatusFooter()
    assert f.progress_text() == ""


def test_status_message():
    f = StatusFooter()
    f.set_status("Planner đang chạy…")
    assert f.status() == "Planner đang chạy…"
```

- [ ] **Step 2: Chạy test để xác nhận fail**

```bash
source .venv/bin/activate
pytest tests/test_footer.py -v
```

Expected: FAIL với `ModuleNotFoundError: No module named 'tui.widgets.footer'`

- [ ] **Step 3: Viết implementation**

Create `tui/widgets/footer.py`:

```python
"""StatusFooter — input request, chọn phase, Run/Stop, progress."""

from __future__ import annotations

from typing import Any, Optional

from textual.containers import Horizontal
from textual.widgets import Button, CheckboxSet, Input, Label, Static

PHASE_LABELS = {
    "api": "API",
    "ui": "UI",
    "chaos": "Chaos",
    "performance": "Perf",
}


def format_elapsed(seconds: int) -> str:
    seconds = max(0, int(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


class StatusFooter(Horizontal):
    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._selected: set[str] = {"api"}
        self._request = ""
        self._status = ""
        self._phase = ""
        self._done = 0
        self._total = 0
        self._phases_box: CheckboxSet | None = None
        self._input: Input | None = None
        self._status_widget: Static | None = None

    def compose(self):
        self._input = Input(placeholder="Mô tả nhiệm vụ test…", id="request")
        self._phases_box = CheckboxSet(
            *[(label, key) for key, label in PHASE_LABELS.items()],
            value={"api"},
            id="phases",
        )
        self._status_widget = Static("", id="status")
        yield self._input
        yield self._phases_box
        yield Button("▶ Run", id="run", variant="primary")
        yield Button("■ Stop", id="stop", variant="error", disabled=True)
        yield self._status_widget

    def on_mount(self) -> None:
        self._input = self.query_one("#request", Input)
        self._phases_box = self.query_one("#phases", CheckboxSet)
        self._status_widget = self.query_one("#status", Static)

    # ----- state (test được không cần terminal) -----

    def selected_phases(self) -> list[str]:
        return [p for p in PHASE_LABELS if p in self._selected]

    def toggle_phase(self, phase: str) -> None:
        if phase not in PHASE_LABELS:
            return
        if phase in self._selected:
            self._selected.discard(phase)
        else:
            self._selected.add(phase)

    def set_all_phases(self, value: bool) -> None:
        self._selected = set(PHASE_LABELS) if value else set()
        if self._phases_box is not None:
            self._phases_box.value = set(PHASE_LABELS) if value else set()

    def request(self) -> str:
        return self._request

    def set_request(self, text: str) -> None:
        self._request = text or ""
        if self._input is not None:
            self._input.value = self._request

    def status(self) -> str:
        return self._status

    def set_status(self, text: str) -> None:
        self._status = text or ""
        if self._status_widget is not None:
            self._status_widget.update(self._status)

    def set_progress(self, phase: str, done: int, total: int) -> None:
        self._phase = phase
        self._done = done
        self._total = total

    def progress_text(self) -> str:
        if not self._total:
            return ""
        return f"{self._phase} {self._done}/{self._total}"

    def set_running(self, running: bool) -> None:
        try:
            self.query_one("#run", Button).disabled = running
            self.query_one("#stop", Button).disabled = not running
        except Exception:
            pass
```

- [ ] **Step 4: Chạy test để xác nhận pass**

```bash
source .venv/bin/activate
pytest tests/test_footer.py -v
```

Expected: 10 passed

- [ ] **Step 5: Commit**

```bash
git add tui/widgets/footer.py tests/test_footer.py
git commit -m "feat(tui): add StatusFooter with request input and phase selection

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 14: ReviewModalScreen

Bọc `ReviewModel` vào `ModalScreen` của Textual.

**Files:**
- Create: `tui/widgets/review_modal.py`
- Test: `tests/test_review_modal.py`

- [ ] **Step 1: Viết test failing**

Create `tests/test_review_modal.py`:

```python
from __future__ import annotations

import pytest

pytest.importorskip("textual")

from tui.review import ReviewModel
from tui.widgets.review_modal import ReviewModalScreen


def test_modal_exposes_model():
    cases = [{"id": "A", "type": "api", "priority": "critical", "title": "one"}]
    m = ReviewModel(cases)
    screen = ReviewModalScreen(m)
    assert screen.model is m


def test_modal_summary_text():
    plan = {
        "title": "Auth Plan",
        "test_cases": [{"id": "A"}, {"id": "B"}],
        "risks": ["r1", "r2"],
        "estimated_duration_min": 20,
    }
    screen = ReviewModalScreen(ReviewModel(plan["test_cases"]), plan)
    text = screen.summary_text()
    assert "Auth Plan" in text
    assert "2 cases" in text
    assert "20 min" in text
    assert "2 risks" in text


def test_modal_summary_without_optional_fields():
    plan = {"title": "P", "test_cases": [{"id": "A"}]}
    screen = ReviewModalScreen(ReviewModel(plan["test_cases"]), plan)
    text = screen.summary_text()
    assert "P" in text
    assert "risks" not in text


def test_modal_approve_passes_kept_cases():
    cases = [{"id": "A", "type": "api"}, {"id": "B", "type": "ui"}]
    m = ReviewModel(cases)
    m.toggle(1)
    screen = ReviewModalScreen(m)
    assert screen.approve() == [{"id": "A", "type": "api"}]


def test_modal_reject_returns_none():
    screen = ReviewModalScreen(ReviewModel([{"id": "A"}]))
    assert screen.reject() is None
```

- [ ] **Step 2: Chạy test để xác nhận fail**

```bash
source .venv/bin/activate
pytest tests/test_review_modal.py -v
```

Expected: FAIL với `ModuleNotFoundError: No module named 'tui.widgets.review_modal'`

- [ ] **Step 3: Viết implementation**

Create `tui/widgets/review_modal.py`:

```python
"""ReviewModalScreen — modal duyệt Test Plan."""

from __future__ import annotations

from typing import Any, Optional

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, Footer, Label, Static

from tui.review import ReviewModel


class ReviewModalScreen(ModalScreen):
    BINDINGS = [
        ("space", "toggle", "Bật/tắt"),
        ("a", "all", "Bật tất cả"),
        ("n", "none", "Bỏ tất cả"),
    ]

    def __init__(self, model: ReviewModel, plan: Optional[dict[str, Any]] = None) -> None:
        super().__init__()
        self.model = model
        self.plan = plan or {}
        self._table: DataTable | None = None

    def summary_text(self) -> str:
        parts = [str(self.plan.get("title") or "Test Plan")]
        n = len(self.model.cases)
        parts.append(f"{n} cases")
        est = self.plan.get("estimated_duration_min")
        if est:
            parts.append(f"est. {est} min")
        risks = self.plan.get("risks") or []
        if risks:
            parts.append(f"{len(risks)} risks")
        return " · ".join(parts)

    def approve(self) -> Optional[list[dict[str, Any]]]:
        if not self.model.can_approve:
            return None
        return self.model.kept_cases()

    def reject(self) -> None:
        return None

    def compose(self) -> ComposeResult:
        with Vertical(id="review-dialog"):
            yield Label(f"Review: {self.summary_text()}")
            self._table = DataTable(id="review-table")
            yield self._table
            yield Label(
                "↑↓ chọn · space bật/tắt · a tất cả · n bỏ tất cả",
                classes="hint",
            )
            with Horizontal(id="review-buttons"):
                yield Button("✗ Reject", id="reject", variant="error")
                yield Button("✓ Approve & Run", id="approve", variant="success")
        yield Footer()

    def on_mount(self) -> None:
        self._table = self.query_one("#review-table", DataTable)
        for name in ("On", "ID", "Priority", "Type", "Title"):
            self._table.add_column(name)
        self._render()

    def _render(self) -> None:
        if self._table is None:
            return
        self._table.clear()
        for checked, cid, prio, ctype, title in self.model.rows():
            self._table.add_row(
                "[x]" if checked else "[ ]", cid, prio, ctype, title
            )
        self._sync_approve_button()

    def _sync_approve_button(self) -> None:
        try:
            btn = self.query_one("#approve", Button)
            btn.disabled = not self.model.can_approve
        except Exception:
            pass

    def action_toggle(self) -> None:
        self.model.toggle(self.model.cursor)
        self.model.move_cursor(1)
        self._render()

    def action_all(self) -> None:
        self.model.set_all(True)
        self._render()

    def action_none(self) -> None:
        self.model.set_all(False)
        self._render()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "approve":
            self.dismiss(self.approve())
        elif event.button.id == "reject":
            self.dismiss(None)

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        idx = event.cursor_row
        if 0 <= idx < len(self.model.cases):
            self.model.cursor = idx
            self.model.toggle(idx)
            self.model.move_cursor(1)
            self._render()

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        if event.cursor_row is not None and 0 <= event.cursor_row < len(self.model.cases):
            self.model.cursor = event.cursor_row
```

- [ ] **Step 4: Chạy test để xác nhận pass**

```bash
source .venv/bin/activate
pytest tests/test_review_modal.py -v
```

Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add tui/widgets/review_modal.py tests/test_review_modal.py
git commit -m "feat(tui): add ReviewModalScreen wrapping ReviewModel

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 15: QCTApp — layout + điều phối event

Ghép tất cả. Đây là task cuối cùng của TUI.

**Files:**
- Create: `tui/app.py`
- Test: `tests/test_app.py`

- [ ] **Step 1: Viết test failing**

Create `tests/test_app.py`:

```python
from __future__ import annotations

import pytest

pytest.importorskip("textual")

from tui.app import QCTApp


def test_app_css_defines_layout():
    app = QCTApp()
    assert "#history" in app.CSS
    assert "#log" in app.CSS
    assert "#cases" in app.CSS


def test_handle_plan_ready_stores_plan():
    app = QCTApp()
    app._handle_event({"kind": "plan_ready", "payload": {
        "test_plan": {"title": "P", "test_cases": [{"id": "A"}]},
        "count": 1,
    }})
    assert app.pending_plan is not None
    assert app.pending_plan["title"] == "P"


def test_handle_test_result_accumulates():
    app = QCTApp()
    app._handle_event({"kind": "test_result", "payload": {
        "id": "A", "type": "api", "status": "passed", "duration_ms": 1,
        "error_message": None, "title": "one",
    }})
    assert app.received_results == 1


def test_handle_run_error_sets_message():
    app = QCTApp()
    app._handle_event({"kind": "run_error", "payload": {"message": "bad"}})
    assert "bad" in app.last_error


def test_handle_cancelled_sets_message():
    app = QCTApp()
    app._handle_event({"kind": "cancelled", "payload": {"phase": "review"}})
    assert "Huỷ" in app.last_error or "hủy" in app.last_error


def test_handle_run_done_clears_error():
    app = QCTApp()
    app.last_error = "old"
    app._handle_event({"kind": "run_done", "payload": {
        "report_path": "/r/x.json", "summary": "done",
    }})
    assert app.last_error == ""


def test_start_run_requires_request():
    app = QCTApp()
    assert app.start_run() is False
    assert "request" in app.last_error.lower()


def test_start_run_sets_running_without_touching_graph():
    """start_run chỉ set state; thread chưa spawn nên không gọi LLM thật."""
    app = QCTApp()
    app.footer.set_request("Test auth")
    app.start_run()
    assert app.running is True
    assert app.last_error == ""
    # Dọn dẹp: runner chưa spawn nên cancel là no-op an toàn
    app.stop_run()


def test_stop_run_cancels():
    app = QCTApp()
    app.footer.set_request("Test auth")
    app.start_run()
    app.stop_run()
    assert app.running is False
```

- [ ] **Step 2: Chạy test để xác nhận fail**

```bash
source .venv/bin/activate
pytest tests/test_app.py -v
```

Expected: FAIL với `ModuleNotFoundError: No module named 'tui.app'`

- [ ] **Step 3: Viết implementation**

Create `tui/app.py`:

```python
"""QCTApp — dashboard TUI cho QC Agent."""

from __future__ import annotations

import time
from typing import Any, Optional

from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Footer as KeyFooter, Header, Label

from agents import emitter
from agents.graph import qc_graph
from config.settings import settings
from tui.bus import get_bus, silence_agent_console
from tui.gate import ReviewGate
from tui.history_reader import load_history
from tui.runner import JobRunner, build_initial_state
from tui.widgets.casestable import CaseTable
from tui.widgets.footer import StatusFooter, format_elapsed
from tui.widgets.history import HistoryPane
from tui.widgets.logpane import LogPane
from tui.widgets.review_modal import ReviewModalScreen
from tui.review import ReviewModel

PHASE_KEYS = {"1": "api", "2": "ui", "3": "chaos", "4": "performance"}


class QCTApp(App):
    BINDINGS = [
        ("r", "run", "Chạy"),
        ("c", "stop", "Dừng"),
        ("f", "focus_request", "Ô request"),
        ("question_mark", "help", "Phím tắt"),
        ("q", "quit", "Thoát"),
    ]

    CSS = """
    #body { height: 1fr; }
    #history { width: 28; border-right: solid $panel; }
    #cases { width: 46; border-left: solid $panel; }
    #log { height: 1fr; border: round $panel; }
    #footer-bar { height: auto; border-top: solid $panel; }
    #status { width: 1fr; padding: 0 1; }
    #review-dialog { width: 90%; height: 80%; border: thick $accent; padding: 1 2; }
    #review-table { height: 1fr; }
    #review-buttons { height: auto; align: right middle; }
    .hint { color: $text-muted; }
    """

    def __init__(self) -> None:
        super().__init__()
        self.bus = get_bus()
        self.runner: Optional[JobRunner] = None
        self.review_gate: Optional[ReviewGate] = None
        self.pending_plan: Optional[dict[str, Any]] = None
        self.last_error: str = ""
        self.received_results = 0
        self.running = False
        self._started_at = 0.0
        self._done_count = 0
        self._total_count = 0
        self._current_phase = ""
        self._ticker: Any = None
        self.pending_state: Optional[dict[str, Any]] = None
        self.logpane: Optional[LogPane] = None
        self.casetable: Optional[CaseTable] = None
        self.history: Optional[HistoryPane] = None
        # Khởi tạo sẵn widget con để test được ngoài event loop;
        # compose() sẽ gán lại bản thật khi app mount.
        self.footer = StatusFooter()
        self.logpane = LogPane()
        self.casetable = CaseTable()
        self.history = HistoryPane()

    # ----- layout -----

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="body"):
            self.history = HistoryPane(id="history")
            yield self.history
            self.logpane = LogPane(id="log")
            yield self.logpane
            self.casetable = CaseTable(id="cases")
            yield self.casetable
        self.footer = StatusFooter(id="footer-bar")
        yield self.footer
        yield KeyFooter()

    def on_mount(self) -> None:
        silence_agent_console()
        self.bus.bind(self.get_loop())
        self.refresh_history()
        self.set_interval(1.0, self._tick)
        self.set_interval(0.15, self._drain_bus)

    def on_button_pressed(self, event: Any) -> None:
        if getattr(event.button, "id", None) == "run":
            if self.start_run():
                self.spawn_job()

    # ----- event loop glue -----

    def _drain_bus(self) -> None:
        while not self.bus.queue.empty():
            self._handle_event(self.bus.queue.get_nowait())

    def _tick(self) -> None:
        if not self.running:
            return
        if self.footer is None:
            return
        elapsed = int(time.time() - self._started_at)
        self.footer.set_status(
            f"{self._current_phase} · elapsed {format_elapsed(elapsed)}"
        )

    def _handle_event(self, ev: dict[str, Any]) -> None:
        kind = ev.get("kind")
        payload = ev.get("payload") or {}

        if kind == "log":
            if self.logpane:
                self.logpane.append_event(
                    payload.get("level", "info"), payload.get("text", "")
                )
        elif kind == "node_start":
            self._current_phase = payload.get("node", "")
            if self.footer:
                self.footer.set_status(f"▶ {self._current_phase}…")
            if self.logpane:
                self.logpane.append_line(f"▶ {payload.get('node', '')}")
        elif kind == "node_end":
            self._current_phase = payload.get("node", "")
            if self.logpane:
                self.logpane.append_line(
                    f"  ✓ {payload.get('step', '')}"
                )
        elif kind == "plan_ready":
            self.pending_plan = payload.get("test_plan") or {}
            self._open_review(self.pending_plan)
        elif kind == "test_result":
            self.received_results += 1
            self._done_count += 1
            if self.casetable:
                self.casetable.add_result(payload)
            if self.footer:
                self.footer.set_progress(
                    self._current_phase, self._done_count, self._total_count
                )
            if self.logpane:
                mark = self.casetable.status_mark(payload.get("status")) if self.casetable else "·"
                self.logpane.append_line(
                    f"  {mark} {payload.get('id')} → {payload.get('status')}"
                )
        elif kind == "run_done":
            self.running = False
            self.last_error = ""
            if self.footer:
                self.footer.set_running(False)
                self.footer.set_status("✓ Xong")
            if self.logpane:
                self.logpane.append_line(f"✓ {payload.get('summary', '')}")
            self.refresh_history()
        elif kind == "run_error":
            self.running = False
            self.last_error = str(payload.get("message", ""))
            if self.footer:
                self.footer.set_running(False)
                self.footer.set_status(f"✗ {self.last_error}")
        elif kind == "cancelled":
            self.running = False
            if self.footer:
                self.footer.set_running(False)
                self.footer.set_status("✗ Đã huỷ")
            if self.review_gate:
                self.review_gate.resolve(None)

    # ----- actions -----

    def start_run(self) -> bool:
        request = self.footer.request().strip() if self.footer else ""
        if not request:
            self.last_error = "Cần nhập request trước khi chạy"
            return False

        self.last_error = ""
        self._done_count = 0
        self._total_count = 0
        self._started_at = time.time()
        if self.casetable:
            self.casetable.clear_rows()
        if self.logpane:
            self.logpane.clear()
        if self.footer:
            self.footer.set_running(True)
            self.footer.set_status("▶ Đang khởi tạo…")
            if self.casetable:
                self.casetable.set_phase_filter(self.footer.selected_phases())
                self._total_count = 0

        self.review_gate = ReviewGate()
        self.runner = JobRunner(
            graph=qc_graph, bus=self.bus, review_gate=self.review_gate
        )
        emitter.set_bus(self.bus)
        state = build_initial_state(
            request=request,
            phases=self.footer.selected_phases() if self.footer else ["api"],
            ui_headed=False,
        )
        self.pending_state = state
        self.running = True
        return True

    def spawn_job(self) -> None:
        """Tách khỏi start_run để test gọi được mà không chạy thread thật."""
        if self.runner is None or self.pending_state is None:
            return
        self.runner.spawn(self.pending_state)
        self.pending_state = None

    def stop_run(self) -> None:
        if self.runner and self.running:
            self.runner.cancel()
            if self.footer:
                self.footer.set_status("Đang huỷ…")
        self.running = False
        if self.footer:
            self.footer.set_running(False)

    def action_run(self) -> None:
        if self.start_run():
            self.spawn_job()

    def action_stop(self) -> None:
        self.stop_run()

    def action_focus_request(self) -> None:
        try:
            self.query_one("#request").focus()
        except Exception:
            pass

    def action_help(self) -> None:
        self.notify(
            "r chạy · c dừng · 1-4 bật/tắt phase · f focus request · q thoát",
            title="Phím tắt",
        )

    def _open_review(self, plan: dict[str, Any]) -> None:
        cases = list(plan.get("test_cases") or [])
        model = ReviewModel(cases)
        self._total_count = len(cases)
        if self.footer:
            self.footer.set_status("Chờ duyệt Test Plan…")

        def done(kept: Optional[list[dict[str, Any]]]) -> None:
            if self.review_gate:
                self.review_gate.resolve(kept)

        self.push_screen(ReviewModalScreen(model, plan), done)

    def refresh_history(self) -> None:
        if self.history is None:
            return
        runs, broken = load_history(settings.reports_dir)
        self.history.set_runs(runs)
        self.history.set_broken(broken)


def main() -> None:
    QCTApp().run()


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Chạy test để xác nhận pass**

```bash
source .venv/bin/activate
pytest tests/test_app.py -v
```

Expected: 8 passed

- [ ] **Step 5: Chạy toàn bộ test**

```bash
source .venv/bin/activate
pytest -q
```

Expected: tất cả pass

- [ ] **Step 6: Commit**

```bash
git add tui/app.py tests/test_app.py
git commit -m "feat(tui): add QCTApp dashboard

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 16: Nối CLI + kiểm tra key bindings

**Files:**
- Modify: `main.py:21-46` (thêm command `tui`)

- [ ] **Step 1: Thêm command `tui` vào main.py**

Sửa `main.py`, thêm import ở đầu file sau `from config.settings import settings`:

```python
from tui.bus import reset_bus
from tui.runner import build_initial_state  # noqa: F401  (dùng bởi tui app)
```

Thêm command mới trước `@app.command()\ndef version():`:

```python
@app.command()
def tui():
    """Mở giao diện TUI dashboard."""
    from agents import emitter
    from config.settings import settings as s
    from tui.app import QCTApp

    if not s.openai_api_key:
        console.print(
            "[bold red]Thiếu OPENAI_API_KEY.[/bold red]\n"
            "Tạo file .env ở thư mục gốc và điền:\n"
            "  OPENAI_API_KEY=sk-..."
        )
        raise typer.Exit(1)

    reset_bus()
    emitter.set_bus(None)
    QCTApp().run()
```

- [ ] **Step 2: Xác nhận cả 2 lệnh CLI hoạt động**

```bash
source .venv/bin/activate
python main.py --help
python main.py tui --help
python main.py run --help
```

Expected: cả 3 lệnh in help, có `tui` trong danh sách commands

- [ ] **Step 3: Xác nhận `tui` chặn khi thiếu API key**

Tạm đổi tên `.env` để key không được nạp:

```bash
source .venv/bin/activate
mv .env .env.bak
python main.py tui
```

Expected: in `Thiếu OPENAI_API_KEY` + hướng dẫn, thoát với exit code 1, **không** mở TUI

Khôi phục lại:

```bash
mv .env.bak .env
```

- [ ] **Step 4: Chạy toàn bộ test lần cuối**

```bash
source .venv/bin/activate
pytest -q
```

Expected: tất cả pass

- [ ] **Step 5: Smoke test TUI thật**

```bash
source .venv/bin/activate
python main.py tui
```

Kiểm tra trong TUI:
1. Header hiện, sidebar History hiện (hoặc "Chưa có lịch sử")
2. Cột Logs và Test Cases hiện
3. Ô Request nhận được text
4. Bấm `1`–`4` toggle được phase
5. Nhấn `q` thoát được

Expected: mọi bước trên hoạt động, không crash

- [ ] **Step 6: Smoke test chạy job thật (cần backend + OPENAI_API_KEY)**

```bash
source .venv/bin/activate
python main.py tui
```

Nhập request (ví dụ "Test API login flow"), nhấn `r` hoặc bấm nút `▶ Run`
(`start_run()` chuẩn bị state, `spawn_job()` mới spawn thread):
1. Log stream hiện `▶ planner`
2. Modal Review mở ra với danh sách test case
3. Bỏ tick 1 case, bấm Approve & Run
4. Log hiện từng test case, bảng Test Cases cập nhật dần
5. Khi xong, header đổi sang "✓ Xong", sidebar có thêm run mới

Expected: toàn bộ flow chạy được

- [ ] **Step 7: Xác nhận CLI cũ không hỏng**

```bash
source .venv/bin/activate
python main.py run "Test API login flow" --ci 2>&1 | tail -20
```

Expected: vẫn chạy như trước, có bảng kết quả (cần backend đang chạy + API key hợp lệ)

- [ ] **Step 8: Commit**

```bash
git add main.py
git commit -m "feat: add 'main.py tui' command

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 17: Cập nhật README

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Thêm mục TUI vào README**

Sửa `README.md`, ngay sau phần `## Cách dùng` và trước `### Flow khi chạy`, chèn:

````markdown
### 🖥️ TUI Dashboard (Textual)

```bash
python main.py tui
```

Mở giao diện dashboard:
- **Sidebar** — lịch sử các lần chạy (đọc từ `reports/`)
- **Logs** — log realtime từ planner/generator/executor
- **Test Cases** — bảng test case cập nhật trạng thái khi chạy
- **Footer** — ô nhập request, chọn phase, nút Run/Stop, progress

Phím tắt:

| Phím | Hành động |
|------|-----------|
| `r` | Chạy job |
| `c` | Dừng job (cooperative) |
| `1` `2` `3` `4` | Bật/tắt phase api / ui / chaos / performance |
| `f` | Focus ô request |
| `?` | Xem phím tắt |
| `q` | Thoát |

Human review: sau khi planner sinh Test Plan, modal mở ra cho phép **bật/tắt từng
test case** trước khi chạy. Test case bị bỏ sẽ không được generator sinh lại và
không chạy.

> Lệnh `python main.py run` vẫn hoạt động như cũ, dùng cho CI/headless.
````

- [ ] **Step 2: Cập nhật mục "Cấu trúc" trong README**

Sửa khối cấu trúc trong `README.md`, thêm vào sau dòng `├── main.py                 # CLI entrypoint`:

```
├── tui/                   # Textual TUI
│   ├── bus.py            # Event bus (thread-safe)
│   ├── gate.py           # ReviewGate (chặn thread chờ approve)
│   ├── runner.py         # JobRunner (chạy graph trong thread)
│   ├── filters.py        # Lọc test case theo phase
│   ├── review.py         # ReviewModel (bật/tắt test case)
│   ├── history_reader.py # Đọc reports/report_*.json
│   ├── app.py            # QCTApp
│   └── widgets/          # LogPane, CaseTable, HistoryPane, ReviewModal, StatusFooter
├── tests/                # pytest
```

- [ ] **Step 3: Thêm mục troubleshooting Python version**

Thêm vào cuối `README.md`:

```markdown
---

## Yêu cầu môi trường

- **Python 3.11+** (bắt buộc — code dùng `str | None` cần Python ≥ 3.10)
- Backend cần test đang chạy ở `DEFAULT_BASE_URL`
- `OPENAI_API_KEY` hợp lệ trong `.env`

Nếu gặp `TypeError: unsupported operand type(s) for |` → đang dùng Python < 3.10.
```

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "docs: document TUI command and Python 3.11 requirement

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Verification cuối

Chạy trước khi báo cáo hoàn thành:

```bash
source .venv/bin/activate

# 1. Toàn bộ test pass
pytest -q

# 2. Cả 2 lệnh CLI còn nguyên
python main.py --help
python main.py run --help
python main.py tui --help

# 3. TUI mở được
python main.py tui   # thoát bằng q

# 4. Không còn import lỗi
python -c "import agents.graph, tui.app; print('imports OK')"
```

Tất cả phải pass trước khi coi là xong. Nếu `pytest` fail → dừng, sửa, không báo cáo
"hoàn thành".
