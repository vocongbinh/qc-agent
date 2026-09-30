"""Test Case Generator – chuẩn hóa / bổ sung chi tiết test case từ Test Plan."""

from __future__ import annotations

import json
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from agents.state import AgentState, TestCase
from config.settings import settings


GENERATOR_SYSTEM = """Bạn là Test Case Generator trong QC Agent.
Nhiệm vụ: Dựa trên Test Plan đã được human approve, chuẩn hóa và bổ sung chi tiết các test case (api + ui).

Yêu cầu chung:
- Giữ nguyên id, title, module, requirement_id, priority, type nếu đã có.
- Đảm bảo mọi test case đều có đủ: preconditions, test_data, steps (đánh số), expected, postconditions.

Với type = "api":
- steps.action dạng "METHOD /path" (ví dụ: "POST /api/v1/auth/login").
- expected.status_code bắt buộc.
- expected.body dùng key hoặc "exists".
- Nếu login/API trả token: thêm "extract": {"token": "token"} (hoặc "access_token": "access_token") trong expected để Hybrid UI dùng được.

Với type = "ui":
- steps.action chỉ dùng: goto | fill | click | expect | press | wait.
- Ưu tiên selector ỔN ĐỊNH theo thứ tự:
  1. [data-testid="..."] hoặc [data-test="..."]
  2. role + name (ví dụ button với text rõ)
  3. label / placeholder
  4. id có ý nghĩa (#email, #password)
  5. Tránh class CSS random (css-xyz123, sc-abc...)
- data cho goto: {"url": "https://..."}
- data cho fill: {"selector": "[data-testid=email]", "value": "{{email}}"}
- data cho click: {"selector": "button[type=submit]", "value": null}
  hoặc selector text-friendly: "button:has-text('Đăng nhập')"
- data cho expect: {"selector": "[data-testid=dashboard]", "state": "visible"}
- Mỗi UI test case nên có bước expect cuối để xác nhận kết quả.
- preconditions phải nêu rõ trang bắt đầu (ví dụ: đang ở /login, chưa đăng nhập).


Với type = "chaos":
- Thêm field "chaos": {
    "action": "stop_container" | "concurrent" | "network_delay" | "toxiproxy_latency" | "toxiproxy_timeout" | "kill_redis" | "kill_kafka" | "kill_db" | "kafka_probe",
    "target": "redis",          // tên container Docker
    "restore": true,
    "concurrency": 50,          // cho concurrent
    "delay_ms": 2000,           // cho network_delay
    "timeout": 10
  }
- steps: HTTP probe sau khi inject fault, ví dụ action "GET /api/v1/health"
- expected: {"status_code": [200, 503], "must_not_status": [500], "min_success_rate": 0.8, "max_duration_ms": 5000}
- Mục tiêu: hệ thống degrade gracefully, không 500 trắng.


Với type = "performance":
- Field "performance": {
    "kind": "api" | "fe" | "k6",
    "vus": 10,
    "duration_sec": 15,
    "url": "https://..."   // cho FE
  }
- steps: API "GET /health" hoặc FE không bắt buộc nếu có performance.url
- expected: {
    "min_success_rate": 0.95,
    "p95_duration_ms": 500,
    "min_rps": 50,
    "max_page_load_ms": 3000,
    "max_lcp_ms": 2500
  }

Chỉ trả về JSON array các test case, không giải thích.

Ví dụ 1 phần tử:
{
  "id": "TC_AUTH_LOGIN_001",
  "title": "Đăng nhập thành công với tài khoản hợp lệ",
  "description": "...",
  "module": "Authentication",
  "requirement_id": "US-102",
  "priority": "critical",
  "type": "api",
  "author": "QC-Agent",
  "tags": ["auth", "login"],
  "preconditions": ["User active", "Chưa login"],
  "test_data": {"email": "user@test.com", "password": "Pass123!"},
  "environment": "staging",
  "steps": [
    {
      "step": 1,
      "action": "POST /api/v1/auth/login",
      "data": {"email": "{{email}}", "password": "{{password}}"},
      "description": "Gửi request login"
    }
  ],
  "expected": {
    "status_code": 200,
    "body": {"token": "exists"},
    "side_effects": ["Session created"],
    "response_time_ms_max": 2000
  },
  "postconditions": ["User logged in"],
  "status": "untested"
}
"""


def create_generator_llm():
    return ChatOpenAI(
        model=settings.generator_model,
        temperature=settings.temperature,
        api_key=settings.openai_api_key,
    )


def generator_node(state: AgentState) -> dict[str, Any]:
    """Node: chuẩn hóa + bổ sung chi tiết test case từ Test Plan đã approve."""
    if not state.get("test_plan"):
        return {
            "error": "Không có Test Plan để generate",
            "current_step": "generator_failed",
        }

    if not state.get("human_approved"):
        return {
            "error": "Test Plan chưa được human approve",
            "current_step": "generator_failed",
        }

    llm = create_generator_llm()
    plan_str = json.dumps(state["test_plan"], ensure_ascii=False, indent=2)

    messages = [
        SystemMessage(content=GENERATOR_SYSTEM),
        HumanMessage(
            content=f"Test Plan đã được approve:\n{plan_str}\n\n"
            "Hãy trả về JSON array các test case đã chuẩn hóa (ưu tiên type=api)."
        ),
    ]

    response = llm.invoke(messages)
    raw = response.content.strip()

    if raw.startswith("```"):
        lines = raw.split("\n")
        raw = "\n".join(lines[1:])
        if raw.endswith("```"):
            raw = raw[:-3]
        if raw.startswith("json"):
            raw = raw[4:]
    raw = raw.strip()

    try:
        tests = json.loads(raw)
        if not isinstance(tests, list):
            raise ValueError("Output không phải JSON array")

        # Validate từng test case nhẹ
        validated = []
        for t in tests:
            try:
                validated.append(TestCase.model_validate(t).model_dump())
            except Exception:
                # Giữ nguyên nếu validate fail (để debug)
                validated.append(t)

    except Exception as e:
        return {
            "generated_tests": [],
            "error": f"Generator lỗi: {e}\nRaw (first 600 chars): {raw[:600]}",
            "current_step": "generator_failed",
            "messages": [response],
        }

    return {
        "generated_tests": validated,
        "current_step": "generator_done",
        "error": None,
        "messages": [response],
    }
