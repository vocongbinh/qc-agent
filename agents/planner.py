"""Planner Agent – phân tích yêu cầu + tài liệu + code → Test Plan (chuẩn gần IEEE 829)."""

from __future__ import annotations

import json
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from agents.state import AgentState, TestPlan
from config.settings import settings


PLANNER_SYSTEM = """Bạn là Planner Agent chuyên nghiệp trong hệ thống QC Agent.
Nhiệm vụ: Phân tích yêu cầu của user, tài liệu (PRD, API doc, User Story...) và code để lập **Test Plan** đầy đủ, ưu tiên theo risk.

### Quy tắc quan trọng (Phase 1 + Phase 2):
1. **Mặc định ưu tiên type = "api"**. Chỉ tạo type = "ui" khi user yêu cầu UI/E2E. Chỉ tạo type = "chaos" khi user yêu cầu resilience/chaos. Chỉ tạo type = "performance" khi user yêu cầu performance / load test / lighthouse / web vitals.
2. Có thể tạo api + ui + chaos trong cùng 1 Test Plan nếu user yêu cầu đầy đủ.
3. Mỗi test case phải **nguyên tử**, **độc lập**, **tái lặp được**.
4. Phải có **preconditions**, **test_data**, **steps** đánh số, **expected** rõ ràng.
5. Gắn **requirement_id** nếu tìm thấy trong tài liệu (US-xxx, REQ-xxx...).
6. Gắn **module** rõ ràng (Authentication, Payment, Order...).
7. Với UI test, steps dùng action: goto | fill | click | expect | press | wait.

### Schema TestPlan (bắt buộc tuân thủ):
{
  "title": "Test Plan – Authentication API",
  "summary": "Kiểm thử các API liên quan đến đăng nhập, đăng xuất, refresh token",
  "scope": "api",
  "priority_order": ["critical", "high", "medium", "low"],
  "test_cases": [
    {
      "id": "TC_AUTH_LOGIN_001",
      "title": "Đăng nhập thành công với tài khoản hợp lệ",
      "description": "Xác minh API login trả về token khi credentials đúng",
      "module": "Authentication",
      "requirement_id": "US-102",
      "priority": "critical",
      "type": "api",
      "author": "QC-Agent",
      "tags": ["auth", "login", "smoke"],
      "preconditions": [
        "User đã được tạo và đang active trong hệ thống",
        "Chưa có session đăng nhập"
      ],
      "test_data": {
        "email": "test_user@company.com",
        "password": "P@ssw0rd2026"
      },
      "environment": "staging",
      "steps": [
        {
          "step": 1,
          "action": "POST /api/v1/auth/login",
          "data": {
            "email": "{{email}}",
            "password": "{{password}}"
          },
          "description": "Gửi request đăng nhập với credentials hợp lệ"
        }
      ],
      "expected": {
        "status_code": 200,
        "body": {
          "token": "exists",
          "user.id": "exists"
        },
        "side_effects": ["Session được tạo trên server"],
        "response_time_ms_max": 2000
      },
      "postconditions": [
        "User đã có session hợp lệ"
      ],
      "status": "untested"
    }
  ],
  "risks": ["Token hết hạn quá nhanh", "Rate limit chưa được test"],
  "assumptions": ["Backend đang chạy ở staging", "Test data đã được seed"],
  "estimated_duration_min": 20,
  "created_by": "QC-Agent-Planner"
}

Chỉ trả về JSON hợp lệ, không giải thích thêm, không bọc markdown.
"""


def create_planner_llm():
    return ChatOpenAI(
        model=settings.planner_model,
        temperature=settings.temperature,
        api_key=settings.openai_api_key,
    )


def planner_node(state: AgentState) -> dict[str, Any]:
    """Node: tạo Test Plan từ user request + documents + code."""
    llm = create_planner_llm()

    docs_content = "\n\n".join(state.get("documents") or ["(không có tài liệu)"])
    code_info = ", ".join(state.get("code_paths") or ["(không có đường dẫn code)"])

    user_content = f"""Yêu cầu của user:
{state["user_request"]}

Tài liệu:
{docs_content}

Đường dẫn code liên quan:
{code_info}

OpenAPI (nếu có):
{state.get("openapi_spec") or "(không có)"}

Lưu ý: Mặc định ưu tiên type="api".
- type="ui" khi user yêu cầu UI/E2E.
- type="chaos" khi resilience/redis/kafka/db chết/network.
- type="performance" khi load test, p95, RPS, lighthouse, web vitals, FE performance.
Chaos test case cần field "chaos": {"action": "stop_container"|"concurrent"|"network_delay", "target": "redis", "restore": true, "concurrency": 50}.
"""

    messages = [
        SystemMessage(content=PLANNER_SYSTEM),
        HumanMessage(content=user_content),
    ]

    response = llm.invoke(messages)
    raw = response.content.strip()

    # Loại bỏ markdown code block nếu có
    if raw.startswith("```"):
        lines = raw.split("\n")
        raw = "\n".join(lines[1:])
        if raw.endswith("```"):
            raw = raw[:-3]
        if raw.startswith("json"):
            raw = raw[4:]
    raw = raw.strip()

    try:
        plan_dict = json.loads(raw)
        # Validate bằng Pydantic
        TestPlan.model_validate(plan_dict)
    except Exception as e:
        return {
            "test_plan": None,
            "error": f"Planner không sinh được Test Plan hợp lệ: {e}\nRaw (first 800 chars): {raw[:800]}",
            "current_step": "planner_failed",
            "messages": [response],
        }

    return {
        "test_plan": plan_dict,
        "current_step": "planner_done",
        "error": None,
        "messages": [response],
        "human_approved": False,  # Bắt buộc human review ở Phase 1
    }
