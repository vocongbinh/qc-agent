# QC Agent – Phase 1 (Foundation)

AI-powered Quality Control Agent built with **LangGraph**.

Phase 1 tập trung vào **API / Backend testing**:
- CLI Agent trên terminal
- Planner sinh Test Plan (schema gần chuẩn IEEE 829 / ISTQB)
- **Human review bắt buộc** trước khi chạy
- Generator chuẩn hóa test case API
- API Executor (httpx) hỗ trợ multi-step + biến `{{token}}`
- Reporter: terminal + JSON + Markdown + lưu test cases

Ngoài CLI headless, dự án có một **TUI dashboard** (`python main.py tui`) chạy
cùng một `qc_graph` — xem [Dùng TUI dashboard](#dùng-tui-dashboard).

---

## Yêu cầu môi trường

- **Python 3.11+ (bắt buộc).** Code dùng chú thích kiểu `str | None` ở
  `config/settings.py`, `tui/bus.py` và các module khác — toán tử `|` cho kiểu
  union chỉ có từ Python 3.10. Dự án được phát triển và test trên **Python
  3.12**.
  > Nếu bạn gặp `TypeError: unsupported operand type(s) for |` lúc import
  > `config.settings` hoặc `tui.bus` thì đó là dấu hiệu bạn đang chạy Python
  > < 3.10 — hãy nâng cấp interpreter, đừng sửa code.
- **Backend phải đang chạy** ở `DEFAULT_BASE_URL` (mặc định `http://localhost:8000`),
  nếu không các test case kiểu API sẽ fail.
- **`OPENAI_API_KEY` phải được set** (qua biến môi trường hoặc file `.env`).
  Cả `main.py tui` lẫn `main.py run` đều dừng ngay với thông báo rõ ràng nếu
  thiếu key này.

---

## Cấu trúc

```
qc-agent/
├── agents/
│   ├── state.py            # Schema TestCase / TestPlan (chuẩn hóa)
│   ├── planner.py          # Planner Agent
│   ├── generator.py        # Test Case Generator
│   ├── api_executor.py     # API runner (multi-step, context)
│   ├── ui_executor.py      # UI runner (Playwright)
│   ├── chaos_executor.py   # Chaos runner (Toxiproxy)
│   ├── performance_executor.py
│   ├── emitter.py          # Phát event sang EventBus của TUI
│   ├── reporter.py         # Report JSON + Markdown
│   └── graph.py            # LangGraph definition
├── tui/
│   ├── app.py              # QCTApp — dashboard Textual
│   ├── runner.py           # JobRunner: chạy qc_graph trong worker thread
│   ├── bus.py              # EventBus một chiều thread → event loop
│   ├── gate.py             # ReviewGate: chặn worker chờ human review
│   ├── review.py           # ReviewModel: trạng thái tick/untick test case
│   ├── history_reader.py   # Đọc report cũ cho sidebar
│   ├── filters.py          # Lọc test case theo phase
│   └── widgets/            # footer, case table, log pane, history, review modal
├── tools/
│   └── toxiproxy_client.py
├── config/settings.py
├── tests/                  # pytest + pytest-asyncio
├── test_cases/             # Generated test cases được lưu ở đây
├── reports/                # Report JSON + Markdown
├── main.py                 # CLI entrypoint (`run`, `version`, `tui`)
├── requirements.txt
└── .env.example
```

---

## Cài đặt

```bash
cd qc-agent
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env
# Sửa .env → điền OPENAI_API_KEY=sk-...
```

Chạy test:

```bash
.venv/bin/pytest -q
```

### Biến môi trường quan trọng

```env
OPENAI_API_KEY=sk-...
# DEFAULT_BASE_URL=http://localhost:8000   # Backend cần test
```

---

## Cách dùng

```bash
# Cơ bản
python main.py run "Test API login flow của hệ thống"

# Có tài liệu + OpenAPI
python main.py run "Test toàn bộ authentication API" \
  --doc ./docs/PRD.md \
  --doc ./docs/api.md \
  --openapi ./openapi.yaml \
  --code ./backend/src

# CI — bỏ human review
python main.py run "Smoke test login" --ci
```

### Flow khi chạy

1. **Planner** phân tích request + tài liệu → sinh Test Plan (JSON)
2. **Human Review** (bắt buộc) → bạn xem và approve / hủy
3. **Generator** chuẩn hóa chi tiết test case
4. **API Executor** chạy các test case (gọi backend thật)
5. **Reporter** in kết quả + lưu:
   - `reports/report_YYYYMMDD_HHMMSS.json`
   - `reports/report_YYYYMMDD_HHMMSS.md`
   - `test_cases/generated_YYYYMMDD_HHMMSS.json`

---

## Dùng TUI dashboard

```bash
python main.py tui
```

Mở dashboard Textual trên cùng terminal:

```
┌ Lịch sử ────────┬ Bảng test case ──────────────────────────┐
│ run-3  passed   │ id            status    type              │
│ run-2  failed   │ TC_LOGIN_001  passed    api               │
│ run-1  passed   │ TC_LOGIN_002  failed    api               │
│                 ├───────────────────────────────────────────┤
│                 │ Log: ▶ planner · ✓ generator · ! TC_002 ✗  │
├─────────────────┴───────────────────────────────────────────┤
│ [ Mô tả nhiệm vụ test…      ] [x]API [ ]UI [ ]Chaos [ ]Perf │
│ [▶ Run] [■ Stop]                Đang chạy · api · 2/10      │
└─────────────────────────────────────────────────────────────┘
```

- **Sidebar trái** — lịch sử các run trước, đọc từ `reports/`.
- **Giữa** — bảng test case, lọc theo các phase đang chọn; kết quả stream về
  ngay khi executor chạy xong từng case.
- **Log** — dòng sự kiện của agent (bắt đầu/kết thúc node, kết quả từng case,
  lỗi).
- **Dòng dưới** — nhập yêu cầu, chọn phase, nút Run/Stop (Stop bị disable khi
  không có run), thanh tiến trình.

### Phím tắt

| Phím   | Việc                                          |
|--------|-----------------------------------------------|
| `r`    | **Run** — bắt đầu chạy với yêu cầu đang nhập |
| `c`    | **Stop** — dừng run ở phase hiện tại         |
| `1`    | Bật/tắt phase **API**                        |
| `2`    | Bật/tắt phase **UI**                         |
| `3`    | Bật/tắt phase **Chaos**                      |
| `4`    | Bật/tắt phase **Performance**                |
| `f`    | **Focus** — nhảy con trỏ về ô nhập yêu cầu   |
| `?`    | **Help** — bảng phím tắt                     |
| `q`    | **Quit** — nhấn 2 lần nếu đang có run        |

Mặc định chỉ phase **API** được chọn. Run mà không chọn phase nào sẽ bị từ
chối ngay (thanh trạng thái báo lỗi, không spawn thread).

### Review: tick / untick từng test case

Khi Planner xong, TUI mở **modal review** với bảng test case — mỗi dòng mặc
định đều được tick. Bạn có thể:

- `↑` / `↓` di chuyển giữa các test case
- `space` tick/untick test case đang chọn
- `a` bật tất cả, `n` bỏ tất cả
- **✓ Approve & Run** để chạy, **✗ Reject** (hoặc `esc`) để hủy

**Test case bạn untick sẽ bị gỡ khỏi test plan _trước khi_ Generator chạy**
(`tui/runner.py` ghi plan đã lọc + `human_approved=True` vào graph state rồi
mới stream sang generator). Generator vì thế không sinh lại những case đó —
nếu không, chúng sẽ quay lại dù bạn đã bỏ. Bỏ tất cả thì không approve được,
và **Reject** nghĩa là dừng run ngay ở phase review.

Modal có timeout 300 giây: không duyệt thì worker coi như reject và dừng.

### CLI `run` không thay đổi

`python main.py run` **giữ nguyên** hành vi cũ và vẫn là đường chạy cho
**CI / headless** — đây là thứ script hoá gọi, không cần terminal thật. Lệnh
`tui` chỉ là một *entrypoint khác* trên cùng `qc_graph`; nó import Textual
trong lúc chạy, nên `main.py run` không phải trả giá import (và vẫn chạy được
ngay cả khi Textual chưa cài).

---

## Schema Test Case (Phase 1)

```json
{
  "id": "TC_AUTH_LOGIN_001",
  "title": "Đăng nhập thành công với tài khoản hợp lệ",
  "module": "Authentication",
  "requirement_id": "US-102",
  "priority": "critical",
  "type": "api",
  "preconditions": ["User active", "Chưa login"],
  "test_data": {
    "email": "user@test.com",
    "password": "Pass123!"
  },
  "steps": [
    {
      "step": 1,
      "action": "POST /api/v1/auth/login",
      "data": {"email": "{{email}}", "password": "{{password}}"}
    }
  ],
  "expected": {
    "status_code": 200,
    "body": {"token": "exists"},
    "extract": {"token": "token"}
  },
  "postconditions": ["User logged in"],
  "status": "untested"
}
```

- `{{email}}`, `{{password}}` được thay từ `test_data`.
- `extract` giúp lấy giá trị từ response để dùng cho step sau (ví dụ lấy `token` rồi gọi API có auth).

---

## Lưu ý quan trọng

- Phase 1 **ưu tiên API**. UI test sẽ làm ở Phase 2.
- Human review Test Plan là **bắt buộc** trong Phase 1.

---

## Tiếp theo

Xem file `../QC_Agent_Architecture.md` để biết roadmap Phase 2 (UI + self-healing), Phase 3 (Chaos), Phase 4 (Performance + Allure).
