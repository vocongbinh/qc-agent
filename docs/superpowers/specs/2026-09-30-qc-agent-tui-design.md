# QC Agent TUI — Design Spec

**Date:** 2026-09-30
**Status:** Approved
**Scope:** Textual TUI dashboard cho QC Agent (Phase 1–4)

---

## 1. Mục tiêu

Thêm giao diện TUI (terminal UI) cho QC Agent để thay thế trải nghiệm `python main.py run`
hiện tại vốn chỉ in log tuần tự ra terminal. TUI cho phép:

- Theo dõi job test đang chạy realtime (log, tiến độ, từng test case)
- Review Test Plan trước khi chạy, **có chọn lọc/bật-tắt từng test case**
- Xem lịch sử các lần chạy trước
- Chọn phase (api / ui / chaos / performance) khi tạo job

**Không nằm trong scope:**
- Chat-style từng lượt (chỉ có dashboard)
- SQLite / database (lịch sử đọc từ `reports/`)
- Viết lại executor thành async
- Thay đổi routing logic trong `agents/graph.py`
- Thay đổi hành vi của lệnh `python main.py run` (giữ nguyên cho CI/headless)

---

## 2. Quyết định kiến trúc

### 2.1 Hướng đã chọn: Worker thread + event bus (Hướng A)

LangGraph chạy trong **thread nền**, TUI chạy trên event loop chính. Giao tiếp qua
`asyncio.Queue` có thread-safe bridge.

**Lý do chọn A, không chọn async end-to-end (hướng B):**

Điều tra code cho thấy hướng B **không thuần async được** và không tiết kiệm công sức:

| Module | Blocking hiện tại | Ước lượng khi async |
|---|---|---|
| `agents/planner.py:144` | `llm.invoke()` | `ainvoke()` — bắt buộc, nếu không TUI treo 30s mỗi LLM call |
| `agents/generator.py:144` | `llm.invoke()` | `ainvoke()` — bắt buộc |
| `agents/api_executor.py:135` | `httpx.Client` | `httpx.AsyncClient` — dễ, ~50 dòng |
| `agents/ui_executor.py` (475 dòng) | `sync_playwright` | `async_playwright` — viết lại gần như toàn bộ 18KB |
| `agents/chaos_executor.py` (20KB) | `subprocess.run` (docker), Toxiproxy httpx sync, Redis/Kafka | **không async được** → `asyncio.to_thread` |
| `agents/performance_executor.py` (15KB) | `ThreadPoolExecutor`, k6 subprocess, lighthouse | **không async được** → `asyncio.to_thread` |

Hướng B thực tế chỉ async được 4/6 module, phần còn lại vẫn phải bridge qua thread.
Chi phí viết lại lớn hơn nhiều so với lợi ích nhận được ở giai đoạn này.

**Hệ quả của hướng A (chấp nhận được):**
- Cancel là **cooperative**: set `cancel_flag`, executor dừng sau node hiện tại.
  LLM call không interrupt được → có thể phải chờ hết planner (~30s).
  UI hiển thị "cancelling…" và disable nút.
- `ui_executor` chạy tuần tự, không song song. Đây là kỹ thuật nợ cho phase sau.

### 2.2 Ràng buộc Python

Venv hiện tại là **Python 3.9.6** và code **không chạy được** trên đó:
`config/settings.py:13` dùng `str | None` (PEP 604, cần Python ≥ 3.10), và pydantic
raise `TypeError: Unable to evaluate type annotation 'int | None'` ngay khi import.

Yêu cầu: **Python 3.11+**.

---

## 3. Kiến trúc & data flow

### 3.1 Cấu trúc thư mục mới

```
tui/
├── __init__.py
├── bus.py            # EventBus: emit(Event) -> asyncio.Queue
├── app.py            # QCTApp(App): layout, bindings
├── runner.py         # JobRunner: thread wrapper cho qc_graph
└── widgets/
    ├── __init__.py
    ├── history.py    # HistoryPane: đọc reports/report_*.json
    ├── logpane.py    # LogPane: RichLog realtime
    ├── casestable.py # CaseTable: DataTable test case + filter
    └── review.py     # ReviewModal: duyệt Test Plan
```

### 3.2 Event bus

Dataclass bất biến, đẩy vào queue từ thread worker, UI đọc trên event loop:

```python
@dataclass(frozen=True)
class Event:
    kind: str
    payload: dict[str, Any]
```

Các `kind` được emit:

