# QC Agent — Architecture & Development Guidelines (AGENTS.md)

Tài liệu này đúc kết các quy chuẩn phát triển cốt lõi cho **QC Agent**, kế thừa và chọn lọc từ các dự án mã nguồn mở hàng đầu (**OpenCode**, **Oh My Pi**), tinh chỉnh phù hợp cho stack **Python 3.11+ / LangGraph / Textual**.

---

## 1. Kiến trúc hệ thống (System Architecture)

### 1.1 Phân tách trách nhiệm (Separation of Concerns)
* **TUI Layer (`tui/`):** Chỉ chịu trách nhiệm hiển thị giao diện terminal, bắt sự kiện phím chuột và điều hướng.
  - Tuyệt đối **không gọi trực tiếp** các tác vụ blocking (Playwright, Docker CLI, k6, LLM API) trên main thread của Textual.
  - Giao tiếp với Core thông qua **`EventBus` (`asyncio.Queue`)** một chiều. Mọi thay đổi trên màn hình đều phải đi qua `_handle_event`.
* **Core Agent Layer (`agents/`):**
  - Chạy trong **Worker Thread** tách biệt hoàn toàn với UI loop.
  - Bắn sự kiện ra ngoài thông qua `emitter` (`test_result`, `node_start`, `plan_ready`, `log`).
* **Auth & LLM Adapter Layer (`auth/`, `agents/llm_factory.py`):**
  - Quản lý token, tự động làm mới (Auto-Refresh) và khám phá model động (Dynamic Discovery) độc lập với UI.

---

## 2. Tiêu chuẩn Giao diện TUI (OpenCode Design Standards)

### 2.1 Hộp nhập lệnh 2 tầng (Two-Tier Prompt Box)
* **Tầng 1 (Prompt Input):** Chiếm trọn **100% chiều ngang màn hình** (`width: 1fr`). Viền khung rõ ràng (`border: tall $primary`), ký tự nhắc lệnh `❯`.
* **Tầng 2 (Status & Meta Bar):**
  - Bên trái: Các phím tắt hướng dẫn mờ (`Enter: Run · Ctrl+C: Stop · /: Lệnh · Ctrl+P: Palette`).
  - Ở giữa: Trạng thái tiến độ (`phase 1/4`) hoặc spinner khi đang xử lý.
  - Bên phải: Model Badge (`⚡ gemini-2.5-flash`) và trạng thái (`Sẵn sàng / Đang chạy...`).

### 2.2 Trải nghiệm điều hướng nhanh (Fast Interactions)
* **Autocomplete khi gõ `/`:** Popup gợi ý menu (`/model`, `/login`, `/status`, `/help`) nổi lên ngay trên thanh nhập, hỗ trợ phím `Tab` để tự điền, `↑↓` để chọn, `Esc` để đóng.
* **Command Palette (`Ctrl + P` hoặc `F1`):** Mọi thao tác và cấu hình phải tìm kiếm và thực thi được qua Command Palette mà không bắt buộc dùng chuột.
* **Selection Modals:** Danh sách chọn lựa (chọn Model, chọn Provider) phải hiển thị dạng popup overlay, hỗ trợ phím số nhanh (`1, 2, 3...`) và phím mũi tên.
* **Đảm bảo không vỡ layout:** Giao diện phải co giãn an toàn, không bị tràn hay rớt dòng trên các màn hình terminal hẹp chuẩn **`80x24`**.

### 2.3 Thuật ngữ & Hiển thị
* Giữ nguyên các thuật ngữ kỹ thuật chuẩn mực của developer: `API`, `UI`, `Chaos`, `Perf`, `Pass`, `Fail`, `Model`, `Provider`, `Token`, `Run`, `Stop`. Không dịch gượng gạo sang các từ tối nghĩa.

---

## 3. Phong cách viết Code (Python Code Style)

### 3.1 Tối giản & Tránh trừu tượng hóa sớm (No Premature Abstraction)
* **Giữ code tập trung:** Viết logic trong một hàm rõ ràng; không tự tiện tách nhỏ thành các hàm helper chỉ dùng một lần (single-use helpers) trừ khi có nhu cầu tái sử dụng thực tế hoặc giảm độ phức tạp rõ rệt.
* **Happy Path:** Viết hàm theo luồng chạy thành công chính. Các hàm phụ trợ chi tiết (nếu có) đặt ngay bên dưới hàm chính.

### 3.2 Kiểm soát luồng & Biến (Control Flow & Variables)
* **Early Return:** Hạn chế lồng `if/else` sâu. Luôn ưu tiên thoát sớm bằng guard clauses:
  ```python
  # Tốt
  if not condition:
      return None
  do_something()

  # Tránh
  if condition:
      do_something()
  else:
      return None
  ```
* **Hạn chế `try/catch` bừa bãi:** Chỉ bọc `try/except` tại các ranh giới I/O thực sự có nguy cơ lỗi (kết nối mạng, parse JSON không đáng tin, tương tác phần cứng/OS).

### 3.3 Kiểu dữ liệu & Type Hints (Modern Python 3.11+)
* Luôn khai báo Type Annotations đầy đủ cho các hàm và methods (`str | None`, `dict[str, Any]`, `list[str]`).
* Sử dụng Pydantic cho việc validate dữ liệu có cấu trúc và Structured Outputs từ LLM.

---

## 4. Nguyên tắc Kiểm thử (Testing Guidelines)

* **Kiểm thử trên implementation thật:** Hạn chế mock tối đa; không mock các hàm nội bộ của hệ thống.
* **Chỉ mock I/O bên ngoài:** Chỉ mock các tác vụ gọi ra Internet (Google OAuth HTTP requests, SSE streams, OpenAI API calls) hoặc các lệnh CLI của OS (k6, Docker) khi chạy trong unit test.
* **Đảm bảo test suite luôn xanh:** Mọi tính năng hoặc thay đổi giao diện mới phải đi kèm unit test và đảm bảo không phá vỡ bất kỳ test nào trước đó. Chạy toàn bộ test suite bằng:
  ```bash
  pytest
  ```
