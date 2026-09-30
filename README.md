# QC Agent – Phase 1 (Foundation)

AI-powered Quality Control Agent built with **LangGraph**.

Phase 1 tập trung vào **API / Backend testing**:
- CLI Agent trên terminal
- Planner sinh Test Plan (schema gần chuẩn IEEE 829 / ISTQB)
- **Human review bắt buộc** trước khi chạy
- Generator chuẩn hóa test case API
- API Executor (httpx) hỗ trợ multi-step + biến `{{token}}`
- Reporter: terminal + JSON + Markdown + lưu test cases

---

## Cấu trúc

```
qc-agent/
├── agents/
│   ├── state.py            # Schema TestCase / TestPlan (chuẩn hóa)
│   ├── planner.py          # Planner Agent
│   ├── generator.py        # Test Case Generator
│   ├── api_executor.py     # API runner (multi-step, context)
│   ├── reporter.py         # Report JSON + Markdown
│   └── graph.py            # LangGraph definition
├── config/settings.py
├── test_cases/             # Generated test cases được lưu ở đây
├── reports/                # Report JSON + Markdown
├── main.py                 # CLI entrypoint
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

### Biến môi trường quan trọng

```env
OPENAI_API_KEY=sk-...
# DEFAULT_BASE_URL=http://localhost:8000   # Backend cần test
```

---

## Cách dùng

```bash
# Cơ bản
python main.py "Test API login flow của hệ thống"

# Có tài liệu + OpenAPI
python main.py "Test toàn bộ authentication API" \
  --doc ./docs/PRD.md \
  --doc ./docs/api.md \
  --openapi ./openapi.yaml \
  --code ./backend/src
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

- **Backend phải đang chạy** ở `DEFAULT_BASE_URL` (mặc định `http://localhost:8000`).
- Phase 1 **ưu tiên API**. UI test sẽ làm ở Phase 2.
- Human review Test Plan là **bắt buộc** trong Phase 1.

---

## Tiếp theo

Xem file `../QC_Agent_Architecture.md` để biết roadmap Phase 2 (UI + self-healing), Phase 3 (Chaos), Phase 4 (Performance + Allure).