| kind | payload | TUI phản ứng |
|---|---|---|
| `node_start` | `node: str` | breadcrumb + spinner |
| `node_end` | `node: str` | breadcrumb tick |
| `log` | `level: str`, `text: str` | append vào LogPane |
| `plan_ready` | `test_plan: dict` | mở ReviewModal, block worker |
| `test_result` | `id`, `type`, `status`, `duration_ms`, `error_message` | update dòng trong CaseTable |
| `phase_progress` | `phase: str`, `done: int`, `total: int` | progress bar |
| `run_done` | `report_path: str`, `summary: str` | bật nút mở report |
| `run_error` | `message: str` | banner lỗi |
| `cancelled` | `phase: str` | đánh dấu job đã dừng |

`EventBus.emit()` phải **thread-safe**: dùng `loop.call_soon_threadsafe(queue.put_nowait, event)`.

### 3.3 Luồng một job

```
User nhấn Run
  → JobRunner.spawn() tạo thread, sinh thread_id = uuid4()
  → thread chạy qc_graph.stream(initial_state, config)

  ── PAUSE 1: planner_done ──
  → bus.emit(plan_ready)
  → thread BLOCK tại review_gate.wait(timeout=300)
  → TUI mở ReviewModal, user bấm Approve hoặc Reject
  → main thread gọi review_gate.resolve(kept_test_cases | None)
      None = Reject → thread thoát, emit(cancelled)
  → thread: graph.update_state(config, {"test_plan": {..., "test_cases": kept},
                                         "human_approved": True})
  → thread: qc_graph.stream(None, config)

  ── PAUSE 2: generator_done ── (tự động, không cần UI)
  → lọc generated_tests theo state["phases"]
  → graph.update_state(config, {"generated_tests": filtered})
  → qc_graph.stream(None, config)

  → reporter xong → bus.emit(run_done)
```

Hai pause point là bắt buộc:
- **Pause 1** để human review có chọn lọc, và sửa `test_plan` *trước khi* generator
  chạy — generator nhận plan đã lọc nên không sinh lại test case bị bỏ.
- **Pause 2** vì generator có thể sinh thêm type ngoài ý muốn (prompt ghi "ưu tiên
  type=api" nhưng không ràng buộc). Lọc lần hai ở `generated_tests` là chốt chặn
  cuối, bảo đảm executor không chạy type ngoài `phases`.

### 3.4 Tương tác với LangGraph

`agents/graph.py:74` compile với `MemorySaver` checkpointer. Mỗi job dùng `thread_id`
uuid riêng nên **state các job tách biệt**, chạy song song an toàn dù dùng chung
singleton `qc_graph`.

`JobRunner` tái sử dụng đúng pattern của `main.py:89-124`: break khỏi generator
khi thấy `current_step == "planner_done"`, `update_state`, rồi `stream(None, config)`.

### 3.5 Im lặng console của agent khi chạy TUI

Executor in ra `Console()` riêng từ thread khác → xen kẽ với frame TUI, gây nhấp nháy.

**Cơ chế đã chốt: thay thế object `console` trong module agent.**

`rich.console.Console.file` resolve `sys.stdout` **tại thời điểm ghi**, nên redirect
`sys.stdout` sẽ phá luôn output của Textual. Thay vào đó, `silence_agent_console()`
trong `tui/bus.py`:

1. Duyệt `sys.modules`, chỉ các module tên bắt đầu bằng `agents.`
2. Với mỗi module có attribute `console` là `rich.console.Console`:
   set `module.console = Console(file=io.StringIO(), width=200)`
3. Idempotent — gọi nhiều lần cũng được

Buffer `StringIO` giữ lại output để `LogPane` có thể hiển thị lại nếu cần; dung lượng
tối đa 1000 dòng, gọi `truncate(0)` khi vượt.

Điều kiện: chỉ gọi khi khởi đột `python main.py tui`, sau khi đã import xong
`agents.*`. Lệnh `run` không bị ảnh hưởng — `silence_agent_console()` không được gọi.

---

## 4. UI Specification

### 4.1 Layout tổng thể

```
┌ QC Agent ──────────────────────────── py3.11 · api:8000 · gpt-4o ───────┐
│ ┌─History───┐ ┌─ Logs ─────────────────────┐ ┌─ Test Cases ───────────┐  │
│ │● #a3f2    │ │▶ planner  gpt-4o…          │ │ ID         Type  St   │  │
│ │  12 tests │ │  ✓ 6/6  1.2s              │ │ TC_LOGIN_001 api   ✓  │  │
│ │  2 failed │ │▶ generator…                │ │ TC_LOGIN_002 api   ✗  │  │
│ │  #9c1e    │ │▶ api executor  (12 cases)  │ │                        │  │
│ │  30 tests │ │  ✓ TC_LOGIN_001 (120ms)    │ │                        │  │
│ │  all pass │ │  ✗ TC_LOGIN_002 → 401…     │ │                        │  │
│ └───────────┘ └────────────────────────────┘ └────────────────────────┘  │
│ ▸ Request: Test toàn bộ auth API   [phase: api ▾] [▶ Run] [■ Stop]      │
│ ⣾ api 6/12   elapsed 0:42                                                  │
└──────────────────────────────────────────────────────────────────────────┘
```

**Cột 1 — History (width 26):** đọc `reports/report_*.json`, sắp xếp mới nhất trước.
Mỗi dòng: `mtime · total · pass/fail`. Click để nạp `execution_result` vào
CaseTable và LogPane. Thư mục không tồn tại hoặc rỗng → hiện "Chưa có lịch sử".

**Cột 2 — LogPane (flex):** `RichLog`, wrap, autoscroll trừ khi user đang scroll ngược.

**Cột 3 — CaseTable (width 46):** `DataTable`, cột `ID / Type / Status / ms`.
Sắp xếp theo thứ tự chạy. Row được tô màu theo status. Filter theo phase đang chọn.

**Hàng dưới:** Input request, dropdown phase, nút Run/Stop, progress + elapsed.

### 4.2 Bàn phím

| Phím | Hành động |
|---|---|
| `r` | Chạy job với nội dung ô Request |
| `c` | Cooperative cancel job đang chạy |
| `1` `2` `3` `4` | Bật/tắt lọc phase api / ui / chaos / performance |
| `f` | Focus ô Request |
| `enter` (trên CaseTable) | Mở chi tiết test case đang chọn |
| `?` | Bảng phím tắt |
| `q` | Thoát (có confirm nếu job đang chạy) |

### 4.3 Review modal

```
┌─ Review Test Plan ── "Test Plan – Authentication API" ─────────────┐
│ 12 cases · est. 20 min · 3 risks · 2 assumptions                   │
│ ┌─────────────────────────────────────────────────────────────────┐ │
│ │ [x] TC_LOGIN_001  critical  api   Đăng nhập thành công          │ │
│ │ [x] TC_LOGIN_002  critical  api   Sai password → 401             │ │
│ │ [ ] TC_UI_001    high      ui    Trang login render (headed)    │ │
│ └─────────────────────────────────────────────────────────────────┘ │
│ ↑↓ chọn · space bật/tắt · a bật tất cả · n bỏ tất cả               │
│                             [ ✗ Reject ]  [ ✓ Approve & Run ]       │
└─────────────────────────────────────────────────────────────────────┘
```

- Bấm **Reject** → `review_gate.resolve(None)`, thread thoát, `bus.emit(cancelled)`,
  không chạy executor.
- Bấm **Approve & Run** → `review_gate.resolve(kept_cases)`. Các case bị bỏ tick bị
  **lọc khỏi `test_plan["test_cases"]`** trong `update_state` (Pause 1). Generator
  nhận plan đã lọc nên không sinh lại case bị bỏ.
- **Không tick case nào** → Approve bị disable, chỉ có thể Reject.
- Không có phản hồi trong 300 giây → auto-resolve `None` (coi như Reject) +
  `bus.emit(run_error, "Review timeout 300s")`.

### 4.4 Chọn phase

Dropdown gồm 4 phase, mặc định tick `api`. Giá trị tick được map thành
`initial_state["phases"]`, dùng để lọc `generated_tests` sau generator
(test case có `type` không nằm trong `phases` sẽ bị loại).

---

## 5. Thay đổi cần thiết

### 5.1 File mới

| File | Nội dung |
|---|---|
| `tui/bus.py` | `Event` dataclass, `EventBus`, `silence_agent_console()` |
| `tui/runner.py` | `JobRunner`, `ReviewGate` |
| `tui/app.py` | `QCTApp` |
| `tui/widgets/*.py` | 4 widget |
| `tests/test_bus.py` | test event bus |
| `tests/test_runner.py` | test resume/reject/cancel |
| `tests/test_review.py` | test toggle + filter |
| `tests/test_history.py` | test parse report, empty dir |

### 5.2 File sửa

| File | Thay đổi |
|---|---|
| `requirements.txt` | `+textual>=0.60.0`, `+pytest`, `+pytest-asyncio` |
| `main.py` | Thêm command `tui`; giữ nguyên `run` |
| `agents/state.py` | `AgentState` += `phases: list[str]`, `job_id: str` (dùng `total=False` để không phá `main.py`) |
| `agents/api_executor.py` | Emit `test_result` (giữ nguyên `console.print`) |
| `agents/ui_executor.py` | Emit `test_result` |
| `agents/chaos_executor.py` | Emit `test_result` |
| `agents/performance_executor.py` | Emit `test_result` |
| `agents/planner.py`, `agents/generator.py` | Emit `node_start` / `node_end` |
| `agents/reporter.py` | Emit `run_done` |
| `README.md` | Document lệnh `tui` |

### 5.3 Ràng buộc quan trọng

- `agents/graph.py` **không sửa** — routing logic giữ nguyên.
- `main.py run` **không sửa hành vi** — vẫn chạy headless cho CI.
- Không thêm dependency ngoài `textual` + `pytest` (dev).

---

## 6. Xử lý lỗi

| Tình huống | Xử lý |
|---|---|
| `OPENAI_API_KEY` thiếu | Chặn ngay khi khởi động TUI, hiện banner hướng dẫn, không vào dashboard |
| Planner fail (LLM trả JSON dở) | `run_error` + log raw 800 ký tự, giữ modal đóng, job kết thúc |
| Generator fail | `run_error`, không chạy executor |
| `reports/` không tồn tại | HistoryPane hiện empty state, không crash |
| Report JSON hỏng (parse fail) | Bỏ qua file đó, hiện dim "1 file lỗi" cuối sidebar |
| Test case HTTP timeout | Đã xử lý ở `api_executor:104` (`timeout=30.0`, catch Exception) |
| Thread crash ngoài dự kiến | `JobRunner` bọc try/except, emit `run_error`, giải phóng gate để không treo |
| User đóng TUI khi job chạy | Confirm dialog; nếu xác nhận thì set cancel + chờ tối đa 5s rồi thoát |
| Review timeout | Auto-reject + `run_error` |

---

## 7. Testing

**Unit test (`pytest`):**
- `test_bus.py` — emit/subscribe đúng thứ tự, thread-safe không mất event
- `test_runner.py` — mock `qc_graph`: verify resume sau approve, reject dừng đúng,
  cancel flag hoạt động, thread crash không treo gate
- `test_review.py` — toggle case, lọc theo phase, danh sách rỗng
- `test_history.py` — parse report hợp lệ, thư mục rỗng, JSON hỏng

**Integration test:**
- Chạy thật `qc_graph` với state giả (`human_approved=True`, không test case)
  → xác nhận tới `reporter_done` và sinh file trong `reports/`

**Smoke test thủ công:**
1. `python main.py tui` với backend local, chạy 1 job phase api
2. Xác nhận log stream realtime, từng dòng test case cập nhật
3. Bỏ tick 1 test case ở review → xác nhận executor không chạy case đó
4. Bấm Stop giữa chừng → UI hiện "cancelling…"
5. Mở lại TUI → sidebar thấy run vừa rồi, click xem lại được

**Verification bắt buộc trước khi coi là xong:**
```bash
pytest -q                      # tất cả test pass
python main.py run --help      # CLI cũ không hỏng
python main.py tui --help      # lệnh mới hoạt động
```

---

## 8. Thứ tự triển khai

Mỗi bước là một commit riêng, chạy được sau mỗi bước.

| # | Bước | Nội dung | Rủi ro |
|---|---|---|---|
| 1 | Nền | Nâng Python 3.11, `pip install -r requirements.txt`, thêm `textual` + `pytest`, xác nhận `python main.py run --help` chạy | Thấp |
| 2 | Event bus | `tui/bus.py` + `test_bus.py` | Thấp |
| 3 | Job runner | `tui/runner.py` + `test_runner.py` — **làm trước UI vì đây là phần dễ vỡ nhất** | Cao |
| 4 | Widgets | `logpane`, `casestable`, `history`, `review` | Trung bình |
| 5 | App + nối CLI | `tui/app.py`, command `main.py tui`, cập nhật README | Trung bình |

Bước 3 tách riêng khỏi UI là cố ý: pattern `break` generator → `update_state` →
`stream(None)` từ `main.py:89-124` cần được xác nhận hoạt động với thread + gate
trước khi dựng giao diện lên trên.

---

## 9. Câu hỏi đã trả lời

| Câu hỏi | Trả lời |
|---|---|
| Thay thế hay bổ sung CLI? | **Bổ sung.** Thêm lệnh `tui`, giữ nguyên `run`. |
| CI headless? | `run` hiện tại phục vụ, không cần lệnh CI mới. |
| Luồng chính? | Dashboard theo job (Claude Code style), không phải chat. |
| Human review? | Modal, bật/tắt từng test case. |
| Phạm vi phase? | Cả 4, chọn khi tạo job. |
| Lịch sử? | Đọc `reports/`, không dùng SQLite. |
| Nguồn log? | Event bus mới, `console.print` giữ nguyên cho CLI. |
| Kiến trúc? | Hướng A (worker thread + event bus), không async end-to-end. |
